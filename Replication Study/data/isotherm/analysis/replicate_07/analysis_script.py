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
from typing import List, Dict, Optional, Tuple, Any, Iterable

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


def parse_semicolon_floats(value: Any, field_name: str) -> List[float]:
    """Parse semicolon-separated string of floats from metadata.

    Handles comma or dot as decimal separator, strips whitespace.
    Raises ValueError on failure.
    """
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(f"Metadata field '{field_name}' must be string or number, got {type(value)}")
    parts = [p.strip() for p in value.split(';') if p.strip() != ""]
    floats: List[float] = []
    for p in parts:
        p_std = p.replace(',', '.')
        try:
            floats.append(float(p_std))
        except ValueError:
            raise ValueError(f"Cannot parse float token '{p}' in field '{field_name}'")
    return floats


def get_required_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and validate required metadata fields.

    Raises informative errors on problems.
    """
    required_fields = [
        'Number of ligand concentrations',
        'Number of salt concentrations',
        'Replicates',
        'Ligand concentrations',
        'Salt concentrations',
        'Ligand concentration unit',
        'Salt concentration unit',
        'Resin Mass',
        'Total volume',
        'Calibration Curve Slope',
        'Calibration Curve Intercept',
    ]

    missing = [f for f in required_fields if f not in metadata]
    if missing:
        raise KeyError(f"Missing expected metadata field(s): {', '.join(missing)}")

    md: Dict[str, Any] = {}

    try:
        n_ligand = int(str(metadata['Number of ligand concentrations']['value']).strip())
        n_salt = int(str(metadata['Number of salt concentrations']['value']).strip())
        replicates = int(str(metadata['Replicates']['value']).strip())
    except Exception as e:
        raise ValueError(f"Failed to parse integer metadata (numbers of concentrations/replicates): {e}")

    ligand_concs = parse_semicolon_floats(metadata['Ligand concentrations']['value'], 'Ligand concentrations')
    salt_concs = parse_semicolon_floats(metadata['Salt concentrations']['value'], 'Salt concentrations')

    if len(ligand_concs) != n_ligand:
        raise ValueError(
            f"Number of ligand concentrations ({n_ligand}) does not match length of Ligand concentrations list ({len(ligand_concs)})."
        )
    if len(salt_concs) != n_salt:
        raise ValueError(
            f"Number of salt concentrations ({n_salt}) does not match length of Salt concentrations list ({len(salt_concs)})."
        )

    ligand_unit = str(metadata['Ligand concentration unit']['value']).strip()
    salt_unit = str(metadata['Salt concentration unit']['value']).strip()

    try:
        resin_mass = float(str(metadata['Resin Mass']['value']).replace(',', '.'))
    except Exception as e:
        raise ValueError(f"Failed to parse 'Resin Mass' as float: {e}")

    try:
        total_volume = float(str(metadata['Total volume']['value']).replace(',', '.'))
    except Exception as e:
        raise ValueError(f"Failed to parse 'Total volume' as float: {e}")

    try:
        slope = float(str(metadata['Calibration Curve Slope']['value']).replace(',', '.'))
        intercept = float(str(metadata['Calibration Curve Intercept']['value']).replace(',', '.'))
    except Exception as e:
        raise ValueError(f"Failed to parse calibration parameters: {e}")

    if slope == 0:
        raise ValueError("Calibration Curve Slope must not be zero (would make inversion impossible).")

    md['n_ligand'] = n_ligand
    md['n_salt'] = n_salt
    md['replicates'] = replicates
    md['ligand_concs'] = ligand_concs
    md['salt_concs'] = salt_concs
    md['ligand_unit'] = ligand_unit
    md['salt_unit'] = salt_unit
    md['resin_mass_mg'] = resin_mass
    md['total_volume_uL'] = total_volume
    md['calib_slope'] = slope
    md['calib_intercept'] = intercept

    return md


def well_index_to_row_col(index: int) -> Tuple[int, int]:
    """Convert flat well index (0-95) to (row_idx, col_idx) both 0-based.

    We assume 8 rows (A-H) and 12 columns (1-12). Indexing row-major: all columns of row A,
    then row B, etc., matching Tecan export layout from row 34, col B.
    """
    if index < 0 or index >= 96:
        raise ValueError(f"Well index out of range 0-95: {index}")
    row = index % 8
    col = index // 8
    return row, col


def build_plate_mapping(md: Dict[str, Any]) -> pd.DataFrame:
    """Build mapping from well index to experimental conditions.

    Returns DataFrame with columns:
        well_index, row_idx, col_idx, row_label, col_label,
        ligand_index, salt_index, replicate_index,
        c0 (initial ligand concentration), salt_conc
    For wells that are not used (outside ligand concentration range), ligand_index is None.
    """
    n_ligand = md['n_ligand']
    n_salt = md['n_salt']
    replicates = md['replicates']
    ligand_concs = md['ligand_concs']
    salt_concs = md['salt_concs']

    if n_salt * replicates > 12:
        raise ValueError(
            f"Number of columns required (n_salt * replicates = {n_salt * replicates}) exceeds 12. Plate layout not supported."
        )
    if n_ligand > 8:
        raise ValueError(
            f"Number of ligand concentrations ({n_ligand}) exceeds 8 rows. Plate layout not supported."
        )

    rows_labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']

    records: List[Dict[str, Any]] = []

    # Iterate over all wells row-major
    for idx in range(96):
        row_idx, col_idx = well_index_to_row_col(idx)
        row_label = rows_labels[row_idx]
        col_label = col_idx + 1

        # Determine salt index and replicate index from column index
        # Columns are grouped by replicate for each salt concentration.
        # Example: n_salt=4, replicates=3 => cols 0-2 salt0, 3-5 salt1, 6-8 salt2, 9-11 salt3
        if col_idx >= n_salt * replicates:
            # Columns beyond used ones: considered unused
            salt_index = None
            rep_index = None
        else:
            salt_index = col_idx // replicates
            rep_index = col_idx % replicates

        # Determine ligand index from row index: ascending from top
        if row_idx >= n_ligand:
            ligand_index = None
            c0 = None
        else:
            ligand_index = row_idx
            c0 = ligand_concs[ligand_index]

        if salt_index is None or ligand_index is None:
            salt_conc = None
        else:
            salt_conc = salt_concs[salt_index]

        records.append({
            'well_index': idx,
            'row_idx': row_idx,
            'col_idx': col_idx,
            'row_label': row_label,
            'col_label': col_label,
            'ligand_index': ligand_index,
            'salt_index': salt_index,
            'replicate_index': rep_index,
            'c0': c0,
            'salt_conc': salt_conc,
        })

    mapping_df = pd.DataFrame.from_records(records)
    return mapping_df


def langmuir_isotherm(c: np.ndarray, qmax: float, K: float) -> np.ndarray:
    """Langmuir isotherm: q = qmax * K * c / (1 + K * c)."""
    return qmax * K * c / (1.0 + K * c)


def fit_langmuir(cE: np.ndarray, q: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fit Langmuir parameters qmax and K to given data.

    Returns (qmax, K, r2). Returns (None, None, None) if fit fails or not enough points.
    """
    # Require at least 3 points for a meaningful fit
    mask = np.isfinite(cE) & np.isfinite(q)
    cE_clean = cE[mask]
    q_clean = q[mask]
    if cE_clean.size < 3:
        return None, None, None

    # Initial guesses
    qmax0 = float(np.nanmax(q_clean)) if np.all(np.isfinite(q_clean)) else 1.0
    if qmax0 <= 0:
        qmax0 = 1.0
    # Rough guess for K: assume midpoint roughly at median cE
    try:
        c_mid = float(np.nanmedian(cE_clean))
        if c_mid <= 0:
            c_mid = 1.0
    except Exception:
        c_mid = 1.0
    K0 = 1.0 / max(c_mid, 1e-9)

    p0 = [qmax0, K0]
    bounds = ([0.0, 0.0], [np.inf, np.inf])

    try:
        popt, pcov = curve_fit(langmuir_isotherm, cE_clean, q_clean, p0=p0, bounds=bounds, maxfev=10000)
        qmax_fit, K_fit = popt
        if not np.isfinite(qmax_fit) or not np.isfinite(K_fit):
            return None, None, None
        q_pred = langmuir_isotherm(cE_clean, qmax_fit, K_fit)
        if np.allclose(q_clean, np.mean(q_clean)):
            # Degenerate: almost constant
            return None, None, None
        # Compute R^2 manually to avoid external dependencies
        ss_res = float(np.sum((q_clean - q_pred) ** 2))
        ss_tot = float(np.sum((q_clean - np.mean(q_clean)) ** 2))
        if ss_tot == 0:
            r2 = None
        else:
            r2 = 1.0 - ss_res / ss_tot
        return float(qmax_fit), float(K_fit), float(r2) if r2 is not None else None
    except Exception as e:
        print(f"WARNING: Langmuir fit failed: {e}")
        return None, None, None


