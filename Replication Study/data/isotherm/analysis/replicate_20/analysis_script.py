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
        return {
            "available": False,
            "total_files": 0,
            "files": [],
            "error": str(e),
        }


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
        raise Exception(
            f"Cannot connect to device control server at {DEVICE_CONTROL_SERVER}"
        )
    except requests.exceptions.Timeout:
        raise Exception("Timeout while fetching Tecan data from device control server")
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
        os.path.join(directory, sorted_folders[n]) if len(sorted_folders) > n else None
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
        excel_files = [
            f for f in files_in_folder if f.lower().endswith(".xlsx")
        ]
        if not excel_files:
            return None
        excel_files.sort(
            key=lambda f: os.path.getctime(Path(excel_export_path) / f),
            reverse=True,
        )
        return str(Path(excel_export_path) / excel_files[0])
    except Exception:
        return None


def langmuir_isotherm(c_e: np.ndarray, q_max: float, K: float) -> np.ndarray:
    """Langmuir isotherm model: q = q_max * K * c_e / (1 + K * c_e)."""
    return q_max * K * c_e / (1.0 + K * c_e)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[float, float, float]:
    """Fit Langmuir isotherm and return q_max, K, and R2.

    Uses constrained curve_fit with sensible bounds and data-derived initial guesses.
    Raises RuntimeError if fit fails or parameters are outside identifiable range.
    """

    # Filter out non-finite points
    mask = np.isfinite(c_e) & np.isfinite(q)
    c_e_fit = c_e[mask]
    q_fit = q[mask]

    if c_e_fit.size < 3:
        raise RuntimeError("Not enough data points for Langmuir fit (need at least 3)")

    # Initial guesses
    q_max_guess = float(np.nanmax(q_fit)) if np.nanmax(q_fit) > 0 else 1.0
    # Use mid-range of c_e for initial K guess (~1/c_mid)
    c_min = float(np.nanmin(c_e_fit))
    c_max = float(np.nanmax(c_e_fit))
    if c_max <= 0:
        raise RuntimeError("Equilibrium concentrations for fit are not positive")
    c_mid = 0.5 * (c_min + c_max) if c_min > 0 else 0.5 * c_max
    K_guess = 1.0 / c_mid if c_mid > 0 else 1.0

    p0 = [q_max_guess, K_guess]
    # Bounds: strictly positive but not absurdly large
    lower_bounds = [0.0, 0.0]
    upper_bounds = [q_max_guess * 100.0 if q_max_guess > 0 else 1e6, 1e6]

    try:
        popt, pcov = curve_fit(
            langmuir_isotherm,
            c_e_fit,
            q_fit,
            p0=p0,
            bounds=(lower_bounds, upper_bounds),
            maxfev=10000,
        )
    except Exception as e:
        raise RuntimeError(f"Langmuir fit failed: {e}")

    q_max_fit, K_fit = popt

    # Basic sanity checks
    if not np.isfinite(q_max_fit) or not np.isfinite(K_fit):
        raise RuntimeError("Fitted Langmuir parameters are not finite")

    # Compute R2 manually (1 - SS_res / SS_tot)
    q_pred = langmuir_isotherm(c_e_fit, q_max_fit, K_fit)
    ss_res = float(np.nansum((q_fit - q_pred) ** 2))
    ss_tot = float(np.nansum((q_fit - np.nanmean(q_fit)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    return float(q_max_fit), float(K_fit), float(r2)


def parse_semicolon_list(value: str, field_name: str) -> List[float]:
    """Parse semicolon-separated string into list of floats, handling commas as decimal separator."""
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(
            f"Metadata field '{field_name}' must be a string or number, got {type(value)}"
        )
    parts = [p.strip() for p in value.split(";") if p.strip() != ""]
    if not parts:
        raise ValueError(
            f"Metadata field '{field_name}' is an empty or invalid semicolon-separated string"
        )
    numbers: List[float] = []
    for p in parts:
        # Replace comma decimal separator if present
        p_norm = p.replace(",", ".")
        try:
            numbers.append(float(p_norm))
        except ValueError as e:
            raise ValueError(
                f"Cannot parse '{p}' in metadata field '{field_name}' as float: {e}"
            )
    return numbers


def auto_detect_experiment_id(data_folder: str) -> str:
    """Auto-detect the most recent experiment_<id>.json file and return its id as string."""
    folder = Path(data_folder)
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Data folder does not exist: {data_folder}")
    candidates = list(folder.glob("experiment_*.json"))
    if not candidates:
        raise FileNotFoundError(
            f"No experiment_*.json files found in data folder: {data_folder}"
        )
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    newest = candidates[0]
    name = newest.stem  # experiment_<id>
    parts = name.split("_", 1)
    if len(parts) != 2 or not parts[1]:
        raise ValueError(f"Cannot extract experiment ID from filename: {newest.name}")
    print(f"INFO: Auto-detected experiment ID {parts[1]} from {newest.name}")
    return parts[1]


def map_well_to_indices(
    row_idx: int,
    col_idx: int,
    n_ligand: int,
    n_salt: int,
    replicates: int,
) -> Tuple[int, int, int]:
    """Map well indices (0-based row, 0-based col) to ligand index, salt index, and replicate index.

    Layout rules (row-wise):
      - In each column, ligand concentrations c0 increase with row index (A..H).
        Ligand index is simply row_idx (0..n_ligand-1) for rows that contain data.
      - Salt concentrations are grouped by replicate in columns and increase with column index.
        For each salt concentration there are `replicates` consecutive columns.

    salt_group_index = col_idx // replicates
    replicate_index   = col_idx % replicates

    Returns (ligand_index, salt_index, replicate_index), each 0-based.
    """
    ligand_index = row_idx
    salt_index = col_idx // replicates
    replicate_index = col_idx % replicates
    if ligand_index < 0 or ligand_index >= n_ligand:
        raise IndexError(
            f"Row index {row_idx} out of range for {n_ligand} ligand concentrations"
        )
    if salt_index < 0 or salt_index >= n_salt:
        raise IndexError(
            f"Column index {col_idx} maps to salt index {salt_index}, out of range for {n_salt} salt concentrations with {replicates} replicates"
        )
    if replicate_index < 0 or replicate_index >= replicates:
        raise IndexError(
            f"Column index {col_idx} maps to replicate index {replicate_index}, out of range for {replicates} replicates"
        )
    return ligand_index, salt_index, replicate_index


def analyze_experiment(
    experiment_id: str = None,
    data_folder: str = "../data",
    results_folder: str = "../results",
) -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm.

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

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ("", "-"):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from sys.argv: {experiment_id}")
        else:
            experiment_id = auto_detect_experiment_id(data_folder)
            print(f"INFO: Auto-detected experiment ID: {experiment_id}")
    analysis_results["experiment_id"] = experiment_id

    # Locate experiment JSON: prefer root ../experiment_{id}.json, fallback to data folder
    exp_json_root = Path("..") / f"experiment_{experiment_id}.json"
    data_folder_path = Path(data_folder)
    data_file_path = (
        exp_json_root
        if exp_json_root.exists()
        else data_folder_path / f"experiment_{experiment_id}.json"
    )

    print(f"INFO: Loading experiment data from: {data_file_path}")

    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        msg = f"Data file not found: {data_file_path}"
        print(f"ERROR: {msg}")
        analysis_results["message"] = msg
        return analysis_results
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON format in {data_file_path}: {e}"
        print(f"ERROR: {msg}")
        analysis_results["message"] = msg
        return analysis_results

    # Extract metadata / eLabFTW extra fields
    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        msg = f"Missing expected metadata structure in experiment JSON: {e}"
        print(f"ERROR: {msg}")
        analysis_results["message"] = msg
        return analysis_results

    # Helper to safely get a metadata value
    def get_meta_value(key: str, required: bool = True):
        try:
            field = metadata[key]
            value = field.get("value") if isinstance(field, dict) else None
        except KeyError:
            if required:
                raise KeyError(f"Missing expected metadata field: {key}")
            else:
                analysis_results["warnings"].append(
                    f"Optional metadata field '{key}' is missing"
                )
                return None
        return value

    try:
        n_ligand_str = get_meta_value("Number of ligand concentrations")
        n_salt_str = get_meta_value("Number of salt concentrations")
        replicates_str = get_meta_value("Replicates")
        ligand_conc_str = get_meta_value("Ligand concentrations")
        salt_conc_str = get_meta_value("Salt concentrations")
        ligand_unit = get_meta_value("Ligand concentration unit")
        salt_unit = get_meta_value("Salt concentration unit")
        slope_str = get_meta_value("Calibration Curve Slope")
        intercept_str = get_meta_value("Calibration Curve Intercept")
        resin_mass_str = get_meta_value("Resin Mass")
        total_volume_str = get_meta_value("Total volume")

        # Parse integers
        n_ligand = int(str(n_ligand_str))
        n_salt = int(str(n_salt_str))
        replicates = int(str(replicates_str))

        if n_ligand <= 0 or n_ligand > 8:
            raise ValueError(
                f"Number of ligand concentrations must be between 1 and 8, got {n_ligand}"
            )
        if n_salt <= 0:
            raise ValueError(
                f"Number of salt concentrations must be positive, got {n_salt}"
            )
        if replicates <= 0:
            raise ValueError(
                f"Number of replicates must be positive, got {replicates}"
            )

        # Parse concentration lists
        ligand_concs = parse_semicolon_list(
            ligand_conc_str, "Ligand concentrations"
        )
        salt_concs = parse_semicolon_list(salt_conc_str, "Salt concentrations")

        if len(ligand_concs) != n_ligand:
            raise ValueError(
                f"Number of ligand concentrations ({n_ligand}) does not match length of 'Ligand concentrations' list ({len(ligand_concs)})"
            )
        if len(salt_concs) != n_salt:
            raise ValueError(
                f"Number of salt concentrations ({n_salt}) does not match length of 'Salt concentrations' list ({len(salt_concs)})"
            )

        # Calibration parameters
        slope = float(str(slope_str).replace(",", "."))
        intercept = float(str(intercept_str).replace(",", "."))
        if slope == 0:
            raise ValueError("Calibration Curve Slope must not be zero")

        # Resin mass and total volume
        resin_mass = float(str(resin_mass_str).replace(",", "."))
        total_volume = float(str(total_volume_str).replace(",", "."))
        if resin_mass <= 0 or total_volume <= 0:
            raise ValueError(
                f"Resin Mass and Total volume must be positive, got m_resin={resin_mass}, v_total={total_volume}"
            )

    except Exception as e:
        msg = f"Error parsing metadata: {e}"
        print(f"ERROR: {msg}")
        analysis_results["message"] = msg
        return analysis_results

    # Store key metadata in results
    analysis_results["metadata"].update(
        {
            "n_ligand": n_ligand,
            "n_salt": n_salt,
            "replicates": replicates,
            "ligand_concentrations": ligand_concs,
            "salt_concentrations": salt_concs,
            "ligand_concentration_unit": ligand_unit,
            "salt_concentration_unit": salt_unit,
            "calibration_slope": slope,
            "calibration_intercept": intercept,
            "resin_mass_mg": resin_mass,
            "total_volume_uL": total_volume,
        }
    )

    print("INFO: Fetching Tecan data for experiment {0} from device control server...".format(experiment_id))

    # Fetch Tecan data (single file pattern)
    try:
        data_info = check_tecan_data_availability(experiment_id)
        if not data_info.get("available", False):
            raise FileNotFoundError(
                f"No Tecan data available for experiment {experiment_id}: {data_info.get('error', 'Unknown error')}"
            )

        print(
            "INFO: Tecan data found on server: {0} file(s)".format(
                data_info.get("total_files", 0)
            )
        )
        tecan_data_path_str = fetch_tecan_data_file(experiment_id, results_folder)
    except Exception as device_server_error:
        print(
            "WARNING: Device control server access failed: {0}. Falling back to local file search...".format(
                device_server_error
            )
        )
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            msg = (
                "Tecan data not available - analysis skipped. This is expected for test runs."
            )
            analysis_results["message"] = msg
            analysis_results["status"] = "success"
            analysis_results["note"] = (
                "No Tecan data found on server or local system."
            )
            print("INFO: {0}".format(msg))
            # Save analysis results JSON before returning
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
        if not most_recent_excel_source:
            msg = "No recent Tecan Excel file found - analysis skipped."
            analysis_results["message"] = msg
            analysis_results["status"] = "success"
            print("INFO: {0}".format(msg))
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        tecan_data_path = results_folder_path / f"tecan_data_{experiment_id}.xlsx"
        shutil.copy(most_recent_excel_source, tecan_data_path)
        tecan_data_path_str = str(tecan_data_path)
        print(
            "INFO: Raw data copied from '{0}' to '{1}'".format(
                most_recent_excel_source, tecan_data_path
            )
        )

    tecan_data_path = Path(tecan_data_path_str)
    analysis_results["data_outputs"]["tecan_raw_excel"] = str(
        tecan_data_path.resolve()
    )

    # Read absorbance data from Excel (single endpoint measurement)
    # Absorbance data starts at row 34 (0-based index 33), column B (1)
    num_columns = 12  # 96-well plate, 12 columns
    num_rows = 8  # rows A-H

    try:
        raw_df = pd.read_excel(
            tecan_data_path,
            header=None,
            skiprows=33,
            usecols=list(range(1, 1 + num_columns)),
            nrows=num_rows,
        )
    except Exception as e:
        msg = f"Failed to read Tecan Excel data: {e}"
        print(f"ERROR: {msg}")
        analysis_results["message"] = msg
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # raw_df: rows 0-7 correspond to A-H, columns 0-11 correspond to 1-12
    absorbance_array = raw_df.to_numpy(dtype=float)

    # Construct structured DataFrame with mapping
    wells: List[Dict[str, Any]] = []
    well_rows = ["A", "B", "C", "D", "E", "F", "G", "H"]

    for r in range(num_rows):
        for c in range(num_columns):
            well_name = f"{well_rows[r]}{c + 1}"
            absorbance = absorbance_array[r, c]
            if np.isnan(absorbance):
                continue
            try:
                ligand_index, salt_index, rep_index = map_well_to_indices(
                    r, c, n_ligand, n_salt, replicates
                )
            except IndexError as e:
                # Wells outside defined regions are simply ignored
                analysis_results["warnings"].append(
                    f"Skipping well {well_name}: {e}"
                )
                continue

            c0 = ligand_concs[ligand_index]
            c_salt = salt_concs[salt_index]

            # Compute equilibrium concentration cE using inverted calibration curve
            c_e = (absorbance - intercept) / slope
            if c_e < 0 or c_e > max(ligand_concs) * 1.01:
                analysis_results["warnings"].append(
                    f"Equilibrium concentration cE={c_e:.6g} for well {well_name} is outside [0, max(c0)] range; check calibration."
                )

            wells.append(
                {
                    "well": well_name,
                    "row": well_rows[r],
                    "column": c + 1,
                    "row_index": r,
                    "col_index": c,
                    "ligand_index": ligand_index,
                    "salt_index": salt_index,
                    "replicate_index": rep_index,
                    "c0": c0,
                    "salt_concentration": c_salt,
                    "absorbance": absorbance,
                    "c_e": c_e,
                }
            )

    if not wells:
        msg = "No usable well data found after mapping Tecan plate layout."
        print(f"ERROR: {msg}")
        analysis_results["message"] = msg
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    wells_df = pd.DataFrame(wells)

    # Sanity-check: cE should vary within a column as c0 varies
    for c in range(num_columns):
        col_mask = wells_df["col_index"] == c
        col_data = wells_df[col_mask]
        if col_data.empty:
            continue
        c0_range = col_data["c0"].max() - col_data["c0"].min()
        c_e_range = col_data["c_e"].max() - col_data["c_e"].min()
        if c0_range > 0 and c_e_range < 0.05 * c0_range:
            analysis_results["warnings"].append(
                f"Equilibrium concentration variation across column {c + 1} is small compared to c0 variation. This may indicate inverted or incorrect calibration."
            )

    # Group by (ligand_index, salt_index) and aggregate replicates
    group_cols = ["ligand_index", "salt_index"]
    grouped = wells_df.groupby(group_cols)

    summary_rows: List[Dict[str, Any]] = []
    for (lig_idx, salt_idx), df_group in grouped:
        if len(df_group) != replicates:
            msg = (
                f"Parameter combination (ligand_index={lig_idx}, salt_index={salt_idx}) has {len(df_group)} replicate wells, expected {replicates}."
            )
            print(f"ERROR: {msg}")
            analysis_results["message"] = msg
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        c0_val = ligand_concs[lig_idx]
        salt_val = salt_concs[salt_idx]
        c_e_vals = df_group["c_e"].to_numpy()
        c_e_mean = float(np.mean(c_e_vals))
        c_e_std = float(np.std(c_e_vals, ddof=1)) if len(c_e_vals) > 1 else 0.0

        # Sanity-check c_e_mean
        if c_e_mean < -1e-9 or c_e_mean - c0_val > 1e-6:
            analysis_results["warnings"].append(
                f"Mean equilibrium concentration cE_mean={c_e_mean:.6g} for c0={c0_val:.6g} is outside [0, c0]; check calibration."
            )

        # Calculate loading q = (c0 - cE_mean) * v_total / m_resin
        q_val = (c0_val - c_e_mean) * total_volume / resin_mass

        summary_rows.append(
            {
                "ligand_index": lig_idx,
                "salt_index": salt_idx,
                "c0": c0_val,
                "salt_concentration": salt_val,
                "c_e_mean": c_e_mean,
                "c_e_std": c_e_std,
                "q": q_val,
                "n_replicates": len(df_group),
            }
        )

    expected_groups = n_ligand * n_salt
    if len(summary_rows) != expected_groups:
        msg = (
            f"Number of aggregated parameter combinations {len(summary_rows)} does not match expected {expected_groups} (n_ligand * n_salt). Check plate mapping."
        )
        print(f"ERROR: {msg}")
        analysis_results["message"] = msg
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    summary_df = pd.DataFrame(summary_rows)

    # Sort summary for readability
    summary_df.sort_values(
        by=["salt_concentration", "c0"], ascending=[True, True], inplace=True
    )

    # Save well-level and summary data as CSV
    wells_csv_path = results_folder_path / f"wells_processed_{experiment_id}.csv"
    summary_csv_path = (
        results_folder_path / f"loading_isotherm_summary_{experiment_id}.csv"
    )
    wells_df.to_csv(wells_csv_path, index=False)
    summary_df.to_csv(summary_csv_path, index=False)
    analysis_results["data_outputs"]["wells_processed_csv"] = str(
        wells_csv_path.resolve()
    )
    analysis_results["data_outputs"]["summary_csv"] = str(
        summary_csv_path.resolve()
    )

    # Perform Langmuir fits per salt concentration
    fit_results: List[Dict[str, Any]] = []

    unique_salt_values = sorted(summary_df["salt_concentration"].unique())

    plt.figure(figsize=(8, 6))
    cmap = plt.get_cmap("viridis")
    n_colors = max(len(unique_salt_values), 1)

    for idx, salt_val in enumerate(unique_salt_values):
        sub_df = summary_df[summary_df["salt_concentration"] == salt_val]
        c_e_vals = sub_df["c_e_mean"].to_numpy()
        q_vals = sub_df["q"].to_numpy()

        # Ensure we only fit within calibration/experimental range
        if np.nanmax(c_e_vals) > max(ligand_concs) * 1.01:
            analysis_results["warnings"].append(
                f"Equilibrium concentrations for salt {salt_val} exceed prepared c0 range; fit may be unreliable."
            )

        try:
            q_max_fit, K_fit, r2 = fit_langmuir(c_e_vals, q_vals)
            fit_success = True
            msg_fit = ""
        except RuntimeError as e:
            q_max_fit, K_fit, r2 = float("nan"), float("nan"), float("nan")
            fit_success = False
            msg_fit = str(e)
            analysis_results["warnings"].append(
                f"Langmuir fit failed for salt {salt_val}: {e}"
            )

        fit_results.append(
            {
                "salt_concentration": salt_val,
                "q_max": q_max_fit,
                "K": K_fit,
                "R2": r2,
                "fit_success": fit_success,
                "fit_message": msg_fit,
            }
        )

        color = cmap(float(idx) / max(n_colors - 1, 1)) if n_colors > 1 else "C0"

        # Plot raw data points
        plt.scatter(
            c_e_vals,
            q_vals,
            color=color,
            label=None,
            alpha=0.7,
            edgecolors="k",
            s=40,
        )

        # Plot fit curve if successful
        if fit_success:
            c_e_grid = np.linspace(
                max(0.0, float(np.nanmin(c_e_vals))),
                float(np.nanmax(c_e_vals)),
                200,
            )
            q_fit_curve = langmuir_isotherm(c_e_grid, q_max_fit, K_fit)
            label = (
                f"Salt {salt_val:g} {salt_unit or ''}: q_max={q_max_fit:.3g}, K={K_fit:.3g}, R2={r2:.3f}"
            )
            plt.plot(c_e_grid, q_fit_curve, color=color, label=label)
        else:
            label = f"Salt {salt_val:g} {salt_unit or ''}: fit failed ({msg_fit})"
            # Add a dummy entry to legend
            plt.plot([], [], color=color, label=label)

    plt.xlabel(
        f"Equilibrium ligand concentration cE [{ligand_unit}]"
        if ligand_unit
        else "Equilibrium ligand concentration cE"
    )
    plt.ylabel(
        f"Loading q [{ligand_unit} * uL / mg]"
        if ligand_unit
        else "Loading q (concentration * uL / mg)"
    )
    plt.title("Loading isotherms at different salt concentrations")
    plt.legend(fontsize=8)
    plt.tight_layout()

    plot_png_path = (
        results_folder_path / f"loading_isotherms_{experiment_id}.png"
    )
    plot_pdf_path = (
        results_folder_path / f"loading_isotherms_{experiment_id}.pdf"
    )
    plt.savefig(plot_png_path, dpi=300)
    plt.savefig(plot_pdf_path)
    plt.close()

    analysis_results["plots"]["loading_isotherms_png"] = str(plot_png_path.resolve())
    analysis_results["plots"]["loading_isotherms_pdf"] = str(plot_pdf_path.resolve())

    # Save fit results
    fit_df = pd.DataFrame(fit_results)
    fit_csv_path = results_folder_path / f"langmuir_fit_results_{experiment_id}.csv"
    fit_df.to_csv(fit_csv_path, index=False)
    analysis_results["data_outputs"]["langmuir_fit_csv"] = str(
        fit_csv_path.resolve()
    )

    # Finalize
    analysis_results["status"] = "success"
    analysis_results["message"] = (
        "Analysis completed successfully for experiment {0}.".format(
            experiment_id
        )
    )
    analysis_results["files_processed"] = 1

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, "w") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(
        description="Analyze Tecan plate reader data for loading isotherm determination."
    )
    parser.add_argument(
        "experiment_id",
        nargs="?",
        help="Experiment ID. If not provided, attempts to auto-detect the most recent.",
    )
    parser.add_argument(
        "--data-folder",
        default="../data",
        help="Path to the folder containing experiment_ID.json files. Default: ../data",
    )
    parser.add_argument(
        "--results-folder",
        default="../results",
        help=(
            "Path to the folder where all analysis outputs will be saved. Default: ../results"
        ),
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
                "ERROR: Analysis failed: {0}".format(
                    results.get("message", "Unknown error.")
                )
            )
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
