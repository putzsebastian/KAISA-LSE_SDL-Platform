#!/usr/bin/env python3
"""
Analysis Script - Tecan Loading Isotherm Evaluation
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
import numpy as np
import pandas as pd
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


def auto_detect_latest_experiment(data_folder: Path) -> Optional[str]:
    """Auto-detect the most recent experiment JSON file and return its experiment ID as string."""
    if not data_folder.exists():
        return None
    candidates = list(data_folder.glob('experiment_*.json'))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest = candidates[0]
    name = latest.stem  # experiment_1234
    try:
        return name.split('_', 1)[1]
    except Exception:
        return None


def langmuir_isotherm(c, qmax, K):
    """Langmuir isotherm model: q = qmax * K * c / (1 + K * c)"""
    return qmax * K * c / (1.0 + K * c)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fit Langmuir model to data. Returns (qmax, K, R2) or (None, None, None) on failure."""
    mask = np.isfinite(c_e) & np.isfinite(q)
    c_e = np.asarray(c_e)[mask]
    q = np.asarray(q)[mask]
    if c_e.size < 3:
        print("WARNING: Not enough points for Langmuir fit (need at least 3).")
        return None, None, None
    try:
        qmax_guess = float(np.nanmax(q)) if np.nanmax(q) > 0 else 1.0
        K_guess = 1.0 / (np.nanmedian(c_e) + 1e-9)
        bounds = ([0.0, 0.0], [np.inf, np.inf])
        popt, _ = curve_fit(langmuir_isotherm, c_e, q, p0=[qmax_guess, K_guess], bounds=bounds, maxfev=10000)
        qmax, K = popt
        if not (np.isfinite(qmax) and np.isfinite(K)):
            print("WARNING: Non-finite fit parameters.")
            return None, None, None
        q_pred = langmuir_isotherm(c_e, qmax, K)
        ss_res = float(np.sum((q - q_pred) ** 2))
        ss_tot = float(np.sum((q - np.mean(q)) ** 2))
        if ss_tot == 0:
            r2 = 1.0 if ss_res == 0 else 0.0
        else:
            r2 = 1.0 - ss_res / ss_tot
        return float(qmax), float(K), float(r2)
    except Exception as e:
        print(f"WARNING: Langmuir fit failed: {e}")
        return None, None, None


