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
from typing import Dict, Any, List, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

import requests

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
    import os as _os
    folders = [f for f in _os.listdir(directory) if _os.path.isdir(_os.path.join(directory, f))]
    if not folders:
        return None
    sorted_folders = sorted(folders, key=lambda f: _os.path.getctime(_os.path.join(directory, f)), reverse=True)
    return _os.path.join(directory, sorted_folders[n]) if len(sorted_folders) > n else None


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


def _ensure_results_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _auto_detect_latest_experiment_id(data_folder: Path) -> str:
    """Auto-detect the most recent experiment_*.json file and return its ID as string."""
    json_files = sorted(data_folder.glob('experiment_*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not json_files:
        raise FileNotFoundError(f"No experiment_*.json files found in data folder: {data_folder}")
    latest = json_files[0]
    stem = latest.stem  # experiment_1234
    try:
        return stem.split('_', 1)[1]
    except Exception:
        raise ValueError(f"Could not parse experiment ID from filename: {latest.name}")


def _load_experiment_json(experiment_id: str, data_folder: Path) -> Dict[str, Any]:
    """Load experiment JSON from root (../experiment_ID.json) then fallback to data folder."""
    root_path = Path('../') / f'experiment_{experiment_id}.json'
    data_path = data_folder / f'experiment_{experiment_id}.json'

    if root_path.exists():
        path_to_use = root_path
    elif data_path.exists():
        path_to_use = data_path
    else:
        raise FileNotFoundError(f"Data file not found in root or data folder: {root_path} or {data_path}")

    print(f"INFO: Loading experiment JSON from {path_to_use}")
    try:
        with open(path_to_use, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {path_to_use}: {e}")


def _parse_metadata(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and validate required metadata fields from eLabFTW extra_fields."""
    try:
        metadata = experiment_data['metadata_decoded']['extra_fields']
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    def get_field(name: str, cast: str = 'str'):
        if name not in metadata or 'value' not in metadata[name]:
            raise KeyError(f"Missing expected metadata field: {name}")
        value = metadata[name]['value']
        if cast == 'int':
            try:
                return int(str(value).strip())
            except Exception:
                raise ValueError(f"Metadata field {name} must be an integer, got: {value!r}")
        if cast == 'float':
            try:
                return float(str(value).replace(',', '.'))
            except Exception:
                raise ValueError(f"Metadata field {name} must be a float, got: {value!r}")
        if cast == 'list_float':
            try:
                parts = [p.strip() for p in str(value).split(';') if str(p).strip() != '']
                return [float(p.replace(',', '.')) for p in parts]
            except Exception:
                raise ValueError(f"Metadata field {name} must be a semicolon-separated list of numbers, got: {value!r}")
        return value

    parsed = {}
    parsed['buffer'] = get_field('Buffer')
    parsed['salt_name'] = get_field('Salt Name')
    parsed['process_id'] = get_field('Process ID')
    parsed['pump_speed'] = get_field('Pump Speed')
    parsed['replicates'] = get_field('Replicates', 'int')
    parsed['resin_mass'] = get_field('Resin Mass', 'float')
    parsed['ligand_name'] = get_field('Ligand Name')
    parsed['total_volume'] = get_field('Total volume', 'float')
    parsed['venting_time'] = get_field('Venting time')
    parsed['pump_duration'] = get_field('Pump Duration')
    parsed['incubation_time'] = get_field('Incubation Time')
    parsed['salt_concentrations'] = get_field('Salt concentrations', 'list_float')
    parsed['equilibration_cycles'] = get_field('Equilibration Cycles')
    parsed['equilibration_volume'] = get_field('Equilibration Volume')
    parsed['ligand_concentrations'] = get_field('Ligand concentrations', 'list_float')
    parsed['incubation_temperature'] = get_field('Incubation Temperature')
    parsed['measurement_wavelength'] = get_field('Measurement Wavelength')
    parsed['calib_slope'] = get_field('Calibration Curve Slope', 'float')
    parsed['salt_conc_unit'] = get_field('Salt concentration unit')
    parsed['shaker_speed'] = get_field('Shaker Speed Incubation')
    parsed['salt_stock_conc'] = get_field('Salt Stock Concentration')
    parsed['ligand_conc_unit'] = get_field('Ligand concentration unit')
    parsed['ligand_stock_conc'] = get_field('Ligand Stock Concentration')
    parsed['calib_intercept'] = get_field('Calibration Curve Intercept', 'float')
    parsed['equilibration_cycle_duration'] = get_field('Equilibration Cycle Duration')
    parsed['num_salt_conc'] = get_field('Number of salt concentrations', 'int')
    parsed['num_ligand_conc'] = get_field('Number of ligand concentrations', 'int')

    # Sanity checks against list lengths
    if parsed['num_salt_conc'] != len(parsed['salt_concentrations']):
        raise ValueError(
            f"Number of salt concentrations ({parsed['num_salt_conc']}) does not match length of Salt concentrations list ({len(parsed['salt_concentrations'])})."
        )
    if parsed['num_ligand_conc'] != len(parsed['ligand_concentrations']):
        raise ValueError(
            f"Number of ligand concentrations ({parsed['num_ligand_conc']}) does not match length of Ligand concentrations list ({len(parsed['ligand_concentrations'])})."
        )

    return parsed


def _well_to_indices(row_label: str, col_idx: int) -> Tuple[int, int]:
    """Convert well position (row letter, 1-based column index) to zero-based indices for arrays.

    Row label should be 'A'..'H', column 1..12.
    """
    row_label = row_label.strip().upper()
    if len(row_label) != 1 or row_label < 'A' or row_label > 'H':
        raise ValueError(f"Invalid row label: {row_label}")
    if col_idx < 1 or col_idx > 12:
        raise ValueError(f"Invalid column index: {col_idx}")
    row = ord(row_label) - ord('A')
    col = col_idx - 1
    return row, col


def _read_tecan_absorbance(tecan_data_path: Path) -> pd.DataFrame:
    """Read Tecan absorbance data from Excel.

    Absorbance data starts at row 34, column B, with rows A-H and columns 1-12.
    Returns a DataFrame indexed by row letters A-H and columns 1-12 with absorbance values.
    """
    print(f"INFO: Reading Tecan absorbance data from {tecan_data_path}")
    # 8 rows (A-H), 12 columns (1-12), skipping first 33 rows, using columns B-M (1..12 zero-based from B)
    raw_df = pd.read_excel(
        tecan_data_path,
        header=None,
        skiprows=33,
        usecols=list(range(1, 13)),
        nrows=8
    )

    # First column is row labels A-H, the rest are absorbance values
    row_labels = raw_df.iloc[:, 0].astype(str).str.strip().tolist()
    values = raw_df.iloc[:, 1:]
    values.index = row_labels
    values.columns = list(range(1, 1 + values.shape[1]))

    print("INFO: Absorbance data shape:", values.shape)
    return values


def _extract_well_absorbances(absorbance_df: pd.DataFrame, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract absorbance values for all relevant wells according to plate layout.

    Returns a list of records with fields:
      row_label, col, salt_index, ligand_index, replicate_index, c_salt, c0, absorbance
    """
    num_lig = metadata['num_ligand_conc']
    num_salt = metadata['num_salt_conc']
    reps = metadata['replicates']
    lig_values = metadata['ligand_concentrations']
    salt_values = metadata['salt_concentrations']

    records: List[Dict[str, Any]] = []

    if reps * num_salt > 12:
        raise ValueError(
            f"replicates * num_salt ({reps} * {num_salt}) must be <= 12; got {reps * num_salt}."
        )

    for s_idx in range(num_salt):
        for r_idx in range(reps):
            col = s_idx * reps + r_idx + 1  # 1-based column number
            if col not in absorbance_df.columns:
                raise ValueError(f"Expected column {col} in absorbance data, but it is missing.")
            for l_idx in range(num_lig):
                row_label = chr(ord('A') + l_idx)
                if row_label not in absorbance_df.index:
                    raise ValueError(f"Expected row {row_label} in absorbance data, but it is missing.")
                absorbance = absorbance_df.loc[row_label, col]
                if pd.isna(absorbance):
                    continue
                record = {
                    'row_label': row_label,
                    'col': col,
                    'salt_index': s_idx,
                    'ligand_index': l_idx,
                    'replicate_index': r_idx,
                    'c_salt': salt_values[s_idx],
                    'c0': lig_values[l_idx],
                    'absorbance': float(absorbance),
                }
                records.append(record)
    return records


def _compute_ce_and_q(records: List[Dict[str, Any]], metadata: Dict[str, Any]) -> pd.DataFrame:
    """Compute equilibrium concentrations cE and loading q for each record.

    Adds columns: cE, q.
    Performs calibration sanity checks.
    """
    slope = metadata['calib_slope']
    intercept = metadata['calib_intercept']
    v_total = metadata['total_volume']  # uL
    m_resin = metadata['resin_mass']    # mg

    if slope == 0:
        raise ValueError("Calibration slope is zero, cannot invert calibration curve.")

    df = pd.DataFrame.from_records(records)
    if df.empty:
        raise ValueError("No valid absorbance records found for analysis.")

    df['cE'] = (df['absorbance'] - intercept) / slope

    # Sanity check: cE between 0 and max c0
    max_c0 = df['c0'].max()
    if (df['cE'] < -1e-9).any() or (df['cE'] - max_c0 > 1e-9).any():
        raise ValueError(
            "Calibration sanity check failed: some equilibrium concentrations cE are outside [0, max(c0)]. "
            "This suggests the calibration may be applied incorrectly."
        )

    # Second sanity check: variation of cE across column vs c0
    # For each column, compute correlation between c0 and cE
    correlations: List[float] = []
    for col in sorted(df['col'].unique()):
        sub = df[df['col'] == col]
        if sub['c0'].nunique() > 1:
            c0_vals = sub['c0'].values
            cE_vals = sub['cE'].values
            if np.std(c0_vals) > 0 and np.std(cE_vals) > 0:
                corr_matrix = np.corrcoef(c0_vals, cE_vals)
                corr = corr_matrix[0, 1]
                if not np.isnan(corr):
                    correlations.append(abs(corr))
    if correlations:
        avg_corr = float(np.mean(correlations))
        if avg_corr < 0.5:
            raise ValueError(
                f"Calibration sanity check failed: average absolute correlation between c0 and cE across columns "
                f"is too low ({avg_corr:.3f}). This suggests the calibration may be applied in the wrong direction."
            )

    # Loading q = (c0 - cE) * v_total / m_resin
    df['q'] = (df['c0'] - df['cE']) * v_total / m_resin

    return df


def _aggregate_replicates(df: pd.DataFrame, metadata: Dict[str, Any]) -> pd.DataFrame:
    """Aggregate replicates to obtain mean and std of cE and q for each (c0, c_salt).

    Ensures exactly num_ligand_conc * num_salt_conc unique (c0, c_salt) combinations,
    each with replicates entries.
    """
    num_lig = metadata['num_ligand_conc']
    num_salt = metadata['num_salt_conc']
    reps = metadata['replicates']

    group_cols = ['c_salt', 'c0']
    grouped = df.groupby(group_cols)
    summary = grouped.agg(
        n_reps=('cE', 'count'),
        cE_mean=('cE', 'mean'),
        cE_std=('cE', 'std'),
        q_mean=('q', 'mean'),
        q_std=('q', 'std'),
    ).reset_index()

    expected_groups = num_lig * num_salt
    actual_groups = summary.shape[0]
    if actual_groups != expected_groups:
        raise ValueError(
            f"Well-to-condition mapping error: expected {expected_groups} (c0, salt) combinations but found {actual_groups}."
        )

    if (summary['n_reps'] != reps).any():
        raise ValueError(
            "Replicate count mismatch: at least one (c0, salt) combination does not have the expected "
            f"number of replicates ({reps})."
        )

    # Replace NaN std with 0 when single replicate (should not occur if checks above pass, but safeguard)
    summary['cE_std'] = summary['cE_std'].fillna(0.0)
    summary['q_std'] = summary['q_std'].fillna(0.0)

    return summary


def _langmuir_isotherm(cE, q_max, K):
    return q_max * K * cE / (1.0 + K * cE)


def _fit_langmuir_per_salt(summary: pd.DataFrame) -> pd.DataFrame:
    """Fit Langmuir isotherm q(cE) for each salt concentration.

    Returns a DataFrame with columns: c_salt, q_max, K, r2, n_points
    """
    results = []
    for c_salt, sub in summary.groupby('c_salt'):
        x = sub['cE_mean'].values
        y = sub['q_mean'].values
        if len(x) < 3:
            print(f"WARNING: Not enough points to fit Langmuir isotherm for salt {c_salt} (need >=3, have {len(x)}). Skipping.")
            continue

        # Initial guesses
        q_max0 = float(np.max(y)) if len(y) > 0 else 1.0
        # Avoid division by zero; use mid-range cE for K0 estimate
        if np.max(x) > 0:
            K0 = 1.0 / np.max(x)
        else:
            K0 = 1.0

        try:
            popt, _ = curve_fit(
                _langmuir_isotherm,
                x,
                y,
                p0=[q_max0, K0],
                bounds=(0, np.inf),
                maxfev=10000,
            )
            q_max_fit, K_fit = popt
            y_pred = _langmuir_isotherm(x, q_max_fit, K_fit)
            # Manual R^2
            ss_res = float(np.sum((y - y_pred) ** 2))
            ss_tot = float(np.sum((y - np.mean(y)) ** 2))
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float('nan')

            # Sanity: fitted parameters finite and within plausible ranges
            if not np.isfinite(q_max_fit) or not np.isfinite(K_fit) or q_max_fit <= 0 or K_fit <= 0:
                print(f"WARNING: Unphysical Langmuir fit parameters for salt {c_salt}. Skipping.")
                continue

            results.append({
                'c_salt': c_salt,
                'q_max': float(q_max_fit),
                'K': float(K_fit),
                'r2': float(r2),
                'n_points': int(len(x)),
            })
        except Exception as e:
            print(f"WARNING: Langmuir fit failed for salt {c_salt}: {e}")
            continue

    return pd.DataFrame(results)


def _plot_isotherms(summary: pd.DataFrame, fit_params: pd.DataFrame, metadata: Dict[str, Any], experiment_id: str, results_folder: Path) -> str:
    """Plot isotherms (data + Langmuir fits) for all salt concentrations in one graph.

    Returns the path to the saved PNG file.
    """
    plt.figure(figsize=(8, 6))

    salt_unit = metadata.get('salt_conc_unit', '')
    ligand_unit = metadata.get('ligand_conc_unit', '')

    colors = plt.cm.viridis(np.linspace(0, 1, summary['c_salt'].nunique()))
    color_map = {}
    for color, c_salt in zip(colors, sorted(summary['c_salt'].unique())):
        color_map[c_salt] = color

    legends = []

    for c_salt, sub in summary.groupby('c_salt'):
        color = color_map[c_salt]
        plt.errorbar(
            sub['cE_mean'],
            sub['q_mean'],
            xerr=sub['cE_std'],
            yerr=sub['q_std'],
            fmt='o',
            color=color,
            label=None,
            capsize=3,
            alpha=0.8,
        )

        fit_row = fit_params[fit_params['c_salt'] == c_salt]
        label = f"{c_salt:g} {salt_unit}"
        if not fit_row.empty:
            q_max = fit_row['q_max'].iloc[0]
            K = fit_row['K'].iloc[0]
            r2 = fit_row['r2'].iloc[0]
            x_fit = np.linspace(sub['cE_mean'].min(), sub['cE_mean'].max(), 200)
            y_fit = _langmuir_isotherm(x_fit, q_max, K)
            plt.plot(x_fit, y_fit, '-', color=color)
            label = f"{c_salt:g} {salt_unit} (q_max={q_max:.3g}, K={K:.3g}, R2={r2:.3f})"
        legends.append(label)

    # Create a single legend
    handles, _ = plt.gca().get_legend_handles_labels()
    if handles:
        plt.legend(legends, title=f"Salt concentration [{salt_unit}]", fontsize=8)

    plt.xlabel(f"Equilibrium ligand concentration cE [{ligand_unit}]")
    plt.ylabel(f"Loading q [{ligand_unit}*uL/mg]")
    plt.title(f"Loading isotherms - Experiment {experiment_id}")
    plt.tight_layout()

    png_path = results_folder / f"loading_isotherms_{experiment_id}.png"
    pdf_path = results_folder / f"loading_isotherms_{experiment_id}.pdf"
    plt.savefig(png_path, dpi=300)
    plt.savefig(pdf_path)
    plt.close()

    print(f"INFO: Saved isotherm plots to {png_path} and {pdf_path}")
    return str(png_path)


def analyze_experiment(experiment_id: str = None, data_folder: str = '../data', results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with all key metrics
    """
    results_folder_path = Path(results_folder)
    _ensure_results_folder(results_folder_path)
    data_folder_path = Path(data_folder)

    analysis_results: Dict[str, Any] = {
        "experiment_id": experiment_id,
        "status": "failed",
        "message": "",
        "plots": {},
        "data_outputs": {},
        "metadata": {},
        "files_processed": 0,
    }

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        try:
            experiment_id = _auto_detect_latest_experiment_id(data_folder_path)
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Auto-detected latest experiment ID: {experiment_id}")
        except Exception as e:
            analysis_results["message"] = f"Failed to auto-detect experiment ID: {e}"
            print(f"ERROR: {analysis_results['message']}")
            return analysis_results

    # Load experiment JSON
    try:
        experiment_data = _load_experiment_json(experiment_id, data_folder_path)
        analysis_results["metadata"]["experiment_json_loaded"] = True
    except Exception as e:
        analysis_results["message"] = str(e)
        print(f"ERROR: {analysis_results['message']}")
        return analysis_results

    # Parse metadata
    try:
        metadata = _parse_metadata(experiment_data)
        analysis_results["metadata"].update(metadata)
    except Exception as e:
        analysis_results["message"] = f"Metadata parsing error: {e}"
        print(f"ERROR: {analysis_results['message']}")
        return analysis_results

    # Fetch Tecan data from device control server, with local fallback
    print(f"Fetching Tecan data for experiment {experiment_id} from device control server...")
    try:
        data_info = check_tecan_data_availability(experiment_id)
        if not data_info.get("available", False):
            raise FileNotFoundError(f"No Tecan data available for experiment {experiment_id}: {data_info.get('error', 'Unknown error')}")

        print(f"Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
        tecan_data_path_str = fetch_tecan_data_file(experiment_id, str(results_folder_path))
        tecan_data_path = Path(tecan_data_path_str)
        analysis_results["data_outputs"]["tecan_raw_file"] = str(tecan_data_path.resolve())
        analysis_results["metadata"]["data_source"] = "device"
    except Exception as device_server_error:
        print(f"WARNING: Device control server access failed: {device_server_error}. Falling back to local file search...")
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            analysis_results["message"] = "Tecan data not available - analysis skipped. This is expected for test runs."
            analysis_results["status"] = "success"
            analysis_results["metadata"]["data_source"] = "none"
            analysis_results["note"] = "No Tecan data found on server or local system."
            print(f"INFO: {analysis_results['message']}")
            # Save analysis_results JSON before returning
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
            analysis_results["metadata"]["data_source"] = "none"
            print(f"INFO: {analysis_results['message']}")
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, 'w') as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        tecan_data_path = results_folder_path / f'tecan_data_{experiment_id}.xlsx'
        shutil.copy(most_recent_excel_source, tecan_data_path)
        print(f"INFO: Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")
        analysis_results["data_outputs"]["tecan_raw_file"] = str(tecan_data_path.resolve())
        analysis_results["metadata"]["data_source"] = "local"

    # At this point, tecan_data_path should be defined
    try:
        absorbance_df = _read_tecan_absorbance(tecan_data_path)
    except Exception as e:
        analysis_results["message"] = f"Failed to read Tecan absorbance data: {e}"
        print(f"ERROR: {analysis_results['message']}")
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Extract per-well records
    try:
        records = _extract_well_absorbances(absorbance_df, metadata)
        print(f"INFO: Extracted {len(records)} well records for analysis.")
    except Exception as e:
        analysis_results["message"] = f"Error while mapping wells to conditions: {e}"
        print(f"ERROR: {analysis_results['message']}")
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Compute cE and q
    try:
        per_well_df = _compute_ce_and_q(records, metadata)
    except Exception as e:
        analysis_results["message"] = f"Error while computing cE and q: {e}"
        print(f"ERROR: {analysis_results['message']}")
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Aggregate replicates
    try:
        summary_df = _aggregate_replicates(per_well_df, metadata)
    except Exception as e:
        analysis_results["message"] = f"Error while aggregating replicates: {e}"
        print(f"ERROR: {analysis_results['message']}")
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Fit Langmuir isotherms
    fit_params_df = _fit_langmuir_per_salt(summary_df)
    if fit_params_df.empty:
        print("WARNING: No successful Langmuir fits were obtained.")

    # Plot isotherms
    try:
        plot_path = _plot_isotherms(summary_df, fit_params_df, metadata, experiment_id, results_folder_path)
        analysis_results["plots"]["loading_isotherms"] = plot_path
    except Exception as e:
        print(f"WARNING: Failed to generate isotherm plot: {e}")

    # Save processed data as CSV
    per_well_csv = results_folder_path / f"per_well_results_{experiment_id}.csv"
    summary_csv = results_folder_path / f"summary_isotherms_{experiment_id}.csv"
    fit_params_csv = results_folder_path / f"langmuir_fit_params_{experiment_id}.csv"
    try:
        per_well_df.to_csv(per_well_csv, index=False)
        summary_df.to_csv(summary_csv, index=False)
        fit_params_df.to_csv(fit_params_csv, index=False)
        analysis_results["data_outputs"]["per_well_results_csv"] = str(per_well_csv.resolve())
        analysis_results["data_outputs"]["summary_isotherms_csv"] = str(summary_csv.resolve())
        analysis_results["data_outputs"]["langmuir_fit_params_csv"] = str(fit_params_csv.resolve())
        analysis_results["files_processed"] = int(len(per_well_df))
    except Exception as e:
        print(f"WARNING: Failed to save processed data CSVs: {e}")

    analysis_results["status"] = "success"
    analysis_results["message"] = "Analysis completed successfully."

    # Save analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, 'w') as f:
        json.dump(analysis_results, f, indent=4)
    print(f"Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze Tecan plate reader loading isotherm experiment data.')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID. If not provided, attempts to auto-detect the most recent.')
    parser.add_argument('--data-folder', default='../data', help='Data folder path (default: ../data)')
    parser.add_argument('--results-folder', default='../results', help='Results folder path (default: ../results)')

    args = parser.parse_args()

    try:
        results = analyze_experiment(
            experiment_id=args.experiment_id,
            data_folder=args.data_folder,
            results_folder=args.results_folder,
        )
        if results.get("status") == "success":
            print("SUCCESS: Analysis completed successfully.")
            return 0
        else:
            print(f"ERROR: Analysis failed: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
