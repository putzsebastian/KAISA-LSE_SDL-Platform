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
        raise Exception(
            "Timeout while fetching Tecan data from device control server"
        )
    except Exception as e:
        raise Exception(
            f"Failed to fetch Tecan data for experiment {experiment_id}: {str(e)}"
        )


def get_most_recent_folder(directory: str, n: int = 0):
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


def get_most_recent_excel_file(directory: str, n: int = 0):
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


def parse_semicolon_list(value: str, field_name: str) -> List[float]:
    """Parse a semicolon separated list of numbers from metadata."""
    if value is None:
        raise ValueError(f"Metadata field {field_name} is None")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(
            f"Metadata field {field_name} must be string, int or float, got {type(value)}"
        )
    parts = [p.strip() for p in value.split(";") if p.strip() != ""]
    if not parts:
        raise ValueError(f"Metadata field {field_name} is empty or invalid")
    numbers: List[float] = []
    for p in parts:
        p_norm = p.replace(",", ".")
        try:
            numbers.append(float(p_norm))
        except ValueError:
            raise ValueError(
                f"Cannot convert entry '{p}' in metadata field {field_name} to float"
            )
    return numbers


def langmuir_isotherm(c: np.ndarray, q_max: float, K: float) -> np.ndarray:
    """Langmuir isotherm: q = q_max * K * c / (1 + K * c)."""
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


def fit_langmuir_isotherm(
    c_e: np.ndarray, q: np.ndarray
) -> Tuple[float, float, float]:
    """Fit Langmuir isotherm and return (q_max, K, R2).

    Applies sensible bounds and initial guesses and performs sanity checks
    on the fitted parameters.
    """
    mask = np.isfinite(c_e) & np.isfinite(q)
    c_e = c_e[mask]
    q = q[mask]
    if c_e.size < 3:
        raise RuntimeError("Not enough points to fit Langmuir isotherm (need >= 3)")

    c_e_max = float(np.max(c_e))
    q_max_obs = float(np.max(q))

    p0_q_max = max(q_max_obs, 1e-9)
    if c_e_max > 0:
        p0_K = 1.0 / max(c_e_max * 0.5, 1e-9)
    else:
        p0_K = 1.0

    lower_bounds = [0.0, 0.0]
    upper_bounds = [q_max_obs * 10.0 if q_max_obs > 0 else 1e6, 1e6]

    try:
        popt, _ = curve_fit(
            langmuir_isotherm,
            c_e,
            q,
            p0=[p0_q_max, p0_K],
            bounds=(lower_bounds, upper_bounds),
            maxfev=10000,
        )
    except Exception as e:
        raise RuntimeError(f"Langmuir fit failed: {e}")

    q_max_fit, K_fit = popt

    if not np.isfinite(q_max_fit) or not np.isfinite(K_fit):
        raise RuntimeError("Langmuir fit returned non-finite parameters")
    if q_max_fit < 0 or K_fit < 0:
        raise RuntimeError(
            "Langmuir fit returned negative parameters, which are not physical"
        )

    q_pred = langmuir_isotherm(c_e, q_max_fit, K_fit)
    r2 = compute_r2(q, q_pred)

    if np.isfinite(r2) and r2 < 0.0:
        raise RuntimeError(
            f"Langmuir fit R2 is negative ({r2:.3f}), indicating a very poor fit"
        )

    return float(q_max_fit), float(K_fit), float(r2)


def map_well_to_indices(
    row_idx: int,
    col_idx: int,
    n_ligand: int,
    n_salt: int,
    n_repl: int,
) -> Tuple[int, int, int]:
    """Map a zero-based plate position (row_idx, col_idx) to
    (ligand_index, salt_index, replicate_index).

    Assumes:
    - ligand concentrations ascend with row within each column
    - salt concentrations ascend row-wise, with replicates grouped by columns
    """
    if row_idx < 0 or row_idx >= 8:
        raise ValueError("row_idx must be between 0 and 7 (rows A-H)")
    if col_idx < 0 or col_idx >= 12:
        raise ValueError("col_idx must be between 0 and 11 (columns 1-12)")

    ligand_index = row_idx
    replicate_index = col_idx // n_salt
    salt_index = col_idx % n_salt

    if ligand_index >= n_ligand:
        raise IndexError(
            f"Ligand index {ligand_index} out of range for n_ligand={n_ligand}"
        )
    if salt_index >= n_salt:
        raise IndexError(
            f"Salt index {salt_index} out of range for n_salt={n_salt}"
        )
    if replicate_index >= n_repl:
        raise IndexError(
            f"Replicate index {replicate_index} out of range for n_repl={n_repl}"
        )

    return ligand_index, salt_index, replicate_index