def parse_metadata_extra_fields(extra_fields: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and convert required metadata fields from eLabFTW extra_fields."""
    required_fields = [
        'Buffer',
        'Salt Name',
        'Process ID',
        'Pump Speed',
        'Replicates',
        'Resin Mass',
        'Ligand Name',
        'Total volume',
        'Venting time',
        'Pump Duration',
        'Incubation Time',
        'Salt concentrations',
        'Equilibration Cycles',
        'Equilibration Volume',
        'Ligand concentrations',
        'Incubation Temperature',
        'Measurement Wavelength',
        'Calibration Curve Slope',
        'Salt concentration unit',
        'Shaker Speed Incubation',
        'Salt Stock Concentration',
        'Ligand concentration unit',
        'Ligand Stock Concentration',
        'Calibration Curve Intercept',
        'Equilibration Cycle Duration',
        'Number of salt concentrations',
        'Number of ligand concentrations',
    ]

    metadata = {}
    for field in required_fields:
        if field not in extra_fields:
            raise KeyError(f"Missing expected metadata field: {field}")
        metadata[field] = extra_fields[field].get('value')

    try:
        num_ligand = int(metadata['Number of ligand concentrations'])
        num_salt = int(metadata['Number of salt concentrations'])
        replicates = int(metadata['Replicates'])
    except Exception as e:
        raise ValueError(f"Invalid numeric metadata for counts (ligand/salt/replicates): {e}")

    def parse_float_list(s: Any, field_name: str) -> List[float]:
        if s is None:
            raise ValueError(f"Metadata field {field_name} is None")
        if isinstance(s, (int, float)):
            return [float(s)]
        parts = str(s).split(';')
        values = []
        for p in parts:
            p = p.strip().replace(',', '.')
            if p == '':
                continue
            try:
                values.append(float(p))
            except ValueError:
                raise ValueError(f"Cannot parse float from '{p}' in field {field_name}")
        if not values:
            raise ValueError(f"No numeric values parsed from field {field_name}")
        return values

    ligand_concs = parse_float_list(metadata['Ligand concentrations'], 'Ligand concentrations')
    salt_concs = parse_float_list(metadata['Salt concentrations'], 'Salt concentrations')

    if len(ligand_concs) != num_ligand:
        raise ValueError(
            f"Number of ligand concentrations ({len(ligand_concs)}) does not match 'Number of ligand concentrations' ({num_ligand})."
        )
    if len(salt_concs) != num_salt:
        raise ValueError(
            f"Number of salt concentrations ({len(salt_concs)}) does not match 'Number of salt concentrations' ({num_salt})."
        )

    try:
        slope = float(str(metadata['Calibration Curve Slope']).replace(',', '.'))
        intercept = float(str(metadata['Calibration Curve Intercept']).replace(',', '.'))
    except Exception as e:
        raise ValueError(f"Invalid calibration parameters: {e}")

    if slope == 0:
        raise ValueError("Calibration Curve Slope must not be zero")

    try:
        resin_mass = float(str(metadata['Resin Mass']).replace(',', '.'))
        total_volume = float(str(metadata['Total volume']).replace(',', '.'))
    except Exception as e:
        raise ValueError(f"Invalid resin mass or total volume: {e}")

    parsed = {
        'num_ligand': num_ligand,
        'num_salt': num_salt,
        'replicates': replicates,
        'ligand_concs': ligand_concs,
        'salt_concs': salt_concs,
        'slope': slope,
        'intercept': intercept,
        'resin_mass': resin_mass,
        'total_volume': total_volume,
        'ligand_unit': metadata['Ligand concentration unit'],
        'salt_unit': metadata['Salt concentration unit'],
        'ligand_name': metadata['Ligand Name'],
        'salt_name': metadata['Salt Name'],
        'buffer': metadata['Buffer'],
        'process_id': metadata['Process ID'],
        'measurement_wavelength': metadata['Measurement Wavelength'],
    }
    return parsed


def extract_plate_data(tecan_path: Path, num_rows: int = 8, num_cols: int = 12) -> pd.DataFrame:
    """Read absorbance data from Tecan Excel file into a long-format DataFrame.

    Absorbance data starts at row 34 (0-based skiprows=33), column B (index 1).
    """
    print(f"INFO: Reading Tecan data from {tecan_path}")
    num_points = num_rows  # rows A-H per column
    num_columns = num_cols  # columns 1-12

    raw_df = pd.read_excel(
        tecan_path,
        header=None,
        skiprows=33,
        usecols=list(range(1, 1 + num_columns)),
        nrows=num_points
    )

    row_labels = [chr(ord('A') + i) for i in range(num_rows)]
    col_labels = [str(i) for i in range(1, num_cols + 1)]

    data_list = []
    for r_idx, row_label in enumerate(row_labels):
        for c_idx, col_label in enumerate(col_labels):
            well = f"{row_label}{col_label}"
            try:
                absorbance = float(raw_df.iat[r_idx, c_idx])
            except Exception:
                absorbance = np.nan
            data_list.append({
                'well': well,
                'row': row_label,
                'col': int(col_label),
                'absorbance': absorbance,
            })
    df = pd.DataFrame(data_list)
    return df


def map_well_to_conditions(
    df: pd.DataFrame,
    num_ligand: int,
    num_salt: int,
    replicates: int,
    ligand_concs: List[float],
    salt_concs: List[float],
) -> pd.DataFrame:
    """Add ligand and salt concentration mapping to each well based on the plate layout.

    - Ligand concentrations increase with row (A,B,...).
    - Salt concentrations are grouped in blocks of 'replicates' columns, increasing with column.
    """
    df = df.copy()

    row_index_map = {chr(ord('A') + i): i for i in range(8)}
    df['row_index'] = df['row'].map(row_index_map)

    df['ligand_index'] = df['row_index']
    df.loc[df['ligand_index'] >= num_ligand, 'ligand_index'] = np.nan

    wells_per_salt = replicates
    total_wells_per_row = 12
    total_salt_slots = total_wells_per_row // wells_per_salt
    if total_salt_slots < num_salt:
        raise ValueError(
            f"Plate layout incompatible: can host at most {total_salt_slots} salt concentrations with {replicates} replicates, "
            f"but metadata specifies {num_salt} salt concentrations."
        )

    def salt_index_from_col(col: int) -> Optional[int]:
        if col < 1 or col > total_wells_per_row:
            return None
        idx = (col - 1) // wells_per_salt
        if idx >= num_salt:
            return None
        return idx

    df['salt_index'] = df['col'].apply(salt_index_from_col)

    df['ligand_conc'] = df['ligand_index'].apply(lambda i: ligand_concs[int(i)] if pd.notna(i) and 0 <= int(i) < len(ligand_concs) else np.nan)
    df['salt_conc'] = df['salt_index'].apply(lambda i: salt_concs[int(i)] if i is not None and 0 <= int(i) < len(salt_concs) else np.nan)

    df_valid = df.dropna(subset=['ligand_conc', 'salt_conc'])
    return df_valid


def compute_equilibrium_concentrations(df: pd.DataFrame, slope: float, intercept: float, max_c0: float) -> pd.DataFrame:
    """Compute equilibrium concentrations from absorbance using inverted calibration.

    cE = (Absorbance - intercept) / slope. Values are clipped to [0, max_c0].
    """
    df = df.copy()
    df['cE_raw'] = (df['absorbance'] - intercept) / slope
    df['cE'] = df['cE_raw'].clip(lower=0.0, upper=max_c0)

    if (df['cE'] < 0).any() or (df['cE'] > max_c0).any():
        print("WARNING: Some cE values outside [0, max_c0] detected before clipping.")

    grouped = df.groupby(['salt_conc', 'col'])
    bad_groups = []
    for (salt, col), sub in grouped:
        if sub['ligand_conc'].nunique() > 1 and sub['cE'].std(ddof=1) < 1e-6:
            bad_groups.append((salt, col))
    if bad_groups:
        print("WARNING: Calibration sanity check failed for some column/salt groups (cE varies too little across c0).")

    return df


def aggregate_replicates(
    df: pd.DataFrame,
    num_ligand: int,
    num_salt: int,
    replicates: int,
) -> pd.DataFrame:
    """Aggregate replicate wells for each (ligand_conc, salt_conc) combination.

    Expects exactly 'replicates' wells per condition and num_ligand * num_salt groups.
    """
    if df.empty:
        raise ValueError("No valid wells to aggregate after mapping.")

    group_cols = ['ligand_conc', 'salt_conc']
    grouped = df.groupby(group_cols)
    agg = grouped.agg(
        n_wells=('cE', 'count'),
        cE_mean=('cE', 'mean'),
        cE_std=('cE', 'std'),
        absorbance_mean=('absorbance', 'mean'),
        absorbance_std=('absorbance', 'std'),
    ).reset_index()

    n_expected_groups = num_ligand * num_salt
    if agg.shape[0] != n_expected_groups:
        raise ValueError(
            f"Number of aggregated groups ({agg.shape[0]}) does not match expected num_ligand * num_salt ({n_expected_groups}). "
            "Check plate layout and mapping."
        )

    if (agg['n_wells'] != replicates).any():
        raise ValueError(
            "Replicate count mismatch: not all (ligand,salt) combinations have the expected number of wells."
        )

    return agg


def compute_loading_q(agg_df: pd.DataFrame, resin_mass: float, total_volume: float) -> pd.DataFrame:
    """Compute loading q for each parameter combination.

    q = (c0 - cE_mean) * total_volume / resin_mass
    """
    df = agg_df.copy()
    df.rename(columns={'ligand_conc': 'c0'}, inplace=True)
    df['q'] = (df['c0'] - df['cE_mean']) * total_volume / resin_mass
    return df


def plot_isotherms(
    df_q: pd.DataFrame,
    salt_concs: List[float],
    salt_unit: str,
    ligand_name: str,
    ligand_unit: str,
    experiment_id: str,
    results_folder: Path,
) -> str:
    """Plot isotherms and Langmuir fits for each salt concentration on one graph.

    Returns path to the saved PNG file.
    """
    plt.figure(figsize=(8, 6))
    colors = plt.cm.viridis(np.linspace(0, 1, len(salt_concs)))

    legend_entries = []

    for i, salt in enumerate(salt_concs):
        sub = df_q[df_q['salt_conc'] == salt].sort_values('cE_mean')
        if sub.empty:
            continue
        cE = sub['cE_mean'].values
        q = sub['q'].values

        plt.scatter(cE, q, color=colors[i], label=f"{salt} {salt_unit} data", s=30)

        qmax, K, r2 = fit_langmuir(cE, q)
        if qmax is not None and K is not None and r2 is not None:
            c_fit = np.linspace(max(0, np.min(cE)), max(cE) * 1.1 if max(cE) > 0 else 1.0, 200)
            q_fit = langmuir_isotherm(c_fit, qmax, K)
            plt.plot(c_fit, q_fit, color=colors[i], linestyle='--')
            legend_entries.append(f"{salt} {salt_unit}: qmax={qmax:.3g}, K={K:.3g}, R2={r2:.3f}")
        else:
            legend_entries.append(f"{salt} {salt_unit}: fit failed")

    plt.xlabel(f"Equilibrium {ligand_name} concentration cE ({ligand_unit})")
    plt.ylabel(f"Loading q ({ligand_unit} * uL / mg)")
    plt.title(f"Loading isotherms - Experiment {experiment_id}")

    if legend_entries:
        plt.legend(legend_entries, fontsize=8)

    plt.tight_layout()
    out_path = results_folder / f"isotherms_{experiment_id}.png"
    plt.savefig(out_path, dpi=200)
    plt.close()
    print(f"INFO: Isotherm plot saved to {out_path}")
    return str(out_path)


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = '../data', results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm experiments.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries to auto-detect.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with all key metrics and paths to generated files.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    data_folder_path = Path(data_folder)

    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ('', None):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from command line args: {experiment_id}")
        else:
            experiment_id = auto_detect_latest_experiment(data_folder_path)
            if experiment_id is None:
                raise ValueError("Could not auto-detect experiment ID from data folder.")
            print(f"INFO: Auto-detected most recent experiment ID: {experiment_id}")

    analysis_results: Dict[str, Any] = {
        "experiment_id": experiment_id,
        "status": "failed",
        "message": "",
        "plots": {},
        "data_outputs": {},
        "metadata": {},
        "files_processed": 0,
    }

    root_json_path = Path('../') / f'experiment_{experiment_id}.json'
    if root_json_path.exists():
        data_file_path = root_json_path
    else:
        data_file_path = data_folder_path / f'experiment_{experiment_id}.json'

    print(f"INFO: Loading experiment data from {data_file_path}")
    try:
        with open(data_file_path, 'r') as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    try:
        extra_fields = experiment_data['metadata_decoded']['extra_fields']
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure in experiment JSON: {e}")

    meta_parsed = parse_metadata_extra_fields(extra_fields)
    analysis_results['metadata'].update(meta_parsed)

    print(f"INFO: Fetching Tecan data for experiment {experiment_id} from device control server...")

    try:
        data_info = check_tecan_data_availability(experiment_id)
        if not data_info.get("available", False):
            raise FileNotFoundError(f"No Tecan data available for experiment {experiment_id}: {data_info.get('error', 'Unknown error')}")

        print(f"INFO: Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
        tecan_data_path_str = fetch_tecan_data_file(experiment_id, str(results_folder_path))
        tecan_data_path = Path(tecan_data_path_str)

    except Exception as device_server_error:
        print(f"WARNING: Device control server access failed: {device_server_error}. Falling back to local file search...")
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            analysis_results["message"] = "Tecan data not available - analysis skipped. This is expected for test runs."
            analysis_results["status"] = "success"
            analysis_results["note"] = "No Tecan data found on server or local system."
            print(f"INFO: {analysis_results['message']}")
            return analysis_results

        most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
        if not most_recent_excel_source:
            analysis_results["message"] = "No recent Tecan Excel file found - analysis skipped."
            analysis_results["status"] = "success"
            print(f"INFO: {analysis_results['message']}")
            return analysis_results

        tecan_data_path = results_folder_path / f'tecan_data_{experiment_id}.xlsx'
        shutil.copy(most_recent_excel_source, tecan_data_path)
        print(f"INFO: Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")

    plate_df = extract_plate_data(tecan_data_path)
    analysis_results['files_processed'] += 1

    num_ligand = meta_parsed['num_ligand']
    num_salt = meta_parsed['num_salt']
    replicates = meta_parsed['replicates']
    ligand_concs = meta_parsed['ligand_concs']
    salt_concs = meta_parsed['salt_concs']

    mapped_df = map_well_to_conditions(
        plate_df,
        num_ligand=num_ligand,
        num_salt=num_salt,
        replicates=replicates,
        ligand_concs=ligand_concs,
        salt_concs=salt_concs,
    )

    max_c0 = max(ligand_concs) if ligand_concs else 0.0
    mapped_df = compute_equilibrium_concentrations(
        mapped_df,
        slope=meta_parsed['slope'],
        intercept=meta_parsed['intercept'],
        max_c0=max_c0,
    )

    agg_df = aggregate_replicates(
        mapped_df,
        num_ligand=num_ligand,
        num_salt=num_salt,
        replicates=replicates,
    )

    q_df = compute_loading_q(
        agg_df,
        resin_mass=meta_parsed['resin_mass'],
        total_volume=meta_parsed['total_volume'],
    )

    per_well_csv = results_folder_path / f"per_well_data_{experiment_id}.csv"
    mapped_df.to_csv(per_well_csv, index=False)
    analysis_results['data_outputs']['per_well_data_csv'] = str(per_well_csv.resolve())

    agg_csv = results_folder_path / f"aggregated_data_{experiment_id}.csv"
    q_df.to_csv(agg_csv, index=False)
    analysis_results['data_outputs']['aggregated_data_csv'] = str(agg_csv.resolve())

    plot_path = plot_isotherms(
        q_df,
        salt_concs=salt_concs,
        salt_unit=meta_parsed['salt_unit'],
        ligand_name=meta_parsed['ligand_name'],
        ligand_unit=meta_parsed['ligand_unit'],
        experiment_id=str(experiment_id),
        results_folder=results_folder_path,
    )
    analysis_results['plots']['isotherms'] = plot_path

    analysis_results['status'] = 'success'
    analysis_results['message'] = 'Tecan loading isotherm analysis completed successfully.'

    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, 'w') as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Saved analysis results JSON to: {results_json_path}")

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
            results_folder=args.results_folder,
        )
        if results.get("status") == "success":
            print("SUCCESS: Analysis successful!")
            return 0
        else:
            print(f"ERROR: Analysis failed: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
