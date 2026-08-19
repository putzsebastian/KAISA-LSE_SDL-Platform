#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

This script loads experiment metadata from an experiment_<ID>.json file (eLabFTW export)
and AKTA chromatography results either from the AKTA control server or from local
result files. It plots the chromatogram (UV 280 nm and conductivity), detects peaks
in the UV signal, computes their retention times, and saves all results to the
results folder.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import requests

# AKTA control server configuration
AKTA_CONTROL_SERVER = os.getenv("AKTA_CONTROL_SERVER", "http://localhost:5001")
AKTA_API_KEY = os.getenv("AKTA_API_KEY", "akta-control-key")


def fetch_akta_results_from_server(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results JSON from AKTA control server.

    Returns a dict with an inner "results" structure as provided by the server,
    or None if not available / server unreachable.
    """
    try:
        headers = {"X-API-Key": AKTA_API_KEY}
        url = f"{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}"
        print(f"INFO: Requesting AKTA results from server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            print("INFO: AKTA results retrieved from server.")
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


def load_local_akta_results(experiment_id: str, results_folder_path: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local files.

    Priority:
      1) JSON results file in results folder: akta_results_<ID>.json
      2) data.csv in current working directory

    Returns a standardized dict with at least keys: time, uv1 (if present), cond (if present).
    """
    akta_json = results_folder_path / f"akta_results_{experiment_id}.json"
    akta_csv = Path("data.csv")

    if akta_json.exists():
        print(f"INFO: Loading AKTA JSON results from {akta_json}")
        try:
            with open(akta_json, "r") as f:
                data = json.load(f)
            return data
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA JSON file: {e}")

    if akta_csv.exists():
        print(f"INFO: Loading AKTA CSV results from {akta_csv}")
        try:
            df = pd.read_csv(akta_csv, na_values=["-"])
            # Convert to list-based dict
            akta_data: Dict[str, Any] = {}
            for col in df.columns:
                # Drop NaNs but keep alignment by simply converting to python list
                series = df[col]
                if series.dtype.kind in "if":
                    akta_data[col] = series.astype(float).tolist()
                else:
                    akta_data[col] = series.tolist()
            return akta_data
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA CSV file: {e}")

    print("INFO: No local AKTA result file found.")
    return None


def standardize_akta_data(raw: Dict[str, Any]) -> Dict[str, np.ndarray]:
    """Standardize AKTA data structure from server or local.

    Expected fields (if present):
      - time: list of floats (seconds)
      - uv1: list of floats (mAU, 280 nm)
      - cond: list of floats (mS/cm)

    Missing values (None) are converted to np.nan. Returns a dict whose values
    are numpy arrays.
    """
    result: Dict[str, np.ndarray] = {}

    # Time is mandatory for plotting; raise if not present or empty
    if "time" not in raw:
        raise ValueError("AKTA data does not contain required 'time' field.")
    time_list = raw.get("time", [])
    if not isinstance(time_list, list) or len(time_list) == 0:
        raise ValueError("AKTA 'time' field is empty or invalid.")

    def _to_float_array(values: List[Any]) -> np.ndarray:
        arr = []
        for v in values:
            if v is None:
                arr.append(np.nan)
            else:
                try:
                    arr.append(float(v))
                except Exception:
                    arr.append(np.nan)
        return np.array(arr, dtype=float)

    result["time"] = _to_float_array(time_list)

    if "uv1" in raw:
        result["uv1"] = _to_float_array(raw.get("uv1", []))
    if "cond" in raw:
        result["cond"] = _to_float_array(raw.get("cond", []))

    # Ensure all arrays have the same length as time by truncation
    n = result["time"].shape[0]
    for key in list(result.keys()):
        if key == "time":
            continue
        if result[key].shape[0] > n:
            result[key] = result[key][:n]
        elif result[key].shape[0] < n:
            # pad with nan
            pad = np.full(n - result[key].shape[0], np.nan)
            result[key] = np.concatenate([result[key], pad])

    return result


def detect_peaks(time: np.ndarray, signal: np.ndarray, height_factor: float = 0.2, min_distance_points: int = 5) -> List[Dict[str, Any]]:
    """Simple peak detection on a 1D signal.

    Args:
        time: 1D numpy array of time points (seconds).
        signal: 1D numpy array of signal values (e.g. UV).
        height_factor: Fraction of (max - baseline) used as minimum height above baseline.
        min_distance_points: Minimum index distance between neighboring peaks.

    Returns:
        List of dicts: [{"index": int, "time": float, "height": float}, ...]
    """
    if time.size == 0 or signal.size == 0:
        return []

    # Remove NaNs for threshold calculation but keep indices
    valid = np.isfinite(signal)
    if not np.any(valid):
        return []

    baseline = np.nanpercentile(signal[valid], 5)
    max_val = np.nanmax(signal[valid])
    amplitude = max_val - baseline
    if amplitude <= 0:
        return []

    threshold = baseline + height_factor * amplitude

    peaks: List[int] = []
    # Internal points only
    for i in range(1, len(signal) - 1):
        if not np.isfinite(signal[i]):
            continue
        if signal[i] >= threshold and signal[i] >= signal[i - 1] and signal[i] >= signal[i + 1]:
            # Check distance to previous accepted peak
            if peaks and (i - peaks[-1]) < min_distance_points:
                # keep the higher one
                if signal[i] > signal[peaks[-1]]:
                    peaks[-1] = i
            else:
                peaks.append(i)

    peak_info: List[Dict[str, Any]] = []
    for idx in peaks:
        peak_info.append({
            "index": int(idx),
            "time": float(time[idx]),
            "height": float(signal[idx]),
        })
    return peak_info


def plot_chromatogram(
    experiment_id: str,
    akta_data: Dict[str, np.ndarray],
    peaks: List[Dict[str, Any]],
    results_folder_path: Path,
) -> str:
    """Plot chromatogram with UV 280 nm and conductivity.

    If peaks list is not empty, annotate them with retention times.

    Returns path to saved PNG file.
    """
    time_sec = akta_data["time"]
    time_min = time_sec / 60.0

    uv = akta_data.get("uv1", None)
    cond = akta_data.get("cond", None)

    fig, ax1 = plt.subplots(figsize=(10, 5))

    if uv is not None:
        ax1.plot(time_min, uv, color="blue", linewidth=0.9, label="UV 280 nm")
        ax1.set_ylabel("UV 280 nm (mAU)", color="blue")
        ax1.tick_params(axis="y", labelcolor="blue")
    else:
        ax1.set_ylabel("Signal")

    ax1.set_xlabel("Time (min)")

    ax2 = None
    if cond is not None:
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond, color="red", linewidth=0.9, alpha=0.7, label="Conductivity")
        ax2.set_ylabel("Conductivity (mS/cm)", color="red")
        ax2.tick_params(axis="y", labelcolor="red")

    ax1.set_title(f"AKTA Chromatogram - Experiment {experiment_id}")

    # Annotate peaks on UV trace if any
    if uv is not None and peaks:
        for peak in peaks:
            t_min = peak["time"] / 60.0
            h = peak["height"]
            ax1.axvline(x=t_min, color="gray", linestyle="--", linewidth=0.6)
            ax1.annotate(
                f"{t_min:.2f} min",
                xy=(t_min, h),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                rotation=90,
                color="black",
            )

    fig.tight_layout()

    plot_path = results_folder_path / f"chromatogram_{experiment_id}.png"
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    print(f"INFO: Chromatogram plot saved to: {plot_path}")
    return str(plot_path)


