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
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

import requests

# Device control server configuration for Tecan (from system instructions)
DEVICE_CONTROL_SERVER = os.getenv("DEVICE_CONTROL_SERVER", "http://localhost:8000")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "your-secure-api-key-here")


# ============================================================================
# Tecan helper functions (from system blueprint, unmodified except for prints)
# ============================================================================

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


# ============================================================================
# Utility helpers
# ============================================================================


def auto_detect_latest_experiment_id(data_folder: Path) -> Optional[str]:
    """Auto-detect the most recent experiment_<id>.json file in data_folder.

    Returns the detected experiment ID as string, or None if none found.
    """
    if not data_folder.exists():
        return None
    candidates = sorted(data_folder.glob("experiment_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    latest = candidates[0]
    name = latest.stem  # experiment_<id>
    parts = name.split("experiment_")
    if len(parts) != 2 or not parts[1]:
        return None
    return parts[1]


def parse_semicolon_list(value: Any, field_name: str) -> List[float]:
    """Parse a semicolon-separated string into a list of floats.

    Accepts both comma and dot as decimal separators.
    Raises ValueError on any token that cannot be converted.
    """
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")

    if isinstance(value, (int, float)):
        return [float(value)]

    if not isinstance(value, str):
        raise TypeError(f"Metadata field '{field_name}' must be a string, got {type(value)}")

    tokens = [t.strip() for t in value.split(";") if t.strip() != ""]
    if not tokens:
        raise ValueError(f"Metadata field '{field_name}' is an empty or invalid semicolon-separated string")

    result = []
    for t in tokens:
        t_norm = t.replace(",", ".")
        try:
            result.append(float(t_norm))
        except ValueError:
            raise ValueError(f"Cannot convert token '{t}' in metadata field '{field_name}' to float")
    return result


def parse_float(value: Any, field_name: str) -> float:
    """Parse a single value into float, accepting comma decimal separator."""
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise TypeError(f"Metadata field '{field_name}' must be a string or number, got {type(value)}")
    v = value.strip().replace(",", ".")
    try:
        return float(v)
    except ValueError:
        raise ValueError(f"Cannot convert metadata field '{field_name}' value '{value}' to float")


def parse_int(value: Any, field_name: str) -> int:
    """Parse a value into int."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"Metadata field '{field_name}' float value {value} is not an integer")
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if not isinstance(value, str):
        raise TypeError(f"Metadata field '{field_name}' must be a string or number, got {type(value)}")
    v = value.strip()
    try:
        return int(v)
    except ValueError:
        # try float first
        try:
            f = float(v.replace(",", "."))
        except ValueError:
            raise ValueError(f"Cannot convert metadata field '{field_name}' value '{value}' to int")
        if not f.is_integer():
            raise ValueError(f"Metadata field '{field_name}' value '{value}' is not an integer")
        return int(f)


# ============================================================================
# Core analysis logic
# ============================================================================


def r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Simple R^2 implementation to avoid external sklearn dependency."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    ss_res = np.nansum((y_true - y_pred) ** 2)
    ss_tot = np.nansum((y_true - np.nanmean(y_true)) ** 2)
    if ss_tot == 0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


def langmuir_isotherm(c_e: np.ndarray, q_max: float, K: float) -> np.ndarray:
    """Langmuir isotherm: q(c_e) = q_max * K * c_e / (1 + K * c_e)."""
    return q_max * K * c_e / (1.0 + K * c_e)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fit Langmuir isotherm to data.

    Returns (q_max, K, r2). If fit fails or is not meaningful, returns (None, None, None).
    """
    # Remove NaNs
    mask = np.isfinite(c_e) & np.isfinite(q)
    c_e_fit = c_e[mask]
    q_fit = q[mask]

    if c_e_fit.size < 3:
        print("WARNING: Not enough points for Langmuir fit (need at least 3).")
        return None, None, None

    # Initial guesses: q_max approx max(q), K approx 1 / median(c_e)
    q_max0 = float(np.nanmax(q_fit)) if np.any(np.isfinite(q_fit)) else 1.0
    positive_c = c_e_fit[c_e_fit > 0]
    if positive_c.size > 0:
        K0 = 1.0 / np.median(positive_c)
    else:
        K0 = 1.0

    p0 = [q_max0, K0]
    bounds = ([0.0, 0.0], [np.inf, np.inf])

    try:
        popt, _pcov = curve_fit(langmuir_isotherm, c_e_fit, q_fit, p0=p0, bounds=bounds, maxfev=10000)
        q_max_fit, K_fit = popt
        if not np.isfinite(q_max_fit) or not np.isfinite(K_fit):
            print("WARNING: Non-finite Langmuir parameters obtained.")
            return None, None, None
        q_pred = langmuir_isotherm(c_e_fit, q_max_fit, K_fit)
        r2 = r2_score(q_fit, q_pred)
        if not np.isfinite(r2) or r2 < 0.0:
            print("WARNING: Langmuir fit R^2 is invalid or negative, treat as failed fit.")
            return None, None, None
        return float(q_max_fit), float(K_fit), float(r2)
    except Exception as e:
        print(f"WARNING: Langmuir fit failed: {e}")
        return None, None, None


def map_well_to_indices(row_idx: int, col_idx: int,
                        num_ligand_conc: int,
                        num_salt_conc: int,
                        replicates: int) -> Optional[Tuple[int, int, int]]:
    """Map a well position (0-based row_idx, col_idx) to (ligand_index, salt_index, replicate_index).

    Plate rules (96-well):
    - Rows are A..H mapped to indices 0..7.
    - For each column, ligand concentration index is the row_idx (0..num_ligand_conc-1).
      Rows beyond num_ligand_conc contain no data.
    - Salt layout: columns are grouped by replicates, sequential in salt concentration.
      For each salt concentration, there are 'replicates' consecutive columns.

      Example: num_salt_conc=4, replicates=3 -> columns:
          0-2 -> salt_index 0
          3-5 -> salt_index 1
          6-8 -> salt_index 2
          9-11 -> salt_index 3

    If the well lies outside experimental design (e.g. row beyond num_ligand_conc or
    column beyond num_salt_conc * replicates), returns None.
    """
    if row_idx < 0 or row_idx >= 8:
        return None
    if col_idx < 0 or col_idx >= 12:
        return None

    if row_idx >= num_ligand_conc:
        # Row not used in this experiment
        return None

    total_used_columns = num_salt_conc * replicates
    if col_idx >= total_used_columns:
        # Column not used in this experiment
        return None

    salt_index = col_idx // replicates
    replicate_index = col_idx % replicates
    ligand_index = row_idx  # A->0, B->1, ... ascending c0

    return ligand_index, salt_index, replicate_index


def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = '../data',
                       results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm experiments.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with all key metrics and file paths.
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

    try:
        # ------------------------------------------------------------------
        # Determine experiment_id
        # ------------------------------------------------------------------
        data_folder_path = Path(data_folder)
        if experiment_id is None:
            # If CLI args are available during function call, respect them
            if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
                experiment_id = sys.argv[1]
                print(f"INFO: Using experiment_id from sys.argv: {experiment_id}")
            else:
                print("INFO: Auto-detecting latest experiment ID from data folder...")
                detected = auto_detect_latest_experiment_id(data_folder_path)
                if detected is None:
                    raise FileNotFoundError("Could not auto-detect experiment ID: no experiment_*.json files found.")
                experiment_id = detected
                print(f"INFO: Auto-detected experiment_id: {experiment_id}")
        analysis_results["experiment_id"] = experiment_id

        # ------------------------------------------------------------------
        # Load experiment JSON (first try root ../experiment_<id>.json)
        # ------------------------------------------------------------------
        root_json_path = Path('../') / f'experiment_{experiment_id}.json'
        if root_json_path.exists():
            data_file_path = root_json_path
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

        analysis_results["metadata"]["data_file"] = str(data_file_path.resolve())

        # ------------------------------------------------------------------
        # Extract required metadata from eLabFTW extra_fields
        # ------------------------------------------------------------------
        try:
            metadata = experiment_data['metadata_decoded']['extra_fields']
        except KeyError as e:
            raise KeyError(f"Missing expected metadata structure 'metadata_decoded.extra_fields': {e}")

        def get_meta_value(key: str) -> Any:
            if key not in metadata:
                raise KeyError(f"Missing expected metadata field: {key}")
            field = metadata[key]
            if not isinstance(field, dict) or 'value' not in field:
                raise KeyError(f"Metadata field '{key}' is malformed (expected dict with 'value').")
            return field['value']

        # Required numeric fields
        num_ligand_conc = parse_int(get_meta_value('Number of ligand concentrations'), 'Number of ligand concentrations')
        num_salt_conc = parse_int(get_meta_value('Number of salt concentrations'), 'Number of salt concentrations')
        replicates = parse_int(get_meta_value('Replicates'), 'Replicates')

        ligand_conc_list = parse_semicolon_list(get_meta_value('Ligand concentrations'), 'Ligand concentrations')
        salt_conc_list = parse_semicolon_list(get_meta_value('Salt concentrations'), 'Salt concentrations')

        ligand_conc_unit = str(get_meta_value('Ligand concentration unit'))
        salt_conc_unit = str(get_meta_value('Salt concentration unit'))

        calibration_slope = parse_float(get_meta_value('Calibration Curve Slope'), 'Calibration Curve Slope')
        calibration_intercept = parse_float(get_meta_value('Calibration Curve Intercept'), 'Calibration Curve Intercept')

        resin_mass_mg = parse_float(get_meta_value('Resin Mass'), 'Resin Mass')
        total_volume_uL = parse_float(get_meta_value('Total volume'), 'Total volume')

        # Basic consistency checks
        if num_ligand_conc != len(ligand_conc_list):
            raise ValueError(
                f"Number of ligand concentrations ({num_ligand_conc}) does not match length of 'Ligand concentrations' list ({len(ligand_conc_list)})."
            )
        if num_salt_conc != len(salt_conc_list):
            raise ValueError(
                f"Number of salt concentrations ({num_salt_conc}) does not match length of 'Salt concentrations' list ({len(salt_conc_list)})."
            )

        if calibration_slope == 0:
            raise ValueError("Calibration Curve Slope is zero; cannot invert calibration.")

        if resin_mass_mg <= 0:
            raise ValueError("Resin Mass must be positive.")
        if total_volume_uL <= 0:
            raise ValueError("Total volume must be positive.")

        analysis_results["metadata"].update({
            "num_ligand_concentrations": num_ligand_conc,
            "num_salt_concentrations": num_salt_conc,
            "replicates": replicates,
            "ligand_concentrations": ligand_conc_list,
            "salt_concentrations": salt_conc_list,
            "ligand_concentration_unit": ligand_conc_unit,
            "salt_concentration_unit": salt_conc_unit,
            "calibration_slope": calibration_slope,
            "calibration_intercept": calibration_intercept,
            "resin_mass_mg": resin_mass_mg,
            "total_volume_uL": total_volume_uL,
        })

        # ------------------------------------------------------------------
        # Fetch Tecan Excel data (single-file experiment pattern)
        # ------------------------------------------------------------------
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
                msg = "Tecan data not available - analysis skipped. This is expected for test runs."
                analysis_results["message"] = msg
                analysis_results["status"] = "success"
                analysis_results["note"] = "No Tecan data found on server or local system."
                print(f"INFO: {msg}")
                # Save JSON and return early
                results_json_file = f"analysis_results_{experiment_id}.json"
                results_json_path = results_folder_path / results_json_file
                with open(results_json_path, 'w') as f:
                    json.dump(analysis_results, f, indent=4)
                print(f"Saved analysis results JSON to: {results_json_path}")
                return analysis_results

            most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
            if not most_recent_excel_source:
                msg = "No recent Tecan Excel file found - analysis skipped."
                analysis_results["message"] = msg
                analysis_results["status"] = "success"
                print(f"INFO: {msg}")
                results_json_file = f"analysis_results_{experiment_id}.json"
                results_json_path = results_folder_path / results_json_file
                with open(results_json_path, 'w') as f:
                    json.dump(analysis_results, f, indent=4)
                print(f"Saved analysis results JSON to: {results_json_path}")
                return analysis_results

            tecan_data_path = results_folder_path / f'tecan_data_{experiment_id}.xlsx'
            shutil.copy(most_recent_excel_source, tecan_data_path)
            print(f"INFO: Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")

        analysis_results["data_outputs"]["raw_tecan_excel"] = str(Path(tecan_data_path).resolve())
        analysis_results["files_processed"] += 1

        # ------------------------------------------------------------------
        # Read absorbance data from Excel (starts at row 34, column B)
        # ------------------------------------------------------------------
        print("INFO: Reading absorbance data from Excel file...")
        num_rows = 8   # rows A-H
        num_cols = 12  # columns 1-12
        try:
            raw_df = pd.read_excel(
                tecan_data_path,
                header=None,
                skiprows=33,  # zero-based index 33 -> Excel row 34
                usecols=list(range(1, 1 + num_cols)),  # B to M (12 columns)
                nrows=num_rows
            )
        except Exception as e:
            raise RuntimeError(f"Failed to read Tecan Excel data: {e}")

        # raw_df shape should be (8,12) corresponding to rows A-H, cols 1-12
        if raw_df.shape[0] < num_rows or raw_df.shape[1] < num_cols:
            print("WARNING: Unexpected shape of raw data frame; some wells may be missing.")

        # Convert to numeric (coerce errors to NaN)
        absorbance_array = pd.DataFrame(raw_df).apply(pd.to_numeric, errors='coerce').to_numpy()

        # ------------------------------------------------------------------
        # Map wells to conditions and compute cE per well
        # ------------------------------------------------------------------
        print("INFO: Computing equilibrium concentrations cE for each well...")

        well_records: List[Dict[str, Any]] = []
        max_c0 = max(ligand_conc_list) if ligand_conc_list else 0.0

        for row_idx in range(num_rows):
            for col_idx in range(num_cols):
                mapping = map_well_to_indices(row_idx, col_idx, num_ligand_conc, num_salt_conc, replicates)
                if mapping is None:
                    continue
                ligand_index, salt_index, replicate_index = mapping

                absorbance = absorbance_array[row_idx, col_idx]
                if pd.isna(absorbance):
                    continue  # skip missing datapoint

                # Calibration: Abs = slope * c + intercept -> invert
                c_e = (absorbance - calibration_intercept) / calibration_slope

                # Sanity check: cE within [0, max_c0]
                if c_e < -0.05 * max_c0 or c_e > 1.05 * max_c0:
                    msg = (
                        f"Equilibrium concentration cE={c_e:.4g} for well row {row_idx}, col {col_idx} "
                        f"is outside expected range [0, {max_c0}]. "
                        f"This may indicate incorrect calibration parameters or mapping."
                    )
                    print("WARNING: " + msg)
                    analysis_results["warnings"].append(msg)

                # Store record
                record = {
                    "row_idx": row_idx,
                    "col_idx": col_idx,
                    "well_id": f"{chr(ord('A') + row_idx)}{col_idx + 1}",
                    "ligand_index": ligand_index,
                    "salt_index": salt_index,
                    "replicate_index": replicate_index,
                    "c0": ligand_conc_list[ligand_index],
                    "salt_concentration": salt_conc_list[salt_index],
                    "absorbance": float(absorbance),
                    "cE": float(c_e),
                }
                well_records.append(record)

        if not well_records:
            raise RuntimeError("No valid well records found after mapping and calibration.")

        # Convert to DataFrame
        wells_df = pd.DataFrame(well_records)

        # Check that cE varies strongly along a column as c0 varies (for each salt and column)
        print("INFO: Performing calibration sanity checks (variation of cE vs c0)...")
        for salt_idx in range(num_salt_conc):
            for rep_idx in range(replicates):
                subset = wells_df[(wells_df["salt_index"] == salt_idx) & (wells_df["replicate_index"] == rep_idx)]
                if subset.empty:
                    continue
                if subset["cE"].nunique() <= 1:
                    msg = (
                        f"Equilibrium concentrations cE do not vary across ligand concentrations for "
                        f"salt_index={salt_idx}, replicate_index={rep_idx}. "
                        f"This suggests calibration might have been applied incorrectly."
                    )
                    print("WARNING: " + msg)
                    analysis_results["warnings"].append(msg)

        # Save per-well data
        per_well_csv = results_folder_path / f"per_well_results_{experiment_id}.csv"
        wells_df.to_csv(per_well_csv, index=False)
        analysis_results["data_outputs"]["per_well_results_csv"] = str(per_well_csv.resolve())

        # ------------------------------------------------------------------
        # Aggregate replicates by (ligand_index, salt_index)
        # ------------------------------------------------------------------
        print("INFO: Aggregating replicates by ligand and salt concentration...")

        grouped = wells_df.groupby(["ligand_index", "salt_index"], as_index=False).agg(
            c0_mean=("c0", "mean"),
            cE_mean=("cE", "mean"),
            cE_std=("cE", "std"),
            n_replicates=("cE", "count"),
            salt_concentration=("salt_concentration", "mean"),
        )

        expected_groups = num_ligand_conc * num_salt_conc
        actual_groups = grouped.shape[0]
        if actual_groups != expected_groups:
            raise RuntimeError(
                f"Well-to-condition mapping error: expected {expected_groups} aggregated points "
                f"({num_ligand_conc} ligand concentrations x {num_salt_conc} salt concentrations), "
                f"but obtained {actual_groups}. Check plate layout and mapping logic."
            )

        # Check replicate counts
        bad_rep = grouped[grouped["n_replicates"] != replicates]
        if not bad_rep.empty:
            msg = (
                "Some condition groups do not have the expected number of replicates "
                f"({replicates}). This may indicate missing or extra wells."
            )
            print("WARNING: " + msg)
            analysis_results["warnings"].append(msg)

        # If std is NaN (single replicate), set to 0 for reporting
        grouped["cE_std"] = grouped["cE_std"].fillna(0.0)

        # ------------------------------------------------------------------
        # Compute loading q for each condition
        # q = (c0 - cE) * v_total / m_resin
        # ------------------------------------------------------------------
        print("INFO: Computing loading q for each condition...")
        grouped["q"] = (grouped["c0_mean"] - grouped["cE_mean"]) * total_volume_uL / resin_mass_mg

        # Sanity: q should not be negative
        negative_q = grouped[grouped["q"] < -1e-9]
        if not negative_q.empty:
            msg = "Some computed loading values q are negative. This may indicate data or calibration issues."
            print("WARNING: " + msg)
            analysis_results["warnings"].append(msg)

        # Save aggregated data
        aggregated_csv = results_folder_path / f"aggregated_results_{experiment_id}.csv"
        grouped.to_csv(aggregated_csv, index=False)
        analysis_results["data_outputs"]["aggregated_results_csv"] = str(aggregated_csv.resolve())

        # ------------------------------------------------------------------
        # Langmuir fits per salt concentration
        # ------------------------------------------------------------------
        print("INFO: Performing Langmuir fits for each salt concentration...")
        fit_results: List[Dict[str, Any]] = []

        for salt_idx in range(num_salt_conc):
            salt_group = grouped[grouped["salt_index"] == salt_idx].copy()
            if salt_group.empty:
                continue

            c_e_vals = salt_group["cE_mean"].to_numpy(dtype=float)
            q_vals = salt_group["q"].to_numpy(dtype=float)

            # Only consider non-negative c_e and q
            mask = (c_e_vals >= 0) & (q_vals >= 0)
            c_e_vals = c_e_vals[mask]
            q_vals = q_vals[mask]

            if c_e_vals.size < 3:
                msg = f"Not enough valid points for Langmuir fit at salt index {salt_idx}. Skipping fit."
                print("WARNING: " + msg)
                analysis_results["warnings"].append(msg)
                fit_results.append({
                    "salt_index": salt_idx,
                    "salt_concentration": salt_conc_list[salt_idx],
                    "q_max": None,
                    "K": None,
                    "R2": None,
                })
                continue

            q_max_fit, K_fit, r2_fit = fit_langmuir(c_e_vals, q_vals)
            fit_results.append({
                "salt_index": salt_idx,
                "salt_concentration": salt_conc_list[salt_idx],
                "q_max": q_max_fit,
                "K": K_fit,
                "R2": r2_fit,
            })

        fit_results_df = pd.DataFrame(fit_results)
        fit_results_csv = results_folder_path / f"langmuir_fit_results_{experiment_id}.csv"
        fit_results_df.to_csv(fit_results_csv, index=False)
        analysis_results["data_outputs"]["langmuir_fit_results_csv"] = str(fit_results_csv.resolve())

        # ------------------------------------------------------------------
        # Plot all isotherms with fits
        # ------------------------------------------------------------------
        print("INFO: Generating isotherm plot...")
        plt.figure(figsize=(8, 6))

        colors = plt.cm.viridis(np.linspace(0, 1, num_salt_conc))
        for salt_idx in range(num_salt_conc):
            salt_group = grouped[grouped["salt_index"] == salt_idx].copy()
            if salt_group.empty:
                continue

            c_e_vals = salt_group["cE_mean"].to_numpy(dtype=float)
            q_vals = salt_group["q"].to_numpy(dtype=float)
            salt_label = f"{salt_conc_list[salt_idx]} {salt_conc_unit}"

            # Scatter data points
            plt.scatter(c_e_vals, q_vals, color=colors[salt_idx], label=f"Data {salt_label}")

            # Fit curve (if available)
            fit_row = fit_results_df[fit_results_df["salt_index"] == salt_idx]
            if not fit_row.empty and pd.notna(fit_row.iloc[0]["q_max"]) and pd.notna(fit_row.iloc[0]["K"]):
                q_max_fit = float(fit_row.iloc[0]["q_max"])
                K_fit = float(fit_row.iloc[0]["K"])
                r2_fit = fit_row.iloc[0]["R2"]

                c_e_fit_range = np.linspace(0, max(c_e_vals) * 1.1 if np.max(c_e_vals) > 0 else 1.0, 200)
                q_fit_curve = langmuir_isotherm(c_e_fit_range, q_max_fit, K_fit)

                label_fit = f"Fit {salt_label} (qmax={q_max_fit:.3g}, K={K_fit:.3g}, R2={r2_fit:.3f})"
                plt.plot(c_e_fit_range, q_fit_curve, color=colors[salt_idx], linestyle='--', label=label_fit)

        plt.xlabel(f"Equilibrium concentration cE [{ligand_conc_unit}]")
        plt.ylabel(f"Loading q [{ligand_conc_unit} * uL / mg]")
        plt.title("Loading isotherms at different salt concentrations")
        plt.legend(fontsize=8)
        plt.tight_layout()

        plot_png = results_folder_path / f"isotherms_{experiment_id}.png"
        plot_pdf = results_folder_path / f"isotherms_{experiment_id}.pdf"
        plt.savefig(plot_png, dpi=300)
        plt.savefig(plot_pdf)
        plt.close()

        analysis_results["plots"]["isotherms_png"] = str(plot_png.resolve())
        analysis_results["plots"]["isotherms_pdf"] = str(plot_pdf.resolve())

        # ------------------------------------------------------------------
        # Finalize results
        # ------------------------------------------------------------------
        analysis_results["status"] = "success"
        analysis_results["message"] = "Analysis completed successfully."

    except Exception as e:
        analysis_results["status"] = "failed"
        analysis_results["message"] = str(e)
        print(f"ERROR: {e}")

    # Save the analysis results as JSON
    exp_id_for_file = analysis_results.get("experiment_id", "unknown")
    results_json_file = f"analysis_results_{exp_id_for_file}.json"
    results_json_path = results_folder_path / results_json_file
    try:
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
    except Exception as e:
        print(f"ERROR: Failed to save analysis results JSON: {e}")

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
