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

# Device control server configuration for Tecan Spark
DEVICE_CONTROL_SERVER = os.getenv("DEVICE_CONTROL_SERVER", "http://localhost:8000")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "your-secure-api-key-here")


# ------------------------
# Helper functions: logging
# ------------------------

def log_info(message: str) -> None:
    print(f"INFO: {message}")


def log_warning(message: str) -> None:
    print(f"WARNING: {message}")


# -------------------------------------------------
# Tecan device server and local fallback helpers
# -------------------------------------------------

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


def get_most_recent_folder(directory, n=0):
    """Finds the n-th most recent subfolder in a given directory."""
    import os as _os

    folders = [f for f in _os.listdir(directory) if _os.path.isdir(_os.path.join(directory, f))]
    if not folders:
        return None
    sorted_folders = sorted(
        folders,
        key=lambda f: _os.path.getctime(_os.path.join(directory, f)),
        reverse=True,
    )
    return _os.path.join(directory, sorted_folders[n]) if len(sorted_folders) > n else None


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
        excel_files.sort(key=lambda f: os.path.getctime(Path(excel_export_path) / f), reverse=True)
        return str(Path(excel_export_path) / excel_files[0])
    except Exception:
        return None


# -------------------------------------------------
# Core analysis helpers
# -------------------------------------------------

def parse_semicolon_floats(value: str, field_name: str) -> List[float]:
    """Parse semicolon-separated string of numbers into list of floats.

    Accepts comma or dot as decimal separator. Strips whitespace.
    Raises ValueError with a clear message on failure.
    """

    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(f"Metadata field '{field_name}' must be string or number, got {type(value)}")

    items = [item.strip() for item in value.split(";") if item.strip() != ""]
    floats: List[float] = []
    for idx, item in enumerate(items):
        token = item.replace(",", ".")
        try:
            floats.append(float(token))
        except ValueError:
            raise ValueError(
                f"Cannot parse value '{item}' at position {idx + 1} in field '{field_name}' as float"
            )
    return floats


