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


def fetch_tecan_data_file(
    experiment_id: str, save_to_folder: str, file_index: int = 0
) -> str:
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
        raise Exception(
            "Timeout while fetching Tecan data from device control server"
        )
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


def parse_semicolon_floats(value: str, field_name: str) -> List[float]:
    """Parse a semicolon-separated string into a list of floats.

    Comma decimal separators are accepted and converted.
    """
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(
            f"Metadata field '{field_name}' must be string or number, got {type(value)}"
        )
    parts = [p.strip() for p in value.split(";") if p.strip() != ""]
    floats: List[float] = []
    for p in parts:
        p_clean = p.replace(",", ".")
        try:
            floats.append(float(p_clean))
        except ValueError:
            raise ValueError(
                f"Cannot convert token '{p}' in metadata field '{field_name}' to float"
            )
    if not floats:
        raise ValueError(
            f"Metadata field '{field_name}' does not contain any valid numeric entries"
        )
    return floats


def parse_positive_int(value: Any, field_name: str) -> int:
    """Parse a positive integer metadata field."""
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    try:
        iv = int(str(value).strip())
    except Exception:
        raise ValueError(
            f"Metadata field '{field_name}' must be an integer, got {value!r}"
        )
    if iv <= 0:
        raise ValueError(
            f"Metadata field '{field_name}' must be positive, got {iv}"
        )
    return iv


def parse_positive_float(value: Any, field_name: str) -> float:
    """Parse a positive float metadata field (accepting comma decimal)."""
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    s = str(value).strip().replace(",", ".")
    try:
        fv = float(s)
    except Exception:
        raise ValueError(
            f"Metadata field '{field_name}' must be numeric, got {value!r}"
        )
    if fv <= 0:
        raise ValueError(
            f"Metadata field '{field_name}' must be positive, got {fv}"
        )
    return fv