def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = '../data',
                       results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm experiments.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with all key metrics
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
        # Try CLI arg
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            analysis_results["experiment_id"] = experiment_id
        else:
            # Auto-detect most recent experiment_*.json in data folder
            data_path = Path(data_folder)
            if not data_path.exists() or not data_path.is_dir():
                raise FileNotFoundError(f"Data folder does not exist: {data_folder}")
            json_files = list(data_path.glob("experiment_*.json"))
            if not json_files:
                raise FileNotFoundError(f"No experiment_*.json files found in data folder: {data_folder}")
            latest_file = max(json_files, key=lambda p: p.stat().st_mtime)
            name = latest_file.stem  # experiment_<id>
            if "_" in name:
                experiment_id = name.split("_", 1)[1]
            else:
                experiment_id = name
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Auto-detected experiment ID: {experiment_id}")

    if experiment_id is None:
        raise ValueError("Experiment ID could not be determined.")

    data_folder_path = Path(data_folder)
    if not data_folder_path.exists() or not data_folder_path.is_dir():
        raise FileNotFoundError(f"Data folder does not exist or is not a directory: {data_folder}")

    # Load experiment JSON (prefer root ../experiment_ID.json, then data folder)
    exp_json_root = Path('..') / f'experiment_{experiment_id}.json'
    if exp_json_root.exists():
        data_file_path = exp_json_root
    else:
        data_file_path = data_folder_path / f'experiment_{experiment_id}.json'

    try:
        with open(data_file_path, 'r') as f:
            experiment_data = json.load(f)
        analysis_results["files_processed"] += 1
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # Extract metadata_decoded.extra_fields
    try:
        metadata_all = experiment_data['metadata_decoded']
        extra_fields = metadata_all.get('extra_fields', {})
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    try:
        md = get_required_metadata(extra_fields)
    except Exception as e:
        raise

    analysis_results["metadata"].update(md)

    print(f"INFO: Metadata parsed: n_ligand={md['n_ligand']}, n_salt={md['n_salt']}, replicates={md['replicates']}")

    # Fetch Tecan data (single-file pattern)
    print(f"Fetching Tecan data for experiment {experiment_id} from device control server...")

    try:
        data_info = check_tecan_data_availability(experiment_id)
        if not data_info.get("available", False):
            raise FileNotFoundError(f"No Tecan data available for experiment {experiment_id}: {data_info.get('error', 'Unknown error')}")

        print(f"Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
        tecan_data_path_str = fetch_tecan_data_file(experiment_id, str(results_folder_path))
        tecan_data_path = Path(tecan_data_path_str)

    except Exception as device_server_error:
        print(f"Device control server access failed: {device_server_error}. Falling back to local file search...")
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            analysis_results["message"] = "Tecan data not available - analysis skipped. This is expected for test runs."
            analysis_results["status"] = "success"
            analysis_results["note"] = "No Tecan data found on server or local system."
            print(f"INFO: {analysis_results['message']}")
            # Save JSON and return early
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

    # Read absorbance data from Tecan Excel
    num_columns = 12  # 96-well plate: 12 columns
    num_rows = 8      # 8 rows (A-H)

    try:
        raw_df = pd.read_excel(
            tecan_data_path,
            header=None,
            skiprows=33,
            usecols=list(range(1, 1 + num_columns)),
            nrows=num_rows
        )
    except Exception as e:
        raise RuntimeError(f"Failed to read Tecan Excel data at {tecan_data_path}: {e}")

    # Flatten to 96 wells in column-major order (A1..H1, A2..H2, ..., A12..H12)
    # raw_df has shape (8, 12), row 0 is A, col 0 is column 1
    if raw_df.shape != (num_rows, num_columns):
        raise ValueError(f"Unexpected raw data shape: {raw_df.shape}, expected (8, 12)")

    absorbances = raw_df.values.astype(float).flatten(order='F')

    if absorbances.size != 96:
        raise ValueError(f"Unexpected number of wells: {absorbances.size}, expected 96")

    mapping_df = build_plate_mapping(md)

    if len(mapping_df) != 96:
        raise RuntimeError("Internal error: plate mapping must have 96 entries")

    # Combine mapping with absorbances
    mapping_df = mapping_df.copy()
    mapping_df['absorbance'] = absorbances

    # Filter to used wells (both ligand_index and salt_index not None)
    used = mapping_df.dropna(subset=['ligand_index', 'salt_index'])

    # Compute equilibrium concentrations cE using inverted calibration curve
    slope = md['calib_slope']
    intercept = md['calib_intercept']

    used['cE'] = (used['absorbance'] - intercept) / slope

    # Sanity checks on cE
    # 1) cE between 0 and max c0 for each well
    max_c0 = max(md['ligand_concs']) if md['ligand_concs'] else 0.0
    invalid_cE_mask = (used['cE'] < 0) | (used['cE'] > max_c0 * 1.001)
    if invalid_cE_mask.any():
        n_invalid = int(invalid_cE_mask.sum())
        msg = (
            f"Sanity check failed: {n_invalid} wells have equilibrium concentration outside [0, max c0]. "
            f"Check calibration parameters (slope, intercept)."
        )
        raise ValueError(msg)

    # 2) cE must vary strongly across a column as c0 varies
    cE_variations: List[str] = []
    for col_idx in used['col_idx'].unique():
        col_wells = used[used['col_idx'] == col_idx]
        if col_wells.empty:
            continue
        cE_col = col_wells['cE'].values
        if np.nanmax(cE_col) - np.nanmin(cE_col) < 0.05 * max_c0:
            cE_variations.append(str(col_idx + 1))
    if cE_variations:
        msg = (
            "Sanity check warning: equilibrium concentrations show little variation across "
            f"column(s) {', '.join(cE_variations)} relative to max c0. "
            "This may indicate a calibration issue (e.g. applied in wrong direction)."
        )
        print(f"WARNING: {msg}")
        analysis_results['warnings'].append(msg)

    # Group replicates: by ligand_index and salt_index
    group_cols = ['ligand_index', 'salt_index']
    grouped = used.groupby(group_cols, as_index=False).agg(
        cE_mean=('cE', 'mean'),
        cE_std=('cE', 'std'),
        absorbance_mean=('absorbance', 'mean'),
        absorbance_std=('absorbance', 'std'),
        n_wells=('cE', 'size'),
    )

    expected_groups = md['n_ligand'] * md['n_salt']
    if len(grouped) != expected_groups:
        raise RuntimeError(
            f"Grouping mismatch: expected {expected_groups} (n_ligand * n_salt) groups, "
            f"but found {len(grouped)}. Check well-to-condition mapping."
        )

    # Verify replicate count for each group
    wrong_reps = grouped[grouped['n_wells'] != md['replicates']]
    if not wrong_reps.empty:
        details = wrong_reps[['ligand_index', 'salt_index', 'n_wells']].to_dict(orient='records')
        raise RuntimeError(
            f"Replicate count mismatch for some groups (expected {md['replicates']} per combination). Details: {details}"
        )

    # Attach physical concentrations
    grouped['c0'] = grouped['ligand_index'].astype(int).map(lambda i: md['ligand_concs'][i])
    grouped['salt_conc'] = grouped['salt_index'].astype(int).map(lambda i: md['salt_concs'][i])

    # Calculate loading q = (c0 - cE_mean) * v_total / m_resin
    v_total = md['total_volume_uL']
    m_resin = md['resin_mass_mg']

    grouped['q'] = (grouped['c0'] - grouped['cE_mean']) * v_total / m_resin

    # Prepare summary DataFrame
    summary_cols = [
        'ligand_index', 'salt_index', 'c0', 'salt_conc',
        'cE_mean', 'cE_std', 'q', 'absorbance_mean', 'absorbance_std', 'n_wells'
    ]
    summary_df = grouped[summary_cols].copy()

    # Save processed data
    raw_output_csv = results_folder_path / f"tecan_plate_raw_{experiment_id}.csv"
    mapping_df.to_csv(raw_output_csv, index=False)
    analysis_results['data_outputs']['raw_plate_with_mapping'] = str(raw_output_csv.resolve())

    summary_output_csv = results_folder_path / f"loading_isotherm_summary_{experiment_id}.csv"
    summary_df.to_csv(summary_output_csv, index=False)
    analysis_results['data_outputs']['loading_isotherm_summary'] = str(summary_output_csv.resolve())

    # Fit Langmuir isotherms for each salt concentration
    salt_values_sorted = sorted(md['salt_concs'])

    fit_results: List[Dict[str, Any]] = []

    plt.figure(figsize=(8, 6))

    colors = plt.cm.viridis(np.linspace(0, 1, len(salt_values_sorted))) if salt_values_sorted else []

    for i, salt_val in enumerate(salt_values_sorted):
        df_salt = summary_df[summary_df['salt_conc'] == salt_val]
        if df_salt.empty:
            msg = f"No data points found for salt concentration {salt_val} {md['salt_unit']}. Skipping fit."
            print(f"WARNING: {msg}")
            analysis_results['warnings'].append(msg)
            continue

        cE_vals = df_salt['cE_mean'].to_numpy(dtype=float)
        q_vals = df_salt['q'].to_numpy(dtype=float)

        qmax, K, r2 = fit_langmuir(cE_vals, q_vals)

        if qmax is None or K is None:
            msg = f"Langmuir fit failed or not meaningful for salt concentration {salt_val} {md['salt_unit']}."
            print(f"WARNING: {msg}")
            analysis_results['warnings'].append(msg)
            # Still plot points without fit
            plt.errorbar(cE_vals, q_vals, fmt='o', color=colors[i], label=f"Salt {salt_val} {md['salt_unit']} (no fit)")
            continue

        # Sanity-check fitted parameters: qmax should be within data-supported range
        q_max_obs = float(np.nanmax(q_vals))
        if not (0 <= qmax <= 10 * max(q_max_obs, 1e-9)):
            msg = (
                f"Fitted qmax={qmax:.3g} for salt {salt_val} {md['salt_unit']} is outside supported range "
                f"based on observed data (max q ~ {q_max_obs:.3g}). Marking fit as invalid."
            )
            print(f"WARNING: {msg}")
            analysis_results['warnings'].append(msg)
            plt.errorbar(cE_vals, q_vals, fmt='o', color=colors[i], label=f"Salt {salt_val} {md['salt_unit']} (no valid fit)")
            fit_results.append({
                'salt_conc': salt_val,
                'qmax': None,
                'K': None,
                'r2': None,
            })
            continue

        # Generate fit curve over range of observed cE
        cE_grid = np.linspace(0, max(cE_vals) * 1.05 if np.max(cE_vals) > 0 else 1.0, 200)
        q_fit = langmuir_isotherm(cE_grid, qmax, K)

        label = f"Salt {salt_val} {md['salt_unit']}: qmax={qmax:.3g}, K={K:.3g}, R2={r2:.3f}" if r2 is not None else f"Salt {salt_val} {md['salt_unit']}: qmax={qmax:.3g}, K={K:.3g}"

        # Plot data points
        plt.errorbar(cE_vals, q_vals, yerr=None, fmt='o', color=colors[i], label=label)
        # Plot fit line
        plt.plot(cE_grid, q_fit, '-', color=colors[i], alpha=0.7)

        fit_results.append({
            'salt_conc': salt_val,
            'qmax': qmax,
            'K': K,
            'r2': r2,
        })

    plt.xlabel(f"Equilibrium ligand concentration cE [{md['ligand_unit']}]")
    plt.ylabel(f"Loading q [{md['ligand_unit']} * uL / mg]")
    plt.title("Loading isotherms from Tecan plate reader")
    plt.legend(fontsize=8)
    plt.tight_layout()

    plot_png = results_folder_path / f"loading_isotherms_{experiment_id}.png"
    plt.savefig(plot_png, dpi=300)
    plt.close()

    analysis_results['plots']['loading_isotherms_png'] = str(plot_png.resolve())

    # Also save fit results as CSV and keep values in metadata
    fit_results_df = pd.DataFrame(fit_results)
    fit_results_csv = results_folder_path / f"langmuir_fit_results_{experiment_id}.csv"
    fit_results_df.to_csv(fit_results_csv, index=False)
    analysis_results['data_outputs']['langmuir_fit_results_csv'] = str(fit_results_csv.resolve())
    analysis_results['metadata']['langmuir_fit_results'] = fit_results

    # Final status
    analysis_results['status'] = 'success'
    analysis_results['message'] = 'Analysis completed successfully.'

    # Save analysis results JSON
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
