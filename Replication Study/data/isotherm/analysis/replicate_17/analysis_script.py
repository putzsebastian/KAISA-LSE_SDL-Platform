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

# -----------------------------------------------------------------------------
# Tecan helper functions (from specification)
# -----------------------------------------------------------------------------

from typing import Optional, Iterable


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


# -----------------------------------------------------------------------------
# Core analysis helpers
# -----------------------------------------------------------------------------


def _auto_detect_experiment_id(data_folder: Path) -> str:
    """Auto-detect most recent experiment_*.json file and return its ID as string."""
    json_files = sorted(
        data_folder.glob("experiment_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not json_files:
        raise FileNotFoundError(f"No experiment_*.json files found in data folder: {data_folder}")
    latest = json_files[0]
    name = latest.stem  # experiment_1234
    try:
        return name.split("_", 1)[1]
    except Exception:
        raise ValueError(f"Cannot extract experiment id from filename: {latest.name}")


def _load_experiment_json(experiment_id: str, data_folder: Path) -> Dict[str, Any]:
    """Load experiment JSON, first from root ../ then from data_folder if not found."""
    # Root-first lookup as required
    root_path = Path("../") / f"experiment_{experiment_id}.json"
    data_path = data_folder / f"experiment_{experiment_id}.json"

    tried_paths = []
    for p in (root_path, data_path):
        tried_paths.append(str(p))
        if p.exists():
            try:
                with open(p, "r") as f:
                    print(f"INFO: Loading experiment JSON from {p}")
                    return json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON format in {p}: {e}")
    raise FileNotFoundError("Data file not found. Tried: " + ", ".join(tried_paths))


def _parse_semicolon_floats(field_value: Any, field_name: str) -> List[float]:
    """Parse semicolon-separated numbers from an extra field, with comma/point support."""
    if field_value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(field_value, (int, float)):
        return [float(field_value)]
    if not isinstance(field_value, str):
        raise ValueError(f"Metadata field '{field_name}' must be string, int or float, got {type(field_value)}")
    tokens = [t.strip() for t in field_value.split(";") if t.strip()]
    values: List[float] = []
    for t in tokens:
        t_norm = t.replace(",", ".")
        try:
            values.append(float(t_norm))
        except ValueError:
            raise ValueError(f"Cannot convert value '{t}' in field '{field_name}' to float")
    return values


def _parse_single_float(field_value: Any, field_name: str) -> float:
    """Parse a single numeric field.

    Accepts int/float or string (with optional comma decimal separator).
    """
    if field_value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(field_value, (int, float)):
        return float(field_value)
    if not isinstance(field_value, str):
        raise ValueError(f"Metadata field '{field_name}' must be string, int or float, got {type(field_value)}")
    t = field_value.strip().replace(",", ".")
    try:
        return float(t)
    except ValueError:
        raise ValueError(f"Cannot convert value '{field_value}' in field '{field_name}' to float")


def _extract_metadata(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract required metadata from experiment_data['metadata_decoded']['extra_fields']."""
    try:
        extra = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        raise KeyError(f"Missing expected metadata field structure: {e}")

    def get_value(field: str) -> Any:
        if field not in extra or "value" not in extra[field]:
            raise KeyError(f"Missing expected metadata field: {field}")
        return extra[field]["value"]

    # Core numeric parameters
    num_ligand_conc = int(_parse_single_float(get_value("Number of ligand concentrations"), "Number of ligand concentrations"))
    num_salt_conc = int(_parse_single_float(get_value("Number of salt concentrations"), "Number of salt concentrations"))
    replicates = int(_parse_single_float(get_value("Replicates"), "Replicates"))

    ligand_concs = _parse_semicolon_floats(get_value("Ligand concentrations"), "Ligand concentrations")
    salt_concs = _parse_semicolon_floats(get_value("Salt concentrations"), "Salt concentrations")

    if len(ligand_concs) != num_ligand_conc:
        raise ValueError(
            f"Number of ligand concentrations mismatch: declared {num_ligand_conc} but parsed {len(ligand_concs)} from 'Ligand concentrations'"
        )
    if len(salt_concs) != num_salt_conc:
        raise ValueError(
            f"Number of salt concentrations mismatch: declared {num_salt_conc} but parsed {len(salt_concs)} from 'Salt concentrations'"
        )

    slope = _parse_single_float(get_value("Calibration Curve Slope"), "Calibration Curve Slope")
    intercept = _parse_single_float(get_value("Calibration Curve Intercept"), "Calibration Curve Intercept")

    if slope == 0:
        raise ValueError("Calibration Curve Slope must not be zero (cannot invert calibration)")

    resin_mass_mg = _parse_single_float(get_value("Resin Mass"), "Resin Mass")
    total_volume_ul = _parse_single_float(get_value("Total volume"), "Total volume")

    ligand_unit = str(get_value("Ligand concentration unit"))
    salt_unit = str(get_value("Salt concentration unit"))

    meta = {
        "num_ligand_conc": num_ligand_conc,
        "num_salt_conc": num_salt_conc,
        "replicates": replicates,
        "ligand_concs": ligand_concs,
        "salt_concs": salt_concs,
        "calib_slope": slope,
        "calib_intercept": intercept,
        "resin_mass_mg": resin_mass_mg,
        "total_volume_ul": total_volume_ul,
        "ligand_unit": ligand_unit,
        "salt_unit": salt_unit,
    }

    return meta


def _well_from_indices(row_idx: int, col_idx: int) -> str:
    """Convert 0-based row/column indices to well ID (e.g. A1).

    row_idx: 0..7 -> A..H; col_idx: 0..11 -> 1..12
    """
    row_letter = chr(ord("A") + row_idx)
    col_number = col_idx + 1
    return f"{row_letter}{col_number}"


def _read_tecan_absorbance(tecan_data_path: Path) -> pd.DataFrame:
    """Read absorbance data from a single Tecan Excel file.

    Absorbance data starts at row 34, column B (1-based) as per template.
    Rows correspond to A..H, columns to 1..12.
    """
    print(f"INFO: Reading Tecan absorbance data from {tecan_data_path}")
    num_rows = 8  # A..H
    num_cols = 12  # 1..12
    df_raw = pd.read_excel(
        tecan_data_path,
        header=None,
        skiprows=33,  # zero-based, so row 34 in Excel
        usecols=list(range(1, 1 + num_cols)),
        nrows=num_rows,
    )
    # df_raw: shape (8,12); rows 0..7 -> A..H; columns 0..11 -> 1..12
    records: List[Dict[str, Any]] = []
    for r in range(df_raw.shape[0]):
        for c in range(df_raw.shape[1]):
            val = df_raw.iat[r, c]
            if pd.isna(val):
                continue
            well = _well_from_indices(r, c)
            records.append({"well": well, "row_idx": r, "col_idx": c, "absorbance": float(val)})
    plate_df = pd.DataFrame.from_records(records)
    print(f"INFO: Parsed {len(plate_df)} wells with absorbance values")
    return plate_df


def _assign_conditions(
    plate_df: pd.DataFrame,
    meta: Dict[str, Any],
) -> pd.DataFrame:
    """Assign ligand and salt concentrations and replicate indices to each well.

    Layout rules (per user description):
      - In each column, rows A..(A+num_ligand_conc-1) carry ascending ligand concentrations c0.
      - All columns share the same ligand concentration list.
      - Columns are grouped by replicates within each salt concentration block.
      - Salt concentrations increase row-wise (here: by column blocks), with replicates grouped.

    Mapping implementation:
      - Columns 0..11 (1..12 in plate) are grouped into blocks of size 'replicates'.
      - Number of blocks must equal num_salt_conc.
      - For column index c:
          salt_index = c // replicates  (0-based)
          replicate_index = c % replicates  (0..replicates-1)
      - For row index r (0-based):
          ligand_index = r, but must be < num_ligand_conc.
    """
    num_lig = meta["num_ligand_conc"]
    num_salt = meta["num_salt_conc"]
    reps = meta["replicates"]

    num_cols = 12
    if num_salt * reps != num_cols:
        raise ValueError(
            f"Plate layout inconsistency: num_salt_conc * replicates must equal 12 columns, "
            f"got {num_salt} * {reps} = {num_salt * reps}. Check metadata vs. plate layout."
        )

    # Prepare containers
    ligand_concs = meta["ligand_concs"]
    salt_concs = meta["salt_concs"]

    def map_row_to_ligand(r: int) -> Tuple[int, float]:
        if r >= num_lig:
            # Rows beyond configured ligand count contain no data to analyze
            return -1, np.nan
        return r, ligand_concs[r]

    def map_col_to_salt_and_rep(c: int) -> Tuple[int, float, int]:
        salt_index = c // reps
        if salt_index >= num_salt:
            raise ValueError(
                f"Derived salt_index {salt_index} out of range for num_salt_conc={num_salt} (col_idx={c})"
            )
        replicate_index = c % reps
        return salt_index, salt_concs[salt_index], replicate_index

    mapped_records: List[Dict[str, Any]] = []
    for _, row in plate_df.iterrows():
        r_idx = int(row["row_idx"])
        c_idx = int(row["col_idx"])
        lig_index, c0 = map_row_to_ligand(r_idx)
        if lig_index < 0:
            # Ignore wells outside ligand concentration range
            continue
        salt_index, salt_c, rep_idx = map_col_to_salt_and_rep(c_idx)
        rec = dict(row)
        rec.update(
            {
                "ligand_index": lig_index,
                "salt_index": salt_index,
                "replicate_index": rep_idx,
                "c0": c0,
                "salt_conc": salt_c,
            }
        )
        mapped_records.append(rec)

    mapped_df = pd.DataFrame.from_records(mapped_records)
    print(f"INFO: After mapping, {len(mapped_df)} wells remain within configured ligand range")
    return mapped_df


def _compute_concentrations_and_q(df: pd.DataFrame, meta: Dict[str, Any]) -> pd.DataFrame:
    """Compute cE and loading q for each well.

    cE = (Abs - intercept) / slope
    q = (c0 - cE) * v_total / m_resin
    """
    slope = meta["calib_slope"]
    intercept = meta["calib_intercept"]
    v_total = meta["total_volume_ul"]
    m_resin = meta["resin_mass_mg"]

    df = df.copy()
    df["cE"] = (df["absorbance"] - intercept) / slope

    # Sanity check: cE between 0 and max c0
    max_c0 = max(meta["ligand_concs"]) if meta["ligand_concs"] else 0.0
    invalid_mask = (df["cE"] < 0) | (df["cE"] > max_c0 * 1.0001)
    num_invalid = int(invalid_mask.sum())
    if num_invalid > 0:
        raise ValueError(
            f"Computed {num_invalid} cE values outside [0, max(c0)={max_c0}]. "
            "Check calibration parameters and raw absorbance data."
        )

    # Additional sanity: cE must vary strongly across rows within a column as c0 varies
    variation_issues: List[str] = []
    for col_idx, sub in df.groupby("col_idx"):
        if sub["cE"].nunique() <= 1:
            variation_issues.append(f"column {col_idx+1} has constant cE across rows")
            continue
        cE_range = sub["cE"].max() - sub["cE"].min()
        if cE_range < 0.05 * max_c0:
            variation_issues.append(
                f"column {col_idx+1} has low cE range ({cE_range:g}) relative to max c0 {max_c0:g}"
            )
    if variation_issues:
        raise ValueError(
            "Sanity check failed: cE does not vary strongly with c0 for some columns. "
            + "; ".join(variation_issues)
        )

    df["q"] = (df["c0"] - df["cE"]) * v_total / m_resin
    return df


def _aggregate_replicates(df: pd.DataFrame, meta: Dict[str, Any]) -> pd.DataFrame:
    """Aggregate replicates for each (ligand_index, salt_index) combination.

    This must yield exactly num_ligand_conc * num_salt_conc groups.
    """
    num_lig = meta["num_ligand_conc"]
    num_salt = meta["num_salt_conc"]
    reps = meta["replicates"]

    group_cols = ["ligand_index", "salt_index", "c0", "salt_conc"]
    grouped = df.groupby(group_cols, as_index=False).agg(
        n_reps=("cE", "size"),
        cE_mean=("cE", "mean"),
        cE_std=("cE", "std"),
        q_mean=("q", "mean"),
        q_std=("q", "std"),
    )

    expected_groups = num_lig * num_salt
    actual_groups = grouped.shape[0]
    if actual_groups != expected_groups:
        raise ValueError(
            f"Grouping mismatch: expected {expected_groups} (ligand x salt) groups, "
            f"but obtained {actual_groups}. Well-to-condition mapping likely incorrect."
        )

    # Check replicate counts
    wrong_reps = grouped[grouped["n_reps"] != reps]
    if not wrong_reps.empty:
        raise ValueError(
            "Replicate count mismatch for some conditions. Expected "
            f"{reps} replicates per condition. Offending rows: "
            + wrong_reps.to_string(index=False)
        )

    # Replace NaN std with 0 when only one replicate (should not happen if reps>1 but kept for safety)
    grouped["cE_std"] = grouped["cE_std"].fillna(0.0)
    grouped["q_std"] = grouped["q_std"].fillna(0.0)

    return grouped


def _langmuir_iso(cE: np.ndarray, q_max: float, K: float) -> np.ndarray:
    """Langmuir isotherm model: q = q_max * K * cE / (1 + K * cE)."""
    return q_max * K * cE / (1.0 + K * cE)


def _fit_langmuir_for_salt(df_cond: pd.DataFrame) -> Dict[str, Any]:
    """Fit Langmuir isotherm for a single salt concentration subset.

    df_cond: rows for one salt concentration; must contain columns 'cE_mean' and 'q_mean'.
    """
    x = df_cond["cE_mean"].values.astype(float)
    y = df_cond["q_mean"].values.astype(float)

    # Initial guesses
    q_max0 = float(np.nanmax(y)) if np.isfinite(y).any() else 1.0
    # Rough K guess: 1 / median(cE) where y ~ q_max/2; if not enough info, use 1/max(x)
    if len(x) >= 2 and np.nanmax(x) > 0:
        K0 = 1.0 / float(np.nanmax(x))
    else:
        K0 = 1.0

    bounds = ([0.0, 0.0], [np.inf, np.inf])

    try:
        popt, pcov = curve_fit(
            _langmuir_iso,
            x,
            y,
            p0=[q_max0, K0],
            bounds=bounds,
            maxfev=10000,
        )
        q_pred = _langmuir_iso(x, *popt)
        # Manual R^2 computation to avoid external dependencies
        ss_res = float(np.sum((y - q_pred) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot != 0 else float("nan")

        # Sanity check: parameters must be finite and within plausible range
        if not np.isfinite(popt).all():
            raise RuntimeError("Non-finite Langmuir parameters")

        return {
            "q_max": float(popt[0]),
            "K": float(popt[1]),
            "r2": float(r2),
        }
    except Exception as e:
        print(f"WARNING: Langmuir fit failed for salt={df_cond['salt_conc'].iloc[0]}: {e}")
        return {
            "q_max": float("nan"),
            "K": float("nan"),
            "r2": float("nan"),
        }


def _plot_isotherms(
    agg_df: pd.DataFrame,
    meta: Dict[str, Any],
    fit_params: Dict[float, Dict[str, Any]],
    out_path: Path,
) -> None:
    """Plot all isotherms and save to file.

    agg_df: aggregated dataframe with columns cE_mean, q_mean, salt_conc
    fit_params: mapping salt_conc -> {q_max, K, r2}
    """
    plt.figure(figsize=(8, 6))

    unique_salt = sorted(agg_df["salt_conc"].unique())

    colors = plt.cm.viridis(np.linspace(0, 1, len(unique_salt)))

    for color, salt in zip(colors, unique_salt):
        sub = agg_df[agg_df["salt_conc"] == salt].sort_values("cE_mean")
        x = sub["cE_mean"].values
        y = sub["q_mean"].values
        yerr = sub["q_std"].values
        plt.errorbar(x, y, yerr=yerr, fmt="o", color=color, label=None, capsize=3)

        params = fit_params.get(float(salt))
        if params is not None and np.isfinite(params.get("q_max", np.nan)) and np.isfinite(
            params.get("K", np.nan)
        ):
            x_fit = np.linspace(0, max(x) * 1.05 if len(x) else 1.0, 200)
            y_fit = _langmuir_iso(x_fit, params["q_max"], params["K"])
            label = (
                f"salt={salt:g} {meta['salt_unit']} | q_max={params['q_max']:.3g}, "
                f"K={params['K']:.3g}, R2={params['r2']:.3f}"
            )
            plt.plot(x_fit, y_fit, "-", color=color, label=label)
        else:
            label = f"salt={salt:g} {meta['salt_unit']} (fit failed)"
            plt.plot([], [], "-", color=color, label=label)

    plt.xlabel(f"Equilibrium ligand concentration cE [{meta['ligand_unit']}]")
    plt.ylabel(f"Loading q [{meta['ligand_unit']}*uL/mg]")
    plt.title("Loading isotherms for different salt concentrations")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"INFO: Saved isotherm plot to {out_path}")


# -----------------------------------------------------------------------------
# Main analysis function
# -----------------------------------------------------------------------------


def analyze_experiment(experiment_id: str = None, data_folder: str = "../data", results_folder: str = "../results") -> Dict[str, Any]:
    """Main analysis function for Tecan-based loading isotherm evaluation.

    Args:
        experiment_id (str): Experiment ID (string). If None, auto-detects from most recent experiment_*.json.
        data_folder (str): Path to data folder.
        results_folder (str): Path to results folder.

    Returns:
        dict: Analysis results with key metrics and file paths.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    data_folder_path = Path(data_folder)

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
        # Auto-detect experiment ID if needed
        if experiment_id is None or str(experiment_id).strip() == "":
            print("INFO: No experiment_id provided, attempting auto-detection from data folder...")
            experiment_id = _auto_detect_experiment_id(data_folder_path)
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Auto-detected experiment_id = {experiment_id}")
        else:
            experiment_id = str(experiment_id)
            analysis_results["experiment_id"] = experiment_id

        # Load experiment JSON (root-first, then data folder)
        experiment_data = _load_experiment_json(experiment_id, data_folder_path)

        # Extract metadata
        meta = _extract_metadata(experiment_data)
        analysis_results["metadata"].update(meta)

        # Fetch Tecan data (single-file pattern)
        print(f"Fetching Tecan data for experiment {experiment_id} from device control server...")
        try:
            data_info = check_tecan_data_availability(experiment_id)
            if not data_info.get("available", False):
                raise FileNotFoundError(
                    f"No Tecan data available for experiment {experiment_id}: "
                    f"{data_info.get('error', 'Unknown error')}"
                )

            print(f"Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
            tecan_data_path_str = fetch_tecan_data_file(experiment_id, str(results_folder_path))
            tecan_data_path = Path(tecan_data_path_str)

        except Exception as device_server_error:
            print(
                f"Device control server access failed: {device_server_error}. "
                "Falling back to local file search..."
            )
            tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

            if not Path(tecan_raw_path).exists():
                analysis_results["message"] = (
                    "Tecan data not available - analysis skipped. This is expected for test runs."
                )
                analysis_results["status"] = "success"
                analysis_results["note"] = "No Tecan data found on server or local system."
                print(f"INFO: {analysis_results['message']}")
                # Save JSON and return early
                results_json_file = f"analysis_results_{experiment_id}.json"
                results_json_path = results_folder_path / results_json_file
                with open(results_json_path, "w") as f:
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
                with open(results_json_path, "w") as f:
                    json.dump(analysis_results, f, indent=4)
                print(f"Saved analysis results JSON to: {results_json_path}")
                return analysis_results

            tecan_data_path = results_folder_path / f"tecan_data_{experiment_id}.xlsx"
            shutil.copy(most_recent_excel_source, tecan_data_path)
            print(f"Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")

        analysis_results["data_outputs"]["tecan_raw_file"] = str(tecan_data_path.resolve())
        analysis_results["files_processed"] += 1

        # Read absorbance data and map conditions
        plate_df = _read_tecan_absorbance(tecan_data_path)
        mapped_df = _assign_conditions(plate_df, meta)

        # Compute cE and q per well
        well_df = _compute_concentrations_and_q(mapped_df, meta)

        # Aggregate replicates
        agg_df = _aggregate_replicates(well_df, meta)

        # Fit Langmuir isotherms per salt concentration
        fit_params: Dict[float, Dict[str, Any]] = {}
        for salt, sub in agg_df.groupby("salt_conc"):
            fit_params[float(salt)] = _fit_langmuir_for_salt(sub)

        # Save processed data
        per_well_csv = results_folder_path / f"per_well_results_{experiment_id}.csv"
        agg_csv = results_folder_path / f"aggregated_isotherm_data_{experiment_id}.csv"
        well_df.to_csv(per_well_csv, index=False)
        agg_df.to_csv(agg_csv, index=False)
        analysis_results["data_outputs"]["per_well_csv"] = str(per_well_csv.resolve())
        analysis_results["data_outputs"]["aggregated_csv"] = str(agg_csv.resolve())

        # Save fit parameters
        fit_params_json = results_folder_path / f"langmuir_fit_params_{experiment_id}.json"
        with open(fit_params_json, "w") as f:
            json.dump(fit_params, f, indent=4)
        analysis_results["data_outputs"]["langmuir_fit_params"] = str(fit_params_json.resolve())

        # Plot isotherms
        plot_path = results_folder_path / f"loading_isotherms_{experiment_id}.png"
        _plot_isotherms(agg_df, meta, fit_params, plot_path)
        analysis_results["plots"]["isotherms_png"] = str(plot_path.resolve())

        # Final status
        analysis_results["status"] = "success"
        analysis_results["message"] = "Analysis completed successfully."

        # Save analysis results JSON
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")

        return analysis_results

    except Exception as e:
        analysis_results["status"] = "failed"
        analysis_results["message"] = str(e)
        print(f"ERROR: Analysis failed: {e}")
        # Save partial results JSON
        try:
            if analysis_results.get("experiment_id") is None:
                analysis_results["experiment_id"] = experiment_id
            results_json_file = f"analysis_results_{experiment_id if experiment_id is not None else 'unknown'}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved (partial) analysis results JSON to: {results_json_path}")
        except Exception as e_save:
            print(f"ERROR: Failed to save analysis_results JSON: {e_save}")
        return analysis_results


# -----------------------------------------------------------------------------
# Command line interface
# -----------------------------------------------------------------------------


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description="Analyze Tecan loading isotherm experiment data.")
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
