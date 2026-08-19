#!/usr/bin/env python3
"""
Analysis Script - Tecan plate reader loading isotherm evaluation
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
DEVICE_CONTROL_SERVER = os.getenv("DEVICE_CONTROL_SERVER", "http://localhost:8000")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "your-secure-api-key-here")


# -----------------------------------------------------------------------------
# Tecan helper functions (as required by specification)
# -----------------------------------------------------------------------------

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


def fetch_all_tecan_data_files(experiment_id: str, save_to_folder: str) -> List[str]:
    """Fetch ALL Tecan Excel data files for an experiment.
    USE THIS FOR MULTI-MEASUREMENT EXPERIMENTS."""
    headers = {'X-API-Key': DEVICE_API_KEY}
    save_folder = Path(save_to_folder)
    save_folder.mkdir(parents=True, exist_ok=True)
    downloaded_files = []
    try:
        list_url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}/list"
        list_response = requests.get(list_url, headers=headers, timeout=10)
        if list_response.status_code == 404:
            raise FileNotFoundError(f"No Tecan data found for experiment {experiment_id}")
        list_response.raise_for_status()
        files_info = list_response.json()
        total_files = files_info.get('total_files', 0)
        print(f"Found {total_files} Tecan data file(s) for experiment {experiment_id}")
        if total_files == 0:
            raise FileNotFoundError(f"No Tecan data files available for experiment {experiment_id}")
        for file_info in files_info.get('files', []):
            filename = file_info['filename']
            file_url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}/file/{filename}"
            try:
                response = requests.get(file_url, headers=headers, timeout=30)
                response.raise_for_status()
                file_path = save_folder / filename
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                downloaded_files.append(str(file_path))
                print(f"  Downloaded: {filename}")
            except Exception as e:
                print(f"  WARNING: Failed to download {filename}: {e}")
        print(f"Successfully downloaded {len(downloaded_files)} of {total_files} files")
        return downloaded_files
    except requests.exceptions.ConnectionError:
        raise Exception(f"Cannot connect to device control server at {DEVICE_CONTROL_SERVER}")
    except requests.exceptions.Timeout:
        raise Exception("Timeout while fetching Tecan data from device control server")
    except FileNotFoundError:
        raise
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


def get_all_recent_excel_files(directory: str, max_files: int = 10, since_timestamp: float = None) -> List[str]:
    """Find ALL recent Excel files from Tecan workspace folders.
    USE THIS FOR LOCAL FALLBACK WITH MULTI-FILE EXPERIMENTS."""
    excel_files = []
    try:
        folders = [f for f in os.listdir(directory) if os.path.isdir(os.path.join(directory, f))]
        sorted_folders = sorted(folders, key=lambda f: os.path.getctime(os.path.join(directory, f)), reverse=True)
        for folder_name in sorted_folders:
            folder_path = Path(directory) / folder_name
            folder_ctime = os.path.getctime(folder_path)
            if since_timestamp and folder_ctime < since_timestamp:
                continue
            excel_export_path = folder_path / "Export" / "xlsx"
            if not excel_export_path.exists() or not excel_export_path.is_dir():
                continue
            for xlsx_file in excel_export_path.glob("*.xlsx"):
                file_ctime = os.path.getctime(xlsx_file)
                if since_timestamp and file_ctime < since_timestamp:
                    continue
                excel_files.append({"path": str(xlsx_file), "ctime": file_ctime})
                if len(excel_files) >= max_files:
                    break
            if len(excel_files) >= max_files:
                break
    except Exception as e:
        print(f"WARNING: Error getting Excel files from {directory}: {e}")
    excel_files.sort(key=lambda x: x["ctime"], reverse=True)
    return [f["path"] for f in excel_files]


# -----------------------------------------------------------------------------
# Core analysis helpers
# -----------------------------------------------------------------------------

def langmuir_isotherm(c_e: np.ndarray, q_max: float, K: float) -> np.ndarray:
    """Langmuir isotherm: q = q_max * K * c_e / (1 + K * c_e)."""
    return q_max * K * c_e / (1.0 + K * c_e)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fit Langmuir isotherm to data.

    Returns (q_max, K, r2). If fit fails or parameters are not meaningful,
    returns (None, None, None).
    """
    # Remove NaNs
    mask = np.isfinite(c_e) & np.isfinite(q)
    c_e_clean = c_e[mask]
    q_clean = q[mask]

    if c_e_clean.size < 3:
        print("WARNING: Not enough points to fit Langmuir isotherm (need at least 3)")
        return None, None, None

    # Initial guesses: q_max ~ max(q), K ~ 1 / median(c_e)
    q_max0 = float(np.nanmax(q_clean)) if np.nanmax(q_clean) > 0 else 1.0
    median_c = float(np.nanmedian(c_e_clean)) if np.nanmedian(c_e_clean) > 0 else 1.0
    K0 = 1.0 / median_c

    try:
        popt, _ = curve_fit(
            langmuir_isotherm,
            c_e_clean,
            q_clean,
            p0=[q_max0, K0],
            bounds=([0.0, 0.0], [np.inf, np.inf]),
            maxfev=10000,
        )
        q_max_fit, K_fit = popt
        if not np.isfinite(q_max_fit) or not np.isfinite(K_fit):
            print("WARNING: Non-finite Langmuir fit parameters")
            return None, None, None

        q_pred = langmuir_isotherm(c_e_clean, q_max_fit, K_fit)
        ss_res = float(np.sum((q_clean - q_pred) ** 2))
        ss_tot = float(np.sum((q_clean - np.mean(q_clean)) ** 2))
        if ss_tot <= 0:
            print("WARNING: Total sum of squares is non-positive; cannot compute R2")
            return None, None, None
        r2 = 1.0 - ss_res / ss_tot
        if r2 < 0 or r2 > 1.0 + 1e-6:
            print("WARNING: R2 outside [0,1] range, treating fit as invalid")
            return None, None, None
        return float(q_max_fit), float(K_fit), float(r2)
    except Exception as e:
        print(f"WARNING: Langmuir fit failed: {e}")
        return None, None, None


