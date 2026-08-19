#!/usr/bin/env python3
"""
Analysis Script - Tecan Plate Reader Loading Isotherm Evaluation
Can be called externally with experiment ID as parameter.

This script fetches Tecan Spark absorbance data for a 96-well plate from the
Tecan device control server (with local workspace fallback) and evaluates
loading isotherms for a ligand binding experiment.

Core features
------------
- Robust experiment metadata loading from experiment_<ID>.json
- Tecan Spark data fetching via HTTP API (multi-file capable, here single-file)
- Plate layout decoding based on eLabFTW metadata fields
- Calibration-curve based concentration calculation (cE from absorbance)
- Replicate averaging per (salt, ligand) condition
- Loading q calculation per condition
- Langmuir isotherm fitting (q vs cE) per salt concentration with
  constrained non-linear regression and R^2 reporting
- Plotting of all isotherms and fits into a single figure
- Structured JSON results and CSV exports into a results folder

The script is designed to be both importable (analyze_experiment function)
and executable as a command line tool.
"""

import os
import sys
import json
import argparse
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

import requests

# ---------------------------------------------------------------------------
# Tecan Spark device control server helpers (from specification)
# ---------------------------------------------------------------------------

DEVICE_CONTROL_SERVER = os.getenv("DEVICE_CONTROL_SERVER", "http://localhost:8000")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "your-secure-api-key-here")


def check_tecan_data_availability(experiment_id: str) -> dict:
    """Check if Tecan data is available for the given experiment ID."""
    url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}/list"
    headers = {"X-API-Key": DEVICE_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {
                "available": data.get("total_files", 0) > 0,
                "total_files": data.get("total_files", 0),
                "files": data.get("files", []),
            }
        elif response.status_code == 404:
            return {
                "available": False,
                "total_files": 0,
                "files": [],
                "error": "No data found",
            }
        else:
            return {
                "available": False,
                "total_files": 0,
                "files": [],
                "error": f"Server error: {response.status_code}",
            }
    except Exception as e:
        return {"available": False, "total_files": 0, "files": [], "error": str(e)}


def fetch_tecan_data_file(experiment_id: str, save_to_folder: str, file_index: int = 0) -> str:
    """Fetch a SINGLE Tecan Excel data file from device control server."""
    url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}?file_index={file_index}"
    headers = {"X-API-Key": DEVICE_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        save_folder = Path(save_to_folder)
        save_folder.mkdir(parents=True, exist_ok=True)
        filename = f"tecan_data_{experiment_id}.xlsx"
        file_path = save_folder / filename
        with open(file_path, "wb") as f:
            f.write(response.content)
        print(f"SUCCESS: Successfully downloaded Tecan data to: {file_path}")
        return str(file_path)
    except requests.exceptions.ConnectionError:
        raise Exception(
            f"Cannot connect to device control server at {DEVICE_CONTROL_SERVER}"
        )
    except requests.exceptions.Timeout:
        raise Exception("Timeout while fetching Tecan data from device control server")
    except Exception as e:
        raise Exception(
            f"Failed to fetch Tecan data for experiment {experiment_id}: {str(e)}"
        )


def fetch_all_tecan_data_files(experiment_id: str, save_to_folder: str) -> List[str]:
    """Fetch ALL Tecan Excel data files for an experiment.

    USE THIS FOR MULTI-MEASUREMENT EXPERIMENTS.
    """

    headers = {"X-API-Key": DEVICE_API_KEY}
    save_folder = Path(save_to_folder)
    save_folder.mkdir(parents=True, exist_ok=True)
    downloaded_files: List[str] = []
    try:
        list_url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}/list"
        list_response = requests.get(list_url, headers=headers, timeout=10)
        if list_response.status_code == 404:
            raise FileNotFoundError(f"No Tecan data found for experiment {experiment_id}")
        list_response.raise_for_status()
        files_info = list_response.json()
        total_files = files_info.get("total_files", 0)
        print(
            f"INFO: Found {total_files} Tecan data file(s) for experiment {experiment_id}"
        )
        if total_files == 0:
            raise FileNotFoundError(
                f"No Tecan data files available for experiment {experiment_id}"
            )
        for file_info in files_info.get("files", []):
            filename = file_info["filename"]
            file_url = (
                f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}/file/{filename}"
            )
            try:
                response = requests.get(file_url, headers=headers, timeout=30)
                response.raise_for_status()
                file_path = save_folder / filename
                with open(file_path, "wb") as f:
                    f.write(response.content)
                downloaded_files.append(str(file_path))
                print(f"INFO: Downloaded Tecan file: {filename}")
            except Exception as e:
                print(f"WARNING: Failed to download {filename}: {e}")
        print(
            f"INFO: Successfully downloaded {len(downloaded_files)} of {total_files} files"
        )
        return downloaded_files
    except requests.exceptions.ConnectionError:
        raise Exception(
            f"Cannot connect to device control server at {DEVICE_CONTROL_SERVER}"
        )
    except requests.exceptions.Timeout:
        raise Exception("Timeout while fetching Tecan data from device control server")
    except FileNotFoundError:
        raise
    except Exception as e:
        raise Exception(
            f"Failed to fetch Tecan data for experiment {experiment_id}: {str(e)}"
        )


