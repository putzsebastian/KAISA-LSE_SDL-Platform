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
from typing import Dict, Any, List, Tuple, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

# Device control server configuration for Tecan
DEVICE_CONTROL_SERVER = os.getenv("DEVICE_CONTROL_SERVER", "http://localhost:8000")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "your-secure-api-key-here")


def check_tecan_data_availability(experiment_id: str) -> dict:
    """Check if Tecan data is available for the given experiment ID."""
    import requests

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
    import requests

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
        raise Exception(f"Cannot connect to device control server at {DEVICE_CONTROL_SERVER}")
    except requests.exceptions.Timeout:
        raise Exception("Timeout while fetching Tecan data from device control server")
    except Exception as e:
        raise Exception(f"Failed to fetch Tecan data for experiment {experiment_id}: {str(e)}")


def get_most_recent_folder(directory: str, n: int = 0) -> Optional[str]:
    """Finds the n-th most recent subfolder in a given directory."""
    folders = [f for f in os.listdir(directory) if os.path.isdir(os.path.join(directory, f))]
    if not folders:
        return None
    sorted_folders = sorted(folders, key=lambda f: os.path.getctime(os.path.join(directory, f)), reverse=True)
    return os.path.join(directory, sorted_folders[n]) if len(sorted_folders) > n else None


def get_most_recent_excel_file(directory: str, n: int = 0) -> Optional[str]:
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


def parse_semicolon_floats(value: str, field_name: str) -> List[float]:
    """Parse a semicolon-separated string into a list of floats with robust handling."""
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(f"Metadata field '{field_name}' must be string, got {type(value)}")
    parts = [p.strip() for p in value.split(";") if p.strip() != ""]
    if not parts:
        raise ValueError(f"Metadata field '{field_name}' is empty")
    floats: List[float] = []
    for p in parts:
        p_norm = p.replace(",", ".")
        try:
            floats.append(float(p_norm))
        except ValueError:
            raise ValueError(f"Cannot convert token '{p}' in metadata field '{field_name}' to float")
    return floats


def safe_float(value: Any, field_name: str) -> float:
    """Convert a scalar metadata value to float with clear errors."""
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        txt = value.strip().replace(",", ".")
        if txt == "":
            raise ValueError(f"Metadata field '{field_name}' is empty string")
        try:
            return float(txt)
        except ValueError:
            raise ValueError(f"Cannot convert metadata field '{field_name}' value '{value}' to float")
    raise ValueError(f"Metadata field '{field_name}' must be numeric or string, got {type(value)}")


def langmuir_isotherm(c: np.ndarray, q_max: float, K: float) -> np.ndarray:
    """Langmuir isotherm: q(c) = q_max * K * c / (1 + K * c)."""
    return q_max * K * c / (1.0 + K * c)


def compute_r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Compute coefficient of determination R^2."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    if ss_tot == 0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[Optional[Tuple[float, float]], Optional[float], str]:
    """Fit Langmuir isotherm with bounds and sanity checks.

    Returns (params, r2, warning_message). params is (q_max, K) or None if fit failed.
    """
    mask = np.isfinite(c_e) & np.isfinite(q)
    c = c_e[mask]
    y = q[mask]
    if c.size < 3:
        return None, None, "Not enough points for Langmuir fit (need >= 3)."

    # Initial guesses based on data
    max_q = float(np.nanmax(y)) if y.size > 0 else 1.0
    positive_c = c[c > 0]
    if positive_c.size == 0:
        return None, None, "All equilibrium concentrations are zero or negative; cannot fit Langmuir model."
    c_mid = float(np.median(positive_c))
    if c_mid <= 0:
        c_mid = float(np.nanmax(positive_c))
    K0 = 1.0 / max(c_mid, 1e-9)
    q0 = max_q if max_q > 0 else 1.0

    bounds = ([0.0, 0.0], [np.inf, np.inf])

    try:
        popt, _pcov = curve_fit(
            langmuir_isotherm,
            c,
            y,
            p0=[q0, K0],
            bounds=bounds,
            maxfev=10000,
        )
    except Exception as e:
        return None, None, f"Langmuir fit failed: {e}"

    q_max_fit, K_fit = popt

    if not (np.isfinite(q_max_fit) and np.isfinite(K_fit)):
        return None, None, "Langmuir fit returned non-finite parameters."

    # Sanity: exclude runaway solutions where parameters are unidentifiable
    if q_max_fit <= 0 or K_fit < 0:
        return None, None, "Langmuir fit returned non-physical parameters (q_max <= 0 or K < 0)."

    y_pred = langmuir_isotherm(c, q_max_fit, K_fit)
    try:
        r2 = compute_r2(y, y_pred)
    except Exception:
        r2 = float("nan")

    return (float(q_max_fit), float(K_fit)), (float(r2) if r2 is not None else None), ""