def parse_semicolon_floats(value: Any, field_name: str) -> List[float]:
    """Parse a semicolon-separated string into a list of floats.

    Raises ValueError with a clear message if parsing fails.
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
        p_clean = p.replace(',', '.')
        try:
            floats.append(float(p_clean))
        except ValueError:
            raise ValueError(f"Cannot parse value '{p}' in metadata field '{field_name}' as float")
    return floats


def safe_float(value: Any, field_name: str) -> float:
    """Convert a value to float, supporting comma decimals and raising clear errors."""
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"Metadata field '{field_name}' must be string or number, got {type(value)}")
    v = value.strip().replace(',', '.')
    try:
        return float(v)
    except ValueError:
        raise ValueError(f"Cannot parse metadata field '{field_name}' value '{value}' as float")


def well_name_from_indices(row_idx: int, col_idx: int) -> str:
    """Convert zero-based (row_idx, col_idx) to well name like 'A1'."""
    row_letter = chr(ord('A') + row_idx)
    col_number = col_idx + 1
    return f"{row_letter}{col_number}"


# -----------------------------------------------------------------------------
# Main analysis function
# -----------------------------------------------------------------------------

def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = '../data',
                       results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for Tecan-based loading isotherm.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries to auto-detect.
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

    # ------------------------------------------------------------------
    # Auto-detect experiment ID if not provided
    # ------------------------------------------------------------------
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ('', None):
            experiment_id = sys.argv[1]
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            # Auto-detect most recent experiment_*.json in data_folder
            data_path = Path(data_folder)
            if not data_path.exists():
                raise FileNotFoundError(f"Data folder does not exist for auto-detection: {data_folder}")
            json_files = sorted(
                data_path.glob('experiment_*.json'),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not json_files:
                raise FileNotFoundError("No experiment_*.json files found for auto-detection.")
            latest = json_files[0]
            name = latest.stem  # experiment_XXXX
            if '_' not in name:
                raise ValueError(f"Cannot extract experiment ID from file name: {latest.name}")
            experiment_id = name.split('_', 1)[1]
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Auto-detected most recent experiment ID: {experiment_id}")

    if experiment_id is None or str(experiment_id).strip() == "":
        raise ValueError("experiment_id is required and could not be determined")

    # Normalize to string
    experiment_id = str(experiment_id)
    analysis_results["experiment_id"] = experiment_id

    # ------------------------------------------------------------------
    # Load experiment JSON (from root ../experiment_ID.json first, then data folder)
    # ------------------------------------------------------------------
    exp_json_root = Path('..') / f'experiment_{experiment_id}.json'
    data_folder_path = Path(data_folder)
    if exp_json_root.exists():
        data_file_path = exp_json_root
    else:
        data_file_path = data_folder_path / f'experiment_{experiment_id}.json'

    print(f"INFO: Loading experiment data from: {data_file_path}")
    try:
        with open(data_file_path, 'r') as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # ------------------------------------------------------------------
    # Extract required metadata / extra fields from eLabFTW
    # ------------------------------------------------------------------
    try:
        metadata = experiment_data['metadata_decoded']['extra_fields']
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    def get_meta_value(field: str, required: bool = True) -> Any:
        try:
            return metadata[field]['value']
        except KeyError:
            if required:
                raise KeyError(f"Missing expected metadata field: {field}")
            analysis_results["warnings"].append(f"Missing optional metadata field: {field}")
            return None

    # Core numeric metadata
    num_ligand_conc = int(safe_float(get_meta_value('Number of ligand concentrations'),
                                     'Number of ligand concentrations'))
    num_salt_conc = int(safe_float(get_meta_value('Number of salt concentrations'),
                                   'Number of salt concentrations'))
    num_replicates = int(safe_float(get_meta_value('Replicates'), 'Replicates'))

    ligand_concentrations = parse_semicolon_floats(
        get_meta_value('Ligand concentrations'), 'Ligand concentrations')
    salt_concentrations = parse_semicolon_floats(
        get_meta_value('Salt concentrations'), 'Salt concentrations')

    if len(ligand_concentrations) != num_ligand_conc:
        raise ValueError(
            f"Number of ligand concentrations ({len(ligand_concentrations)}) does not match "
            f"'Number of ligand concentrations' ({num_ligand_conc})"
        )
    if len(salt_concentrations) != num_salt_conc:
        raise ValueError(
            f"Number of salt concentrations ({len(salt_concentrations)}) does not match "
            f"'Number of salt concentrations' ({num_salt_conc})"
        )

    ligand_conc_unit = str(get_meta_value('Ligand concentration unit'))
    salt_conc_unit = str(get_meta_value('Salt concentration unit'))
    resin_mass_mg = safe_float(get_meta_value('Resin Mass'), 'Resin Mass')
    total_volume_uL = safe_float(get_meta_value('Total volume'), 'Total volume')
    calib_slope = safe_float(get_meta_value('Calibration Curve Slope'), 'Calibration Curve Slope')
    calib_intercept = safe_float(get_meta_value('Calibration Curve Intercept'), 'Calibration Curve Intercept')

    if calib_slope == 0:
        raise ValueError("Calibration Curve Slope is zero, cannot invert calibration curve")

    # Store key metadata in results for traceability
    analysis_results["metadata"].update({
        "num_ligand_concentrations": num_ligand_conc,
        "num_salt_concentrations": num_salt_conc,
        "num_replicates": num_replicates,
        "ligand_concentrations": ligand_concentrations,
        "salt_concentrations": salt_concentrations,
        "ligand_concentration_unit": ligand_conc_unit,
        "salt_concentration_unit": salt_conc_unit,
        "resin_mass_mg": resin_mass_mg,
        "total_volume_uL": total_volume_uL,
        "calibration_slope": calib_slope,
        "calibration_intercept": calib_intercept,
    })

    # ------------------------------------------------------------------
    # Fetch Tecan data: single-file endpoint experiment
    # ------------------------------------------------------------------
    print(f"INFO: Fetching Tecan data for experiment {experiment_id} from device control server...")

    try:
        data_info = check_tecan_data_availability(experiment_id)
        if not data_info.get("available", False):
            raise FileNotFoundError(
                f"No Tecan data available for experiment {experiment_id}: "
                f"{data_info.get('error', 'Unknown error')}"
            )

        print(f"INFO: Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
        tecan_data_path_str = fetch_tecan_data_file(experiment_id, results_folder)
        tecan_data_path = Path(tecan_data_path_str)
    except Exception as device_server_error:
        print(f"WARNING: Device control server access failed: {device_server_error}. "
              f"Falling back to local file search...")
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            analysis_results["message"] = (
                "Tecan data not available - analysis skipped. This is expected for test runs.")
            analysis_results["status"] = "success"
            analysis_results["metadata"]["data_source"] = "none"
            analysis_results["warnings"].append(
                "No Tecan data found on server or local system.")
            print(f"INFO: {analysis_results['message']}")
            # Save JSON and return
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
            analysis_results["warnings"].append(
                "No Tecan Excel files found in local workspace.")
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

    analysis_results["metadata"]["data_source"] = "device_or_local"
    analysis_results["data_outputs"]["raw_tecan_file"] = str(Path(tecan_data_path).resolve())
    analysis_results["files_processed"] += 1

    # ------------------------------------------------------------------
    # Read absorbance data from Excel (single endpoint measurement)
    # Absorbance data starts at row 34 (0-based index 33), column B (index 1)
    # ------------------------------------------------------------------
    num_rows_plate = 8  # A-H
    num_cols_plate = 12  # 1-12

    print(f"INFO: Reading absorbance data from {tecan_data_path}")
    try:
        raw_df = pd.read_excel(
            tecan_data_path,
            header=None,
            skiprows=33,  # skip first 33 rows, so next row is row 34 (A row)
            usecols=list(range(1, 1 + num_cols_plate)),  # columns B-M (12 cols)
            nrows=num_rows_plate,
        )
    except Exception as e:
        raise RuntimeError(f"Failed to read Tecan Excel file '{tecan_data_path}': {e}")

    if raw_df.shape != (num_rows_plate, num_cols_plate):
        print(f"WARNING: Expected absorbance matrix of shape (8,12), got {raw_df.shape}")

    # Map each well to absorbance
    absorbance_records: List[Dict[str, Any]] = []
    for r in range(num_rows_plate):
        for c in range(num_cols_plate):
            well = well_name_from_indices(r, c)
            val = raw_df.iat[r, c]
            try:
                abs_val = float(val)
            except (TypeError, ValueError):
                abs_val = np.nan
            absorbance_records.append({
                "well": well,
                "row_index": r,
                "col_index": c,
                "absorbance": abs_val,
            })

    absorbance_df = pd.DataFrame(absorbance_records)

    # ------------------------------------------------------------------
    # Map wells to experimental conditions based on plate layout
    # ------------------------------------------------------------------
    total_required_wells = num_ligand_conc * num_salt_conc * num_replicates
    if total_required_wells > 96:
        raise ValueError(
            f"Requested combinations exceed 96-well capacity: "
            f"{num_ligand_conc} ligand x {num_salt_conc} salt x {num_replicates} replicates = "
            f"{total_required_wells} wells")

    # Salt mapping along columns, replicates grouped row-wise as in example
    # Example: num_salt_conc=4, replicates=3 -> columns 0-2 salt0, 3-5 salt1, 6-8 salt2, 9-11 salt3
    # In general: block_size = num_replicates, each block is one salt concentration
    block_size = num_replicates
    cols_per_salt = block_size
    if cols_per_salt * num_salt_conc > num_cols_plate:
        raise ValueError(
            f"Plate cannot hold all salt concentrations with given replicates across columns: "
            f"num_salt_conc={num_salt_conc}, replicates={num_replicates}")

    # Map each column index to salt index
    col_to_salt_index: Dict[int, int] = {}
    for salt_idx in range(num_salt_conc):
        for j in range(cols_per_salt):
            col = salt_idx * cols_per_salt + j
            if col < num_cols_plate:
                col_to_salt_index[col] = salt_idx

    # Rows map directly to ligand concentrations up to num_ligand_conc
    # If num_ligand_conc < 8, lower rows are used; higher rows are ignored

    condition_records: List[Dict[str, Any]] = []
    for r in range(num_rows_plate):
        ligand_idx = r
        if ligand_idx >= num_ligand_conc:
            continue  # row not used for data
        c0 = ligand_concentrations[ligand_idx]
        for c in range(num_cols_plate):
            if c not in col_to_salt_index:
                continue
            salt_idx = col_to_salt_index[c]
            if salt_idx >= num_salt_conc:
                continue
            salt_c = salt_concentrations[salt_idx]

            well = well_name_from_indices(r, c)
            abs_val = float(absorbance_df.loc[
                (absorbance_df["row_index"] == r) & (absorbance_df["col_index"] == c),
                "absorbance",
            ].values[0])

            condition_records.append({
                "well": well,
                "row_index": r,
                "col_index": c,
                "ligand_index": ligand_idx,
                "salt_index": salt_idx,
                "c0": c0,
                "salt_concentration": salt_c,
                "absorbance": abs_val,
            })

    condition_df = pd.DataFrame(condition_records)

    if condition_df.empty:
        raise RuntimeError("No wells mapped to conditions; check plate layout and metadata.")

    # It is possible that not all planned wells have data; warn if mismatch
    if len(condition_df) != total_required_wells:
        msg = (
            f"Number of mapped wells ({len(condition_df)}) does not match expected "
            f"({total_required_wells}) from num_ligand_conc x num_salt_conc x replicates. "
            f"Some wells may be missing or mapping may be incorrect.")
        print(f"WARNING: {msg}")
        analysis_results["warnings"].append(msg)

    # ------------------------------------------------------------------
    # Step 1: Calculate equilibrium ligand concentration cE for each well
    #         cE = (Absorbance - intercept) / slope
    # ------------------------------------------------------------------
    condition_df["cE"] = (condition_df["absorbance"] - calib_intercept) / calib_slope

    # Sanity checks: cE between 0 and max c0 (per well) and not flat vs c0 in each column
    max_c0 = max(ligand_concentrations)

    # Global sanity: all cE finite
    invalid_cE_mask = ~np.isfinite(condition_df["cE"])
    if invalid_cE_mask.any():
        count_invalid = int(invalid_cE_mask.sum())
        msg = f"Found {count_invalid} non-finite cE values; these wells will be dropped."
        print(f"WARNING: {msg}")
        analysis_results["warnings"].append(msg)
        condition_df = condition_df.loc[~invalid_cE_mask].copy()

    # Range check per well
    out_of_range_mask = (condition_df["cE"] < 0) | (condition_df["cE"] > max_c0 + 1e-9)
    if out_of_range_mask.any():
        count_oor = int(out_of_range_mask.sum())
        msg = (
            f"{count_oor} cE values are outside [0, max(c0)] range. "
            f"This suggests calibration may be wrong or data has issues.")
        print(f"WARNING: {msg}")
        analysis_results["warnings"].append(msg)

    # Per-column variability check: cE should vary with c0 in each column
    variability_warnings = 0
    for c in range(num_cols_plate):
        df_col = condition_df[condition_df["col_index"] == c]
        if df_col.empty:
            continue
        # compute correlation between c0 and cE
        if df_col["c0"].nunique() < 2:
            continue
        try:
            corr = df_col[["c0", "cE"]].corr().iat[0, 1]
        except Exception:
            continue
        if np.isnan(corr):
            continue
        if abs(corr) < 0.3:
            variability_warnings += 1
    if variability_warnings > 0:
        msg = (
            f"Low correlation between c0 and cE in {variability_warnings} column(s). "
            f"This may indicate calibration not applied correctly.")
        print(f"WARNING: {msg}")
        analysis_results["warnings"].append(msg)

    # ------------------------------------------------------------------
    # Step 2: Aggregate replicates for each (ligand_index, salt_index)
    # ------------------------------------------------------------------
    group_cols = ["ligand_index", "salt_index", "c0", "salt_concentration"]
    grouped = condition_df.groupby(group_cols, as_index=False).agg(
        n_wells=("cE", "count"),
        cE_mean=("cE", "mean"),
        cE_std=("cE", "std"),
        absorbance_mean=("absorbance", "mean"),
        absorbance_std=("absorbance", "std"),
    )

    expected_groups = num_ligand_conc * num_salt_conc
    if len(grouped) != expected_groups:
        raise RuntimeError(
            f"Number of aggregated parameter combinations ({len(grouped)}) does not match "
            f"expected ({expected_groups}) from num_ligand_conc x num_salt_conc. "
            f"Well-to-condition mapping appears inconsistent; aborting analysis.")

    if not np.all(grouped["n_wells"] == num_replicates):
        msg = (
            "Not all parameter combinations have the expected number of replicates: "
            f"expected {num_replicates}, observed counts: "
            f"{sorted(grouped['n_wells'].unique().tolist())}")
        print(f"WARNING: {msg}")
        analysis_results["warnings"].append(msg)

    # Replace NaN std with 0 for single replicate cases
    grouped["cE_std"] = grouped["cE_std"].fillna(0.0)
    grouped["absorbance_std"] = grouped["absorbance_std"].fillna(0.0)

    # ------------------------------------------------------------------
    # Step 3: Calculate loading q for each parameter combination
    #         q = (c0 - cE_mean) * v_total / m_resin
    # ------------------------------------------------------------------
    grouped["q"] = (grouped["c0"] - grouped["cE_mean"]) * total_volume_uL / resin_mass_mg

    # Sanity: q should be non-negative
    negative_q_mask = grouped["q"] < -1e-9
    if negative_q_mask.any():
        count_neg = int(negative_q_mask.sum())
        msg = f"{count_neg} loading values q are negative; check calibration and mapping."
        print(f"WARNING: {msg}")
        analysis_results["warnings"].append(msg)

    # ------------------------------------------------------------------
    # Step 4: Langmuir fit q(cE) for each salt concentration
    # ------------------------------------------------------------------
    fit_results: List[Dict[str, Any]] = []

    for salt_idx, salt_c in enumerate(salt_concentrations):
        df_salt = grouped[grouped["salt_index"] == salt_idx].copy()
        if df_salt.empty:
            msg = f"No data for salt_index={salt_idx} (salt_concentration={salt_c})."
            print(f"WARNING: {msg}")
            analysis_results["warnings"].append(msg)
            continue

        c_e = df_salt["cE_mean"].values
        q_vals = df_salt["q"].values

        q_max_fit, K_fit, r2_fit = fit_langmuir(c_e, q_vals)

        fit_results.append({
            "salt_index": salt_idx,
            "salt_concentration": salt_c,
            "q_max": q_max_fit,
            "K": K_fit,
            "r2": r2_fit,
            "num_points": int(len(df_salt)),
        })

    fit_results_df = pd.DataFrame(fit_results)

    # ------------------------------------------------------------------
    # Step 5: Plot all isotherms (data points + fits) in one graph
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 6))

    colors = plt.cm.viridis(np.linspace(0, 1, max(1, num_salt_conc)))

    for salt_idx, salt_c in enumerate(salt_concentrations):
        df_salt = grouped[grouped["salt_index"] == salt_idx].copy()
        if df_salt.empty:
            continue
        color = colors[salt_idx % len(colors)]
        label_base = f"{salt_c:g} {salt_conc_unit}"

        # Plot data points
        ax.errorbar(
            df_salt["cE_mean"],
            df_salt["q"],
            yerr=None,
            fmt='o',
            color=color,
            label=None,
            alpha=0.8,
        )

        # Overlay fit if available
        fit_row = fit_results_df[fit_results_df["salt_index"] == salt_idx]
        if not fit_row.empty and pd.notna(fit_row["q_max"].iloc[0]) and pd.notna(fit_row["K"].iloc[0]):
            q_max_fit = fit_row["q_max"].iloc[0]
            K_fit = fit_row["K"].iloc[0]
            r2_fit = fit_row["r2"].iloc[0]

            c_e_grid = np.linspace(max(0.0, df_salt["cE_mean"].min()), df_salt["cE_mean"].max(), 200)
            q_fit = langmuir_isotherm(c_e_grid, q_max_fit, K_fit)
            fit_label = (
                f"{label_base} (qmax={q_max_fit:.3g}, K={K_fit:.3g}, "
                f"R2={r2_fit:.3f})" if r2_fit is not None else
                f"{label_base} (qmax={q_max_fit:.3g}, K={K_fit:.3g})"
            )
            ax.plot(c_e_grid, q_fit, '-', color=color, label=fit_label)
        else:
            ax.plot([], [], '-', color=color, label=f"{label_base} (fit failed)")

    ax.set_xlabel(f"Equilibrium ligand concentration cE ({ligand_conc_unit})")
    ax.set_ylabel(f"Loading q ({ligand_conc_unit} * uL / mg)")
    ax.set_title("Loading isotherms at different salt concentrations")
    ax.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
    ax.legend(fontsize=8)
    fig.tight_layout()

    plot_png_path = results_folder_path / f"isotherms_{experiment_id}.png"
    plot_pdf_path = results_folder_path / f"isotherms_{experiment_id}.pdf"
    fig.savefig(plot_png_path, dpi=300)
    fig.savefig(plot_pdf_path)
    plt.close(fig)

    analysis_results["plots"]["isotherms_png"] = str(plot_png_path.resolve())
    analysis_results["plots"]["isotherms_pdf"] = str(plot_pdf_path.resolve())

    # ------------------------------------------------------------------
    # Save processed data (CSV) and fit results
    # ------------------------------------------------------------------
    raw_abs_csv = results_folder_path / f"absorbance_wells_{experiment_id}.csv"
    absorbance_df.to_csv(raw_abs_csv, index=False)

    cond_csv = results_folder_path / f"well_conditions_{experiment_id}.csv"
    condition_df.to_csv(cond_csv, index=False)

    grouped_csv = results_folder_path / f"aggregated_conditions_{experiment_id}.csv"
    grouped.to_csv(grouped_csv, index=False)

    fit_csv = results_folder_path / f"langmuir_fits_{experiment_id}.csv"
    fit_results_df.to_csv(fit_csv, index=False)

    analysis_results["data_outputs"].update({
        "absorbance_wells_csv": str(raw_abs_csv.resolve()),
        "well_conditions_csv": str(cond_csv.resolve()),
        "aggregated_conditions_csv": str(grouped_csv.resolve()),
        "langmuir_fits_csv": str(fit_csv.resolve()),
    })

    # ------------------------------------------------------------------
    # Finalize results
    # ------------------------------------------------------------------
    analysis_results["status"] = "success"
    analysis_results["message"] = "Analysis completed successfully."

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, 'w') as f:
        json.dump(analysis_results, f, indent=4)
    print(f"Saved analysis results JSON to: {results_json_path}")

    return analysis_results


# -----------------------------------------------------------------------------
# Command line interface
# -----------------------------------------------------------------------------

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
