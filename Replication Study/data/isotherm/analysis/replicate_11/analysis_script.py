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
        f for f in os.listdir(directory) if os.path.isdir(os.path.join(directory, f))
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
        excel_files = [f for f in files_in_folder if f.lower().endswith(".xlsx")]
        if not excel_files:
            return None
        excel_files.sort(
            key=lambda f: os.path.getctime(Path(excel_export_path) / f), reverse=True
        )
        return str(Path(excel_export_path) / excel_files[0])
    except Exception:
        return None


def read_tecan_absorbance(tecan_data_path: str) -> pd.DataFrame:
    """Read absorbance data from a single Tecan Excel file.

    Data starts at row 34 (0-based index 33) and column B (0-based index 1).
    Returns a DataFrame with index as row letters A-H and columns 1-12.
    """
    print(f"INFO: Reading Tecan absorbance data from {tecan_data_path}")
    # 8 rows (A-H), 12 columns (1-12)
    num_rows = 8
    num_cols = 12
    df_raw = pd.read_excel(
        tecan_data_path,
        header=None,
        skiprows=33,
        usecols=list(range(1, 1 + num_cols)),
        nrows=num_rows,
    )
    # Index rows A-H
    df_raw.index = list("ABCDEFGH")[:num_rows]
    df_raw.columns = list(range(1, num_cols + 1))
    return df_raw


def parse_semicolon_floats(value: str, field_name: str) -> List[float]:
    """Parse a semicolon separated string into list of floats.

    Handles comma decimal separators, strips whitespace, and ignores empty tokens.
    """
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(
            f"Metadata field '{field_name}' must be string or number, got {type(value)}"
        )
    tokens = [t.strip() for t in str(value).split(";") if t.strip() != ""]
    floats: List[float] = []
    for t in tokens:
        t_norm = t.replace(",", ".")
        try:
            floats.append(float(t_norm))
        except ValueError as e:
            raise ValueError(
                f"Cannot parse token '{t}' in metadata field '{field_name}' as float"
            ) from e
    return floats


def parse_single_float(value: Any, field_name: str) -> float:
    """Parse a single value to float, handling strings with comma decimal."""
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(
            f"Metadata field '{field_name}' must be string or number, got {type(value)}"
        )
    v = value.strip().replace(",", ".")
    try:
        return float(v)
    except ValueError as e:
        raise ValueError(
            f"Cannot parse metadata field '{field_name}' value '{value}' as float"
        ) from e


def langmuir_isotherm(c_e: np.ndarray, q_max: float, k_a: float) -> np.ndarray:
    """Langmuir isotherm: q = q_max * K * c_e / (1 + K * c_e)."""
    return q_max * k_a * c_e / (1.0 + k_a * c_e)


def compute_r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Compute coefficient of determination R^2."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    ss_res = np.nansum((y_true - y_pred) ** 2)
    ss_tot = np.nansum((y_true - np.nanmean(y_true)) ** 2)
    if ss_tot == 0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[float, float, float]:
    """Fit Langmuir isotherm and return (q_max, k_a, r2).

    Applies sensible bounds and initial guesses derived from data.
    """
    if len(c_e) < 3:
        raise ValueError("Need at least 3 points to fit Langmuir isotherm")

    c_e = np.asarray(c_e, dtype=float)
    q = np.asarray(q, dtype=float)

    # Initial guesses: q_max as max(q), k_a from mid-range
    q_max_guess = np.nanmax(q) if np.all(np.isfinite(q)) else 1.0
    if not np.isfinite(q_max_guess) or q_max_guess <= 0:
        q_max_guess = 1.0

    c_nonzero = c_e[np.isfinite(c_e) & (c_e > 0)]
    if c_nonzero.size == 0:
        k_a_guess = 1.0
    else:
        c_mid = np.median(c_nonzero)
        if c_mid <= 0:
            k_a_guess = 1.0
        else:
            k_a_guess = 1.0 / c_mid

    p0 = [q_max_guess, k_a_guess]
    bounds = ([0.0, 0.0], [np.inf, np.inf])

    popt, _ = curve_fit(langmuir_isotherm, c_e, q, p0=p0, bounds=bounds, maxfev=10000)
    q_max_fit, k_a_fit = popt

    # Sanity check fitted parameters
    if not np.all(np.isfinite(popt)) or q_max_fit <= 0 or k_a_fit < 0:
        raise ValueError("Unphysical Langmuir fit parameters obtained")

    q_pred = langmuir_isotherm(c_e, q_max_fit, k_a_fit)
    r2 = compute_r2(q, q_pred)
    if not np.isfinite(r2):
        r2 = float("nan")

    return float(q_max_fit), float(k_a_fit), float(r2)