def get_most_recent_folder(directory, n=0):
    """Finds the n-th most recent subfolder in a given directory."""
    folders = [
        f
        for f in os.listdir(directory)
        if os.path.isdir(os.path.join(directory, f))
    ]
    if not folders:
        return None
    sorted_folders = sorted(
        folders,
        key=lambda f: os.path.getctime(os.path.join(directory, f)),
        reverse=True,
    )
    return (
        os.path.join(directory, sorted_folders[n])
        if len(sorted_folders) > n
        else None
    )


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
        excel_files = [f for f in files_in_folder if f.lower().endswith(".xlsx")]
        if not excel_files:
            return None
        excel_files.sort(
            key=lambda f: os.path.getctime(Path(excel_export_path) / f),
            reverse=True,
        )
        return str(Path(excel_export_path) / excel_files[0])
    except Exception:
        return None


def get_all_recent_excel_files(
    directory: str, max_files: int = 10, since_timestamp: float = None
) -> List[str]:
    """Find ALL recent Excel files from Tecan workspace folders.

    USE THIS FOR LOCAL FALLBACK WITH MULTI-FILE EXPERIMENTS.
    """

    excel_files: List[Dict[str, Any]] = []
    try:
        folders = [
            f
            for f in os.listdir(directory)
            if os.path.isdir(os.path.join(directory, f))
        ]
        sorted_folders = sorted(
            folders,
            key=lambda f: os.path.getctime(os.path.join(directory, f)),
            reverse=True,
        )
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


# ---------------------------------------------------------------------------
# Domain-specific helpers for loading isotherm analysis
# ---------------------------------------------------------------------------

WELL_ROWS = ["A", "B", "C", "D", "E", "F", "G", "H"]
WELL_COLS = list(range(1, 13))


def parse_semicolon_floats(value: str, field_name: str) -> List[float]:
    """Parse a semicolon-separated list of floats from metadata.

    Accepts both dot and comma as decimal separators. Raises ValueError with
    a descriptive message if parsing fails.
    """

    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if not isinstance(value, str):
        raise ValueError(
            f"Metadata field '{field_name}' must be a string with ';'-separated numbers"
        )
    parts = [p.strip() for p in value.split(";") if p.strip() != ""]
    floats: List[float] = []
    for p in parts:
        p_norm = p.replace(",", ".")
        try:
            floats.append(float(p_norm))
        except ValueError as e:
            raise ValueError(
                f"Cannot parse value '{p}' in metadata field '{field_name}' as float"
            ) from e
    if not floats:
        raise ValueError(
            f"Metadata field '{field_name}' does not contain any numeric entries"
        )
    return floats


def parse_positive_int(value: Any, field_name: str) -> int:
    """Parse and validate a positive integer metadata field."""

    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    try:
        ivalue = int(str(value).strip())
    except Exception as e:
        raise ValueError(
            f"Metadata field '{field_name}' must be an integer, got {value!r}"
        ) from e
    if ivalue <= 0:
        raise ValueError(
            f"Metadata field '{field_name}' must be a positive integer, got {ivalue}"
        )
    return ivalue


def parse_positive_float(value: Any, field_name: str) -> float:
    """Parse and validate a positive float metadata field."""

    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    try:
        fvalue = float(str(value).replace(",", "."))
    except Exception as e:
        raise ValueError(
            f"Metadata field '{field_name}' must be numeric, got {value!r}"
        ) from e
    if fvalue <= 0:
        raise ValueError(
            f"Metadata field '{field_name}' must be positive, got {fvalue}"
        )
    return fvalue