def langmuir_isotherm(c, q_max, K):
    """Langmuir isotherm: q = q_max * K * c / (1 + K * c)."""
    return q_max * K * c / (1.0 + K * c)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fit Langmuir isotherm to data.

    Returns (q_max, K, R2). On failure returns (None, None, None).
    """
    mask = np.isfinite(c_e) & np.isfinite(q)
    c_e = c_e[mask]
    q = q[mask]
    if c_e.size < 3:
        print("WARNING: Not enough points to fit Langmuir isotherm (need >=3)")
        return None, None, None

    # Initial guesses: q_max ~ max(q), K ~ 1 / median(c_e[c_e > 0])
    q_max0 = float(np.nanmax(q)) if np.isfinite(np.nanmax(q)) else 1.0
    positive_c = c_e[c_e > 0]
    if positive_c.size == 0:
        print("WARNING: No positive equilibrium concentrations for Langmuir fit")
        return None, None, None
    K0 = 1.0 / float(np.median(positive_c))

    try:
        popt, pcov = curve_fit(
            langmuir_isotherm,
            c_e,
            q,
            p0=[q_max0, K0],
            bounds=([0.0, 0.0], [np.inf, np.inf]),
            maxfev=10000,
        )
        q_pred = langmuir_isotherm(c_e, *popt)

        # Compute R-squared manually to avoid external dependencies
        ss_res = float(np.sum((q - q_pred) ** 2))
        ss_tot = float(np.sum((q - np.mean(q)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

        # Sanity checks
        q_max_fit, K_fit = popt
        if not (np.isfinite(q_max_fit) and np.isfinite(K_fit)):
            print("WARNING: Non-finite Langmuir parameters")
            return None, None, None
        if q_max_fit <= 0 or K_fit <= 0:
            print("WARNING: Non-positive Langmuir parameters")
            return None, None, None

        # Degenerate low-signal check: if data are almost linear, model may be unidentifiable
        if np.nanmax(q) - np.nanmin(q) <= 1e-6:
            print("WARNING: q values span a very small range; Langmuir fit may be unreliable")

        return float(q_max_fit), float(K_fit), float(r2)
    except Exception as e:
        print(f"WARNING: Langmuir fit failed: {e}")
        return None, None, None


def well_position_to_indices(row_label: str, col_index: int) -> Tuple[int, int]:
    """Convert well position like row 'A'-'H' and column index 1-12 into zero-based indices.

    Returns (row_idx, col_idx).
    """
    row_label = str(row_label).strip().upper()
    if len(row_label) != 1 or row_label < "A" or row_label > "H":
        raise ValueError(f"Invalid row label: {row_label!r}")
    if not (1 <= col_index <= 12):
        raise ValueError(f"Invalid column index: {col_index}")
    row_idx = ord(row_label) - ord("A")
    col_idx = col_index - 1
    return row_idx, col_idx


def build_plate_mapping(
    num_ligand_conc: int,
    num_salt_conc: int,
    replicates: int,
) -> Dict[Tuple[int, int], Tuple[int, int, int]]:
    """Build mapping from (row_idx, col_idx) -> (ligand_index, salt_index, replicate_index).

    ligand_index: 0..num_ligand_conc-1 (by row within each column)
    salt_index: 0..num_salt_conc-1 (by groups of replicates across columns)
    replicate_index: 0..replicates-1 (within each salt group, by column)

    The mapping follows the description:
    - In each column, rows A.. correspond to increasing ligand concentration.
    - Across columns, salt concentrations are ascending, with replicates grouped
      together. For a given salt concentration, 'replicates' consecutive columns
      share that salt concentration.
    """
    mapping: Dict[Tuple[int, int], Tuple[int, int, int]] = {}

    if num_ligand_conc > 8:
        raise ValueError(
            "Number of ligand concentrations cannot exceed 8 on a 96-well plate (rows A-H)."
        )

    total_wells_needed = num_ligand_conc * num_salt_conc * replicates
    if total_wells_needed > 96:
        raise ValueError(
            f"Plate layout requires {total_wells_needed} wells, but a 96-well plate has only 96."
        )

    total_columns_needed = num_salt_conc * replicates
    if total_columns_needed > 12:
        raise ValueError(
            f"Plate layout requires {total_columns_needed} columns, but a 96-well plate has only 12."
        )

    for salt_idx in range(num_salt_conc):
        for rep_idx in range(replicates):
            col_idx = salt_idx * replicates + rep_idx  # 0-based
            if col_idx >= 12:
                continue
            for lig_idx in range(num_ligand_conc):
                row_idx = lig_idx  # rows A.. by ligand conc
                if row_idx >= 8:
                    continue
                mapping[(row_idx, col_idx)] = (lig_idx, salt_idx, rep_idx)

    return mapping


def analyze_experiment(
    experiment_id: Optional[str] = None,
    data_folder: str = "../data",
    results_folder: str = "../results",
) -> Dict[str, Any]:
    """Main analysis function for Tecan plate reader loading isotherm.

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

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            analysis_results["experiment_id"] = experiment_id
        else:
            # Auto-detect from most recent experiment_*.json in data_folder
            data_path = Path(data_folder)
            if not data_path.exists():
                raise FileNotFoundError(
                    f"Data folder does not exist for auto-detection: {data_folder}"
                )
            json_files = list(data_path.glob("experiment_*.json"))
            if not json_files:
                raise FileNotFoundError(
                    f"No experiment_*.json files found in data folder for auto-detection: {data_folder}"
                )
            json_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            latest = json_files[0]
            experiment_id = latest.stem.replace("experiment_", "")
            analysis_results["experiment_id"] = experiment_id
            print(
                f"INFO: Auto-detected experiment ID {experiment_id} from file {latest.name}"
            )

    if experiment_id is None:
        raise ValueError("Experiment ID could not be determined.")

    # Load experiment data JSON (try root .. then data_folder)
    data_folder_path = Path(data_folder)
    root_json_path = Path("../") / f"experiment_{experiment_id}.json"
    if root_json_path.exists():
        data_file_path = root_json_path
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

    # Extract required metadata from eLabFTW extra fields
    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure 'metadata_decoded.extra_fields': {e}")

    def get_field(name: str) -> Any:
        if name not in metadata:
            raise KeyError(f"Missing expected metadata field: {name}")
        field = metadata[name]
        if not isinstance(field, dict) or "value" not in field:
            raise KeyError(
                f"Metadata field '{name}' has unexpected structure, expected dict with 'value' key."
            )
        return field["value"]

    # Read numeric experimental design parameters
    num_ligand_conc = parse_positive_int(
        get_field("Number of ligand concentrations"),
        "Number of ligand concentrations",
    )
    num_salt_conc = parse_positive_int(
        get_field("Number of salt concentrations"),
        "Number of salt concentrations",
    )
    replicates = parse_positive_int(get_field("Replicates"), "Replicates")

    ligand_concs = parse_semicolon_floats(
        get_field("Ligand concentrations"), "Ligand concentrations"
    )
    salt_concs = parse_semicolon_floats(
        get_field("Salt concentrations"), "Salt concentrations"
    )

    if len(ligand_concs) != num_ligand_conc:
        raise ValueError(
            f"Number of ligand concentrations ({num_ligand_conc}) does not match length of Ligand concentrations list ({len(ligand_concs)})."
        )
    if len(salt_concs) != num_salt_conc:
        raise ValueError(
            f"Number of salt concentrations ({num_salt_conc}) does not match length of Salt concentrations list ({len(salt_concs)})."
        )

    ligand_conc_unit = str(get_field("Ligand concentration unit")).strip()
    salt_conc_unit = str(get_field("Salt concentration unit")).strip()

    resin_mass_mg = parse_positive_float(get_field("Resin Mass"), "Resin Mass")
    total_volume_uL = parse_positive_float(
        get_field("Total volume"), "Total volume"
    )

    calib_slope = parse_positive_float(
        get_field("Calibration Curve Slope"), "Calibration Curve Slope"
    )
    calib_intercept_field = get_field("Calibration Curve Intercept")
    calib_intercept = float(str(calib_intercept_field).strip().replace(",", "."))

    # Store key metadata in results
    analysis_results["metadata"].update(
        {
            "num_ligand_concentrations": num_ligand_conc,
            "num_salt_concentrations": num_salt_conc,
            "replicates": replicates,
            "ligand_concentrations": ligand_concs,
            "salt_concentrations": salt_concs,
            "ligand_concentration_unit": ligand_conc_unit,
            "salt_concentration_unit": salt_conc_unit,
            "resin_mass_mg": resin_mass_mg,
            "total_volume_uL": total_volume_uL,
            "calibration_slope": calib_slope,
            "calibration_intercept": calib_intercept,
        }
    )

    # Fetch Tecan data: single-file experiment pattern
    print(
        f"INFO: Fetching Tecan data for experiment {experiment_id} from device control server..."
    )
    try:
        data_info = check_tecan_data_availability(experiment_id)
        if not data_info.get("available", False):
            raise FileNotFoundError(
                f"No Tecan data available for experiment {experiment_id}: "
                f"{data_info.get('error', 'Unknown error')}"
            )

        print(
            f"INFO: Tecan data found on server: {data_info.get('total_files', 0)} file(s)"
        )
        tecan_data_path_str = fetch_tecan_data_file(experiment_id, str(results_folder_path))
        tecan_data_path = Path(tecan_data_path_str)
    except Exception as device_server_error:
        print(
            f"WARNING: Device control server access failed: {device_server_error}. Falling back to local file search..."
        )
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            analysis_results["message"] = (
                "Tecan data not available - analysis skipped. This is expected for test runs."
            )
            analysis_results["status"] = "success"
            analysis_results["metadata"]["data_source"] = "none"
            print(f"INFO: {analysis_results['message']}")

            # Save JSON results and return early
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"INFO: Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
        if not most_recent_excel_source:
            analysis_results["message"] = (
                "No recent Tecan Excel file found - analysis skipped."
            )
            analysis_results["status"] = "success"
            analysis_results["metadata"]["data_source"] = "none"
            print(f"INFO: {analysis_results['message']}")

            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"INFO: Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        tecan_data_path = (
            results_folder_path / f"tecan_data_{experiment_id}.xlsx"
        )
        shutil.copy(most_recent_excel_source, tecan_data_path)
        print(
            f"INFO: Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'"
        )

    analysis_results["metadata"]["data_source"] = "tecan_excel"
    analysis_results["data_outputs"]["tecan_raw_excel"] = str(
        tecan_data_path.resolve()
    )

    # Read absorbance data from the Excel file
    # Absorbance data starts at row 34 (0-based index 33), column B (0-based index 1)
    # We read 8 rows (A-H) and 12 columns (1-12)
    num_rows_plate = 8
    num_cols_plate = 12

    print(f"INFO: Reading absorbance data from {tecan_data_path}")
    try:
        raw_df = pd.read_excel(
            tecan_data_path,
            header=None,
            skiprows=33,
            usecols=list(range(1, 1 + num_cols_plate)),
            nrows=num_rows_plate,
        )
    except Exception as e:
        raise RuntimeError(f"Failed to read Tecan Excel file: {e}")

    # raw_df: rows 0..7 (A..H), cols 0..11 (1..12)
    # Build plate mapping and extract per-well data
    plate_mapping = build_plate_mapping(
        num_ligand_conc=num_ligand_conc,
        num_salt_conc=num_salt_conc,
        replicates=replicates,
    )

    records: List[Dict[str, Any]] = []
    for row_idx in range(num_rows_plate):
        for col_idx in range(num_cols_plate):
            if (row_idx, col_idx) not in plate_mapping:
                # Wells outside the design can be ignored
                continue
            lig_idx, salt_idx, rep_idx = plate_mapping[(row_idx, col_idx)]
            try:
                absorbance = float(raw_df.iat[row_idx, col_idx])
            except Exception:
                # Missing or non-numeric absorbance: skip well
                analysis_results["warnings"].append(
                    f"Non-numeric or missing absorbance at well row {row_idx}, col {col_idx}; skipping."
                )
                continue

            # Compute equilibrium concentration c_E using inverted calibration
            c0 = ligand_concs[lig_idx]
            c_e = (absorbance - calib_intercept) / calib_slope

            # Sanity check: c_E must lie between 0 and max(c0)
            if c_e < 0 or c_e > max(ligand_concs) * 1.05:
                analysis_results["warnings"].append(
                    f"Equilibrium concentration cE={c_e:.6g} at well (row {row_idx}, col {col_idx}) is outside expected range [0, {max(ligand_concs)}]."
                )

            # Additional check: cE should not exceed its own c0 by a large margin
            if c_e > c0 * 1.1 + 1e-12:
                analysis_results["warnings"].append(
                    f"Equilibrium concentration cE={c_e:.6g} at well (row {row_idx}, col {col_idx}) exceeds its initial concentration c0={c0:.6g}."
                )

            records.append(
                {
                    "row_index": row_idx,
                    "col_index": col_idx,
                    "row_label": chr(ord("A") + row_idx),
                    "col_number": col_idx + 1,
                    "ligand_index": lig_idx,
                    "salt_index": salt_idx,
                    "replicate_index": rep_idx,
                    "c0": c0,
                    "salt_concentration": salt_concs[salt_idx],
                    "absorbance": absorbance,
                    "cE": c_e,
                }
            )

    if not records:
        raise RuntimeError("No valid absorbance data points were parsed from the Tecan file.")

    per_well_df = pd.DataFrame.from_records(records)

    # Check variation of cE within each column as c0 varies
    for col_idx in sorted(per_well_df["col_index"].unique()):
        col_df = per_well_df[per_well_df["col_index"] == col_idx]
        if col_df["c0"].nunique() <= 1:
            continue
        c_e_range = col_df["cE"].max() - col_df["cE"].min()
        c0_range = col_df["c0"].max() - col_df["c0"].min()
        if c0_range > 0 and c_e_range < 0.05 * c0_range:
            msg = (
                "Equilibrium concentrations cE vary much less than initial concentrations c0 "
                f"in column {col_idx + 1}. This suggests a possible calibration error (e.g. applied in the wrong direction)."
            )
            print(f"WARNING: {msg}")
            analysis_results["warnings"].append(msg)

    # Verify grouping counts: must yield exactly num_ligand_conc * num_salt_conc parameter combinations,
    # each with exactly 'replicates' wells
    group_cols = ["ligand_index", "salt_index"]
    grouped = per_well_df.groupby(group_cols)
    group_sizes = grouped.size().reset_index(name="count")

    expected_groups = num_ligand_conc * num_salt_conc
    actual_groups = group_sizes.shape[0]
    if actual_groups != expected_groups:
        raise RuntimeError(
            f"Well-to-condition mapping mismatch: expected {expected_groups} groups (ligand x salt), "
            f"but found {actual_groups}. Please check plate layout and metadata."
        )

    bad_groups = group_sizes[group_sizes["count"] != replicates]
    if not bad_groups.empty:
        raise RuntimeError(
            "Well-to-condition mapping mismatch: one or more groups do not have the expected "
            f"number of replicates ({replicates}). Offending groups: {bad_groups.to_dict(orient='records')}"
        )

    # Aggregate per-condition: mean and std of cE
    agg_df = grouped.agg(
        cE_mean=("cE", "mean"),
        cE_std=("cE", "std"),
        absorbance_mean=("absorbance", "mean"),
        absorbance_std=("absorbance", "std"),
    ).reset_index()

    # Add c0 and salt concentration as explicit columns
    agg_df["c0"] = agg_df["ligand_index"].apply(lambda i: ligand_concs[int(i)])
    agg_df["salt_concentration"] = agg_df["salt_index"].apply(
        lambda i: salt_concs[int(i)]
    )

    # Compute loading q = (c0 - cE_mean) * v_total / m_resin
    agg_df["q"] = (
        (agg_df["c0"] - agg_df["cE_mean"]) * total_volume_uL / resin_mass_mg
    )

    # Error propagation for q using std of cE (ignoring uncertainty in c0, v_total, m_resin)
    agg_df["q_std"] = agg_df["cE_std"] * total_volume_uL / resin_mass_mg

    # Prepare for Langmuir fits per salt concentration
    fit_records: List[Dict[str, Any]] = []

    # For plotting
    fig, ax = plt.subplots(figsize=(8, 6))

    colors = plt.cm.viridis(np.linspace(0, 1, num_salt_conc))

    for salt_idx, salt_value in enumerate(salt_concs):
        salt_df = agg_df[agg_df["salt_index"] == salt_idx].copy()
        if salt_df.empty:
            continue

        c_e_vals = salt_df["cE_mean"].values.astype(float)
        q_vals = salt_df["q"].values.astype(float)

        # Ensure non-negative cE for fitting
        c_e_vals = np.clip(c_e_vals, a_min=0.0, a_max=None)

        q_max_fit, K_fit, r2 = fit_langmuir(c_e_vals, q_vals)
        fit_records.append(
            {
                "salt_index": salt_idx,
                "salt_concentration": salt_value,
                "q_max": q_max_fit,
                "K": K_fit,
                "R2": r2,
            }
        )

        color = colors[salt_idx]

        # Plot data points with error bars
        ax.errorbar(
            c_e_vals,
            q_vals,
            yerr=salt_df["q_std"].values,
            fmt="o",
            color=color,
            label=None,
            alpha=0.8,
        )

        # Plot fitted curve if available
        if q_max_fit is not None and K_fit is not None:
            c_grid = np.linspace(0, max(c_e_vals) * 1.05, 200)
            q_fit_curve = langmuir_isotherm(c_grid, q_max_fit, K_fit)
            label = (
                f"Salt {salt_value:g} {salt_conc_unit}: q_max={q_max_fit:.3g}, "
                f"K={K_fit:.3g}, R2={r2:.3f}"
            )
            ax.plot(c_grid, q_fit_curve, "-", color=color, label=label)
        else:
            label = f"Salt {salt_value:g} {salt_conc_unit}: fit failed"
            ax.plot([], [], " ", label=label, color=color)

    ax.set_xlabel(f"Equilibrium ligand concentration cE [{ligand_conc_unit}]")
    ax.set_ylabel(f"Loading q [{ligand_conc_unit} * uL / mg]")
    ax.set_title("Loading isotherms at different salt concentrations")
    ax.legend(fontsize=8)
    ax.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()

    isotherm_plot_png = results_folder_path / f"loading_isotherms_{experiment_id}.png"
    isotherm_plot_pdf = results_folder_path / f"loading_isotherms_{experiment_id}.pdf"
    fig.savefig(isotherm_plot_png, dpi=300)
    fig.savefig(isotherm_plot_pdf)
    plt.close(fig)

    analysis_results["plots"]["loading_isotherms_png"] = str(
        isotherm_plot_png.resolve()
    )
    analysis_results["plots"]["loading_isotherms_pdf"] = str(
        isotherm_plot_pdf.resolve()
    )

    # Save processed per-well and aggregated data
    per_well_csv = results_folder_path / f"per_well_data_{experiment_id}.csv"
    agg_csv = results_folder_path / f"aggregated_isotherm_data_{experiment_id}.csv"
    fits_csv = results_folder_path / f"langmuir_fits_{experiment_id}.csv"

    per_well_df.to_csv(per_well_csv, index=False)
    agg_df.to_csv(agg_csv, index=False)
    pd.DataFrame.from_records(fit_records).to_csv(fits_csv, index=False)

    analysis_results["data_outputs"]["per_well_csv"] = str(per_well_csv.resolve())
    analysis_results["data_outputs"]["aggregated_isotherm_csv"] = str(
        agg_csv.resolve()
    )
    analysis_results["data_outputs"]["langmuir_fits_csv"] = str(
        fits_csv.resolve()
    )

    analysis_results["files_processed"] = len(records)

    if analysis_results["warnings"]:
        analysis_results["message"] = (
            "Analysis completed with warnings. Check 'warnings' field for details."
        )
    else:
        analysis_results["message"] = "Analysis completed successfully."
    analysis_results["status"] = "success"

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, "w") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(
        description="Analyze Tecan plate reader experiment data for loading isotherms."
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
            "Path to the folder where all analysis outputs will be saved. "
            "Default: ../results"
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
                f"ERROR: Analysis failed with status {results.get('status')}: {results.get('message', 'Unknown error.')}"
            )
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