def map_well_to_conditions(
    row: str,
    col: int,
    n_lig: int,
    n_salt: int,
    replicates: int,
    ligand_concs: List[float],
    salt_concs: List[float],
) -> Tuple[float, float]:
    """Map a plate well (row, col) to (c0, salt).

    Row is a letter A-H, col is 1-12.

    Layout per user specification:
    - Columns are grouped by salt concentration, each salt block uses 'replicates' columns.
    - Blocks ordered by increasing salt concentration across columns.
    - Within each column, rows contain ligand concentrations ascending A->H.
    Only the first n_lig rows (starting from A) contain data.
    """
    row_idx = ord(row.upper()) - ord("A")  # 0-based
    if row_idx < 0 or row_idx >= n_lig:
        # Outside the ligand-concentration rows, no data expected
        raise ValueError(
            f"Row {row} (index {row_idx}) outside configured NUMBER_OF_LIGAND_CONCENTRATIONS={n_lig}"
        )

    if not (1 <= col <= 12):
        raise ValueError(f"Column {col} out of valid range 1-12")

    total_required_cols = n_salt * replicates
    if total_required_cols > 12:
        raise ValueError(
            f"Plate layout requires {total_required_cols} columns (n_salt * replicates),"
            f" but a 96-well plate has only 12. Check metadata: n_salt={n_salt}, replicates={replicates}."
        )

    col_idx = col - 1  # 0-based

    salt_block_idx = col_idx // replicates  # 0..n_salt-1
    if salt_block_idx >= n_salt:
        raise ValueError(
            f"Column {col} belongs to salt block {salt_block_idx}, beyond NUMBER_OF_SALT_CONCENTRATIONS={n_salt}"
        )

    c0 = ligand_concs[row_idx]
    salt = salt_concs[salt_block_idx]
    return c0, salt