def extract_metadata_fields(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract selected eLabFTW extra_fields with robust error handling.

    Missing fields are reported but do not cause the overall analysis to fail.
    """
    meta_out: Dict[str, Any] = {}

    extra_fields = (
        experiment_data.get("metadata_decoded", {})
        .get("extra_fields", {})
    )

    expected_fields = [
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

    for key in expected_fields:
        field = extra_fields.get(key)
        if isinstance(field, dict) and "value" in field:
            meta_out[key] = field["value"]
        else:
            print(f"WARNING: Missing expected metadata field: {key}")

    return meta_out


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = "../data", results_folder: str = "../results") -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography data.

    Args:
        experiment_id (str): Experiment ID
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
    }

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        # Try CLI argument first (if called directly without using main())
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Using experiment_id from sys.argv: {experiment_id}")
        else:
            # Auto-detect most recent experiment_*.json in data_folder
            data_folder_path = Path(data_folder)
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
            name = latest.stem  # experiment_<ID>
            if "_" in name:
                experiment_id = name.split("_", 1)[1]
            else:
                experiment_id = name
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Auto-detected most recent experiment ID: {experiment_id}")

    # Ensure experiment_id is now defined
    if not experiment_id:
        raise ValueError("experiment_id could not be determined.")

    data_folder_path = Path(data_folder)
    # Preferred location: root folder ../experiment_<ID>.json
    data_file_path = Path("../experiment_" + str(experiment_id) + ".json")
    if not data_file_path.exists():
        # Fallback to data folder
        data_file_path = data_folder_path / f"experiment_{experiment_id}.json"

    print(f"INFO: Loading experiment data from: {data_file_path}")

    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
        analysis_results["files_processed"] += 1
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # Extract metadata fields (non-fatal if some are missing)
    metadata_extracted = extract_metadata_fields(experiment_data)
    analysis_results["metadata"].update(metadata_extracted)

    # Fetch AKTA results from device server first
    print(f"INFO: Fetching AKTA data for experiment {experiment_id} from device control server...")
    akta_raw = fetch_akta_results_from_server(str(experiment_id))

    if akta_raw is None:
        # Fallback to local files
        print("INFO: Falling back to local AKTA result files...")
        akta_raw = load_local_akta_results(str(experiment_id), results_folder_path)

    if akta_raw is None:
        analysis_results["status"] = "success"
        analysis_results["message"] = (
            "AKTA data not available - analysis skipped. This is expected for test runs "
            "or experiments without AKTA output."
        )
        print("INFO: " + analysis_results["message"])
    else:
        # Standardize AKTA data
        try:
            akta_std = standardize_akta_data(akta_raw)
        except Exception as e:
            raise ValueError(f"Failed to standardize AKTA data: {e}")

        # Save standardized data as CSV
        df_out = pd.DataFrame({k: v for k, v in akta_std.items()})
        csv_path = results_folder_path / f"akta_processed_{experiment_id}.csv"
        df_out.to_csv(csv_path, index=False)
        analysis_results["data_outputs"]["akta_processed_csv"] = str(csv_path)
        analysis_results["files_processed"] += 1
        print(f"INFO: Processed AKTA data saved to: {csv_path}")

        # Peak detection on UV trace, if available
        uv_signal = akta_std.get("uv1")
        peaks: List[Dict[str, Any]] = []
        if uv_signal is not None:
            peaks = detect_peaks(akta_std["time"], uv_signal)
            print(f"INFO: Detected {len(peaks)} peaks in UV 280 nm signal.")
        else:
            print("WARNING: No UV 280 nm (uv1) signal found; skipping peak detection.")

        # Compile peak table
        if peaks:
            peaks_df = pd.DataFrame(
                {
                    "peak_index": [p["index"] for p in peaks],
                    "retention_time_s": [p["time"] for p in peaks],
                    "retention_time_min": [p["time"] / 60.0 for p in peaks],
                    "height": [p["height"] for p in peaks],
                }
            )
            peaks_csv_path = results_folder_path / f"akta_peaks_{experiment_id}.csv"
            peaks_df.to_csv(peaks_csv_path, index=False)
            analysis_results["data_outputs"]["akta_peaks_csv"] = str(peaks_csv_path)
            analysis_results["files_processed"] += 1
            analysis_results["metadata"]["num_peaks"] = int(len(peaks))
        else:
            analysis_results["metadata"]["num_peaks"] = 0

        # Plot chromatogram with optional peak labels
        plot_path = plot_chromatogram(str(experiment_id), akta_std, peaks, results_folder_path)
        analysis_results["plots"]["chromatogram_png"] = plot_path

        analysis_results["status"] = "success"
        if peaks:
            analysis_results["message"] = (
                f"AKTA analysis completed. {len(peaks)} peak(s) detected and chromatogram plotted."
            )
        else:
            analysis_results["message"] = (
                "AKTA analysis completed. No peaks detected in UV 280 nm signal; "
                "chromatogram plotted without peak labels."
            )

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, "w") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description="Analyze AKTA chromatography experiment data.")
    parser.add_argument("experiment_id", nargs="?", help="Experiment ID. If omitted, auto-detect most recent.")
    parser.add_argument("--data-folder", default="../data", help="Path to the folder containing experiment_ID.json files. Default: ../data")
    parser.add_argument("--results-folder", default="../results", help="Path to the folder where all analysis outputs will be saved. Default: ../results")

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