def langmuir_isotherm(c: np.ndarray, q_max: float, K: float) -> np.ndarray:
    """Langmuir isotherm: q = q_max * K * c / (1 + K * c)."""

    return q_max * K * c / (1.0 + K * c)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[float, float, float]:
    """Fit Langmuir isotherm and return (q_max, K, R2).

    Applies sensible bounds and initial guesses derived from data.
    Raises ValueError if the fit fails or parameters are not sensible.
    """

    if c_e.size < 3:
        raise ValueError("Not enough data points for Langmuir fit (need at least 3)")

    # Remove NaN/inf
    mask = np.isfinite(c_e) & np.isfinite(q)
    c_e_clean = c_e[mask]
    q_clean = q[mask]
    if c_e_clean.size < 3:
        raise ValueError("Not enough finite data points for Langmuir fit (need at least 3)")

    # Initial guesses
    q_max_guess = float(np.nanmax(q_clean)) if np.nanmax(q_clean) > 0 else 1.0
    c_mid = float(np.nanmedian(c_e_clean)) if np.isfinite(np.nanmedian(c_e_clean)) else 1.0
    K_guess = 1.0 / max(c_mid, 1e-9)

    p0 = [q_max_guess, K_guess]
    bounds = ([0.0, 0.0], [np.inf, np.inf])

    try:
        popt, _ = curve_fit(langmuir_isotherm, c_e_clean, q_clean, p0=p0, bounds=bounds, maxfev=10000)
    except Exception as e:
        raise ValueError(f"Langmuir fit failed: {e}")

    q_max_fit, K_fit = popt

    # Sanity checks
    if not np.isfinite(q_max_fit) or not np.isfinite(K_fit):
        raise ValueError("Langmuir fit returned non-finite parameters")
    if q_max_fit < 0 or K_fit < 0:
        raise ValueError("Langmuir fit returned negative parameters, which are not physical")

    # Compute R2 manually (avoid external dependencies)
    q_pred = langmuir_isotherm(c_e_clean, q_max_fit, K_fit)
    ss_res = float(np.sum((q_clean - q_pred) ** 2))
    ss_tot = float(np.sum((q_clean - np.mean(q_clean)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    # Additional sanity: ensure fitted q_max is within a reasonable multiple of observed max
    observed_max = float(np.nanmax(q_clean)) if np.nanmax(q_clean) > 0 else 1.0
    if q_max_fit > 10.0 * observed_max:
        raise ValueError(
            "Langmuir fit produced q_max much larger than observed data. "
            "Fit likely degenerate in low-signal regime."
        )

    return float(q_max_fit), float(K_fit), float(r2)


# -------------------------------------------------
# Main analysis
# -------------------------------------------------


def analyze_experiment(experiment_id: str = None, data_folder: str = "../data", results_folder: str = "../results") -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm experiments.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, attempts auto-detection.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with key metrics and paths to generated files.
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

    # ------------------------------
    # Auto-detect experiment ID
    # ------------------------------
    data_folder_path = Path(data_folder)
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            analysis_results["experiment_id"] = experiment_id
            log_info(f"Experiment ID taken from sys.argv: {experiment_id}")
        else:
            # Auto-detect most recent experiment_*.json
            json_files = sorted(
                data_folder_path.glob("experiment_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not json_files:
                raise FileNotFoundError(
                    f"No experiment_*.json files found in data folder: {data_folder_path}"
                )
            latest = json_files[0]
            name = latest.stem  # experiment_XXXX
            if "_" in name:
                experiment_id = name.split("_", 1)[1]
            else:
                experiment_id = name
            analysis_results["experiment_id"] = experiment_id
            log_info(f"Auto-detected latest experiment ID: {experiment_id}")

    if experiment_id is None:
        raise ValueError("experiment_id could not be determined.")

    # ------------------------------
    # Load experiment JSON
    # ------------------------------
    data_file_root_first = Path("../") / f"experiment_{experiment_id}.json"
    if data_file_root_first.exists():
        data_file_path = data_file_root_first
    else:
        data_file_path = data_folder_path / f"experiment_{experiment_id}.json"

    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
        analysis_results["metadata"]["experiment_json_path"] = str(data_file_path.resolve())
        log_info(f"Loaded experiment JSON from: {data_file_path}")
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # ------------------------------
    # Extract required metadata
    # ------------------------------
    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        raise KeyError(f"Missing expected metadata field structure: {e}")

    def get_field(name: str, required: bool = True) -> Any:
        if name not in metadata:
            if required:
                raise KeyError(f"Missing expected metadata field: {name}")
            else:
                return None
        return metadata[name].get("value")

    try:
        ligand_conc_str = get_field("Ligand concentrations")
        salt_conc_str = get_field("Salt concentrations")
        n_ligand = int(get_field("Number of ligand concentrations"))
        n_salt = int(get_field("Number of salt concentrations"))
        n_repl = int(get_field("Replicates"))
        calibration_slope = float(str(get_field("Calibration Curve Slope")).replace(",", "."))
        calibration_intercept = float(str(get_field("Calibration Curve Intercept")).replace(",", "."))
        resin_mass = float(str(get_field("Resin Mass")).replace(",", "."))
        total_volume = float(str(get_field("Total volume")).replace(",", "."))
        ligand_conc_unit = str(get_field("Ligand concentration unit"))
        salt_conc_unit = str(get_field("Salt concentration unit"))
    except KeyError as e:
        raise
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid type or value in metadata: {e}")

    ligand_concs = parse_semicolon_floats(ligand_conc_str, "Ligand concentrations")
    salt_concs = parse_semicolon_floats(salt_conc_str, "Salt concentrations")

    if len(ligand_concs) != n_ligand:
        raise ValueError(
            f"Number of ligand concentrations ({len(ligand_concs)}) does not match 'Number of ligand concentrations' ({n_ligand})."
        )
    if len(salt_concs) != n_salt:
        raise ValueError(
            f"Number of salt concentrations ({len(salt_concs)}) does not match 'Number of salt concentrations' ({n_salt})."
        )

    analysis_results["metadata"].update(
        {
            "ligand_concentrations": ligand_concs,
            "salt_concentrations": salt_concs,
            "n_ligand": n_ligand,
            "n_salt": n_salt,
            "n_replicates": n_repl,
            "calibration_slope": calibration_slope,
            "calibration_intercept": calibration_intercept,
            "resin_mass_mg": resin_mass,
            "total_volume_uL": total_volume,
            "ligand_concentration_unit": ligand_conc_unit,
            "salt_concentration_unit": salt_conc_unit,
        }
    )

    # --------------------------------------
    # Fetch Tecan data (single-file pattern)
    # --------------------------------------
    log_info(f"Fetching Tecan data for experiment {experiment_id} from device control server...")

    tecan_data_path: Path

    try:
        data_info = check_tecan_data_availability(str(experiment_id))
        if not data_info.get("available", False):
            raise FileNotFoundError(
                f"No Tecan data available for experiment {experiment_id}: {data_info.get('error', 'Unknown error')}"
            )

        log_info(f"Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
        tecan_data_str = fetch_tecan_data_file(str(experiment_id), str(results_folder_path))
        tecan_data_path = Path(tecan_data_str)
    except Exception as device_server_error:
        log_warning(
            f"Device control server access failed: {device_server_error}. Falling back to local file search..."
        )
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            msg = "Tecan data not available - analysis skipped. This is expected for test runs."
            analysis_results["message"] = msg
            analysis_results["status"] = "success"
            analysis_results["note"] = "No Tecan data found on server or local system."
            log_info(msg)
            # Save JSON before returning
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            log_info(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
        if not most_recent_excel_source:
            msg = "No recent Tecan Excel file found - analysis skipped."
            analysis_results["message"] = msg
            analysis_results["status"] = "success"
            log_info(msg)
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, "w") as f:
                json.dump(analysis_results, f, indent=4)
            log_info(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        tecan_data_path = results_folder_path / f"tEcan_data_{experiment_id}.xlsx"
        try:
            shutil.copy(most_recent_excel_source, tecan_data_path)
        except Exception as e:
            raise IOError(f"Failed to copy local Tecan file: {e}")
        log_info(f"Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")

    analysis_results["data_outputs"]["tecan_raw_excel"] = str(tecan_data_path.resolve())

    # --------------------------------------
    # Read absorbance data from Excel
    # --------------------------------------
    # Absorbance data starts at row 34 (index 33), column B (index 1).
    # We always read 8 rows (A-H) and 12 columns (1-12) for a 96-well plate.

    num_rows_plate = 8
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
        raise IOError(f"Failed to read Tecan Excel file '{tecan_data_path}': {e}")

    # raw_df rows 0..7 correspond to A..H, columns 0..11 correspond to 1..12

    # --------------------------------------
    # Map wells to ligand and salt conditions
    # --------------------------------------
    if n_ligand > num_rows_plate:
        raise ValueError(
            f"Number of ligand concentrations ({n_ligand}) exceeds plate rows (8)."
        )

    if n_salt * n_repl > num_cols_plate:
        raise ValueError(
            f"n_salt * n_repl = {n_salt * n_repl} exceeds available columns (12)."
        )

    # Build list of per-well records
    wells_records: List[Dict[str, Any]] = []

    # Plate row letters A-H
    row_labels = ["A", "B", "C", "D", "E", "F", "G", "H"]

    for col_idx in range(num_cols_plate):  # 0..11 -> columns 1..12
        # Determine salt concentration and replicate index for this column
        salt_block_index = col_idx // n_repl  # 0-based
        repl_index = col_idx % n_repl  # 0-based
        if salt_block_index >= n_salt:
            # Column beyond defined salt blocks - skip
            continue
        salt_conc = salt_concs[salt_block_index]

        for row_idx in range(n_ligand):  # only rows with data
            row_label = row_labels[row_idx]
            well_name = f"{row_label}{col_idx + 1}"
            try:
                absorbance = float(raw_df.iat[row_idx, col_idx])
            except Exception:
                # Missing or non-numeric data; skip this well but record warning
                msg = f"Non-numeric or missing absorbance at well {well_name}; skipping this well."
                analysis_results["warnings"].append(msg)
                log_warning(msg)
                continue

            c0 = ligand_concs[row_idx]

            wells_records.append(
                {
                    "well": well_name,
                    "row": row_label,
                    "column": col_idx + 1,
                    "replicate_index": repl_index,
                    "ligand_index": row_idx,
                    "salt_index": salt_block_index,
                    "salt_concentration": salt_conc,
                    "ligand_c0": c0,
                    "absorbance": absorbance,
                }
            )

    wells_df = pd.DataFrame(wells_records)

    expected_wells = n_ligand * n_salt * n_repl
    if wells_df.shape[0] != expected_wells:
        raise ValueError(
            f"Unexpected number of valid wells: got {wells_df.shape[0]}, expected {expected_wells}. "
            "Check plate layout and metadata (n_ligand, n_salt, replicates)."
        )

    # --------------------------------------
    # Step 1: compute equilibrium concentration cE
    # --------------------------------------

    if calibration_slope == 0:
        raise ValueError("Calibration curve slope is zero; cannot invert calibration.")

    wells_df["cE"] = (wells_df["absorbance"] - calibration_intercept) / calibration_slope

    # Sanity checks: cE must lie between 0 and max c0 for each ligand index
    max_c0 = max(ligand_concs) if ligand_concs else 0.0

    if max_c0 <= 0:
        raise ValueError("Maximum initial ligand concentration c0 must be positive for sanity check.")

    # Global range check
    if ((wells_df["cE"] < -1e-9) | (wells_df["cE"] - max_c0 > 1e-6)).any():
        failing = wells_df[(wells_df["cE"] < -1e-9) | (wells_df["cE"] - max_c0 > 1e-6)].head(5)
        raise ValueError(
            "Equilibrium concentration cE outside [0, max(c0)] for some wells. "
            "Check calibration slope/intercept. Example offending rows: "
            f"{failing.to_dict(orient='records')}"
        )

    # Variation check: within each column (fixed salt and replicate), cE should vary with c0.
    # We check Pearson correlation between c0 and cE; it should be reasonably positive.
    bad_columns: List[int] = []
    for col_idx in range(num_cols_plate):
        subset = wells_df[wells_df["column"] == col_idx + 1]
        if subset.shape[0] < 2:
            continue
        corr_matrix = np.corrcoef(subset["ligand_c0"], subset["cE"])
        corr = float(corr_matrix[0, 1]) if corr_matrix.shape == (2, 2) else float("nan")
        if not np.isfinite(corr) or corr < 0.5:
            bad_columns.append(col_idx + 1)

    if bad_columns:
        raise ValueError(
            "Calibration sanity check failed: equilibrium concentration cE does not correlate "
            f"with initial concentration c0 in columns {bad_columns}. "
            "Calibration may have been applied incorrectly."
        )

    # --------------------------------------
    # Step 2: aggregate replicates by (ligand, salt)
    # --------------------------------------
    group_cols = ["ligand_index", "salt_index"]

    agg_df = (
        wells_df.groupby(group_cols)
        .agg(
            ligand_c0=("ligand_c0", "first"),
            salt_concentration=("salt_concentration", "first"),
            cE_mean=("cE", "mean"),
            cE_std=("cE", "std"),
            absorbance_mean=("absorbance", "mean"),
            absorbance_std=("absorbance", "std"),
            n_repl_actual=("cE", "count"),
        )
        .reset_index()
    )

    if agg_df.shape[0] != n_ligand * n_salt:
        raise ValueError(
            f"Unexpected number of aggregated groups: got {agg_df.shape[0]}, expected {n_ligand * n_salt}. "
            "Check well-to-condition mapping against plate layout."
        )

    if (agg_df["n_repl_actual"] != n_repl).any():
        bad = agg_df[agg_df["n_repl_actual"] != n_repl][
            ["ligand_index", "salt_index", "n_repl_actual"]
        ].to_dict(orient="records")
        raise ValueError(
            "Replicate count mismatch for some (ligand, salt) combinations. Details: "
            f"{bad}"
        )

    # Replace NaN std with 0 for groups with single replicate (should not occur, but safe)
    agg_df["cE_std"] = agg_df["cE_std"].fillna(0.0)
    agg_df["absorbance_std"] = agg_df["absorbance_std"].fillna(0.0)

    # --------------------------------------
    # Step 3: compute loading q
    # --------------------------------------

    # q = (c0 - cE) * v_total / m_resin
    # v_total in uL, m_resin in mg as given (no unit conversion).

    agg_df["q"] = (agg_df["ligand_c0"] - agg_df["cE_mean"]) * total_volume / resin_mass

    # --------------------------------------
    # Step 4: Langmuir fit for each salt concentration
    # --------------------------------------

    fit_results: List[Dict[str, Any]] = []

    for salt_idx in range(n_salt):
        salt_group = agg_df[agg_df["salt_index"] == salt_idx].sort_values("cE_mean")
        if salt_group.shape[0] < 3:
            msg = f"Salt index {salt_idx}: not enough points for Langmuir fit; skipping."
            log_warning(msg)
            analysis_results["warnings"].append(msg)
            continue

        c_e_vals = salt_group["cE_mean"].to_numpy(dtype=float)
        q_vals = salt_group["q"].to_numpy(dtype=float)

        try:
            q_max, K, r2 = fit_langmuir(c_e_vals, q_vals)
            fit_results.append(
                {
                    "salt_index": salt_idx,
                    "salt_concentration": float(salt_concs[salt_idx]),
                    "q_max": q_max,
                    "K": K,
                    "R2": r2,
                }
            )
        except ValueError as e:
            msg = f"Salt index {salt_idx}: Langmuir fit failed: {e}"
            log_warning(msg)
            analysis_results["warnings"].append(msg)

    fits_df = pd.DataFrame(fit_results)

    # --------------------------------------
    # Step 5: Plot isotherms with fits
    # --------------------------------------

    fig, ax = plt.subplots(figsize=(8, 6))

    colors = plt.cm.viridis(np.linspace(0, 1, n_salt))

    for salt_idx in range(n_salt):
        salt_val = salt_concs[salt_idx]
        salt_group = agg_df[agg_df["salt_index"] == salt_idx].sort_values("cE_mean")
        if salt_group.empty:
            continue

        c_e_vals = salt_group["cE_mean"].to_numpy(dtype=float)
        q_vals = salt_group["q"].to_numpy(dtype=float)

        ax.scatter(
            c_e_vals,
            q_vals,
            color=colors[salt_idx],
            label=None,
            alpha=0.7,
            s=40,
        )

        # If we have a successful fit for this salt index, overlay the curve
        fit_row = fits_df[fits_df["salt_index"] == salt_idx]
        if not fit_row.empty:
            q_max = float(fit_row["q_max"].iloc[0])
            K = float(fit_row["K"].iloc[0])
            r2 = float(fit_row["R2"].iloc[0])

            c_grid = np.linspace(max(0.0, float(np.nanmin(c_e_vals))), float(np.nanmax(c_e_vals)), 200)
            q_fit = langmuir_isotherm(c_grid, q_max, K)

            label = (
                f"Salt {salt_val:g} {salt_conc_unit} | q_max={q_max:.3g}, K={K:.3g}, R2={r2:.3f}"
            )
            ax.plot(c_grid, q_fit, color=colors[salt_idx], label=label)
        else:
            # No fit; provide a simpler legend entry
            label = f"Salt {salt_val:g} {salt_conc_unit} (no fit)"
            ax.plot([], [], color=colors[salt_idx], label=label)

    ax.set_xlabel(f"Equilibrium ligand concentration cE [{ligand_conc_unit}]")
    ax.set_ylabel(f"Loading q [{ligand_conc_unit} * uL / mg]")
    ax.set_title("Loading isotherms from Tecan plate reader")
    ax.legend(fontsize=8)
    ax.grid(True, linestyle="--", alpha=0.5)

    fig.tight_layout()
    plot_path_png = results_folder_path / f"loading_isotherms_{experiment_id}.png"
    fig.savefig(plot_path_png, dpi=300)
    plt.close(fig)

    analysis_results["plots"]["loading_isotherms_png"] = str(plot_path_png.resolve())

    # --------------------------------------
    # Save processed data
    # --------------------------------------
    wells_csv_path = results_folder_path / f"wells_processed_{experiment_id}.csv"
    agg_csv_path = results_folder_path / f"aggregated_isotherm_{experiment_id}.csv"
    fits_csv_path = results_folder_path / f"langmuir_fits_{experiment_id}.csv"

    wells_df.to_csv(wells_csv_path, index=False)
    agg_df.to_csv(agg_csv_path, index=False)
    fits_df.to_csv(fits_csv_path, index=False)

    analysis_results["data_outputs"].update(
        {
            "wells_processed_csv": str(wells_csv_path.resolve()),
            "aggregated_isotherm_csv": str(agg_csv_path.resolve()),
            "langmuir_fits_csv": str(fits_csv_path.resolve()),
        }
    )

    analysis_results["files_processed"] = 1
    analysis_results["status"] = "success"
    analysis_results["message"] = "Analysis completed successfully."

    # Save JSON results
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, "w") as f:
        json.dump(analysis_results, f, indent=4)
    log_info(f"Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface"""

    parser = argparse.ArgumentParser(description="Analyze Tecan loading isotherm experiment data.")
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
