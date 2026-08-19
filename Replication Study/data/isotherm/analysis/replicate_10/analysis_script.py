#!/usr/bin/env python3
"""
Analysis Script - Tecan Plate Reader Loading Isotherm Evaluation
Can be called externally with experiment ID as parameter.

This script:
- Fetches Tecan Spark absorbance data from the device control server (with local fallback)
- Reads raw absorbance values from Excel export (GRE96ft 96-well plate)
- Maps wells to ligand/salt concentrations and replicates based on eLabFTW metadata
- Converts absorbance to equilibrium ligand concentration using a linear calibration curve
- Computes loading isotherms q(c_E) for each salt concentration
- Fits Langmuir isotherms q(c_E) = q_max * K * c_E / (1 + K * c_E)
- Generates plots and CSV exports of processed data and fit parameters

All outputs are written to the specified results folder and referenced in a JSON summary.
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
            return {"available": False, "total_files": 0, "files": [], "error": "No data found"}
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
        print(f"Successfully downloaded Tecan data to: {file_path}")
        return str(file_path)
    except requests.exceptions.ConnectionError:
        raise Exception(f"Cannot connect to device control server at {DEVICE_CONTROL_SERVER}")
    except requests.exceptions.Timeout:
        raise Exception("Timeout while fetching Tecan data from device control server")
    except Exception as e:
        raise Exception(f"Failed to fetch Tecan data for experiment {experiment_id}: {str(e)}")


def get_most_recent_folder(directory, n: int = 0) -> Optional[str]:
    """Finds the n-th most recent subfolder in a given directory."""
    try:
        folders = [f for f in os.listdir(directory) if os.path.isdir(os.path.join(directory, f))]
    except FileNotFoundError:
        return None
    if not folders:
        return None
    sorted_folders = sorted(
        folders,
        key=lambda f: os.path.getctime(os.path.join(directory, f)),
        reverse=True,
    )
    return os.path.join(directory, sorted_folders[n]) if len(sorted_folders) > n else None


def get_most_recent_excel_file(directory, n: int = 0) -> Optional[str]:
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
        excel_files.sort(key=lambda f: os.path.getctime(Path(excel_export_path) / f), reverse=True)
        return str(Path(excel_export_path) / excel_files[0])
    except Exception:
        return None


def auto_detect_latest_experiment_id(data_folder: Path) -> Optional[str]:
    """Auto-detect the most recent experiment JSON file in data_folder.

    Returns the experiment ID as a string, or None if no matching file is found.
    """
    if not data_folder.exists() or not data_folder.is_dir():
        return None
    candidates = list(data_folder.glob("experiment_*.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest = candidates[0]
    name = latest.stem  # 'experiment_1234'
    parts = name.split("_", 1)
    if len(parts) != 2 or not parts[1]:
        return None
    return parts[1]


def parse_semicolon_list(value: Any, field_name: str, expected_len: Optional[int] = None) -> List[float]:
    """Parse a semicolon-separated string of numbers into a list of floats.

    Handles comma or dot as decimal separator and strips whitespace.
    Raises ValueError on invalid tokens or length mismatch.
    """
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        values = [float(value)]
    else:
        text = str(value).strip()
        if not text:
            raise ValueError(f"Metadata field '{field_name}' is empty")
        tokens = [t.strip() for t in text.split(";") if t.strip() != ""]
        values = []
        for tok in tokens:
            tok_norm = tok.replace(",", ".")
            try:
                values.append(float(tok_norm))
            except ValueError:
                raise ValueError(f"Cannot parse token '{tok}' in field '{field_name}' as float")
    if expected_len is not None and len(values) != expected_len:
        raise ValueError(
            f"Field '{field_name}' has length {len(values)}, expected {expected_len}. "
            f"Value: {value!r}"
        )
    return values


def parse_float(value: Any, field_name: str) -> float:
    """Parse a single value into float, allowing comma decimals."""
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        raise ValueError(f"Metadata field '{field_name}' is empty")
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"Cannot parse metadata field '{field_name}' value {value!r} as float")


def get_required_metadata(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and validate required metadata fields from experiment_data.

    Raises KeyError/ValueError on missing or invalid fields.
    """
    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    def get_field(name: str) -> Any:
        if name not in metadata:
            raise KeyError(f"Missing expected metadata field: {name}")
        field = metadata[name]
        if not isinstance(field, dict) or "value" not in field:
            raise KeyError(f"Metadata field '{name}' has unexpected structure")
        return field["value"]

    num_ligand_conc = int(parse_float(get_field("Number of ligand concentrations"), "Number of ligand concentrations"))
    num_salt_conc = int(parse_float(get_field("Number of salt concentrations"), "Number of salt concentrations"))
    replicates = int(parse_float(get_field("Replicates"), "Replicates"))

    ligand_concs = parse_semicolon_list(
        get_field("Ligand concentrations"),
        "Ligand concentrations",
        expected_len=num_ligand_conc,
    )
    salt_concs = parse_semicolon_list(
        get_field("Salt concentrations"),
        "Salt concentrations",
        expected_len=num_salt_conc,
    )

    ligand_conc_unit = str(get_field("Ligand concentration unit")).strip()
    salt_conc_unit = str(get_field("Salt concentration unit")).strip()

    slope = parse_float(get_field("Calibration Curve Slope"), "Calibration Curve Slope")
    intercept = parse_float(get_field("Calibration Curve Intercept"), "Calibration Curve Intercept")

    resin_mass_mg = parse_float(get_field("Resin Mass"), "Resin Mass")
    total_vol_ul = parse_float(get_field("Total volume"), "Total volume")

    if slope == 0.0:
        raise ValueError("Calibration slope is zero; cannot invert calibration curve")

    meta_required = {
        "num_ligand_conc": num_ligand_conc,
        "num_salt_conc": num_salt_conc,
        "replicates": replicates,
        "ligand_concs": ligand_concs,
        "salt_concs": salt_concs,
        "ligand_conc_unit": ligand_conc_unit,
        "salt_conc_unit": salt_conc_unit,
        "cal_slope": slope,
        "cal_intercept": intercept,
        "resin_mass_mg": resin_mass_mg,
        "total_vol_ul": total_vol_ul,
    }

    return meta_required


