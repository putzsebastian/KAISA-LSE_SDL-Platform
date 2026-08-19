#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

This script loads AKTA Pure chromatography data, plots the chromatogram
(UV 280 nm and conductivity), identifies UV peaks, computes their
retention times, and annotates the plot when peaks are present.

It is designed for programmatic use (CLI and import), with robust
error handling and JSON-structured results.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import find_peaks

import requests


# AKTA control server configuration (can be overridden via environment)
AKTA_CONTROL_SERVER = os.getenv("AKTA_CONTROL_SERVER", "http://localhost:5001")
AKTA_API_KEY = os.getenv("AKTA_API_KEY", "akta-control-key")


def fetch_akta_results_from_server(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results JSON from the control server.

    Returns a dict with keys similar to akta_results.json structure or
    None if not available on server.
    """
    try:
        headers = {"X-API-Key": AKTA_API_KEY}
        url = f"{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}"
        print(f"INFO: Requesting AKTA results from control server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return data.get("results", {})
        else:
            print(
                f"WARNING: AKTA results not found on server for experiment {experiment_id} "
                f"(status {response.status_code})."
            )
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def load_akta_results_local(experiment_id: str, data_folder: Path, results_folder: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local files as fallback.

    Priority:
      1) results/akta_results_<experiment_id>.json
      2) data/akta_results_<experiment_id>.json
      3) data/data.csv (Orbit CSV) converted to AKTA-like dict
    """
    # 1) results folder JSON
    akta_json_results = results_folder / f"akta_results_{experiment_id}.json"
    if akta_json_results.exists():
        print(f"INFO: Loading AKTA results JSON from results folder: {akta_json_results}")
        with open(akta_json_results, "r") as f:
            return json.load(f)

    # 2) data folder JSON
    akta_json_data = data_folder / f"akta_results_{experiment_id}.json"
    if akta_json_data.exists():
        print(f"INFO: Loading AKTA results JSON from data folder: {akta_json_data}")
        with open(akta_json_data, "r") as f:
            return json.load(f)

    # 3) Orbit CSV in data folder
    akta_csv = data_folder / "data.csv"
    if akta_csv.exists():
        print(f"INFO: Loading AKTA CSV from data folder: {akta_csv}")
        try:
            df = pd.read_csv(akta_csv, na_values=["-"])
        except Exception as e:
            print(f"WARNING: Failed to read AKTA CSV file {akta_csv}: {e}")
            return None
        if "time" not in df.columns:
            print("WARNING: AKTA CSV missing required 'time' column.")
            return None
        data_dict: Dict[str, Any] = {}
        data_dict["time"] = df["time"].dropna().tolist()
        signals: List[str] = []
        for col in df.columns:
            if col == "time":
                continue
            signals.append(col)
            data_dict[col] = df[col].where(~df[col].isna(), None).tolist()
        data_dict["signals"] = signals
        # sample_time is approximate if we have at least 2 time points
        if len(data_dict["time"]) >= 2:
            t_arr = np.array(data_dict["time"], dtype=float)
            dt = np.median(np.diff(t_arr))
            data_dict["sample_time"] = float(dt)
        else:
            data_dict["sample_time"] = None
        return data_dict

    print("INFO: No local AKTA results JSON or CSV found.")
    return None


def ensure_results_folder(results_folder: Path) -> None:
    """Ensure that the results folder exists."""
    results_folder.mkdir(parents=True, exist_ok=True)


def detect_uv_peaks(time_s: np.ndarray, uv_signal: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Detect peaks in the UV signal and return indices and peak properties.

    Uses scipy.signal.find_peaks with basic heuristic parameters.
    """
    if time_s.size == 0 or uv_signal.size == 0:
        return np.array([], dtype=int), {}

    # Remove NaN values for peak detection
    valid = np.isfinite(uv_signal)
    if not np.any(valid):
        return np.array([], dtype=int), {}

    uv_valid = uv_signal[valid]
    time_valid = time_s[valid]

    # Basic heuristics for prominence and distance
    if uv_valid.size < 3:
        return np.array([], dtype=int), {}

    signal_range = float(np.nanmax(uv_valid) - np.nanmin(uv_valid))
    if signal_range <= 0:
        return np.array([], dtype=int), {}

    prominence = 0.05 * signal_range
    distance = max(1, int(0.1 * len(uv_valid)))

    peaks_rel, properties = find_peaks(uv_valid, prominence=prominence, distance=distance)
    if peaks_rel.size == 0:
        return np.array([], dtype=int), {}

    # Map back to original indices
    valid_indices = np.nonzero(valid)[0]
    peaks_idx = valid_indices[peaks_rel]

    return peaks_idx, properties


def plot_chromatogram(
    experiment_id: str,
    akta_data: Dict[str, Any],
    results_folder: Path,
    peaks_idx: Optional[np.ndarray] = None,
    time_unit: str = "min",
) -> Dict[str, str]:
    """Plot chromatogram (UV 280 nm and conductivity) and save as PNG/PDF.

    If peaks are provided, annotate them with retention time labels.

    Returns a dict with paths to generated plot files.
    """
    time_raw = np.array(akta_data.get("time", []), dtype=float)
    if time_unit == "min":
        time = time_raw / 60.0
        x_label = "Time (min)"
    else:
        time = time_raw
        x_label = "Time (s)"

    uv_key = "uv1"
    cond_key = "cond"

    uv = np.array([
        (v if v is not None else np.nan) for v in akta_data.get(uv_key, [np.nan] * len(time))
    ], dtype=float)
    cond = None
    if cond_key in akta_data:
        cond = np.array([
            (v if v is not None else np.nan) for v in akta_data.get(cond_key, [np.nan] * len(time))
        ], dtype=float)

    fig, ax1 = plt.subplots(figsize=(10, 5))

    # Plot UV 280 nm
    ax1.plot(time, uv, color="blue", linewidth=0.8, label="UV 280 nm")
    ax1.set_xlabel(x_label)
    ax1.set_ylabel("UV Absorbance (mAU)", color="blue")
    ax1.tick_params(axis="y", labelcolor="blue")

    # Plot conductivity if available
    if cond is not None:
        ax2 = ax1.twinx()
        ax2.plot(time, cond, color="red", linewidth=0.8, alpha=0.7, label="Conductivity")
        ax2.set_ylabel("Conductivity (mS/cm)", color="red")
        ax2.tick_params(axis="y", labelcolor="red")

    title = f"AKTA Chromatogram - Experiment {experiment_id}"
    ax1.set_title(title)

    # Peak annotations
    if peaks_idx is not None and peaks_idx.size > 0:
        for idx in peaks_idx:
            if idx < 0 or idx >= len(time):
                continue
            rt = time[idx]
            y = uv[idx]
            if not np.isfinite(rt) or not np.isfinite(y):
                continue
            label = f"RT = {rt:.2f} {time_unit}"
            ax1.plot(rt, y, "ko", markersize=4)
            ax1.annotate(
                label,
                xy=(rt, y),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                rotation=90,
            )

    fig.tight_layout()

    plot_png = results_folder / f"chromatogram_{experiment_id}.png"
    plot_pdf = results_folder / f"chromatogram_{experiment_id}.pdf"
    fig.savefig(plot_png, dpi=150)
    fig.savefig(plot_pdf)
    plt.close(fig)

    print(f"INFO: Saved chromatogram plot PNG to: {plot_png}")
    print(f"INFO: Saved chromatogram plot PDF to: {plot_pdf}")

    return {"png": str(plot_png.resolve()), "pdf": str(plot_pdf.resolve())}


def save_processed_data(
    experiment_id: str,
    akta_data: Dict[str, Any],
    peaks_idx: Optional[np.ndarray],
    time_unit: str,
    results_folder: Path,
) -> Dict[str, str]:
    """Save processed AKTA data and peak table as CSV files.

    Returns dict of file paths.
    """
    time_raw = np.array(akta_data.get("time", []), dtype=float)
    if time_unit == "min":
        time = time_raw / 60.0
    else:
        time = time_raw

    df = pd.DataFrame({"time_s": time_raw, "time": time})

    for key in akta_data.get("signals", []):
        values = akta_data.get(key, [])
        if len(values) != len(df):
            # Align length by padding with NaN/None
            padded = list(values) + [None] * max(0, len(df) - len(values))
            padded = padded[: len(df)]
        else:
            padded = values
        df[key] = [np.nan if v is None else v for v in padded]

    processed_csv = results_folder / f"akta_processed_{experiment_id}.csv"
    df.to_csv(processed_csv, index=False)
    print(f"INFO: Saved processed AKTA data to: {processed_csv}")

    peak_csv_path = None
    if peaks_idx is not None and peaks_idx.size > 0:
        peak_records: List[Dict[str, Any]] = []
        for i, idx in enumerate(peaks_idx, start=1):
            if idx < 0 or idx >= len(time_raw):
                continue
            rt_s = float(time_raw[idx])
            if time_unit == "min":
                rt_display = rt_s / 60.0
            else:
                rt_display = rt_s
            record = {
                "peak_number": i,
                "index": int(idx),
                "retention_time_s": rt_s,
                f"retention_time_{time_unit}": rt_display,
            }
            # Include UV height if available
            uv_vals = akta_data.get("uv1", None)
            if uv_vals is not None and idx < len(uv_vals):
                try:
                    record["uv_height_mau"] = float(uv_vals[idx])
                except Exception:
                    record["uv_height_mau"] = None
            peak_records.append(record)

        if peak_records:
            df_peaks = pd.DataFrame(peak_records)
            peak_csv = results_folder / f"akta_peaks_{experiment_id}.csv"
            df_peaks.to_csv(peak_csv, index=False)
            print(f"INFO: Saved AKTA peak table to: {peak_csv}")
            peak_csv_path = str(peak_csv.resolve())

    outputs: Dict[str, str] = {"processed_csv": str(processed_csv.resolve())}
    if peak_csv_path is not None:
        outputs["peaks_csv"] = peak_csv_path
    return outputs


def load_experiment_metadata(experiment_id: str, data_folder: Path) -> Dict[str, Any]:
    """Load experiment JSON and return experiment_data dict.

    Tries root-level JSON '../experiment_<id>.json' first, then data_folder.
    """
    # Root-level JSON first
    root_json = Path("../") / f"experiment_{experiment_id}.json"
    if root_json.exists():
        print(f"INFO: Loading experiment JSON from root folder: {root_json}")
        try:
            with open(root_json, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format in {root_json}: {e}")

    # Fallback to data folder
    data_file_path = data_folder / f"experiment_{experiment_id}.json"
    print(f"INFO: Loading experiment JSON from data folder: {data_file_path}")
    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    return experiment_data


def extract_elab_metadata_fields(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract relevant eLabFTW extra fields with error handling.

    Known fields (if present):
      Process ID, CIP_DURATION, WASH_DURATION, CIP_3_DURATION,
      CIP_4_DURATION, CIP_5_DURATION, WASH_FLOW_RATE, CIP_2_HOLD_DURATION,
      LOAD_INJECT_DURATION, LOAD_INJECT_FLOW_RATE, EQUILIBRATION_DURATION,
      ELUTE_GRADIENT_DURATION, EQUILIBRATION_FLOW_RATE,
      ELUTE_GRADIENT_GRADIENT_END, ELUTE_GRADIENT_GRADIENT_START

    Missing fields are logged but do not cause hard failure for AKTA
    chromatogram analysis.
    """
    meta_out: Dict[str, Any] = {}
    try:
        extra_fields = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError:
        print("WARNING: metadata_decoded.extra_fields not found in experiment JSON.")
        return meta_out

    field_names = [
        "Process ID",
        "CIP_DURATION",
        "WASH_DURATION",
        "CIP_3_DURATION",
        "CIP_4_DURATION",
        "CIP_5_DURATION",
        "WASH_FLOW_RATE",
        "CIP_2_HOLD_DURATION",
        "LOAD_INJECT_DURATION",
        "LOAD_INJECT_FLOW_RATE",
        "EQUILIBRATION_DURATION",
        "ELUTE_GRADIENT_DURATION",
        "EQUILIBRATION_FLOW_RATE",
        "ELUTE_GRADIENT_GRADIENT_END",
        "ELUTE_GRADIENT_GRADIENT_START",
    ]

    for name in field_names:
        try:
            value = extra_fields[name]["value"]
            meta_out[name] = value
        except KeyError:
            print(f"WARNING: Missing expected metadata field: {name}")

    return meta_out


def auto_detect_latest_experiment_id(data_folder: Path) -> Optional[str]:
    """Auto-detect the most recent experiment_<id>.json file in data_folder.

    Returns the experiment ID as string or None if not found.
    """
    candidates = sorted(
        data_folder.glob("experiment_*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not candidates:
        return None
    latest = candidates[0]
    name = latest.stem  # experiment_<id>
    try:
        return name.split("_", 1)[1]
    except Exception:
        return None


def analyze_experiment(
    experiment_id: Optional[str] = None,
    data_folder: str = "../data",
    results_folder: str = "../results",
) -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography run.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with all key metrics and file paths
    """
    data_folder_path = Path(data_folder)
    results_folder_path = Path(results_folder)
    ensure_results_folder(results_folder_path)

    analysis_results: Dict[str, Any] = {
        "experiment_id": experiment_id,
        "status": "failed",
        "message": "",
        "plots": {},
        "data_outputs": {},
        "metadata": {},
        "files_processed": 0,
        "peaks": [],
    }

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment_id from command line: {experiment_id}")
        else:
            print("INFO: No experiment_id provided. Attempting auto-detection from data folder...")
            detected_id = auto_detect_latest_experiment_id(data_folder_path)
            if detected_id is None:
                msg = (
                    "Could not auto-detect experiment ID: no experiment_*.json files "
                    f"found in data folder {data_folder_path}"
                )
                analysis_results["message"] = msg
                print(f"ERROR: {msg}")
                return analysis_results
            experiment_id = detected_id
            print(f"INFO: Auto-detected experiment_id: {experiment_id}")

    analysis_results["experiment_id"] = experiment_id

    # Validate data folder
    if not data_folder_path.exists() or not data_folder_path.is_dir():
        msg = f"Data folder does not exist or is not a directory: {data_folder_path}"
        analysis_results["message"] = msg
        print(f"ERROR: {msg}")
        return analysis_results

    # Load experiment metadata JSON (for traceability and extra fields)
    try:
        experiment_data = load_experiment_metadata(experiment_id, data_folder_path)
    except Exception as e:
        msg = f"Failed to load experiment metadata JSON: {e}"
        analysis_results["message"] = msg
        print(f"ERROR: {msg}")
        return analysis_results

    # Extract eLabFTW extra fields (non-fatal if missing)
    elab_meta = extract_elab_metadata_fields(experiment_data)
    analysis_results["metadata"]["elab_extra_fields"] = elab_meta

    # Fetch AKTA results: server first, then local fallback
    print(f"INFO: Fetching AKTA data for experiment {experiment_id} from control server...")
    akta_data = fetch_akta_results_from_server(str(experiment_id))

    if akta_data is None or not akta_data.get("time"):
        print("INFO: Falling back to local AKTA results search...")
        akta_data = load_akta_results_local(str(experiment_id), data_folder_path, results_folder_path)

    if akta_data is None or not akta_data.get("time"):
        msg = (
            "AKTA data not available - analysis skipped. "
            "This is an expected condition for runs without AKTA output."
        )
        analysis_results["status"] = "success"
        analysis_results["message"] = msg
        print(f"INFO: {msg}")
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Ensure mandatory keys exist
    if "signals" not in akta_data:
        # Infer signals from keys
        inferred = [k for k in akta_data.keys() if k not in ("time", "sample_time")] 
        akta_data["signals"] = inferred

    time_array = np.array(akta_data.get("time", []), dtype=float)
    if time_array.size == 0:
        msg = "AKTA data 'time' array is empty - cannot analyze chromatogram."
        analysis_results["message"] = msg
        analysis_results["status"] = "failed"
        print(f"ERROR: {msg}")
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Detect peaks on UV 280 nm (uv1) if available
    uv_signal = np.array(
        [
            (v if v is not None else np.nan)
            for v in akta_data.get("uv1", [np.nan] * len(time_array))
        ],
        dtype=float,
    )

    peaks_idx, peak_props = detect_uv_peaks(time_array, uv_signal)

    # Compute peak retention times in minutes
    peaks_info: List[Dict[str, Any]] = []
    if peaks_idx.size > 0:
        for i, idx in enumerate(peaks_idx, start=1):
            if idx < 0 or idx >= len(time_array):
                continue
            rt_s = float(time_array[idx])
            rt_min = rt_s / 60.0
            height = float(uv_signal[idx]) if np.isfinite(uv_signal[idx]) else None
            peaks_info.append(
                {
                    "peak_number": i,
                    "index": int(idx),
                    "retention_time_s": rt_s,
                    "retention_time_min": rt_min,
                    "uv_height_mau": height,
                }
            )
        print(f"INFO: Detected {len(peaks_info)} UV peak(s) in chromatogram.")
    else:
        print("INFO: No UV peaks detected in chromatogram.")

    analysis_results["peaks"] = peaks_info

    # Plot chromatogram with peak labels (only if peaks present)
    peaks_idx_for_plot = peaks_idx if peaks_idx.size > 0 else None
    plot_paths = plot_chromatogram(
        experiment_id=str(experiment_id),
        akta_data=akta_data,
        results_folder=results_folder_path,
        peaks_idx=peaks_idx_for_plot,
        time_unit="min",
    )
    analysis_results["plots"]["chromatogram"] = plot_paths

    # Save processed data and peak table
    data_outputs = save_processed_data(
        experiment_id=str(experiment_id),
        akta_data=akta_data,
        peaks_idx=peaks_idx,
        time_unit="min",
        results_folder=results_folder_path,
    )
    analysis_results["data_outputs"].update(data_outputs)

    analysis_results["metadata"]["akta_signals"] = akta_data.get("signals", [])
    analysis_results["metadata"]["sample_time_s"] = akta_data.get("sample_time")
    analysis_results["files_processed"] = 1
    analysis_results["status"] = "success"
    analysis_results["message"] = "AKTA chromatogram analysis completed successfully."

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, "w") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description="Analyze AKTA chromatography experiment data")
    parser.add_argument("experiment_id", nargs="?", help="Experiment ID")
    parser.add_argument("--data-folder", default="../data", help="Data folder path")
    parser.add_argument("--results-folder", default="../results", help="Results folder path")

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
            msg = results.get("message", "Unknown error.")
            print(f"ERROR: Analysis failed: {msg}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