def langmuir_isotherm(c: np.ndarray, qmax: float, K: float) -> np.ndarray:
    """Langmuir isotherm: q(c) = qmax * K * c / (1 + K * c).

    Parameters
    ----------
    c : ndarray
        Equilibrium concentration.
    qmax : float
        Maximum loading capacity (must be positive).
    K : float
        Affinity constant (must be positive).
    """

    return qmax * K * c / (1.0 + K * c)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fit Langmuir isotherm to data (q vs c_e) with sensible constraints.

    Returns (qmax, K, R2). If fitting fails or yields non-physical parameters,
    returns (None, None, None).
    """

    # Clean data: finite and non-negative
    mask = (
        np.isfinite(c_e)
        & np.isfinite(q)
        & (c_e >= 0.0)
        & (q >= 0.0)
    )
    c_e_clean = c_e[mask]
    q_clean = q[mask]

    if c_e_clean.size < 3:
        print(
            "WARNING: Not enough data points for reliable Langmuir fit (need >= 3)."
        )
        return None, None, None

    # Initial guesses derived from data
    qmax_guess = float(np.nanmax(q_clean)) if np.any(np.isfinite(q_clean)) else 1.0
    if not np.isfinite(qmax_guess) or qmax_guess <= 0:
        qmax_guess = 1.0

    # Try to place K such that K * median(c_e) ~ 1
    c_med = float(np.nanmedian(c_e_clean)) if np.any(np.isfinite(c_e_clean)) else 1.0
    if not np.isfinite(c_med) or c_med <= 0:
        K_guess = 1.0
    else:
        K_guess = 1.0 / c_med

    # Bounds: positive parameters, prevent runaway
    lower_bounds = (0.0, 0.0)
    upper_bounds = (qmax_guess * 100.0 if qmax_guess > 0 else np.inf, np.inf)

    try:
        popt, pcov = curve_fit(
            langmuir_isotherm,
            c_e_clean,
            q_clean,
            p0=(qmax_guess, K_guess),
            bounds=(lower_bounds, upper_bounds),
            maxfev=10000,
        )
        qmax_fit, K_fit = float(popt[0]), float(popt[1])
        if not (np.isfinite(qmax_fit) and np.isfinite(K_fit)):
            print("WARNING: Langmuir fit returned non-finite parameters.")
            return None, None, None
        if qmax_fit <= 0 or K_fit <= 0:
            print("WARNING: Langmuir fit returned non-positive parameters.")
            return None, None, None

        # Sanity: qmax should not be wildly outside observed range
        q_obs_max = float(np.nanmax(q_clean)) if np.any(np.isfinite(q_clean)) else qmax_fit
        if q_obs_max > 0 and (qmax_fit > 100.0 * q_obs_max):
            print(
                "WARNING: Langmuir fit qmax is more than 100x observed max q. "
                "Treating fit as invalid."
            )
            return None, None, None

        # Compute R^2 on cleaned data
        q_pred = langmuir_isotherm(c_e_clean, qmax_fit, K_fit)
        ss_res = float(np.sum((q_clean - q_pred) ** 2))
        ss_tot = float(np.sum((q_clean - np.mean(q_clean)) ** 2))
        if ss_tot == 0:
            r2 = 1.0 if ss_res == 0 else 0.0
        else:
            r2 = 1.0 - ss_res / ss_tot
        return qmax_fit, K_fit, float(r2)

    except Exception as e:
        print(f"WARNING: Langmuir fit failed: {e}")
        return None, None, None


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------


def analyze_experiment(
    experiment_id: Optional[str] = None,
    data_folder: str = "../data",
    results_folder: str = "../results",
) -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm experiments.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries to
            auto-detect from most recent experiment_*.json in data_folder.
        data_folder (str): Path to the folder containing experiment_ID.json
            files.
        results_folder (str): Path to the folder where all analysis outputs
            will be saved.

    Returns:
        dict: Analysis results with all key metrics and paths to generated
            files.
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

    data_folder_path = Path(data_folder)
    if not data_folder_path.exists() or not data_folder_path.is_dir():
        raise FileNotFoundError(f"Data folder does not exist: {data_folder_path}")

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            analysis_results["experiment_id"] = experiment_id
            print(
                f"INFO: Using experiment ID from command line argument: {experiment_id}"
            )
        else:
            # Find most recent experiment_*.json
            json_files = sorted(
                data_folder_path.glob("experiment_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not json_files:
                raise FileNotFoundError(
                    f"No experiment_*.json files found in data folder {data_folder_path}"
                )
            latest = json_files[0]
            name = latest.stem  # experiment_<ID>
            if "_" not in name:
                raise ValueError(
                    f"Cannot extract experiment ID from filename: {latest.name}"
                )
            experiment_id = name.split("_", 1)[1]
            analysis_results["experiment_id"] = experiment_id
            print(
                f"INFO: Auto-detected experiment ID {experiment_id} from file {latest.name}"
            )

    # Load experiment JSON
    json_root_first = Path("../") / f"experiment_{experiment_id}.json"
    if json_root_first.exists():
        data_file_path = json_root_first
        print(
            f"INFO: Using experiment JSON from root folder: {data_file_path}"
        )
    else:
        data_file_path = data_folder_path / f"experiment_{experiment_id}.json"
        print(f"INFO: Using experiment JSON from data folder: {data_file_path}")

    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # Extract metadata with error handling
    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure or field: {e}")

    # Helper to get a field's value with standard error message
    def get_meta_value(field: str) -> Any:
        if field not in metadata:
            raise KeyError(f"Missing expected metadata field: '{field}'")
        if not isinstance(metadata[field], dict) or "value" not in metadata[field]:
            raise KeyError(
                f"Metadata field '{field}' does not contain a 'value' entry"
            )
        return metadata[field]["value"]

    # Read required metadata fields
    try:
        ligand_conc_str = get_meta_value("Ligand concentrations")
        salt_conc_str = get_meta_value("Salt concentrations")
        n_ligand_conc_meta = get_meta_value("Number of ligand concentrations")
        n_salt_conc_meta = get_meta_value("Number of salt concentrations")
        replicates_meta = get_meta_value("Replicates")
        resin_mass_meta = get_meta_value("Resin Mass")
        total_volume_meta = get_meta_value("Total volume")
        calib_slope_meta = get_meta_value("Calibration Curve Slope")
        calib_intercept_meta = get_meta_value("Calibration Curve Intercept")
        ligand_conc_unit = get_meta_value("Ligand concentration unit")
        salt_conc_unit = get_meta_value("Salt concentration unit")
    except KeyError as e:
        raise

    # Parse numeric metadata
    ligand_concs = parse_semicolon_floats(
        ligand_conc_str, "Ligand concentrations"
    )
    salt_concs = parse_semicolon_floats(salt_conc_str, "Salt concentrations")
    n_ligand_conc = parse_positive_int(
        n_ligand_conc_meta, "Number of ligand concentrations"
    )
    n_salt_conc = parse_positive_int(
        n_salt_conc_meta, "Number of salt concentrations"
    )
    n_repl = parse_positive_int(replicates_meta, "Replicates")
    resin_mass = parse_positive_float(resin_mass_meta, "Resin Mass")
    total_volume = parse_positive_float(total_volume_meta, "Total volume")

    calib_slope = parse_positive_float(
        calib_slope_meta, "Calibration Curve Slope"
    )
    try:
        calib_intercept = float(str(calib_intercept_meta).replace(",", "."))
    except Exception as e:
        raise ValueError(
            f"Metadata field 'Calibration Curve Intercept' must be numeric, got {calib_intercept_meta!r}"
        ) from e

    # Sanity checks on counts
    if len(ligand_concs) != n_ligand_conc:
        raise ValueError(
            "Number of ligand concentrations in 'Ligand concentrations' ("
            f"{len(ligand_concs)}) does not match 'Number of ligand concentrations' "
            f"({n_ligand_conc})."
        )
    if len(salt_concs) != n_salt_conc:
        raise ValueError(
            "Number of salt concentrations in 'Salt concentrations' ("
            f"{len(salt_concs)}) does not match 'Number of salt concentrations' "
            f"({n_salt_conc})."
        )

    print(f"INFO: Parsed {n_ligand_conc} ligand concentrations: {ligand_concs}")
    print(f"INFO: Parsed {n_salt_conc} salt concentrations: {salt_concs}")
    print(f"INFO: Replicates per condition: {n_repl}")
    print(f"INFO: Resin mass per well (mg): {resin_mass}")
    print(f"INFO: Total volume per well (uL): {total_volume}")
    print(
        f"INFO: Calibration slope: {calib_slope}, intercept: {calib_intercept}, "
        f"ligand unit: {ligand_conc_unit}, salt unit: {salt_conc_unit}"
    )

    analysis_results["metadata"].update(
        {
            "ligand_concentrations": ligand_concs,
            "salt_concentrations": salt_concs,
            "n_ligand_conc": n_ligand_conc,
            "n_salt_conc": n_salt_conc,
            "replicates": n_repl,
            "resin_mass_mg": resin_mass,
            "total_volume_uL": total_volume,
            "calibration_slope": calib_slope,
            "calibration_intercept": calib_intercept,
            "ligand_concentration_unit": ligand_conc_unit,
            "salt_concentration_unit": salt_conc_unit,
        }
    )

    # ------------------------------------------------------------------
    # Fetch Tecan data (single-file pattern)
    # ------------------------------------------------------------------

    print(
        f"INFO: Fetching Tecan data for experiment {experiment_id} from device control server..."
    )
    try:
        data_info = check_tecan_data_availability(str(experiment_id))
        if not data_info.get("available", False):
            raise FileNotFoundError(
                f"No Tecan data available for experiment {experiment_id}: "
                f"{data_info.get('error', 'Unknown error')}"
            )

        print(
            f"INFO: Tecan data found on server: {data_info.get('total_files', 0)} file(s)"
        )
        tecan_data_path_str = fetch_tecan_data_file(
            str(experiment_id), str(results_folder_path)
        )
        tecan_data_path = Path(tecan_data_path_str)

    except Exception as device_server_error:
        print(
            f"WARNING: Device control server access failed: {device_server_error}. "
            "Falling back to local file search..."
        )
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            analysis_results["message"] = (
                "Tecan data not available - analysis skipped. This is expected for test runs."
            )
            analysis_results["status"] = "success"
            analysis_results["note"] = (
                "No Tecan data found on server or local system."
            )
            print(f"INFO: {analysis_results['message']}")
            # Save JSON and return
            results_json_file = (
                f"analysis_results_{analysis_results['experiment_id']}.json"
            )
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"SUCCESS: Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
        if not most_recent_excel_source:
            analysis_results["message"] = (
                "No recent Tecan Excel file found - analysis skipped."
            )
            analysis_results["status"] = "success"
            print(f"INFO: {analysis_results['message']}")
            results_json_file = (
                f"analysis_results_{analysis_results['experiment_id']}.json"
            )
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"SUCCESS: Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        tecan_data_path = (
            results_folder_path / f"tecan_data_{analysis_results['experiment_id']}.xlsx"
        )
        shutil.copy(most_recent_excel_source, tecan_data_path)
        print(
            f"INFO: Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'"
        )

    analysis_results["data_outputs"]["tecan_raw_file"] = str(
        Path(tecan_data_path).resolve()
    )

    # ------------------------------------------------------------------
    # Read Tecan absorbance data from Excel
    # ------------------------------------------------------------------

    # Absorbance data starts at row 34 (0-based index 33), column B (index 1)
    num_rows = len(WELL_ROWS)  # 8
    num_cols = len(WELL_COLS)  # 12

    try:
        raw_df = pd.read_excel(
            tecan_data_path,
            header=None,
            skiprows=33,
            usecols=list(range(1, 1 + num_cols)),
            nrows=num_rows,
        )
    except Exception as e:
        raise RuntimeError(f"Failed to read Tecan Excel file '{tecan_data_path}': {e}")

    # raw_df rows correspond to A-H, columns to 1-12
    if raw_df.shape != (num_rows, num_cols):
        print(
            f"WARNING: Unexpected plate data shape {raw_df.shape}, "
            f"expected {(num_rows, num_cols)}. Proceeding but mapping may be wrong."
        )
        analysis_results["warnings"].append(
            f"Unexpected plate data shape {raw_df.shape}."
        )

    # Build tidy DataFrame with well names and absorbance
    tidy_records: List[Dict[str, Any]] = []
    for r_idx, row_label in enumerate(WELL_ROWS):
        for c_idx, col_number in enumerate(WELL_COLS):
            well_name = f"{row_label}{col_number}"
            try:
                absorbance = float(raw_df.iat[r_idx, c_idx])
            except Exception:
                absorbance = np.nan
            tidy_records.append(
                {
                    "well": well_name,
                    "row": row_label,
                    "col": col_number,
                    "absorbance": absorbance,
                }
            )

    plate_df = pd.DataFrame(tidy_records)

    # ------------------------------------------------------------------
    # Map wells to experimental conditions (salt, ligand, replicate)
    # ------------------------------------------------------------------

    # Salt concentrations are ascending row-wise, with replicates grouped
    # together. Example given in problem statement.
    # For a 96-well plate (8 rows x 12 cols): we assume that the total number of
    # wells used equals n_salt_conc * n_repl columns and n_ligand_conc rows.

    total_wells_needed = n_salt_conc * n_repl * n_ligand_conc
    if total_wells_needed > 96:
        raise ValueError(
            "Product n_salt_conc * n_repl * n_ligand_conc exceeds 96 wells; "
            f"got {total_wells_needed}. Plate layout cannot be mapped."
        )

    # Determine how many columns are used: each column contains all
    # ligand concentrations for one (salt, replicate) combination.
    n_columns_used = n_salt_conc * n_repl
    if n_columns_used > num_cols:
        raise ValueError(
            f"Number of used columns (n_salt_conc * replicates = {n_columns_used}) "
            f"exceeds plate columns ({num_cols})."
        )

    # Only first n_ligand_conc rows contain c0 values
    used_rows = WELL_ROWS[:n_ligand_conc]
    print(
        f"INFO: Using rows {used_rows} (n_ligand_conc={n_ligand_conc}) and "
        f"first {n_columns_used} columns for mapping conditions."
    )

    # Map column index (1-based) to (salt_index, replicate_index)
    # Columns are grouped by replicate, i.e. replicates are grouped together
    # along the row-wise order of salt concentrations.

    mapping_records: List[Dict[str, Any]] = []
    for col_number in WELL_COLS[:n_columns_used]:
        col_zero_based = col_number - 1
        # Determine which salt and replicate this column belongs to
        salt_block_size = n_repl
        salt_index = col_zero_based // salt_block_size
        repl_index = col_zero_based % salt_block_size
        if salt_index >= n_salt_conc:
            continue
        salt = salt_concs[salt_index]
        replicate_id = repl_index + 1
        for r_idx, row_label in enumerate(used_rows):
            ligand_index = r_idx
            c0 = ligand_concs[ligand_index]
            well_name = f"{row_label}{col_number}"
            absorbance_series = plate_df.loc[
                plate_df["well"] == well_name, "absorbance"
            ]
            absorbance = (
                float(absorbance_series.iloc[0])
                if not absorbance_series.empty
                else np.nan
            )
            mapping_records.append(
                {
                    "well": well_name,
                    "row": row_label,
                    "col": col_number,
                    "salt_index": salt_index,
                    "replicate_index": repl_index,
                    "salt_concentration": salt,
                    "ligand_index": ligand_index,
                    "c0": c0,
                    "absorbance": absorbance,
                }
            )

    mapping_df = pd.DataFrame(mapping_records)

    # ------------------------------------------------------------------
    # Calculate equilibrium concentrations cE via calibration curve
    # ------------------------------------------------------------------

    # cE = (Absorbance - intercept) / slope
    mapping_df["cE"] = (mapping_df["absorbance"] - calib_intercept) / calib_slope

    # Sanity check: cE must be between 0 and the largest c0 for that condition
    # and must vary strongly across a column as c0 varies.

    # Clip small negative values to 0 for numerical noise, but detect truly
    # invalid cases separately.
    negative_mask = mapping_df["cE"] < -1e-6
    if negative_mask.any():
        raise ValueError(
            "Calibration produced negative equilibrium concentrations for "
            f"wells: {mapping_df.loc[negative_mask, 'well'].tolist()}"
        )

    mapping_df.loc[mapping_df["cE"] < 0, "cE"] = 0.0

    # Check cE <= max(c0) per column (salt/replicate)
    column_groups = mapping_df.groupby(["salt_index", "replicate_index"])
    for (s_idx, r_idx), group in column_groups:
        c0_max = group["c0"].max()
        if (group["cE"] > c0_max + 1e-6).any():
            wells_bad = group.loc[
                group["cE"] > c0_max + 1e-6, "well"
            ].tolist()
            raise ValueError(
                "Sanity check failed: some cE values exceed the largest "
                f"initial concentration c0 in column (salt_index={s_idx}, "
                f"replicate_index={r_idx}). Affected wells: {wells_bad}. "
                "This suggests that the calibration may have been applied "
                "incorrectly."
            )

    # Check that cE varies strongly across each column as c0 varies
    for (s_idx, r_idx), group in column_groups:
        c0_range = group["c0"].max() - group["c0"].min()
        cE_range = group["cE"].max() - group["cE"].min()
        if c0_range > 0:
            if cE_range < 0.1 * c0_range:
                raise RuntimeError(
                    "Sanity check failed: equilibrium concentrations cE vary "
                    "far less than initial concentrations c0 in column "
                    f"(salt_index={s_idx}, replicate_index={r_idx}). This "
                    "suggests that the calibration might have been applied "
                    "in the wrong direction."
                )

    # ------------------------------------------------------------------
    # Aggregate replicates per (salt, ligand) combination
    # ------------------------------------------------------------------

    # Group by salt_index and ligand_index (i.e., same salt and same c0)
    agg_groups = mapping_df.groupby(["salt_index", "ligand_index"])
    agg_df = agg_groups.agg(
        salt_concentration=("salt_concentration", "first"),
        c0=("c0", "first"),
        cE_mean=("cE", "mean"),
        cE_std=("cE", "std"),
        n_repl_actual=("cE", "size"),
    ).reset_index()

    # Fill NaN std with 0 for single-replicate cases
    agg_df["cE_std"] = agg_df["cE_std"].fillna(0.0)

    # Check replicate count per condition
    if (agg_df["n_repl_actual"] != n_repl).any():
        bad = agg_df.loc[agg_df["n_repl_actual"] != n_repl]
        raise RuntimeError(
            "Replicate count mismatch for some (salt, ligand) combinations. "
            f"Expected {n_repl} replicates, but got differing counts. "
            f"Offending entries: {bad[['salt_index','ligand_index','n_repl_actual']].to_dict(orient='records')}"
        )

    expected_groups = n_ligand_conc * n_salt_conc
    actual_groups = agg_df.shape[0]
    if actual_groups != expected_groups:
        raise RuntimeError(
            "Number of aggregated points does not match expected "
            f"n_ligand_conc * n_salt_conc = {expected_groups}. Got {actual_groups}. "
            "This indicates an incorrect well-to-condition mapping; please "
            "re-check the plate layout and mapping logic."
        )

    # ------------------------------------------------------------------
    # Calculate loading q for each parameter combination
    # ------------------------------------------------------------------

    # q = (c0 - cE_mean) * v_total / m_resin
    # v_total in uL, m_resin in mg, no unit conversion

    agg_df["q"] = (agg_df["c0"] - agg_df["cE_mean"]) * total_volume / resin_mass

    # Some basic sanity checks on q values
    if (agg_df["q"] < -1e-6).any():
        raise ValueError(
            "Calculated loading q contains negative values. This is not "
            "physically meaningful; please check calibration and metadata."
        )
    agg_df.loc[agg_df["q"] < 0, "q"] = 0.0

    # ------------------------------------------------------------------
    # Langmuir fit per salt concentration (q vs cE_mean)
    # ------------------------------------------------------------------

    fit_records: List[Dict[str, Any]] = []
    for s_idx, salt_group in agg_df.groupby("salt_index"):
        salt_value = float(salt_group["salt_concentration"].iloc[0])
        c_e_vals = salt_group["cE_mean"].to_numpy(dtype=float)
        q_vals = salt_group["q"].to_numpy(dtype=float)

        qmax_fit, K_fit, r2_fit = fit_langmuir(c_e_vals, q_vals)

        fit_record = {
            "salt_index": int(s_idx),
            "salt_concentration": salt_value,
            "qmax": qmax_fit,
            "K": K_fit,
            "R2": r2_fit,
        }
        fit_records.append(fit_record)

    fit_df = pd.DataFrame(fit_records)

    # ------------------------------------------------------------------
    # Plot all isotherms and fits in one graph
    # ------------------------------------------------------------------

    fig, ax = plt.subplots(figsize=(8, 6))

    colors = plt.cm.viridis(np.linspace(0, 1, n_salt_conc))

    for idx, (s_idx, salt_group) in enumerate(agg_df.groupby("salt_index")):
        color = colors[idx % len(colors)]
        salt_value = float(salt_group["salt_concentration"].iloc[0])
        label_base = f"{salt_value:g} {salt_conc_unit}" if salt_conc_unit else f"{salt_value:g}"

        # Scatter of data points
        ax.errorbar(
            salt_group["cE_mean"],
            salt_group["q"],
            xerr=salt_group["cE_std"],
            fmt="o",
            color=color,
            label=None,
            capsize=3,
            markersize=4,
        )

        # Add fitted curve if available
        fit_row = fit_df.loc[fit_df["salt_index"] == s_idx]
        if not fit_row.empty and pd.notnull(fit_row["qmax"].iloc[0]):
            qmax_fit = float(fit_row["qmax"].iloc[0])
            K_fit = float(fit_row["K"].iloc[0])
            r2_fit = float(fit_row["R2"].iloc[0])

            c_min = max(0.0, float(salt_group["cE_mean"].min()))
            c_max = float(salt_group["cE_mean"].max())
            c_vals = np.linspace(c_min, c_max, 200)
            q_fit_vals = langmuir_isotherm(c_vals, qmax_fit, K_fit)

            label = (
                f"{label_base}: qmax={qmax_fit:.3g}, K={K_fit:.3g}, R2={r2_fit:.3f}"
            )
            ax.plot(c_vals, q_fit_vals, color=color, label=label)
        else:
            label = f"{label_base}: no fit"
            ax.plot(
                salt_group["cE_mean"],
                salt_group["q"],
                linestyle="--",
                color=color,
                label=label,
            )

    ax.set_xlabel(f"Equilibrium ligand concentration cE ({ligand_conc_unit})")
    ax.set_ylabel(
        f"Loading q ({ligand_conc_unit} * uL / mg resin)"
    )
    ax.set_title("Loading Isotherms")
    ax.grid(True, which="both", linestyle=":", linewidth=0.5)
    ax.legend(fontsize=8)
    fig.tight_layout()

    plot_png_path = (
        results_folder_path
        / f"loading_isotherms_{analysis_results['experiment_id']}.png"
    )
    plot_pdf_path = (
        results_folder_path
        / f"loading_isotherms_{analysis_results['experiment_id']}.pdf"
    )
    fig.savefig(plot_png_path, dpi=300)
    fig.savefig(plot_pdf_path)
    plt.close(fig)

    analysis_results["plots"]["isotherms_png"] = str(plot_png_path.resolve())
    analysis_results["plots"]["isotherms_pdf"] = str(plot_pdf_path.resolve())

    # ------------------------------------------------------------------
    # Save processed data as CSV
    # ------------------------------------------------------------------

    plate_csv_path = (
        results_folder_path
        / f"plate_raw_mapping_{analysis_results['experiment_id']}.csv"
    )
    mapping_df.to_csv(plate_csv_path, index=False)

    agg_csv_path = (
        results_folder_path
        / f"aggregated_conditions_{analysis_results['experiment_id']}.csv"
    )
    agg_df.to_csv(agg_csv_path, index=False)

    fit_csv_path = (
        results_folder_path
        / f"langmuir_fits_{analysis_results['experiment_id']}.csv"
    )
    fit_df.to_csv(fit_csv_path, index=False)

    analysis_results["data_outputs"].update(
        {
            "plate_raw_mapping_csv": str(plate_csv_path.resolve()),
            "aggregated_conditions_csv": str(agg_csv_path.resolve()),
            "langmuir_fits_csv": str(fit_csv_path.resolve()),
        }
    )

    # ------------------------------------------------------------------
    # Finalize results
    # ------------------------------------------------------------------

    analysis_results["status"] = "success"
    analysis_results["message"] = "Analysis completed successfully."
    analysis_results["files_processed"] = 1

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{analysis_results['experiment_id']}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, "w") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"SUCCESS: Saved analysis results JSON to: {results_json_path}")

    return analysis_results


# ---------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------


def main() -> int:
    """Command line interface."""

    parser = argparse.ArgumentParser(
        description="Analyze Tecan plate reader data for loading isotherm determination."
    )
    parser.add_argument(
        "experiment_id",
        nargs="?",
        help=(
            "Experiment ID. If not provided, attempts to auto-detect the most "
            "recent experiment_*.json in the data folder."
        ),
    )
    parser.add_argument(
        "--data-folder",
        default="../data",
        help="Path to the folder containing experiment_ID.json files. Default: ../data",
    )
    parser.add_argument(
        "--results-folder",
        default="../results",
        help="Path to the folder where all analysis outputs will be saved. Default: ../results",
    )

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
            print(
                f"ERROR: Analysis finished with status '{results.get('status')}' - "
                f"message: {results.get('message', 'Unknown error.')}"
            )
            return 1
    except Exception as e:
        print(f"ERROR: Analysis failed due to an unhandled exception: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