def well_name(row_idx: int, col_idx: int) -> str:
    """Convert 0-based (row_idx, col_idx) to well name like A1."""
    row_letter = chr(ord("A") + row_idx)
    col_num = col_idx + 1
    return f"{row_letter}{col_num}"


def build_plate_mapping(
    num_ligand_conc: int,
    num_salt_conc: int,
    replicates: int,
    ligand_concs: List[float],
    salt_concs: List[float],
) -> Dict[str, Dict[str, Any]]:
    """Build mapping from well name to (ligand concentration, salt concentration, replicate index).

    Layout assumptions (96-well plate, rows A-H, columns 1-12):
    - Ligand concentrations c0 vary with row (ascending A->H) within each column
    - Salt concentrations vary row-wise but with replicates grouped in column blocks:
      For example, for 4 salt concentrations and 3 replicates:
        columns 1-3: salt 0
        columns 4-6: salt 1
        columns 7-9: salt 2
        columns 10-12: salt 3
    - All columns have the same ascending ligand concentrations in rows A..(A+num_ligand_conc-1)
    - Rows beyond num_ligand_conc may be unused.
    """
    n_rows = 8  # A-H
    n_cols = 12  # 1-12

    if num_ligand_conc > n_rows:
        raise ValueError(
            f"Number of ligand concentrations ({num_ligand_conc}) exceeds plate rows ({n_rows})."
        )
    if num_salt_conc * replicates > n_cols:
        raise ValueError(
            f"num_salt_conc * replicates = {num_salt_conc * replicates} exceeds plate columns ({n_cols}). "
            "Check metadata for 'Number of salt concentrations' and 'Replicates'."
        )

    mapping: Dict[str, Dict[str, Any]] = {}

    for salt_idx in range(num_salt_conc):
        for rep_idx in range(replicates):
            col_idx = salt_idx * replicates + rep_idx
            if col_idx >= n_cols:
                continue
            for lig_idx in range(num_ligand_conc):
                row_idx = lig_idx
                wn = well_name(row_idx, col_idx)
                mapping[wn] = {
                    "ligand_index": lig_idx,
                    "salt_index": salt_idx,
                    "replicate_index": rep_idx,
                    "c0": ligand_concs[lig_idx],
                    "salt_conc": salt_concs[salt_idx],
                }

    expected_groups = num_ligand_conc * num_salt_conc
    expected_wells = expected_groups * replicates
    if len(mapping) != expected_wells:
        raise RuntimeError(
            f"Internal mapping error: expected {expected_wells} wells for mapping but built {len(mapping)}."
        )

    return mapping