def analyze_experiment(
    experiment_id: str = None, data_folder: str = "../data", results_folder: str = "../results"
) -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm data.

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
        # CLI override if available
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            analysis_results["experiment_id"] = experiment_id
        else:
            # Auto-detect most recent experiment_*.json in data_folder
            data_path = Path(data_folder)
            json_files = sorted(
                data_path.glob("experiment_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not json_files:
                raise FileNotFoundError(
                    f"No experiment_*.json files found in data folder: {data_folder}"
                )
            latest = json_files[0]
            name = latest.stem  # experiment_<id>
            if "_" not in name:
                raise ValueError(
                    f"Cannot extract experiment_id from filename: {latest.name}"
                )
            experiment_id = name.split("_", 1)[1]
            analysis_results["experiment_id"] = experiment_id
            print(
                f"INFO: Auto-detected most recent experiment ID {experiment_id} from {latest.name}"
            )

    if experiment_id is None:
        raise ValueError("experiment_id could not be determined")

    data_folder_path = Path(data_folder)
    if not data_folder_path.exists():
        raise FileNotFoundError(f"Data folder does not exist: {data_folder_path}")

    # Load experiment JSON; check root folder first, then data folder
    exp_json_path_root = Path("../") / f"experiment_{experiment_id}.json"
    exp_json_path_data = data_folder_path / f"experiment_{experiment_id}.json"

    if exp_json_path_root.exists():
        data_file_path = exp_json_path_root
    else:
        data_file_path = exp_json_path_data

    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    analysis_results["metadata"]["data_file"] = str(data_file_path.resolve())

    # Extract required metadata (with error handling)
    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    def get_meta(field: str, required: bool = True) -> Any:
        try:
            return metadata[field]["value"]
        except KeyError:
            if required:
                raise KeyError(f"Missing expected metadata field: {field}")
            else:
                analysis_results["warnings"].append(
                    f"Optional metadata field missing: {field}"
                )
                return None

    # Core metadata fields used in analysis
    lig_conc_str = get_meta("Ligand concentrations")
    salt_conc_str = get_meta("Salt concentrations")
    n_lig_str = get_meta("Number of ligand concentrations")
    n_salt_str = get_meta("Number of salt concentrations")
    replicates_str = get_meta("Replicates")
    resin_mass_str = get_meta("Resin Mass")
    total_volume_str = get_meta("Total volume")
    calib_slope_str = get_meta("Calibration Curve Slope")
    calib_intercept_str = get_meta("Calibration Curve Intercept")
    lig_unit = get_meta("Ligand concentration unit")
    salt_unit = get_meta("Salt concentration unit")

    # Parse numeric metadata
    ligand_concs = parse_semicolon_floats(lig_conc_str, "Ligand concentrations")
    salt_concs = parse_semicolon_floats(salt_conc_str, "Salt concentrations")

    try:
        n_lig = int(str(n_lig_str).strip())
        n_salt = int(str(n_salt_str).strip())
        replicates = int(str(replicates_str).strip())
    except Exception as e:
        raise ValueError(
            f"Cannot parse integer metadata for number of ligand/salt concentrations or replicates: {e}"
        )

    if n_lig <= 0 or n_salt <= 0 or replicates <= 0:
        raise ValueError(
            f"Metadata values must be positive: n_lig={n_lig}, n_salt={n_salt}, replicates={replicates}"
        )

    if len(ligand_concs) != n_lig:
        raise ValueError(
            f"Number of ligand concentrations ({len(ligand_concs)}) does not match"
            f" 'Number of ligand concentrations' metadata ({n_lig})"
        )

    if len(salt_concs) != n_salt:
        raise ValueError(
            f"Number of salt concentrations ({len(salt_concs)}) does not match"
            f" 'Number of salt concentrations' metadata ({n_salt})"
        )

    resin_mass = parse_single_float(resin_mass_str, "Resin Mass")
    total_volume = parse_single_float(total_volume_str, "Total volume")
    calib_slope = parse_single_float(calib_slope_str, "Calibration Curve Slope")
    calib_intercept = parse_single_float(
        calib_intercept_str, "Calibration Curve Intercept"
    )

    if calib_slope == 0:
        raise ValueError("Calibration Curve Slope must not be zero (cannot invert calibration)")

    analysis_results["metadata"].update(
        {
            "ligand_concentrations": ligand_concs,
            "salt_concentrations": salt_concs,
            "n_ligand_concentrations": n_lig,
            "n_salt_concentrations": n_salt,
            "replicates": replicates,
            "resin_mass_mg": resin_mass,
            "total_volume_uL": total_volume,
            "calibration_slope": calib_slope,
            "calibration_intercept": calib_intercept,
            "ligand_concentration_unit": lig_unit,
            "salt_concentration_unit": salt_unit,
        }
    )

    # Fetch Tecan data (single-file pattern)
    print(f"Fetching Tecan data for experiment {experiment_id} from device control server...")

    tecan_data_path: Path
    try:
        data_info = check_tecan_data_availability(str(experiment_id))
        if not data_info.get("available", False):
            raise FileNotFoundError(
                f"No Tecan data available for experiment {experiment_id}: {data_info.get('error', 'Unknown error')}"
            )

        print(
            f"Tecan data found on server: {data_info.get('total_files', 0)} file(s)"
        )
        tecan_data_path_str = fetch_tecan_data_file(
            str(experiment_id), str(results_folder_path)
        )
        tecan_data_path = Path(tecan_data_path_str)
    except Exception as device_server_error:
        print(
            f"Device control server access failed: {device_server_error}. Falling back to local file search..."
        )
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            analysis_results["message"] = (
                "Tecan data not available - analysis skipped. This is expected for test runs."
            )
            analysis_results["status"] = "success"
            analysis_results["metadata"]["data_source"] = "none"
            analysis_results["note"] = (
                "No Tecan data found on server or local system."
            )
            print(f"INFO: {analysis_results['message']}")
            return analysis_results

        most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
        if not most_recent_excel_source:
            analysis_results["message"] = (
                "No recent Tecan Excel file found - analysis skipped."
            )
            analysis_results["status"] = "success"
            analysis_results["metadata"]["data_source"] = "none"
            print(f"INFO: {analysis_results['message']}")
            return analysis_results

        tecan_data_path = results_folder_path / f"tecan_data_{experiment_id}.xlsx"
        shutil.copy(most_recent_excel_source, tecan_data_path)
        print(
            f"Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'"
        )

    analysis_results["metadata"]["data_source"] = "tecan"
    analysis_results["data_outputs"]["tecan_raw_excel"] = str(
        tecan_data_path.resolve()
    )

    # Read absorbance plate data
    plate_df = read_tecan_absorbance(str(tecan_data_path))

    # Build long-format DataFrame for all wells with data
    records: List[Dict[str, Any]] = []
    for row_label, row_series in plate_df.iterrows():
        for col in plate_df.columns:
            absorbance = row_series[col]
            if pd.isna(absorbance):
                continue
            try:
                c0, salt = map_well_to_conditions(
                    row_label,
                    int(col),
                    n_lig,
                    n_salt,
                    replicates,
                    ligand_concs,
                    salt_concs,
                )
            except ValueError as e:
                # Wells outside configured layout are ignored but logged
                analysis_results["warnings"].append(str(e))
                continue

            records.append(
                {
                    "row": row_label,
                    "col": int(col),
                    "well": f"{row_label}{int(col)}",
                    "absorbance": float(absorbance),
                    "c0": float(c0),
                    "salt": float(salt),
                }
            )

    if not records:
        raise ValueError("No valid well data mapped to experimental conditions")

    wells_df = pd.DataFrame.from_records(records)

    # Convert absorbance to equilibrium concentration cE
    wells_df["cE"] = (wells_df["absorbance"] - calib_intercept) / calib_slope

    # Sanity checks on cE values
    max_c0 = max(ligand_concs) if ligand_concs else 0.0
    bad_low = wells_df["cE"] < -1e-9
    bad_high = wells_df["cE"] > max_c0 * 1.001
    if bad_low.any() or bad_high.any():
        raise ValueError(
            "Calculated equilibrium concentrations cE fall outside [0, max(c0)] range. "
            "Check calibration slope/intercept and absorbance values."
        )

    # Check that cE varies across rows within a column as c0 varies
    variation_issue_cols: List[int] = []
    for col in sorted(wells_df["col"].unique()):
        col_data = wells_df[wells_df["col"] == col]
        if col_data.empty:
            continue
        c0_range = col_data["c0"].max() - col_data["c0"].min()
        cE_range = col_data["cE"].max() - col_data["cE"].min()
        if c0_range > 0 and cE_range < 0.05 * c0_range:
            variation_issue_cols.append(col)
    if variation_issue_cols:
        raise ValueError(
            "Equilibrium concentrations cE show too little variation across ligand "
            "concentrations within columns. Calibration may have been applied incorrectly."
        )

    # Group replicates by (c0, salt)
    group_cols = ["c0", "salt"]
    grouped = wells_df.groupby(group_cols, as_index=False).agg(
        n_repl=("cE", "count"),
        cE_mean=("cE", "mean"),
        cE_std=("cE", "std"),
    )

    expected_groups = n_lig * n_salt
    actual_groups = grouped.shape[0]
    if actual_groups != expected_groups:
        raise ValueError(
            f"Number of aggregated points ({actual_groups}) does not match expected "
            f"NUMBER_OF_LIGAND_CONCENTRATIONS x NUMBER_OF_SALT_CONCENTRATIONS = {expected_groups}. "
            f"Check plate layout and metadata."
        )

    wrong_repl = grouped[grouped["n_repl"] != replicates]
    if not wrong_repl.empty:
        raise ValueError(
            "Some (c0, salt) combinations do not have the expected number of replicates "
            f"({replicates}). Offending combinations: "
            + wrong_repl.to_string(index=False)
        )

    # Replace NaN std (single replicate) with 0, though here n_repl should equal replicates>=1
    grouped["cE_std"] = grouped["cE_std"].fillna(0.0)

    # Calculate loading q for each parameter combination
    # q = (c0 - cE_mean) * v_total / m_resin
    grouped["q"] = (grouped["c0"] - grouped["cE_mean"]) * total_volume / resin_mass

    # Prepare per-salt Langmuir fits
    fit_results: List[Dict[str, Any]] = []
    salt_values_sorted = sorted(salt_concs)

    plt.figure(figsize=(8, 6))

    colors = plt.cm.get_cmap("tab10", len(salt_values_sorted))

    for idx, salt_val in enumerate(salt_values_sorted):
        sub = grouped[np.isclose(grouped["salt"], salt_val)]
        if sub.empty:
            analysis_results["warnings"].append(
                f"No data points found for salt concentration {salt_val} {salt_unit}"
            )
            continue

        cE_vals = sub["cE_mean"].values
        q_vals = sub["q"].values

        try:
            q_max, k_a, r2 = fit_langmuir(cE_vals, q_vals)
            fit_results.append(
                {
                    "salt": float(salt_val),
                    "q_max": q_max,
                    "K": k_a,
                    "R2": r2,
                }
            )

            cE_fit = np.linspace(max(0.0, float(np.min(cE_vals))), float(np.max(cE_vals)), 200)
            q_fit = langmuir_isotherm(cE_fit, q_max, k_a)

            label = (
                f"Salt {salt_val} {salt_unit}: q_max={q_max:.3g}, K={k_a:.3g}, R2={r2:.3f}"
            )
            plt.plot(
                cE_vals,
                q_vals,
                "o",
                color=colors(idx),
                label=None,
                alpha=0.8,
            )
            plt.plot(
                cE_fit, q_fit, "-", color=colors(idx), label=label, alpha=0.9
            )
        except Exception as e:
            msg = (
                f"Langmuir fit failed for salt {salt_val} {salt_unit}: {e}. "
                "Data for this salt will be shown without fit."
            )
            analysis_results["warnings"].append(msg)
            print(f"WARNING: {msg}")
            plt.plot(
                cE_vals,
                q_vals,
                "o",
                color=colors(idx),
                label=f"Salt {salt_val} {salt_unit} (no fit)",
                alpha=0.8,
            )

    plt.xlabel(f"Equilibrium ligand concentration cE [{lig_unit}]")
    plt.ylabel(f"Loading q [{lig_unit} * uL / mg]")
    plt.title("Loading Isotherms from Tecan Plate Reader Data")
    plt.legend(fontsize=8)
    plt.tight_layout()

    iso_png = results_folder_path / f"loading_isotherms_{experiment_id}.png"
    iso_pdf = results_folder_path / f"loading_isotherms_{experiment_id}.pdf"
    plt.savefig(iso_png, dpi=300)
    plt.savefig(iso_pdf)
    plt.close()

    analysis_results["plots"]["isotherms_png"] = str(iso_png.resolve())
    analysis_results["plots"]["isotherms_pdf"] = str(iso_pdf.resolve())

    # Save processed data
    wells_csv = results_folder_path / f"wells_processed_{experiment_id}.csv"
    grouped_csv = results_folder_path / f"loading_isotherm_points_{experiment_id}.csv"
    fits_csv = results_folder_path / f"langmuir_fits_{experiment_id}.csv"

    wells_df.to_csv(wells_csv, index=False)
    grouped.to_csv(grouped_csv, index=False)
    pd.DataFrame(fit_results).to_csv(fits_csv, index=False)

    analysis_results["data_outputs"]["wells_processed_csv"] = str(wells_csv.resolve())
    analysis_results["data_outputs"]["isotherm_points_csv"] = str(
        grouped_csv.resolve()
    )
    analysis_results["data_outputs"]["langmuir_fits_csv"] = str(fits_csv.resolve())

    analysis_results["files_processed"] = 1
    analysis_results["status"] = "success"
    analysis_results["message"] = "Analysis completed successfully."

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
        description="Analyze Tecan plate reader loading isotherm experiment data."
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