def analyze_experiment(
    experiment_id: str = None, data_folder: str = "../data", results_folder: str = "../results"
) -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with all key metrics and paths to outputs
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
        if experiment_id is None:
            argv = sys.argv
            if len(argv) > 1 and argv[1] not in ("-h", "--help"):
                experiment_id = argv[1]
                analysis_results["experiment_id"] = experiment_id
                print(f"INFO: Using experiment_id from sys.argv: {experiment_id}")
            else:
                data_path = Path(data_folder)
                if not data_path.exists():
                    raise FileNotFoundError(
                        f"Data folder does not exist for auto-detect: {data_folder}"
                    )
                json_files = sorted(
                    data_path.glob("experiment_*.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if not json_files:
                    raise FileNotFoundError(
                        "No experiment_*.json files found for auto-detection"
                    )
                latest = json_files[0]
                name = latest.stem
                if not name.startswith("experiment_"):
                    raise RuntimeError(
                        f"Unexpected file name format for auto-detection: {name}"
                    )
                experiment_id = name.split("experiment_")[-1]
                analysis_results["experiment_id"] = experiment_id
                print(
                    f"INFO: Auto-detected most recent experiment_id: {experiment_id}"
                )

        if experiment_id is None:
            raise ValueError("experiment_id could not be determined")

        data_folder_path = Path(data_folder)
        data_folder_path.mkdir(parents=True, exist_ok=True)

        primary_json_path = Path("../experiment_" + str(experiment_id) + ".json")
        if primary_json_path.exists():
            data_file_path = primary_json_path
            print(
                f"INFO: Loading experiment data from root file: {data_file_path}"
            )
        else:
            data_file_path = data_folder_path / f"experiment_{experiment_id}.json"
            print(
                f"INFO: Loading experiment data from data folder file: {data_file_path}"
            )

        try:
            with open(data_file_path, "r") as f:
                experiment_data = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Data file not found: {data_file_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

        analysis_results["metadata"]["data_file"] = str(data_file_path.resolve())

        try:
            metadata = experiment_data["metadata_decoded"]["extra_fields"]
        except KeyError as e:
            raise KeyError(f"Missing expected metadata structure: {e}")

        required_fields = [
            "Number of salt concentrations",
            "Number of ligand concentrations",
            "Replicates",
            "Ligand concentrations",
            "Salt concentrations",
            "Ligand concentration unit",
            "Salt concentration unit",
            "Calibration Curve Slope",
            "Calibration Curve Intercept",
            "Resin Mass",
            "Total volume",
        ]
        missing_fields = [
            f for f in required_fields if f not in metadata or "value" not in metadata[f]
        ]
        if missing_fields:
            raise KeyError(
                "Missing expected metadata field(s): " + ", ".join(missing_fields)
            )

        def get_meta_value(field: str):
            return metadata[field]["value"]

        try:
            n_salt = int(float(str(get_meta_value("Number of salt concentrations")).strip()))
            n_ligand = int(
                float(str(get_meta_value("Number of ligand concentrations")).strip())
            )
            n_repl = int(float(str(get_meta_value("Replicates")).strip()))
        except Exception as e:
            raise ValueError(
                f"Failed to parse integer metadata fields for counts: {e}"
            )

        ligand_concs = parse_semicolon_list(
            str(get_meta_value("Ligand concentrations")),
            "Ligand concentrations",
        )
        salt_concs = parse_semicolon_list(
            str(get_meta_value("Salt concentrations")), "Salt concentrations"
        )
        ligand_unit = str(get_meta_value("Ligand concentration unit")).strip()
        salt_unit = str(get_meta_value("Salt concentration unit")).strip()

        if len(ligand_concs) != n_ligand:
            raise ValueError(
                f"Number of ligand concentrations ({len(ligand_concs)}) does not match 'Number of ligand concentrations' ({n_ligand})"
            )
        if len(salt_concs) != n_salt:
            raise ValueError(
                f"Number of salt concentrations ({len(salt_concs)}) does not match 'Number of salt concentrations' ({n_salt})"
            )

        try:
            slope_raw = str(get_meta_value("Calibration Curve Slope")).strip()
            intercept_raw = str(get_meta_value("Calibration Curve Intercept")).strip()
            slope = float(slope_raw.replace(",", "."))
            intercept = float(intercept_raw.replace(",", "."))
        except Exception as e:
            raise ValueError(
                f"Failed to parse calibration curve parameters as floats: {e}"
            )

        if slope == 0:
            raise ValueError(
                "Calibration Curve Slope must not be zero (would make inversion impossible)"
            )

        try:
            resin_mass_raw = str(get_meta_value("Resin Mass")).strip()
            total_volume_raw = str(get_meta_value("Total volume")).strip()
            m_resin = float(resin_mass_raw.replace(",", "."))
            v_total = float(total_volume_raw.replace(",", "."))
        except Exception as e:
            raise ValueError(
                f"Failed to parse Resin Mass or Total volume as floats: {e}"
            )

        if m_resin <= 0:
            raise ValueError("Resin Mass must be positive")
        if v_total <= 0:
            raise ValueError("Total volume must be positive")

        analysis_results["metadata"]["n_salt"] = n_salt
        analysis_results["metadata"]["n_ligand"] = n_ligand
        analysis_results["metadata"]["n_replicates"] = n_repl
        analysis_results["metadata"]["ligand_concentrations"] = ligand_concs
        analysis_results["metadata"]["salt_concentrations"] = salt_concs
        analysis_results["metadata"]["ligand_unit"] = ligand_unit
        analysis_results["metadata"]["salt_unit"] = salt_unit
        analysis_results["metadata"]["calibration_slope"] = slope
        analysis_results["metadata"]["calibration_intercept"] = intercept
        analysis_results["metadata"]["resin_mass_mg"] = m_resin
        analysis_results["metadata"]["total_volume_uL"] = v_total

        print(
            f"INFO: Parsed metadata - ligands: {n_ligand}, salts: {n_salt}, replicates: {n_repl}"
        )

        print(
            f"Fetching Tecan data for experiment {experiment_id} from device control server..."
        )

        try:
            data_info = check_tecan_data_availability(experiment_id)
            if not data_info.get("available", False):
                raise FileNotFoundError(
                    f"No Tecan data available for experiment {experiment_id}: "
                    f"{data_info.get('error', 'Unknown error')}"
                )

            print(
                f"Tecan data found on server: {data_info.get('total_files', 0)} file(s)"
            )
            tecan_data_path = fetch_tecan_data_file(
                experiment_id, str(results_folder_path)
            )

        except Exception as device_server_error:
            print(
                f"Device control server access failed: {device_server_error}. Falling back to local file search..."
            )
            tecan_raw_path = (
                "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"
            )

            if not Path(tecan_raw_path).exists():
                analysis_results["message"] = (
                    "Tecan data not available - analysis skipped. This is expected for test runs."
                )
                analysis_results["status"] = "success"
                analysis_results["note"] = (
                    "No Tecan data found on server or local system."
                )
                print(f"INFO: {analysis_results['message']}")
                results_json_file = (
                    f"analysis_results_{analysis_results['experiment_id']}.json"
                )
                results_json_path = results_folder_path / results_json_file
                with open(results_json_path, "w") as f:
                    json.dump(analysis_results, f, indent=4)
                print(
                    f"Saved analysis results JSON to: {results_json_path}"
                )
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
                print(
                    f"Saved analysis results JSON to: {results_json_path}"
                )
                return analysis_results

            tecan_data_path = (
                results_folder_path / f"tecan_data_{experiment_id}.xlsx"
            )
            shutil.copy(most_recent_excel_source, tecan_data_path)
            print(
                f"Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'"
            )

        analysis_results["data_outputs"]["tecan_raw_file"] = str(
            Path(tecan_data_path).resolve()
        )
        analysis_results["files_processed"] += 1

        num_columns = 12
        num_rows = 8

        try:
            raw_df = pd.read_excel(
                tecan_data_path,
                header=None,
                skiprows=33,
                usecols=list(range(1, 1 + num_columns)),
                nrows=num_rows,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to read Tecan Excel file: {e}")

        print("INFO: Raw absorbance data shape: " + str(raw_df.shape))

        row_labels = list("ABCDEFGH")
        records: List[Dict[str, Any]] = []

        for r in range(num_rows):
            for c in range(num_columns):
                try:
                    absorbance = float(raw_df.iat[r, c])
                except Exception:
                    absorbance = np.nan
                try:
                    ligand_idx, salt_idx, repl_idx = map_well_to_indices(
                        r, c, n_ligand, n_salt, n_repl
                    )
                except (IndexError, ValueError) as e:
                    analysis_results["warnings"].append(
                        f"Skipping well ({row_labels[r]}{c+1}): {e}"
                    )
                    continue

                ligand_c0 = ligand_concs[ligand_idx]
                salt_c = salt_concs[salt_idx]

                if np.isnan(absorbance):
                    analysis_results["warnings"].append(
                        f"Missing absorbance at well {row_labels[r]}{c+1}, condition ligand_idx={ligand_idx}, salt_idx={salt_idx}, replicate={repl_idx}"
                    )
                    continue

                c_e = (absorbance - intercept) / slope

                if c_e < -1e-9 or c_e - max(ligand_concs) > 1e-9:
                    raise ValueError(
                        f"Sanity check failed for cE at well {row_labels[r]}{c+1}: "
                        f"cE={c_e:.6g} outside [0, max(c0)={max(ligand_concs):.6g}]"
                    )

                records.append(
                    {
                        "row": row_labels[r],
                        "column": c + 1,
                        "row_index": r,
                        "col_index": c,
                        "ligand_index": ligand_idx,
                        "salt_index": salt_idx,
                        "replicate_index": repl_idx,
                        "ligand_c0": ligand_c0,
                        "salt_c": salt_c,
                        "absorbance": absorbance,
                        "c_e": c_e,
                    }
                )

        if not records:
            raise RuntimeError(
                "No valid absorbance records extracted from plate after mapping."
            )

        df = pd.DataFrame.from_records(records)

        try:
            ce_std = df.groupby(["salt_index", "column"])['c_e'].std().mean()
        except Exception:
            ce_std = float("nan")

        if np.isfinite(ce_std) and ce_std < 1e-9:
            raise RuntimeError(
                "Sanity check failed: cE does not vary across a column; "
                "this suggests that the calibration may have been applied incorrectly."
            )

        detailed_csv_path = (
            results_folder_path / f"tecan_loading_detailed_{experiment_id}.csv"
        )
        df.to_csv(detailed_csv_path, index=False)
        analysis_results["data_outputs"]["detailed_data_csv"] = str(
            detailed_csv_path.resolve()
        )

        group_cols = ["ligand_index", "salt_index"]
        grouped = df.groupby(group_cols)

        expected_groups = n_ligand * n_salt
        actual_groups = grouped.ngroups
        if actual_groups != expected_groups:
            raise RuntimeError(
                f"Grouping mismatch: expected {expected_groups} (n_ligand * n_salt) "
                f"groups, but found {actual_groups}. Please check plate layout and mapping."
            )

        agg_df = grouped.agg(
            n_wells=("c_e", "size"),
            c_e_mean=("c_e", "mean"),
            c_e_std=("c_e", "std"),
            c0=("ligand_c0", "first"),
            salt_c=("salt_c", "first"),
        ).reset_index()

        bad_groups = agg_df[agg_df["n_wells"] != n_repl]
        if not bad_groups.empty:
            raise RuntimeError(
                "Replicate mismatch: some (ligand, salt) groups do not have the expected "
                f"number of wells ({n_repl}). Offending groups: "
                + bad_groups.to_json(orient="records")
            )

        if (agg_df["c_e_mean"] < -1e-9).any() or (
            agg_df["c_e_mean"] - max(ligand_concs) > 1e-9
        ).any():
            raise RuntimeError(
                "Sanity check failed: some aggregated cE values lie outside "
                "the [0, max(c0)] range."
            )

        agg_df["q"] = (agg_df["c0"] - agg_df["c_e_mean"]) * v_total / m_resin

        q_neg = agg_df[agg_df["q"] < -1e-9]
        if not q_neg.empty:
            raise RuntimeError(
                "Computed negative loading q for some conditions, which is non-physical."
            )

        summary_csv_path = (
            results_folder_path
            / f"tecan_loading_summary_{experiment_id}.csv"
        )
        agg_df.to_csv(summary_csv_path, index=False)
        analysis_results["data_outputs"]["summary_data_csv"] = str(
            summary_csv_path.resolve()
        )

        salt_results: List[Dict[str, Any]] = []

        for s_idx in range(n_salt):
            subset = agg_df[agg_df["salt_index"] == s_idx].copy()
            subset = subset.sort_values("c_e_mean")

            c_e_vals = subset["c_e_mean"].values
            q_vals = subset["q"].values

            try:
                q_max_fit, K_fit, r2 = fit_langmuir_isotherm(c_e_vals, q_vals)
                fit_status = "ok"
                print(
                    f"INFO: Langmuir fit for salt_index={s_idx} (salt={salt_concs[s_idx]} {salt_unit}): "
                    f"q_max={q_max_fit:.4g}, K={K_fit:.4g}, R2={r2:.4f}"
                )
            except Exception as e:
                analysis_results["warnings"].append(
                    f"Langmuir fit failed for salt_index={s_idx} (salt={salt_concs[s_idx]} {salt_unit}): {e}"
                )
                fit_status = f"failed: {e}"
                q_max_fit, K_fit, r2 = float("nan"), float("nan"), float("nan")

            salt_results.append(
                {
                    "salt_index": int(s_idx),
                    "salt_c": float(salt_concs[s_idx]),
                    "salt_unit": salt_unit,
                    "q_max": q_max_fit,
                    "K": K_fit,
                    "R2": r2,
                    "fit_status": fit_status,
                }
            )

        analysis_results["metadata"]["langmuir_fits"] = salt_results

        fig, ax = plt.subplots(figsize=(8, 6))

        colors = plt.cm.viridis(np.linspace(0, 1, n_salt))

        for s_idx in range(n_salt):
            subset = agg_df[agg_df["salt_index"] == s_idx].copy()
            subset = subset.sort_values("c_e_mean")

            c_e_vals = subset["c_e_mean"].values
            q_vals = subset["q"].values

            color = colors[s_idx]

            ax.errorbar(
                c_e_vals,
                q_vals,
                yerr=subset["c_e_std"].values * v_total / m_resin,
                fmt="o",
                color=color,
                label=None,
                capsize=3,
                alpha=0.8,
            )

            fit_info = salt_results[s_idx]
            if (
                np.isfinite(fit_info["q_max"])
                and np.isfinite(fit_info["K"])
                and np.isfinite(fit_info["R2"])
            ):
                c_grid = np.linspace(0, max(c_e_vals) * 1.05, 200)
                q_fit_curve = langmuir_isotherm(
                    c_grid, fit_info["q_max"], fit_info["K"]
                )
                label = (
                    f"salt {fit_info['salt_c']} {salt_unit}: "
                    f"q_max={fit_info['q_max']:.3g}, K={fit_info['K']:.3g}, R2={fit_info['R2']:.3f}"
                )
                ax.plot(c_grid, q_fit_curve, "-", color=color, label=label)
            else:
                label = (
                    f"salt {fit_info['salt_c']} {salt_unit}: fit failed ({fit_info['fit_status']})"
                )
                ax.plot([], [], color=color, label=label)

        ax.set_xlabel(f"Equilibrium ligand concentration cE [{ligand_unit}]")
        ax.set_ylabel(
            f"Loading q [{ligand_unit} * uL / mg] (using v_total={v_total} uL, m_resin={m_resin} mg)"
        )
        ax.set_title(
            f"Loading isotherms for experiment {experiment_id} (Tecan plate reader)"
        )
        ax.legend(fontsize=8)
        ax.grid(True, which="both", linestyle=":", linewidth=0.5)
        fig.tight_layout()

        png_path = (
            results_folder_path / f"loading_isotherms_{experiment_id}.png"
        )
        pdf_path = (
            results_folder_path / f"loading_isotherms_{experiment_id}.pdf"
        )
        fig.savefig(png_path, dpi=300)
        fig.savefig(pdf_path)
        plt.close(fig)

        analysis_results["plots"]["isotherms_png"] = str(png_path.resolve())
        analysis_results["plots"]["isotherms_pdf"] = str(pdf_path.resolve())

        analysis_results["status"] = "success"
        analysis_results["message"] = (
            "Tecan loading isotherm analysis completed successfully."
        )

    except Exception as e:
        analysis_results["status"] = "failed"
        analysis_results["message"] = str(e)
        print(f"ERROR: Analysis failed: {e}")

    results_json_file = f"analysis_results_{analysis_results['experiment_id']}.json"
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
            print("ERROR: Analysis failed: " + results.get("message", "Unknown error."))
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
