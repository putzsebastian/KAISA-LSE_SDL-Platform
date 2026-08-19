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


# --------- Helper functions for analysis ---------


def _safe_float(value, name: str) -> float:
    try:
        if isinstance(value, str):
            value_str = value.replace(",", ".").strip()
            return float(value_str)
        return float(value)
    except Exception:
        raise ValueError(f"Invalid numeric value for {name}: {value!r}")


def _parse_semicolon_list(value: Any, name: str) -> List[float]:
    if value is None:
        raise ValueError(f"Missing value for {name}")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(f"Expected string for {name}, got {type(value)}")
    parts = [p.strip() for p in value.split(";") if p.strip() != ""]
    if not parts:
        raise ValueError(f"No entries found in {name}")
    numbers = []
    for p in parts:
        try:
            p_norm = p.replace(",", ".")
            numbers.append(float(p_norm))
        except Exception:
            raise ValueError(
                f"Could not parse entry {p!r} in {name} as float (original value: {value!r})"
            )
    return numbers


def langmuir_isotherm(c, q_max, K):
    """Langmuir isotherm: q = q_max * K * c / (1 + K * c)."""
    return q_max * K * c / (1.0 + K * c)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[float, float, float]:
    """Fit Langmuir isotherm, return (q_max, K, R2).

    Raises ValueError if fit cannot be performed or is not meaningful.
    """
    if c_e.size < 3:
        raise ValueError("Not enough data points for Langmuir fit (need at least 3)")

    # Initial guesses based on data
    c_e = np.asarray(c_e, dtype=float)
    q = np.asarray(q, dtype=float)

    # Only use finite points
    mask = np.isfinite(c_e) & np.isfinite(q)
    c_e_fit = c_e[mask]
    q_fit = q[mask]
    if c_e_fit.size < 3:
        raise ValueError("Not enough finite data points for Langmuir fit")

    q_max_guess = float(np.nanmax(q_fit)) if np.nanmax(q_fit) > 0 else 1.0
    c_mid = float(np.nanmedian(c_e_fit)) if np.nanmedian(c_e_fit) > 0 else 1.0
    K_guess = 1.0 / max(c_mid, 1e-9)

    p0 = [q_max_guess, K_guess]
    bounds = ([0.0, 0.0], [np.inf, np.inf])

    try:
        popt, pcov = curve_fit(
            langmuir_isotherm,
            c_e_fit,
            q_fit,
            p0=p0,
            bounds=bounds,
            maxfev=10000,
        )
    except Exception as e:
        raise ValueError(f"Langmuir fit failed: {e}")

    q_max, K = popt

    # Sanity checks
    if not np.isfinite(q_max) or not np.isfinite(K):
        raise ValueError("Langmuir fit returned non-finite parameters")
    if q_max <= 0 or K <= 0:
        raise ValueError("Langmuir fit returned non-positive parameters")

    # Compute R2 manually
    q_pred = langmuir_isotherm(c_e_fit, q_max, K)
    ss_res = float(np.sum((q_fit - q_pred) ** 2))
    ss_tot = float(np.sum((q_fit - np.mean(q_fit)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    return float(q_max), float(K), float(r2)


def _auto_detect_experiment_id(data_folder: Path) -> str:
    """Auto-detect most recent experiment_<id>.json file in data_folder."""
    candidates = sorted(
        data_folder.glob("experiment_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            f"No experiment_*.json files found in data folder: {data_folder}"
        )
    latest = candidates[0]
    name = latest.stem  # experiment_1234
    try:
        return name.split("_", 1)[1]
    except Exception:
        raise ValueError(
            f"Could not parse experiment ID from filename: {latest.name}"
        )


def _load_experiment_json(experiment_id: str, data_folder: Path) -> Dict[str, Any]:
    """Load experiment JSON with primary and fallback search paths."""
    # Primary: root-level JSON one directory above data_folder
    root_json_path = data_folder.parent / f"experiment_{experiment_id}.json"
    data_file_path = root_json_path
    if not root_json_path.exists():
        # Fallback: inside data_folder
        data_file_path = data_folder / f"experiment_{experiment_id}.json"

    print(f"INFO: Loading experiment JSON from: {data_file_path}")

    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    return experiment_data


def _extract_metadata_fields(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract required eLabFTW extra fields with validation."""
    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    def get_field(name: str, required: bool = True) -> Any:
        try:
            field = metadata[name]
            return field.get("value")
        except KeyError:
            if required:
                raise KeyError(f"Missing expected metadata field: {name}")
            return None

    extracted = {}
    extracted["ligand_concs_raw"] = get_field("Ligand concentrations")
    extracted["num_ligand_concs"] = get_field("Number of ligand concentrations")
    extracted["salt_concs_raw"] = get_field("Salt concentrations")
    extracted["num_salt_concs"] = get_field("Number of salt concentrations")
    extracted["replicates_raw"] = get_field("Replicates")
    extracted["ligand_unit"] = get_field("Ligand concentration unit")
    extracted["salt_unit"] = get_field("Salt concentration unit")
    extracted["cal_slope_raw"] = get_field("Calibration Curve Slope")
    extracted["cal_intercept_raw"] = get_field("Calibration Curve Intercept")
    extracted["resin_mass_raw"] = get_field("Resin Mass")
    extracted["total_volume_raw"] = get_field("Total volume")

    # Optional context fields (kept for completeness)
    optional_fields = [
        "Buffer",
        "Salt Name",
        "Process ID",
        "Pump Speed",
        "Ligand Name",
        "Venting time",
        "Pump Duration",
        "Incubation Time",
        "Equilibration Cycles",
        "Equilibration Volume",
        "Incubation Temperature",
        "Measurement Wavelength",
        "Shaker Speed Incubation",
        "Salt Stock Concentration",
        "Ligand Stock Concentration",
        "Equilibration Cycle Duration",
    ]
    for name in optional_fields:
        try:
            extracted[name] = metadata[name].get("value")
        except KeyError:
            extracted[name] = None

    return extracted


def _build_well_mapping(
    num_ligand: int, num_salt: int, replicates: int
) -> Dict[str, Dict[str, int]]:
    """Map 96-well positions (A1..H12) to (ligand_index, salt_index, replicate_index).

    Rows A-H: ligand concentrations, ascending with row index.
    Columns: grouped by salt concentration and replicate, row-wise.

    Example: num_salt=4, replicates=3 ->
        columns 1-3: salt 0
        columns 4-6: salt 1
        columns 7-9: salt 2
        columns 10-12: salt 3

    Only first num_ligand rows (A.. up to) are used; remaining rows are ignored.
    """
    rows = ["A", "B", "C", "D", "E", "F", "G", "H"]

    if num_ligand > len(rows):
        raise ValueError(
            f"Number of ligand concentrations {num_ligand} exceeds plate rows (8)"
        )

    if num_salt * replicates != 12:
        raise ValueError(
            "Salt concentrations times replicates must equal 12 columns. "
            f"Got num_salt={num_salt}, replicates={replicates}, "
            f"product={num_salt * replicates} != 12."
        )

    well_map: Dict[str, Dict[str, int]] = {}
    for col in range(1, 13):  # 1..12
        salt_index = (col - 1) // replicates
        replicate_index = (col - 1) % replicates
        for row_idx in range(num_ligand):
            row_letter = rows[row_idx]
            well_name = f"{row_letter}{col}"
            ligand_index = row_idx
            well_map[well_name] = {
                "ligand_index": ligand_index,
                "salt_index": salt_index,
                "replicate_index": replicate_index,
            }
    return well_map


def _extract_plate_absorbance(tecan_path: Path) -> pd.DataFrame:
    """Read Tecan Excel export and return DataFrame with rows A-H, columns 1-12.

    Absorbance data starts at row 34 (0-based index 33) and column B (index 1).
    """
    print(f"INFO: Reading Tecan Excel file: {tecan_path}")
    num_rows = 8  # A-H
    num_cols = 12  # 1-12
    raw_df = pd.read_excel(
        tecan_path,
        header=None,
        skiprows=33,
        usecols=list(range(0, 1 + num_cols)),
        nrows=num_rows,
    )

    # raw_df: first column row labels A-H; next 12 columns are 1..12
    if raw_df.shape[1] < 13:
        raise ValueError(
            f"Unexpected Tecan sheet format: expected at least 13 columns, got {raw_df.shape[1]}"
        )

    plate_values = raw_df.iloc[:, 1 : 1 + num_cols].copy()
    plate_values.index = ["A", "B", "C", "D", "E", "F", "G", "H"]
    plate_values.columns = list(range(1, 13))

    # Melt into tidy format
    tidy = (
        plate_values.reset_index()
        .melt(id_vars="index", var_name="column", value_name="absorbance")
        .rename(columns={"index": "row"})
    )
    tidy["well"] = tidy.apply(
        lambda r: f"{r['row']}{int(r['column'])}", axis=1
    )
    return tidy[["well", "row", "column", "absorbance"]]


def analyze_experiment(
    experiment_id: str = None,
    data_folder: str = "../data",
    results_folder: str = "../results",
) -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with all key metrics
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
        data_folder_path = Path(data_folder)
        if not data_folder_path.exists():
            raise FileNotFoundError(f"Data folder does not exist: {data_folder_path}")

        # Auto-detect experiment ID if not provided
        if experiment_id is None:
            if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
                experiment_id = sys.argv[1]
                print(
                    f"INFO: Using experiment ID from sys.argv: {experiment_id}"
                )
            else:
                print(
                    "INFO: No experiment ID provided. Auto-detecting most recent experiment JSON."
                )
                experiment_id = _auto_detect_experiment_id(data_folder_path)
                print(f"INFO: Auto-detected experiment ID: {experiment_id}")

        analysis_results["experiment_id"] = experiment_id

        # Load experiment JSON (with root/fallback logic)
        experiment_data = _load_experiment_json(experiment_id, data_folder_path)

        # Extract metadata
        meta = _extract_metadata_fields(experiment_data)
        analysis_results["metadata"].update(meta)

        # Parse numeric metadata
        ligand_concs = _parse_semicolon_list(
            meta["ligand_concs_raw"], "Ligand concentrations"
        )
        salt_concs = _parse_semicolon_list(
            meta["salt_concs_raw"], "Salt concentrations"
        )
        num_ligand_from_field = int(
            _safe_float(
                meta["num_ligand_concs"], "Number of ligand concentrations"
            )
        )
        num_salt_from_field = int(
            _safe_float(
                meta["num_salt_concs"], "Number of salt concentrations"
            )
        )
        replicates = int(_safe_float(meta["replicates_raw"], "Replicates"))

        if num_ligand_from_field != len(ligand_concs):
            raise ValueError(
                "Number of ligand concentrations does not match list length: "
                f"field={num_ligand_from_field}, list={len(ligand_concs)}"
            )
        if num_salt_from_field != len(salt_concs):
            raise ValueError(
                "Number of salt concentrations does not match list length: "
                f"field={num_salt_from_field}, list={len(salt_concs)}"
            )

        num_ligand = num_ligand_from_field
        num_salt = num_salt_from_field

        cal_slope = _safe_float(meta["cal_slope_raw"], "Calibration Curve Slope")
        cal_intercept = _safe_float(
            meta["cal_intercept_raw"], "Calibration Curve Intercept")
        resin_mass = _safe_float(meta["resin_mass_raw"], "Resin Mass")
        total_volume = _safe_float(meta["total_volume_raw"], "Total volume")

        if cal_slope == 0:
            raise ValueError("Calibration curve slope is zero, cannot invert calibration")

        analysis_results["metadata"].update(
            {
                "ligand_concs": ligand_concs,
                "salt_concs": salt_concs,
                "replicates": replicates,
                "calibration_slope": cal_slope,
                "calibration_intercept": cal_intercept,
                "resin_mass_mg": resin_mass,
                "total_volume_uL": total_volume,
                "ligand_unit": meta.get("ligand_unit"),
                "salt_unit": meta.get("salt_unit"),
            }
        )

        # Fetch Tecan data (single-file pattern)
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
            tecan_data_path_str = fetch_tecan_data_file(
                experiment_id, str(results_folder_path)
            )
            tecan_data_path = Path(tecan_data_path_str)
        except Exception as device_server_error:
            print(
                f"WARNING: Device control server access failed: {device_server_error}. "
                "Falling back to local file search..."
            )
            tecan_raw_path = (
                "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"
            )

            if not Path(tecan_raw_path).exists():
                msg = (
                    "Tecan data not available - analysis skipped. "
                    "No data found on server or local system."
                )
                analysis_results["message"] = msg
                analysis_results["status"] = "success"
                analysis_results["warnings"].append(msg)
                print(f"INFO: {msg}")
                # Save results JSON even if skipped
                results_json_file = (
                    f"analysis_results_{analysis_results['experiment_id']}.json"
                )
                results_json_path = results_folder_path / results_json_file
                with open(results_json_path, "w") as f:
                    json.dump(analysis_results, f, indent=4)
                print(
                    f"INFO: Saved analysis results JSON to: {results_json_path}"
                )
                return analysis_results

            most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
            if not most_recent_excel_source:
                msg = "No recent Tecan Excel file found - analysis skipped."
                analysis_results["message"] = msg
                analysis_results["status"] = "success"
                analysis_results["warnings"].append(msg)
                print(f"INFO: {msg}")
                results_json_file = (
                    f"analysis_results_{analysis_results['experiment_id']}.json"
                )
                results_json_path = results_folder_path / results_json_file
                with open(results_json_path, "w") as f:
                    json.dump(analysis_results, f, indent=4)
                print(
                    f"INFO: Saved analysis results JSON to: {results_json_path}"
                )
                return analysis_results

            tecan_data_path = (
                results_folder_path / f"tecan_data_{experiment_id}.xlsx"
            )
            shutil.copy(most_recent_excel_source, tecan_data_path)
            print(
                f"INFO: Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'"
            )

        analysis_results["data_outputs"][
            "tecan_raw_excel"
        ] = str(tecan_data_path.resolve())
        analysis_results["files_processed"] += 1

        # Extract plate data
        plate_df = _extract_plate_absorbance(tecan_data_path)

        # Map wells to experimental conditions
        well_map = _build_well_mapping(num_ligand, num_salt, replicates)

        # Join plate data with mapping; drop wells not in mapping
        map_df = (
            pd.DataFrame.from_dict(well_map, orient="index")
            .reset_index()
            .rename(columns={"index": "well"})
        )
        merged = plate_df.merge(map_df, on="well", how="inner")

        # Add ligand and salt concentrations
        merged["c0"] = merged["ligand_index"].apply(lambda i: ligand_concs[i])
        merged["salt"] = merged["salt_index"].apply(lambda i: salt_concs[i])

        # Convert absorbance to equilibrium concentration cE
        merged["cE"] = (merged["absorbance"] - cal_intercept) / cal_slope

        # Sanity checks on cE
        max_c0 = max(ligand_concs)
        if (merged["cE"] < -1e-9).any() or (merged["cE"] > max_c0 + 1e-9).any():
            raise ValueError(
                "Calibration sanity check failed: some cE values lie outside [0, max(c0)]. "
                "Check that the calibration curve has been specified correctly."
            )

        # Check that cE varies strongly across each column as c0 varies
        variation_problems = []
        for col in sorted(merged["column"].unique()):
            sub = merged[merged["column"] == col]
            if sub["c0"].nunique() <= 1:
                continue
            c0_range = sub["c0"].max() - sub["c0"].min()
            ce_range = sub["cE"].max() - sub["cE"].min()
            if c0_range > 0 and ce_range < 0.05 * c0_range:
                variation_problems.append(int(col))
        if variation_problems:
            raise ValueError(
                "Calibration sanity check failed: equilibrium concentrations cE "
                "do not vary sufficiently with initial concentrations c0 in "
                f"columns {variation_problems}. This suggests the calibration "
                "may have been applied in the wrong direction."
            )

        # Aggregate replicates: mean and std of cE for each (ligand_index, salt_index)
        group_cols = ["ligand_index", "salt_index"]
        agg = (
            merged.groupby(group_cols)
            .agg(
                cE_mean=("cE", "mean"),
                cE_std=("cE", "std"),
                absorbance_mean=("absorbance", "mean"),
                absorbance_std=("absorbance", "std"),
                n_reps=("cE", "size"),
            )
            .reset_index()
        )

        expected_groups = num_ligand * num_salt
        if agg.shape[0] != expected_groups:
            raise ValueError(
                "Well-to-condition mapping appears inconsistent: "
                f"expected {expected_groups} groups (num_ligand * num_salt), "
                f"but found {agg.shape[0]}. Check plate layout and metadata."
            )

        if (agg["n_reps"] != replicates).any():
            raise ValueError(
                "Replicate count mismatch: at least one condition does not have "
                f"exactly {replicates} wells."
            )

        # Attach physical concentrations
        agg["c0"] = agg["ligand_index"].apply(lambda i: ligand_concs[i])
        agg["salt"] = agg["salt_index"].apply(lambda i: salt_concs[i])

        # Calculate loading q for each parameter combination
        # q = (c0 - cE_mean) * v_total / m_resin
        agg["q"] = (agg["c0"] - agg["cE_mean"]) * total_volume / resin_mass

        # Save per-well and aggregated data
        wells_csv = (
            results_folder_path
            / f"tecan_loading_per_well_{experiment_id}.csv"
        )
        merged.to_csv(wells_csv, index=False)
        analysis_results["data_outputs"][
            "per_well_data_csv"
        ] = str(wells_csv.resolve())

        agg_csv = (
            results_folder_path
            / f"tecan_loading_aggregated_{experiment_id}.csv"
        )
        agg.to_csv(agg_csv, index=False)
        analysis_results["data_outputs"][
            "aggregated_data_csv"
        ] = str(agg_csv.resolve())

        # Fit Langmuir isotherms per salt concentration and plot
        fig, ax = plt.subplots(figsize=(8, 6))

        fit_results: List[Dict[str, Any]] = []
        colors = plt.cm.viridis(
            np.linspace(0, 1, num_salt if num_salt > 1 else 2)
        )

        for idx, salt_value in enumerate(sorted(salt_concs)):
            sub = agg[agg["salt"] == salt_value]
            if sub.empty:
                continue

            c_e = sub["cE_mean"].values
            q_vals = sub["q"].values

            # Sort by c_e for plotting
            order = np.argsort(c_e)
            c_e_sorted = c_e[order]
            q_sorted = q_vals[order]

            color = colors[idx % len(colors)]
            ax.errorbar(
                c_e_sorted,
                q_sorted,
                yerr=sub["q"].values[order] * 0.0,
                fmt="o",
                color=color,
                label=f"Salt {salt_value} {meta.get('salt_unit', '')} (data)",
            )

            # Perform Langmuir fit
            try:
                q_max, K, r2 = fit_langmuir(c_e_sorted, q_sorted)
                c_fit = np.linspace(0, max(c_e_sorted) * 1.05, 200)
                q_fit = langmuir_isotherm(c_fit, q_max, K)
                ax.plot(
                    c_fit,
                    q_fit,
                    "-",
                    color=color,
                    label=(
                        f"Salt {salt_value} {meta.get('salt_unit', '')} "
                        f"fit (qmax={q_max:.3g}, K={K:.3g}, R2={r2:.3f})"
                    ),
                )
                fit_results.append(
                    {
                        "salt": float(salt_value),
                        "q_max": float(q_max),
                        "K": float(K),
                        "R2": float(r2),
                    }
                )
            except ValueError as e:
                warning_msg = (
                    f"Langmuir fit failed for salt {salt_value}: {e}"
                )
                print(f"WARNING: {warning_msg}")
                analysis_results["warnings"].append(warning_msg)

        ligand_unit = meta.get("ligand_unit") or "conc."
        ax.set_xlabel(f"Equilibrium ligand concentration cE ({ligand_unit})")
        ax.set_ylabel(
            f"Loading q ({ligand_unit} * uL per mg resin)"
        )
        ax.set_title(
            "Loading isotherms from Tecan plate reader (Langmuir fits)"
        )
        ax.legend(fontsize=8)
        ax.grid(True, which="both", ls=":", alpha=0.5)
        fig.tight_layout()

        plot_path_png = (
            results_folder_path
            / f"loading_isotherms_{experiment_id}.png"
        )
        fig.savefig(plot_path_png, dpi=300)
        analysis_results["plots"]["isotherms_png"] = str(
            plot_path_png.resolve()
        )

        plot_path_pdf = (
            results_folder_path
            / f"loading_isotherms_{experiment_id}.pdf"
        )
        fig.savefig(plot_path_pdf)
        analysis_results["plots"]["isotherms_pdf"] = str(
            plot_path_pdf.resolve()
        )
        plt.close(fig)

        # Store fit summary
        fit_df = pd.DataFrame(fit_results)
        if not fit_df.empty:
            fit_csv = (
                results_folder_path
                / f"langmuir_fits_{experiment_id}.csv"
            )
            fit_df.to_csv(fit_csv, index=False)
            analysis_results["data_outputs"][
                "langmuir_fits_csv"
            ] = str(fit_csv.resolve())

        analysis_results["status"] = "success"
        analysis_results["message"] = (
            "Analysis completed successfully. "
            "Isotherms and Langmuir fits generated."
        )

    except Exception as e:
        analysis_results["status"] = "failed"
        analysis_results["message"] = str(e)
        print(f"ERROR: Analysis failed: {e}")

    # Save the analysis results as JSON
    exp_id_for_file = analysis_results.get("experiment_id") or "unknown"
    results_json_file = f"analysis_results_{exp_id_for_file}.json"
    results_json_path = results_folder_path / results_json_file
    try:
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
    except Exception as e:
        print(f"ERROR: Failed to save analysis results JSON: {e}")

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
                f"ERROR: Analysis failed: {results.get('message', 'Unknown error.')}"
            )
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
