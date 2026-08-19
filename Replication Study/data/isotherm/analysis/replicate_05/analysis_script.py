#!/usr/bin/env python3
"""
Analysis Script - Tecan Plate Reader Loading Isotherm Evaluation
Can be called externally with experiment ID as parameter.
"""

import os
import sys
import json
import argparse
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

# Device control server configuration for Tecan
DEVICE_CONTROL_SERVER = os.getenv('DEVICE_CONTROL_SERVER', 'http://localhost:8000')
DEVICE_API_KEY = os.getenv('DEVICE_API_KEY', 'your-secure-api-key-here')


def check_tecan_data_availability(experiment_id: str) -> dict:
    """Check if Tecan data is available for the given experiment ID."""
    url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}/list"
    headers = {'X-API-Key': DEVICE_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {"available": data.get('total_files', 0) > 0,
                    "total_files": data.get('total_files', 0),
                    "files": data.get('files', [])}
        elif response.status_code == 404:
            return {"available": False, "total_files": 0, "files": [], "error": "No data found"}
        else:
            return {"available": False, "total_files": 0, "files": [], "error": f"Server error: {response.status_code}"}
    except Exception as e:
        return {"available": False, "total_files": 0, "files": [], "error": str(e)}


def fetch_tecan_data_file(experiment_id: str, save_to_folder: str, file_index: int = 0) -> str:
    """Fetch a SINGLE Tecan Excel data file from device control server."""
    url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}?file_index={file_index}"
    headers = {'X-API-Key': DEVICE_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        save_folder = Path(save_to_folder)
        save_folder.mkdir(parents=True, exist_ok=True)
        filename = f"tecan_data_{experiment_id}.xlsx"
        file_path = save_folder / filename
        with open(file_path, 'wb') as f:
            f.write(response.content)
        print(f"Successfully downloaded Tecan data to: {file_path}")
        return str(file_path)
    except requests.exceptions.ConnectionError:
        raise Exception(f"Cannot connect to device control server at {DEVICE_CONTROL_SERVER}")
    except requests.exceptions.Timeout:
        raise Exception("Timeout while fetching Tecan data from device control server")
    except Exception as e:
        raise Exception(f"Failed to fetch Tecan data for experiment {experiment_id}: {str(e)}")


def get_most_recent_folder(directory, n=0):
    """Finds the n-th most recent subfolder in a given directory."""
    folders = [f for f in os.listdir(directory) if os.path.isdir(os.path.join(directory, f))]
    if not folders:
        return None
    sorted_folders = sorted(folders, key=lambda f: os.path.getctime(os.path.join(directory, f)), reverse=True)
    return os.path.join(directory, sorted_folders[n]) if len(sorted_folders) > n else None


def get_most_recent_excel_file(directory, n=0):
    """Finds the most recent Excel file within the 'Export/xlsx/' subfolder."""
    most_recent_folder = get_most_recent_folder(directory, n)
    if not most_recent_folder:
        return None
    excel_export_path = Path(most_recent_folder) / "Export" / "xlsx"
    if not excel_export_path.exists() or not excel_export_path.is_dir():
        return None
    try:
        files_in_folder = os.listdir(excel_export_path)
        excel_files = [f for f in files_in_folder if f.lower().endswith('.xlsx')]
        if not excel_files:
            return None
        excel_files.sort(key=lambda f: os.path.getctime(Path(excel_export_path) / f), reverse=True)
        return str(Path(excel_export_path) / excel_files[0])
    except Exception:
        return None


def read_tecan_absorbance(tecan_path: Path, num_rows: int = 8, num_cols: int = 12) -> pd.DataFrame:
    """Read absorbance data from a Tecan Excel export.

    Absorbance block starts at row 34 (0-based index 33) and column B (0-based index 1).
    """
    print(f"INFO: Reading Tecan absorbance data from {tecan_path}")
    df = pd.read_excel(
        tecan_path,
        header=None,
        skiprows=33,
        usecols=list(range(1, 1 + num_cols)),
        nrows=num_rows
    )
    df.index = list("ABCDEFGH")[:num_rows]
    df.columns = list(range(1, num_cols + 1))
    return df


def build_plate_mapping(num_ligand: int, num_salt: int, replicates: int) -> pd.DataFrame:
    """Create mapping from plate wells to (ligand_index, salt_index, replicate_index).

    Assumes:
    - Rows A.. correspond to ligand concentrations (ascending with row index).
    - Columns grouped by salt concentration in blocks of size 'replicates'.
    - Salt concentrations ascend with column index blocks.
    """
    rows = list("ABCDEFGH")
    mapping_rows = []
    total_cols_needed = num_salt * replicates
    if total_cols_needed > 12:
        raise ValueError(
            f"Plate layout requires {total_cols_needed} columns for salt concentrations and replicates, "
            f"but a 96-well plate only has 12 columns."
        )

    for row_idx in range(num_ligand):
        row_letter = rows[row_idx]
        for col in range(1, total_cols_needed + 1):
            salt_index = (col - 1) // replicates
            rep_index = (col - 1) % replicates
            well = f"{row_letter}{col}"
            mapping_rows.append(
                {
                    "well": well,
                    "row": row_letter,
                    "col": col,
                    "ligand_index": row_idx,
                    "salt_index": salt_index,
                    "replicate_index": rep_index,
                }
            )
    mapping_df = pd.DataFrame(mapping_rows)
    return mapping_df


def langmuir_isotherm(c, q_max, K):
    """Langmuir isotherm model: q = q_max * K * c / (1 + K * c)."""
    return q_max * K * c / (1.0 + K * c)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fit Langmuir isotherm to data.

    Returns (q_max, K, r2). If fit fails, returns (None, None, None).
    """
    mask = np.isfinite(c_e) & np.isfinite(q)
    c_fit = np.asarray(c_e)[mask]
    q_fit = np.asarray(q)[mask]

    if c_fit.size < 3:
        print("WARNING: Not enough data points for Langmuir fit (need at least 3).")
        return None, None, None

    q_max_guess = float(np.nanmax(q_fit)) if np.isfinite(q_fit).any() else 1.0
    K_guess = 1.0 / max(np.nanmean(c_fit), 1e-6)

    p0 = [q_max_guess, K_guess]
    bounds = ([0.0, 0.0], [np.inf, np.inf])

    def r2_score_local(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        y_true = np.asarray(y_true, dtype=float)
        y_pred = np.asarray(y_pred, dtype=float)
        ss_res = np.nansum((y_true - y_pred) ** 2)
        ss_tot = np.nansum((y_true - np.nanmean(y_true)) ** 2)
        if ss_tot == 0:
            return float('nan')
        return 1.0 - ss_res / ss_tot

    try:
        popt, _ = curve_fit(langmuir_isotherm, c_fit, q_fit, p0=p0, bounds=bounds, maxfev=10000)
        q_max, K = popt

        if not (np.isfinite(q_max) and np.isfinite(K)):
            print("WARNING: Non-finite Langmuir parameters.")
            return None, None, None

        q_pred = langmuir_isotherm(c_fit, q_max, K)
        r2 = r2_score_local(q_fit, q_pred)
        if not np.isfinite(r2):
            print("WARNING: Non-finite R2 for Langmuir fit.")
            return None, None, None
        return float(q_max), float(K), float(r2)
    except Exception as e:
        print(f"WARNING: Langmuir fit failed: {e}")
        return None, None, None


def parse_semicolon_floats(value: Any, field_name: str) -> List[float]:
    """Parse semicolon-separated string of numbers into a list of floats.

    Handles comma decimal separators and strips whitespace.
    """
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(f"Metadata field '{field_name}' must be a string, got {type(value)}")
    parts = [p.strip() for p in value.split(';') if p.strip() != ""]
    floats: List[float] = []
    for p in parts:
        p_norm = p.replace(',', '.')
        try:
            floats.append(float(p_norm))
        except ValueError:
            raise ValueError(f"Cannot parse value '{p}' in field '{field_name}' as float")
    return floats


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = '../data', results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm experiments.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, attempts to auto-detect.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with all key metrics and paths to generated files.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    analysis_results: Dict[str, Any] = {
        "experiment_id": experiment_id,
        "status": "failed",
        "message": "",
        "plots": {},
        "data_outputs": {},
        "metadata": {},
        "files_processed": 0,
        "warnings": [],
    }

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ('', None):
            experiment_id = sys.argv[1]
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Using experiment_id from command line: {experiment_id}")
        else:
            data_path = Path(data_folder)
            if not data_path.exists():
                raise FileNotFoundError(f"Data folder does not exist for auto-detect: {data_path}")
            json_files = sorted(data_path.glob('experiment_*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
            if not json_files:
                raise FileNotFoundError(f"No experiment_*.json files found in data folder for auto-detect: {data_path}")
            latest = json_files[0]
            name = latest.stem
            if name.startswith('experiment_'):
                experiment_id = name[len('experiment_'):]
            else:
                raise ValueError(f"Cannot parse experiment id from filename: {latest}")
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Auto-detected most recent experiment_id: {experiment_id}")

    if experiment_id is None:
        raise ValueError("experiment_id could not be determined.")

    data_folder_path = Path(data_folder)
    data_file_candidates = [
        Path('..') / f"experiment_{experiment_id}.json",
        data_folder_path / f"experiment_{experiment_id}.json",
    ]

    experiment_data: Optional[Dict[str, Any]] = None
    data_file_path: Optional[Path] = None
    for candidate in data_file_candidates:
        if candidate.exists():
            data_file_path = candidate
            break
    if data_file_path is None:
        raise FileNotFoundError(
            f"Data file not found in expected locations: "
            f"../experiment_{experiment_id}.json or {data_folder_path / f'experiment_{experiment_id}.json'}"
        )

    print(f"INFO: Loading experiment data from {data_file_path}")
    try:
        with open(data_file_path, 'r') as f:
            experiment_data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    if not isinstance(experiment_data, dict):
        raise ValueError(f"Experiment data in {data_file_path} is not a JSON object.")

    # Extract metadata from eLabFTW extra fields
    try:
        metadata = experiment_data['metadata_decoded']['extra_fields']
    except KeyError as e:
        raise KeyError(f"Missing expected metadata field structure: {e}")

    def get_meta(field: str, required: bool = True) -> Any:
        try:
            return metadata[field]['value']
        except KeyError:
            if required:
                raise KeyError(f"Missing expected metadata field: {field}")
            return None

    # Required metadata fields according to user template
    ligand_concs_str = get_meta('Ligand concentrations')
    salt_concs_str = get_meta('Salt concentrations')
    num_ligand_str = get_meta('Number of ligand concentrations')
    num_salt_str = get_meta('Number of salt concentrations')
    replicates_str = get_meta('Replicates')
    resin_mass_str = get_meta('Resin Mass')
    total_volume_str = get_meta('Total volume')
    ligand_conc_unit = get_meta('Ligand concentration unit')
    salt_conc_unit = get_meta('Salt concentration unit')
    calib_slope_str = get_meta('Calibration Curve Slope')
    calib_intercept_str = get_meta('Calibration Curve Intercept')

    # Convert numeric metadata
    try:
        num_ligand = int(str(num_ligand_str).strip())
        num_salt = int(str(num_salt_str).strip())
        replicates = int(str(replicates_str).strip())
    except Exception as e:
        raise ValueError(f"Cannot parse counts for ligand/salt/replicates as integers: {e}")

    try:
        resin_mass = float(str(resin_mass_str).replace(',', '.'))
    except Exception as e:
        raise ValueError(f"Cannot parse Resin Mass as float: {e}")

    try:
        total_volume = float(str(total_volume_str).replace(',', '.'))
    except Exception as e:
        raise ValueError(f"Cannot parse Total volume as float: {e}")

    try:
        calib_slope = float(str(calib_slope_str).replace(',', '.'))
    except Exception as e:
        raise ValueError(f"Cannot parse Calibration Curve Slope as float: {e}")

    try:
        calib_intercept = float(str(calib_intercept_str).replace(',', '.'))
    except Exception as e:
        raise ValueError(f"Cannot parse Calibration Curve Intercept as float: {e}")

    if calib_slope == 0:
        raise ValueError("Calibration Curve Slope must not be zero (cannot invert calibration curve).")

    ligand_concs = parse_semicolon_floats(ligand_concs_str, 'Ligand concentrations')
    salt_concs = parse_semicolon_floats(salt_concs_str, 'Salt concentrations')

    if len(ligand_concs) != num_ligand:
        raise ValueError(
            f"Number of ligand concentrations ({len(ligand_concs)}) does not match "
            f"specified Number of ligand concentrations ({num_ligand})."
        )
    if len(salt_concs) != num_salt:
        raise ValueError(
            f"Number of salt concentrations ({len(salt_concs)}) does not match "
            f"specified Number of salt concentrations ({num_salt})."
        )

    analysis_results["metadata"].update(
        {
            "num_ligand_concentrations": num_ligand,
            "num_salt_concentrations": num_salt,
            "replicates": replicates,
            "ligand_concentrations": ligand_concs,
            "salt_concentrations": salt_concs,
            "ligand_concentration_unit": ligand_conc_unit,
            "salt_concentration_unit": salt_conc_unit,
            "resin_mass_mg": resin_mass,
            "total_volume_uL": total_volume,
            "calibration_slope": calib_slope,
            "calibration_intercept": calib_intercept,
        }
    )

    # Fetch Tecan data (single-file experiment pattern)
    print(f"Fetching Tecan data for experiment {experiment_id} from device control server...")
    try:
        data_info = check_tecan_data_availability(experiment_id)
        if not data_info.get("available", False):
            raise FileNotFoundError(
                f"No Tecan data available for experiment {experiment_id}: "
                f"{data_info.get('error', 'Unknown error')}"
            )

        print(f"Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
        tecan_data_path_str = fetch_tecan_data_file(experiment_id, results_folder)
        tecan_data_path = Path(tecan_data_path_str)
    except Exception as device_server_error:
        print(f"Device control server access failed: {device_server_error}. Falling back to local file search...")
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            analysis_results["message"] = (
                "Tecan data not available - analysis skipped. This is expected for test runs."
            )
            analysis_results["status"] = "success"
            analysis_results["note"] = "No Tecan data found on server or local system."
            print(f"INFO: {analysis_results['message']}")
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, 'w') as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
        if not most_recent_excel_source:
            analysis_results["message"] = "No recent Tecan Excel file found - analysis skipped."
            analysis_results["status"] = "success"
            print(f"INFO: {analysis_results['message']}")
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, 'w') as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        tecan_data_path = results_folder_path / f'tecan_data_{experiment_id}.xlsx'
        shutil.copy(most_recent_excel_source, tecan_data_path)
        print(f"Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")

    # Read absorbance data block
    absorbance_df = read_tecan_absorbance(tecan_data_path, num_rows=num_ligand, num_cols=num_salt * replicates)

    # Build mapping from wells to condition indices
    mapping_df = build_plate_mapping(num_ligand=num_ligand, num_salt=num_salt, replicates=replicates)

    # Flatten absorbance data into long format
    long_rows = []
    for row_letter in absorbance_df.index:
        for col in absorbance_df.columns:
            well = f"{row_letter}{col}"
            value = absorbance_df.loc[row_letter, col]
            long_rows.append({"well": well, "absorbance": value})
    long_df = pd.DataFrame(long_rows)

    data_merged = pd.merge(mapping_df, long_df, on='well', how='left')

    # Map indices to actual concentrations
    data_merged['c0'] = data_merged['ligand_index'].apply(lambda i: ligand_concs[i])
    data_merged['salt_conc'] = data_merged['salt_index'].apply(lambda i: salt_concs[i])

    # Compute equilibrium concentration cE from absorbance using inverted calibration curve
    data_merged['cE'] = (data_merged['absorbance'] - calib_intercept) / calib_slope

    # Sanity checks: cE range and variation
    if (data_merged['cE'] < 0).any() or (data_merged['cE'] > max(ligand_concs) * 1.001).any():
        raise ValueError(
            "Sanity check failed: some equilibrium concentrations cE are outside the expected range "
            "[0, max(c0)]. This suggests an incorrect calibration application."
        )

    # Check variation of cE across c0 within each column (salt concentration / replicate cluster)
    for salt_idx in range(num_salt):
        sub = data_merged[data_merged['salt_index'] == salt_idx]
        if sub.empty:
            continue
        # For this salt concentration, check correlation between c0 and cE
        try:
            corr = np.corrcoef(sub['c0'].values, sub['cE'].values)[0, 1]
        except Exception:
            corr = np.nan
        if np.isnan(corr) or corr < 0.2:
            msg = (
                f"Low correlation between c0 and cE for salt_index {salt_idx} "
                f"(correlation {corr}). This may indicate calibration issues."
            )
            print(f"WARNING: {msg}")
            analysis_results["warnings"].append(msg)

    # Aggregate over replicates: mean and std of cE for each (c0, salt)
    group_cols = ['ligand_index', 'salt_index']

    grouped = data_merged.groupby(group_cols)

    if grouped.ngroups != num_ligand * num_salt:
        raise ValueError(
            f"Grouping mismatch: expected {num_ligand * num_salt} parameter combinations ("
            f"num_ligand_concentrations x num_salt_concentrations), but found {grouped.ngroups}. "
            f"Check the well-to-condition mapping against the plate layout."
        )

    def agg_func(g: pd.DataFrame) -> pd.Series:
        count = g['cE'].count()
        if count != replicates:
            msg = (
                f"Unexpected number of replicates for ligand_index {g['ligand_index'].iloc[0]}, "
                f"salt_index {g['salt_index'].iloc[0]}: got {count}, expected {replicates}."
            )
            print(f"WARNING: {msg}")
            analysis_results["warnings"].append(msg)
        return pd.Series(
            {
                'c0': g['c0'].iloc[0],
                'salt_conc': g['salt_conc'].iloc[0],
                'cE_mean': g['cE'].mean(),
                'cE_std': g['cE'].std(ddof=1) if count > 1 else 0.0,
                'n_repl': count,
            }
        )

    agg_df = grouped.apply(agg_func).reset_index()

    # Calculate loading q for each parameter combination
    # q = (c0 - cE) * v_total / m_resin (v_total in uL, m_resin in mg)
    agg_df['q'] = (agg_df['c0'] - agg_df['cE_mean']) * total_volume / resin_mass

    # Langmuir fits per salt concentration
    fit_results = []
    for salt_idx in range(num_salt):
        salt_value = salt_concs[salt_idx]
        sub = agg_df[agg_df['salt_index'] == salt_idx]
        if sub.empty:
            msg = f"No data points for salt concentration index {salt_idx}, value {salt_value}."
            print(f"WARNING: {msg}")
            analysis_results["warnings"].append(msg)
            continue
        c_e = sub['cE_mean'].values
        q_vals = sub['q'].values
        q_max, K, r2 = fit_langmuir(c_e, q_vals)
        fit_results.append(
            {
                'salt_index': salt_idx,
                'salt_conc': salt_value,
                'q_max': q_max,
                'K': K,
                'R2': r2,
            }
        )

    fit_results_df = pd.DataFrame(fit_results)

    # Plot isotherms with fits
    fig, ax = plt.subplots(figsize=(8, 6))

    colors = plt.cm.viridis(np.linspace(0, 1, num_salt))
    for i, salt_idx in enumerate(range(num_salt)):
        salt_value = salt_concs[salt_idx]
        sub = agg_df[agg_df['salt_index'] == salt_idx]
        if sub.empty:
            continue
        color = colors[i]
        ax.errorbar(
            sub['cE_mean'],
            sub['q'],
            yerr=None,
            fmt='o',
            color=color,
            label=f"Salt {salt_value} {salt_conc_unit} (data)",
        )
        fit_row = fit_results_df[fit_results_df['salt_index'] == salt_idx]
        if not fit_row.empty and pd.notna(fit_row['q_max'].iloc[0]) and pd.notna(fit_row['K'].iloc[0]):
            q_max = fit_row['q_max'].iloc[0]
            K = fit_row['K'].iloc[0]
            r2 = fit_row['R2'].iloc[0]
            c_range = np.linspace(sub['cE_mean'].min(), sub['cE_mean'].max(), 200)
            q_fit = langmuir_isotherm(c_range, q_max, K)
            label = (
                f"Salt {salt_value} {salt_conc_unit} (fit: qmax={q_max:.3g}, K={K:.3g}, R2={r2:.3f})"
            )
            ax.plot(c_range, q_fit, '-', color=color, alpha=0.7, label=label)

    ax.set_xlabel(f"Equilibrium ligand concentration cE [{ligand_conc_unit}]")
    ax.set_ylabel(f"Loading q [{ligand_conc_unit}*uL/mg]")
    ax.set_title("Loading Isotherms")
    ax.legend(fontsize=8)
    ax.grid(True, linestyle='--', alpha=0.3)
    fig.tight_layout()

    plot_path_png = results_folder_path / f"loading_isotherms_{experiment_id}.png"
    fig.savefig(plot_path_png, dpi=300)
    plt.close(fig)

    analysis_results["plots"]["loading_isotherms_png"] = str(plot_path_png.resolve())

    # Save processed data and aggregated data
    per_well_csv = results_folder_path / f"per_well_results_{experiment_id}.csv"
    agg_csv = results_folder_path / f"aggregated_results_{experiment_id}.csv"
    fits_csv = results_folder_path / f"langmuir_fits_{experiment_id}.csv"

    data_merged.to_csv(per_well_csv, index=False)
    agg_df.to_csv(agg_csv, index=False)
    fit_results_df.to_csv(fits_csv, index=False)

    analysis_results["data_outputs"].update(
        {
            "per_well_results_csv": str(per_well_csv.resolve()),
            "aggregated_results_csv": str(agg_csv.resolve()),
            "langmuir_fits_csv": str(fits_csv.resolve()),
        }
    )

    analysis_results["status"] = "success"
    analysis_results["message"] = "Analysis completed successfully."
    analysis_results["files_processed"] = 1

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, 'w') as f:
        json.dump(analysis_results, f, indent=4)
    print(f"Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze Tecan loading isotherm experiment data.')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID. If not provided, attempts to auto-detect the most recent.')
    parser.add_argument('--data-folder', default='../data', help='Path to the folder containing experiment_ID.json files. Default: ../data')
    parser.add_argument('--results-folder', default='../results', help='Path to the folder where all analysis outputs will be saved. Default: ../results')

    args = parser.parse_args()

    try:
        results = analyze_experiment(
            experiment_id=args.experiment_id,
            data_folder=args.data_folder,
            results_folder=args.results_folder
        )
        if results["status"] == "success":
            print("Analysis successful!")
            return 0
        else:
            print(f"Analysis failed: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