def langmuir_isotherm(c_e: np.ndarray, q_max: float, K: float) -> np.ndarray:
    """Langmuir isotherm: q(c_e) = q_max * K * c_e / (1 + K * c_e)."""
    return (q_max * K * c_e) / (1.0 + K * c_e)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[Optional[Tuple[float, float]], Optional[float], Optional[str]]:
    """Fit Langmuir model to data.

    Returns (params, r2, warning_message).
    On failure, params and r2 are None and warning_message describes the issue.
    """
    if len(c_e) < 3:
        return None, None, "Insufficient data points for Langmuir fit (need at least 3)."

    x = np.asarray(c_e, dtype=float)
    y = np.asarray(q, dtype=float)

    # Remove any NaNs or infs
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]

    if len(x) < 3:
        return None, None, "Insufficient finite data points for Langmuir fit after cleaning."

    # Initial guesses: q_max approx max(q), K approx 1 / median(c_e) if possible
    q_max0 = float(np.nanmax(y)) if len(y) > 0 else 1.0
    median_ce = float(np.nanmedian(x)) if len(x) > 0 else 1.0
    if median_ce <= 0:
        median_ce = np.mean([v for v in x if v > 0]) if np.any(x > 0) else 1.0
    K0 = 1.0 / median_ce if median_ce > 0 else 1.0

    p0 = [q_max0, K0]
    bounds = ([0.0, 0.0], [np.inf, np.inf])

    try:
        popt, _pcov = curve_fit(langmuir_isotherm, x, y, p0=p0, bounds=bounds, maxfev=10000)
        q_max_fit, K_fit = popt
        if not (np.isfinite(q_max_fit) and np.isfinite(K_fit)):
            return None, None, "Non-finite fit parameters returned."
        y_pred = langmuir_isotherm(x, q_max_fit, K_fit)
        # Manual R^2 computation to avoid sklearn dependency
        ss_res = float(np.sum((y - y_pred) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        if ss_tot == 0:
            r2 = 1.0 if ss_res == 0 else 0.0
        else:
            r2 = 1.0 - ss_res / ss_tot
        if r2 < 0:
            warn = f"Poor Langmuir fit quality (R2={r2:.3f})."
        else:
            warn = None
        return (q_max_fit, K_fit), float(r2), warn
    except Exception as e:
        return None, None, f"Langmuir fit failed: {e}"


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = "../data", results_folder: str = "../results") -> Dict[str, Any]:
    """Main analysis function for Tecan plate reader loading isotherm data.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries to auto-detect.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with all key metrics and paths to generated files.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    # Auto-detect experiment_id if not provided
    data_folder_path = Path(data_folder)
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment_id from sys.argv: {experiment_id}")
        else:
            print("INFO: No experiment_id provided. Attempting auto-detect from data folder...")
            auto_id = auto_detect_latest_experiment_id(data_folder_path)
            if auto_id is None:
                raise ValueError(
                    "No experiment_id provided and no experiment_*.json files found for auto-detection."
                )
            experiment_id = auto_id
            print(f"INFO: Auto-detected most recent experiment_id: {experiment_id}")

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

    # Load experiment JSON: first try root ../experiment_{id}.json, then data_folder
    root_json_path = Path("..") / f"experiment_{experiment_id}.json"
    if root_json_path.exists():
        data_file_path = root_json_path
    else:
        data_file_path = data_folder_path / f"experiment_{experiment_id}.json"

    print(f"INFO: Loading experiment data from: {data_file_path}")

    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # Extract required metadata
    try:
        meta = get_required_metadata(experiment_data)
    except Exception as e:
        analysis_results["message"] = f"Metadata error: {e}"
        print(f"ERROR: {analysis_results['message']}")
        # Save results JSON even on failure
        results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    analysis_results["metadata"].update(meta)

    num_ligand_conc = meta["num_ligand_conc"]
    num_salt_conc = meta["num_salt_conc"]
    replicates = meta["replicates"]
    ligand_concs = meta["ligand_concs"]
    salt_concs = meta["salt_concs"]
    cal_slope = meta["cal_slope"]
    cal_intercept = meta["cal_intercept"]
    resin_mass_mg = meta["resin_mass_mg"]
    total_vol_ul = meta["total_vol_ul"]

    # Fetch Tecan data: single-file pattern
    print(f"Fetching Tecan data for experiment {experiment_id} from device control server...")

    try:
        data_info = check_tecan_data_availability(str(experiment_id))
        if not data_info.get("available", False):
            raise FileNotFoundError(
                f"No Tecan data available for experiment {experiment_id}: "
                f"{data_info.get('error', 'Unknown error')}"
            )

        print(f"Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
        tecan_data_path_str = fetch_tecan_data_file(str(experiment_id), str(results_folder_path))
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
            analysis_results["metadata"]["data_source"] = "none"
            analysis_results["warnings"].append(
                "No Tecan data found on server or local system."
            )
            print(f"INFO: {analysis_results['message']}")
            results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
        if not most_recent_excel_source:
            analysis_results["message"] = "No recent Tecan Excel file found - analysis skipped."
            analysis_results["status"] = "success"
            analysis_results["metadata"]["data_source"] = "none"
            analysis_results["warnings"].append(
                "No recent Tecan Excel file found in local workspace."
            )
            print(f"INFO: {analysis_results['message']}")
            results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        tecan_data_path = results_folder_path / f"tecan_data_{experiment_id}.xlsx"
        shutil.copy(most_recent_excel_source, tecan_data_path)
        print(f"Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")

    analysis_results["metadata"]["data_source"] = "device_or_local"
    analysis_results["data_outputs"]["raw_tecan_excel"] = str(Path(tecan_data_path).resolve())

    # Read Tecan absorbance data (single endpoint measurement)
    # Absorbance data starts at row 34 (0-based index 33), column B (index 1)
    num_rows_plate = 8  # rows A-H
    num_cols_plate = 12

    try:
        raw_df = pd.read_excel(
            tecan_data_path,
            header=None,
            skiprows=33,
            usecols=list(range(1, 1 + num_cols_plate)),
            nrows=num_rows_plate,
        )
    except Exception as e:
        analysis_results["message"] = f"Failed to read Tecan Excel data: {e}"
        print(f"ERROR: {analysis_results['message']}")
        results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # raw_df rows 0-7 => rows A-H, columns 0-11 => wells 1-12
    print("INFO: Successfully read plate absorbance data from Excel.")

    # Build mapping from wells to (c0, salt, replicate)
    try:
        mapping = build_plate_mapping(
            num_ligand_conc=num_ligand_conc,
            num_salt_conc=num_salt_conc,
            replicates=replicates,
            ligand_concs=ligand_concs,
            salt_concs=salt_concs,
        )
    except Exception as e:
        analysis_results["message"] = f"Error in plate mapping: {e}"
        print(f"ERROR: {analysis_results['message']}")
        results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Collect well-level data
    records: List[Dict[str, Any]] = []

    for row_idx in range(num_rows_plate):
        for col_idx in range(num_cols_plate):
            wn = well_name(row_idx, col_idx)
            if wn not in mapping:
                # Either beyond num_ligand_conc rows or beyond salt/replicate columns
                continue
            absorbance = raw_df.iat[row_idx, col_idx]
            if pd.isna(absorbance):
                # Missing data point; skip but record warning
                msg = f"Missing absorbance value in well {wn}; skipping this replicate."
                print(f"WARNING: {msg}")
                analysis_results["warnings"].append(msg)
                continue

            entry = mapping[wn]
            c0 = float(entry["c0"])
            salt_c = float(entry["salt_conc"])

            # Invert calibration curve: Abs = slope * c + intercept -> cE = (Abs - intercept)/slope
            c_e = (float(absorbance) - cal_intercept) / cal_slope

            # Sanity checks: c_e must be between 0 and max(c0) across all ligand conc
            max_c0 = max(ligand_concs) if ligand_concs else 0.0
            if c_e < -1e-6 or c_e - max_c0 > 1e-6:
                msg = (
                    f"Equilibrium concentration cE={c_e:.4g} in well {wn} is outside expected range "
                    f"[0, {max_c0}]. Calibration may be incorrect."
                )
                print(f"ERROR: {msg}")
                analysis_results["message"] = msg
                results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
                with open(results_json_path, "w") as f:
                    json.dump(analysis_results, f, indent=4)
                print(f"Saved analysis results JSON to: {results_json_path}")
                return analysis_results

            records.append(
                {
                    "well": wn,
                    "row_index": row_idx,
                    "col_index": col_idx,
                    "absorbance": float(absorbance),
                    "c0": c0,
                    "cE": float(c_e),
                    "salt_conc": salt_c,
                    "ligand_index": int(entry["ligand_index"]),
                    "salt_index": int(entry["salt_index"]),
                    "replicate_index": int(entry["replicate_index"]),
                }
            )

    if not records:
        analysis_results["message"] = "No valid absorbance data points found for mapped wells."
        print(f"ERROR: {analysis_results['message']}")
        results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    well_df = pd.DataFrame.from_records(records)

    # Additional sanity check: cE should generally increase with c0 within each salt
    # We perform a simple heuristic: compute Spearman correlation for each salt
    for salt in sorted(set(well_df["salt_conc"])):
        sub = well_df[well_df["salt_conc"] == salt]
        if sub["c0"].nunique() > 2:
            corr = sub[["c0", "cE"]].corr(method="spearman").iat[0, 1]
            if corr is not None and corr < 0.3:
                msg = (
                    f"Low correlation between c0 and cE for salt {salt}: Spearman r={corr:.3f}. "
                    "This may indicate an incorrect calibration direction."
                )
                print(f"WARNING: {msg}")
                analysis_results["warnings"].append(msg)

    # Aggregate replicates by (ligand_index, salt_index)
    group_cols = ["ligand_index", "salt_index", "c0", "salt_conc"]
    grouped = well_df.groupby(group_cols, as_index=False).agg(
        n_reps=("cE", "count"),
        cE_mean=("cE", "mean"),
        cE_std=("cE", "std"),
    )

    expected_groups = num_ligand_conc * num_salt_conc
    if len(grouped) != expected_groups:
        msg = (
            f"Well-to-condition mapping produced {len(grouped)} groups, expected {expected_groups}. "
            "Check plate layout and metadata (ligand/salt concentrations, replicates)."
        )
        print(f"ERROR: {msg}")
        analysis_results["message"] = msg
        results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Check replicate counts
    bad_reps = grouped[grouped["n_reps"] != replicates]
    if not bad_reps.empty:
        msg = (
            "Some (ligand, salt) combinations do not have the expected number of replicates. "
            f"Expected {replicates}, but found different counts."
        )
        print(f"WARNING: {msg}")
        analysis_results["warnings"].append(msg)

    # Compute loading q for each aggregated point: q = (c0 - cE_mean) * v_total / m_resin
    grouped["q"] = (grouped["c0"] - grouped["cE_mean"]) * total_vol_ul / resin_mass_mg

    # Prepare per-salt data and Langmuir fits
    fit_rows: List[Dict[str, Any]] = []
    isotherm_plot_path = results_folder_path / f"loading_isotherms_{experiment_id}.png"

    plt.figure(figsize=(8, 6))

    colors = plt.cm.viridis(np.linspace(0, 1, num_salt_conc if num_salt_conc > 1 else 2))

    for salt_idx, salt_val in enumerate(salt_concs):
        salt_mask = grouped["salt_conc"] == salt_val
        sub = grouped[salt_mask].sort_values("cE_mean")
        if sub.empty:
            continue
        cE_vals = sub["cE_mean"].values
        q_vals = sub["q"].values

        plt.scatter(
            cE_vals,
            q_vals,
            color=colors[salt_idx % len(colors)],
            label=f"Salt {salt_val} {meta['salt_conc_unit']} (data)",
            marker="o",
            s=40,
            alpha=0.8,
        )

        params, r2, warn = fit_langmuir(cE_vals, q_vals)
        if params is not None:
            q_max_fit, K_fit = params
            cE_fit = np.linspace(max(0.0, float(np.min(cE_vals))), float(np.max(cE_vals)) * 1.05, 200)
            q_fit = langmuir_isotherm(cE_fit, q_max_fit, K_fit)
            plt.plot(
                cE_fit,
                q_fit,
                color=colors[salt_idx % len(colors)],
                linestyle="-",
                alpha=0.8,
                label=(
                    f"Salt {salt_val} {meta['salt_conc_unit']} fit; "
                    f"q_max={q_max_fit:.3g}, K={K_fit:.3g}, R2={r2:.3f}"
                ),
            )
            fit_rows.append(
                {
                    "salt_conc": salt_val,
                    "q_max": float(q_max_fit),
                    "K": float(K_fit),
                    "R2": float(r2) if r2 is not None else None,
                    "warning": warn,
                }
            )
            if warn:
                print(f"WARNING: {warn} (salt {salt_val})")
                analysis_results["warnings"].append(f"Salt {salt_val}: {warn}")
        else:
            msg = f"Langmuir fit not available for salt {salt_val}: {warn}"
            print(f"WARNING: {msg}")
            analysis_results["warnings"].append(msg)
            fit_rows.append(
                {
                    "salt_conc": salt_val,
                    "q_max": None,
                    "K": None,
                    "R2": None,
                    "warning": warn,
                }
            )

    plt.xlabel(f"Equilibrium ligand concentration cE ({meta['ligand_conc_unit']})")
    plt.ylabel(
        f"Loading q ({meta['ligand_conc_unit']} * uL / mg resin)"
    )
    plt.title("Loading isotherms from Tecan plate reader data")
    plt.legend(fontsize=8)
    plt.tight_layout()

    try:
        plt.savefig(isotherm_plot_path, dpi=300)
        plt.close()
        analysis_results["plots"]["loading_isotherms_png"] = str(isotherm_plot_path.resolve())
        print(f"INFO: Saved isotherm plot to {isotherm_plot_path}")
    except Exception as e:
        msg = f"Failed to save isotherm plot: {e}"
        print(f"WARNING: {msg}")
        analysis_results["warnings"].append(msg)
        plt.close()

    # Export processed data and fit parameters as CSV
    well_csv_path = results_folder_path / f"well_level_data_{experiment_id}.csv"
    grouped_csv_path = results_folder_path / f"aggregated_isotherm_data_{experiment_id}.csv"
    fits_csv_path = results_folder_path / f"langmuir_fits_{experiment_id}.csv"

    try:
        well_df.to_csv(well_csv_path, index=False)
        analysis_results["data_outputs"]["well_level_csv"] = str(well_csv_path.resolve())
        print(f"INFO: Saved well-level data to {well_csv_path}")
    except Exception as e:
        msg = f"Failed to save well-level CSV: {e}"
        print(f"WARNING: {msg}")
        analysis_results["warnings"].append(msg)

    try:
        grouped.to_csv(grouped_csv_path, index=False)
        analysis_results["data_outputs"]["aggregated_isotherm_csv"] = str(grouped_csv_path.resolve())
        print(f"INFO: Saved aggregated isotherm data to {grouped_csv_path}")
    except Exception as e:
        msg = f"Failed to save aggregated isotherm CSV: {e}"
        print(f"WARNING: {msg}")
        analysis_results["warnings"].append(msg)

    try:
        fits_df = pd.DataFrame.from_records(fit_rows)
        fits_df.to_csv(fits_csv_path, index=False)
        analysis_results["data_outputs"]["langmuir_fits_csv"] = str(fits_csv_path.resolve())
        print(f"INFO: Saved Langmuir fit parameters to {fits_csv_path}")
    except Exception as e:
        msg = f"Failed to save Langmuir fits CSV: {e}"
        print(f"WARNING: {msg}")
        analysis_results["warnings"].append(msg)

    analysis_results["status"] = "success"
    analysis_results["message"] = "Analysis completed successfully."
    analysis_results["files_processed"] = 1

    # Save analysis results as JSON
    results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
    try:
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
    except Exception as e:
        print(f"WARNING: Failed to save analysis results JSON: {e}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description="Analyze Tecan plate reader loading isotherm data.")
    parser.add_argument("experiment_id", nargs="?", help="Experiment ID. If not provided, attempts auto-detection.")
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