def map_well_to_indices(
    well_row: int,
    well_col: int,
    n_ligand: int,
    n_salt: int,
    replicates: int,
) -> Optional[Tuple[int, int, int]]:
    """Map a well position (row index 0-7, col index 0-11) to
    (ligand_index, salt_index, replicate_index).

    This follows the layout described in the user requirements.
    Returns None if the well is outside the used rows.
    """
    # Ligand concentration index comes from row (A=0..H=7) up to n_ligand-1
    if well_row >= n_ligand:
        return None

    # Determine salt index and replicate from column
    # Columns for a given salt concentration are contiguous blocks of "replicates" columns
    # Example: n_salt=4, replicates=3 -> groups of 3 columns
    total_needed_cols = n_salt * replicates
    if well_col >= total_needed_cols:
        return None

    salt_index = well_col // replicates  # 0..n_salt-1
    replicate_index = well_col % replicates  # 0..replicates-1

    if salt_index >= n_salt:
        return None

    return well_row, salt_index, replicate_index


def auto_detect_experiment_id(data_folder: str) -> Optional[str]:
    """Find most recent experiment_*.json file in data_folder and return its ID as string."""
    folder = Path(data_folder)
    if not folder.exists():
        return None
    candidates = list(folder.glob("experiment_*.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest = candidates[0]
    name = latest.name
    try:
        stem = name.split("experiment_")[1].split(".json")[0]
        return stem
    except Exception:
        return None


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = "../data", results_folder: str = "../results") -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm data.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with all key metrics and file paths.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        experiment_id = auto_detect_experiment_id(data_folder)
        if experiment_id is None:
            raise ValueError("Could not auto-detect experiment ID from data folder.")
        print(f"INFO: Auto-detected experiment ID: {experiment_id}")

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
    # Prefer JSON in root folder ../experiment_ID.json, then data folder
    data_file_root_first = Path("../") / f"experiment_{experiment_id}.json"
    if data_file_root_first.exists():
        data_file_path = data_file_root_first
    else:
        data_file_path = data_folder_path / f"experiment_{experiment_id}.json"

    print(f"INFO: Loading experiment JSON from: {data_file_path}")
    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # Extract eLabFTW extra fields
    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    required_meta_fields = [
        "Buffer",
        "Salt Name",
        "Process ID",
        "Pump Speed",
        "Replicates",
        "Resin Mass",
        "Ligand Name",
        "Total volume",
        "Venting time",
        "Pump Duration",
        "Incubation Time",
        "Salt concentrations",
        "Equilibration Cycles",
        "Equilibration Volume",
        "Ligand concentrations",
        "Incubation Temperature",
        "Measurement Wavelength",
        "Calibration Curve Slope",
        "Salt concentration unit",
        "Shaker Speed Incubation",
        "Salt Stock Concentration",
        "Ligand concentration unit",
        "Ligand Stock Concentration",
        "Calibration Curve Intercept",
        "Equilibration Cycle Duration",
        "Number of salt concentrations",
        "Number of ligand concentrations",
    ]

    for field in required_meta_fields:
        if field not in metadata:
            raise KeyError(f"Missing expected metadata field: {field}")

    # Parse key numeric metadata
    n_salt = int(safe_float(metadata["Number of salt concentrations"]["value"], "Number of salt concentrations"))
    n_ligand = int(safe_float(metadata["Number of ligand concentrations"]["value"], "Number of ligand concentrations"))
    replicates = int(safe_float(metadata["Replicates"]["value"], "Replicates"))

    ligand_concs = parse_semicolon_floats(metadata["Ligand concentrations"]["value"], "Ligand concentrations")
    salt_concs = parse_semicolon_floats(metadata["Salt concentrations"]["value"], "Salt concentrations")

    if len(ligand_concs) != n_ligand:
        raise ValueError(
            f"Number of ligand concentrations ({len(ligand_concs)}) does not match 'Number of ligand concentrations' ({n_ligand})."
        )
    if len(salt_concs) != n_salt:
        raise ValueError(
            f"Number of salt concentrations ({len(salt_concs)}) does not match 'Number of salt concentrations' ({n_salt})."
        )

    ligand_unit = str(metadata["Ligand concentration unit"]["value"]).strip()
    salt_unit = str(metadata["Salt concentration unit"]["value"]).strip()

    slope = safe_float(metadata["Calibration Curve Slope"]["value"], "Calibration Curve Slope")
    intercept = safe_float(metadata["Calibration Curve Intercept"]["value"], "Calibration Curve Intercept")

    if slope == 0:
        raise ValueError("Calibration Curve Slope is zero; cannot invert calibration.")

    resin_mass_mg = safe_float(metadata["Resin Mass"]["value"], "Resin Mass")
    total_volume_ul = safe_float(metadata["Total volume"]["value"], "Total volume")

    analysis_results["metadata"].update(
        {
            "ligand_concentrations": ligand_concs,
            "ligand_unit": ligand_unit,
            "salt_concentrations": salt_concs,
            "salt_unit": salt_unit,
            "n_ligand": n_ligand,
            "n_salt": n_salt,
            "replicates": replicates,
            "calibration_slope": slope,
            "calibration_intercept": intercept,
            "resin_mass_mg": resin_mass_mg,
            "total_volume_ul": total_volume_ul,
        }
    )

    # Fetch Tecan data - single-file pattern
    print(f"INFO: Fetching Tecan data for experiment {experiment_id} from device control server...")
    tecan_data_path: Path
    try:
        data_info = check_tecan_data_availability(str(experiment_id))
        if not data_info.get("available", False):
            raise FileNotFoundError(
                f"No Tecan data available for experiment {experiment_id}: {data_info.get('error', 'Unknown error')}"
            )
        print(f"INFO: Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
        tecan_data_path_str = fetch_tecan_data_file(str(experiment_id), str(results_folder_path))
        tecan_data_path = Path(tecan_data_path_str)
    except Exception as device_server_error:
        print(
            f"WARNING: Device control server access failed: {device_server_error}. Falling back to local file search..."
        )
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            analysis_results["message"] = "Tecan data not available - analysis skipped. This is expected for test runs."
            analysis_results["status"] = "success"
            analysis_results["note"] = "No Tecan data found on server or local system."
            print(f"INFO: {analysis_results['message']}")
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"INFO: Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
        if not most_recent_excel_source:
            analysis_results["message"] = "No recent Tecan Excel file found - analysis skipped."
            analysis_results["status"] = "success"
            print(f"INFO: {analysis_results['message']}")
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"INFO: Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        tecan_data_path = results_folder_path / f"tecan_data_{experiment_id}.xlsx"
        shutil.copy(most_recent_excel_source, tecan_data_path)
        print(f"INFO: Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")

    analysis_results["data_outputs"]["tecan_raw_excel"] = str(tecan_data_path.resolve())

    # Read absorbance data from Excel (starts at row 34, column B)
    print("INFO: Reading absorbance data from Tecan Excel file...")
    num_columns = 12  # 96-well plate, 12 columns
    num_rows = 8  # 8 rows (A-H)
    try:
        raw_df = pd.read_excel(
            tecan_data_path,
            header=None,
            skiprows=33,
            usecols=list(range(1, 1 + num_columns)),
            nrows=num_rows,
        )
    except Exception as e:
        raise RuntimeError(f"Failed to read Tecan Excel file '{tecan_data_path}': {e}")

    # raw_df is 8x12, rows 0-7 (A-H), cols 0-11 (1-12)
    # Create long-format DataFrame with well positions and absorbance
    rows_labels = ["A", "B", "C", "D", "E", "F", "G", "H"]
    data_records: List[Dict[str, Any]] = []
    for r in range(num_rows):
        for c in range(num_columns):
            well_row_label = rows_labels[r]
            well_col_number = c + 1
            well_name = f"{well_row_label}{well_col_number}"
            absorbance = raw_df.iat[r, c]
            if pd.isna(absorbance):
                # Missing data point; still record for traceability
                data_records.append(
                    {
                        "well": well_name,
                        "row_index": r,
                        "col_index": c,
                        "absorbance": np.nan,
                    }
                )
                continue
            data_records.append(
                {
                    "well": well_name,
                    "row_index": r,
                    "col_index": c,
                    "absorbance": float(absorbance),
                }
            )

    plate_df = pd.DataFrame(data_records)

    # Map wells to experimental conditions
    mapped_records: List[Dict[str, Any]] = []
    for _, row in plate_df.iterrows():
        mapping = map_well_to_indices(
            well_row=int(row["row_index"]),
            well_col=int(row["col_index"]),
            n_ligand=n_ligand,
            n_salt=n_salt,
            replicates=replicates,
        )
        if mapping is None:
            # Well not used for analysis
            continue
        lig_idx, salt_idx, rep_idx = mapping
        mapped_records.append(
            {
                "well": row["well"],
                "row_index": int(row["row_index"]),
                "col_index": int(row["col_index"]),
                "absorbance": row["absorbance"],
                "ligand_index": lig_idx,
                "salt_index": salt_idx,
                "replicate_index": rep_idx,
                "c0": ligand_concs[lig_idx],
                "salt_conc": salt_concs[salt_idx],
            }
        )

    mapped_df = pd.DataFrame(mapped_records)

    # Verify we have the expected number of wells: n_ligand * n_salt * replicates
    expected_wells = n_ligand * n_salt * replicates
    actual_wells = mapped_df.shape[0]
    if actual_wells != expected_wells:
        raise RuntimeError(
            f"Well-to-condition mapping mismatch: expected {expected_wells} wells, got {actual_wells}. "
            "Check plate layout and metadata (n_ligand, n_salt, replicates)."
        )

    # Calculate equilibrium concentration cE from absorbance
    print("INFO: Calculating equilibrium concentrations from absorbance using inverted calibration curve...")
    mapped_df["cE"] = (mapped_df["absorbance"] - intercept) / slope

    # Sanity checks: cE between 0 and max c0 in that ligand series
    max_c0 = max(ligand_concs) if ligand_concs else 0.0
    if max_c0 <= 0:
        raise RuntimeError("Maximum initial ligand concentration c0 is not positive; cannot validate cE.")

    invalid_cE_mask = (mapped_df["cE"] < 0) | (mapped_df["cE"] > max_c0 * 1.01)
    num_invalid_cE = int(invalid_cE_mask.sum())
    if num_invalid_cE > 0:
        raise RuntimeError(
            f"Calibration sanity check failed: {num_invalid_cE} cE values are outside [0, max(c0)]. "
            "Check that calibration slope and intercept are correct and not applied backwards."
        )

    # Additional sanity: ensure that within a column (fixed salt and replicate), cE varies with c0
    variation_issues = 0
    for salt_idx in range(n_salt):
        for rep_idx in range(replicates):
            subset = mapped_df[(mapped_df["salt_index"] == salt_idx) & (mapped_df["replicate_index"] == rep_idx)]
            if subset.empty:
                continue
            c0_values = subset["c0"].values
            cE_values = subset["cE"].values
            if np.nanmax(c0_values) - np.nanmin(c0_values) <= 0:
                continue
            if np.nanmax(cE_values) - np.nanmin(cE_values) < 0.05 * (np.nanmax(c0_values) - np.nanmin(c0_values)):
                variation_issues += 1

    if variation_issues > 0:
        raise RuntimeError(
            "Calibration sanity check failed: cE does not vary strongly across c0 within one or more columns. "
            "This suggests the calibration may have been applied in the wrong direction."
        )

    # Aggregate replicates: mean and std of cE for each (ligand, salt)
    print("INFO: Aggregating replicates for each ligand/salt condition...")
    grouped = (
        mapped_df.groupby(["ligand_index", "salt_index"], as_index=False)
        .agg(
            cE_mean=("cE", "mean"),
            cE_std=("cE", "std"),
            absorbance_mean=("absorbance", "mean"),
            absorbance_std=("absorbance", "std"),
            n_reps=("cE", "count"),
        )
    )

    # Expect exactly n_ligand * n_salt aggregated points, each with replicates
    if grouped.shape[0] != n_ligand * n_salt:
        raise RuntimeError(
            f"Condition aggregation mismatch: expected {n_ligand * n_salt} groups, got {grouped.shape[0]}. "
            "Check well-to-condition mapping and metadata."
        )

    wrong_rep_counts = grouped[grouped["n_reps"] != replicates]
    if not wrong_rep_counts.empty:
        raise RuntimeError(
            "Some conditions do not have the expected number of replicates. "
            f"Expected {replicates} replicates per condition."
        )

    grouped["c0"] = grouped["ligand_index"].map({i: v for i, v in enumerate(ligand_concs)})
    grouped["salt_conc"] = grouped["salt_index"].map({i: v for i, v in enumerate(salt_concs)})

    # Calculate loading q = (c0 - cE_mean) * v_total / m_resin
    print("INFO: Calculating loading q for each condition...")
    grouped["q"] = (grouped["c0"] - grouped["cE_mean"]) * total_volume_ul / resin_mass_mg

    # Sanity: q should not be negative (allow small numerical tolerance)
    if (grouped["q"] < -1e-6).any():
        raise RuntimeError(
            "Computed negative loading q for some conditions (beyond numerical tolerance). "
            "Check calibration and input metadata (c0, cE, volume, resin mass)."
        )

    # Langmuir fit for each salt concentration: q vs cE_mean
    print("INFO: Performing Langmuir fits for each salt concentration...")
    fit_results: List[Dict[str, Any]] = []

    for salt_idx, salt_value in enumerate(salt_concs):
        subset = grouped[grouped["salt_index"] == salt_idx].copy()
        subset = subset.sort_values("cE_mean")
        c_e_vals = subset["cE_mean"].to_numpy(dtype=float)
        q_vals = subset["q"].to_numpy(dtype=float)

        params, r2, warn_msg = fit_langmuir(c_e_vals, q_vals)

        fit_entry: Dict[str, Any] = {
            "salt_index": salt_idx,
            "salt_conc": salt_value,
            "salt_unit": salt_unit,
            "fit_success": params is not None,
            "q_max": None,
            "K": None,
            "R2": r2,
            "warning": warn_msg,
        }

        if params is not None:
            q_max_fit, K_fit = params

            # Additional sanity: ensure fitted q_max is within a reasonable range
            max_q_obs = float(np.nanmax(q_vals)) if q_vals.size > 0 else 0.0
            if max_q_obs > 0:
                if q_max_fit < 0.5 * max_q_obs or q_max_fit > 5.0 * max_q_obs:
                    warn_text = (
                        "Fitted q_max is outside [0.5, 5]x the maximum observed q; "
                        "fit may be poorly constrained."
                    )
                    analysis_results["warnings"].append(warn_text)
                    fit_entry["warning"] = (fit_entry["warning"] + " " + warn_text).strip()

            fit_entry["q_max"] = q_max_fit
            fit_entry["K"] = K_fit
            fit_entry["R2"] = r2

        fit_results.append(fit_entry)

    fit_results_df = pd.DataFrame(fit_results)

    # Save processed data and fit results as CSV
    plate_csv_path = results_folder_path / f"plate_raw_with_mapping_{experiment_id}.csv"
    mapped_df.to_csv(plate_csv_path, index=False)
    analysis_results["data_outputs"]["plate_raw_with_mapping_csv"] = str(plate_csv_path.resolve())

    grouped_csv_path = results_folder_path / f"isotherm_grouped_{experiment_id}.csv"
    grouped.to_csv(grouped_csv_path, index=False)
    analysis_results["data_outputs"]["grouped_isotherm_csv"] = str(grouped_csv_path.resolve())

    fit_results_csv_path = results_folder_path / f"langmuir_fit_results_{experiment_id}.csv"
    fit_results_df.to_csv(fit_results_csv_path, index=False)
    analysis_results["data_outputs"]["langmuir_fit_results_csv"] = str(fit_results_csv_path.resolve())

    # Plot isotherms and fits
    print("INFO: Generating isotherm plot...")
    fig, ax = plt.subplots(figsize=(8, 6))

    cmap = plt.get_cmap("tab10")

    for i, salt_value in enumerate(salt_concs):
        color = cmap(i % 10)
        subset = grouped[grouped["salt_index"] == i].copy()
        subset = subset.sort_values("cE_mean")

        # y-error: standard deviation of q across replicates cannot be recovered from grouped
        # directly here (we only have mean), so use cE_std propagated into q approx or omit.
        # For simplicity, we plot without error bars on q.
        ax.plot(
            subset["cE_mean"],
            subset["q"],
            "o",
            color=color,
            label=None,
            alpha=0.8,
        )

        # Plot fitted Langmuir curve if fit succeeded
        fit_row = fit_results_df[fit_results_df["salt_index"] == i]
        if not fit_row.empty and bool(fit_row.iloc[0]["fit_success"]):
            q_max_fit = float(fit_row.iloc[0]["q_max"])
            K_fit = float(fit_row.iloc[0]["K"])
            r2_val = fit_row.iloc[0]["R2"]
            c_min = max(0.0, float(subset["cE_mean"].min()))
            c_max = float(subset["cE_mean"].max())
            c_grid = np.linspace(c_min, c_max, 200)
            q_grid = langmuir_isotherm(c_grid, q_max_fit, K_fit)
            if r2_val is None or (isinstance(r2_val, float) and not np.isfinite(r2_val)):
                r2_text = "nan"
            else:
                r2_text = f"{float(r2_val):.3f}"
            label = f"Salt {salt_value:g} {salt_unit}, qmax={q_max_fit:.3g}, K={K_fit:.3g}, R2={r2_text}"
            ax.plot(c_grid, q_grid, "-", color=color, label=label)
        else:
            label = f"Salt {salt_value:g} {salt_unit} (fit failed)"
            ax.plot([], [], " ", label=label, color=color)

    ax.set_xlabel(f"Equilibrium ligand concentration cE [{ligand_unit}]")
    ax.set_ylabel(f"Loading q [{ligand_unit} * uL/mg]")
    ax.set_title("Loading isotherms with Langmuir fits")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, linestyle=":", linewidth=0.5)
    fig.tight_layout()

    plot_png_path = results_folder_path / f"loading_isotherms_{experiment_id}.png"
    fig.savefig(plot_png_path, dpi=300)
    plt.close(fig)

    analysis_results["plots"]["loading_isotherms_png"] = str(plot_png_path.resolve())

    # Also save as PDF
    fig, ax = plt.subplots(figsize=(8, 6))
    for i, salt_value in enumerate(salt_concs):
        color = cmap(i % 10)
        subset = grouped[grouped["salt_index"] == i].copy()
        subset = subset.sort_values("cE_mean")
        ax.plot(
            subset["cE_mean"],
            subset["q"],
            "o",
            color=color,
            label=None,
            alpha=0.8,
        )
        fit_row = fit_results_df[fit_results_df["salt_index"] == i]
        if not fit_row.empty and bool(fit_row.iloc[0]["fit_success"]):
            q_max_fit = float(fit_row.iloc[0]["q_max"])
            K_fit = float(fit_row.iloc[0]["K"])
            r2_val = fit_row.iloc[0]["R2"]
            c_min = max(0.0, float(subset["cE_mean"].min()))
            c_max = float(subset["cE_mean"].max())
            c_grid = np.linspace(c_min, c_max, 200)
            q_grid = langmuir_isotherm(c_grid, q_max_fit, K_fit)
            if r2_val is None or (isinstance(r2_val, float) and not np.isfinite(r2_val)):
                r2_text = "nan"
            else:
                r2_text = f"{float(r2_val):.3f}"
            label = f"Salt {salt_value:g} {salt_unit}, qmax={q_max_fit:.3g}, K={K_fit:.3g}, R2={r2_text}"
            ax.plot(c_grid, q_grid, "-", color=color, label=label)
        else:
            label = f"Salt {salt_value:g} {salt_unit} (fit failed)"
            ax.plot([], [], " ", label=label, color=color)

    ax.set_xlabel(f"Equilibrium ligand concentration cE [{ligand_unit}]")
    ax.set_ylabel(f"Loading q [{ligand_unit} * uL/mg]")
    ax.set_title("Loading isotherms with Langmuir fits")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, linestyle=":", linewidth=0.5)
    fig.tight_layout()

    plot_pdf_path = results_folder_path / f"loading_isotherms_{experiment_id}.pdf"
    fig.savefig(plot_pdf_path)
    plt.close(fig)

    analysis_results["plots"]["loading_isotherms_pdf"] = str(plot_pdf_path.resolve())

    analysis_results["status"] = "success"
    analysis_results["message"] = "Analysis completed successfully."
    analysis_results["files_processed"] = 1

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, "w") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description="Analyze Tecan loading isotherm experiment data.")
    parser.add_argument("experiment_id", nargs="?", help="Experiment ID. If not provided, attempts to auto-detect the most recent.")
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
            print(f"ERROR: Analysis failed: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
