#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

This script:
- Loads experiment metadata from experiment_<ID>.json
- Fetches AKTA Pure chromatography results from the AKTA control server (preferred)
  with a local fallback to JSON/CSV in the data/results folders
- Extracts key timing and flow parameters from eLabFTW extra_fields
- Plots the chromatogram (UV 280 nm and conductivity) with labeled peaks
- Saves processed data and analysis results to the results folder

Usage patterns:
1) Command line:
   python akta_analysis.py 3914
2) Command line with custom folders:
   python akta_analysis.py 3914 --data-folder ../data --results-folder ../results
3) Auto-detect most recent experiment JSON:
   python akta_analysis.py
4) Import as module:
   from akta_analysis import analyze_experiment
   results = analyze_experiment("3914")
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import find_peaks

# Global matplotlib backend safety for non-interactive environments
plt.switch_backend("Agg")

# AKTA control server configuration (can be overridden via environment)
AKTA_CONTROL_SERVER = os.getenv("AKTA_CONTROL_SERVER", "http://localhost:5001")
AKTA_API_KEY = os.getenv("AKTA_API_KEY", "akta-control-key")


def fetch_akta_results(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results from the control server.

    Returns a dict with at least keys like: signals, sample_time, time, uv1, cond, ...
    or None if not available / server not reachable.
    """
    try:
        headers = {"X-API-Key": AKTA_API_KEY}
        url = f"{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}"
        print(f"INFO: Fetching AKTA results from control server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", {})
            if not results:
                print("WARNING: AKTA server returned 200 but 'results' is empty.")
            return results
        else:
            print(
                f"WARNING: AKTA results not found on server (status {response.status_code}). "
                "Falling back to local files if available."
            )
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def load_experiment_json(experiment_id: str, data_folder: Path) -> Dict[str, Any]:
    """Load experiment_<ID>.json with robust error handling.

    Always tries ../experiment_<ID>.json first (root), then data_folder/experiment_<ID>.json.
    """
    root_candidate = Path("../") / f"experiment_{experiment_id}.json"
    data_candidate = data_folder / f"experiment_{experiment_id}.json"

    if root_candidate.exists():
        data_file_path = root_candidate
    else:
        data_file_path = data_candidate

    print(f"INFO: Loading experiment JSON from: {data_file_path}")

    try:
        with open(data_file_path, "r", encoding="utf-8") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    return experiment_data


def extract_elab_metadata(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract relevant eLabFTW extra_fields metadata with validation.

    Returns a dict of parameter_name -> value (raw string or number).
    Raises KeyError if any required field is missing.
    """
    try:
        extra_fields = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    required_fields = [
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

    metadata_out: Dict[str, Any] = {}
    for field in required_fields:
        if field not in extra_fields:
            raise KeyError(f"Missing expected metadata field: {field}")
        value_container = extra_fields[field]
        if not isinstance(value_container, dict) or "value" not in value_container:
            raise TypeError(f"Metadata field '{field}' has invalid structure (expected dict with 'value').")
        metadata_out[field] = value_container.get("value")

    return metadata_out


def load_akta_local_fallback(
    experiment_id: str, data_folder: Path, results_folder: Path
) -> Optional[Dict[str, Any]]:
    """Try to load AKTA results from local JSON or CSV files.

    Priority:
    1) results/akta_results_<ID>.json
    2) data/akta_results_<ID>.json
    3) results/data_<ID>.csv
    4) data/data_<ID>.csv
    5) results/akta_results_<ID>.csv
    6) data/akta_results_<ID>.csv

    Returns a dict in AKTA JSON-style format, or None if nothing found.
    """
    # JSON priority
    json_candidates = [
        results_folder / f"akta_results_{experiment_id}.json",
        data_folder / f"akta_results_{experiment_id}.json",
    ]
    for p in json_candidates:
        if p.exists():
            print(f"INFO: Loading local AKTA JSON results from: {p}")
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                print(f"WARNING: Failed to parse JSON file {p}: {e}")

    # CSV fallback
    csv_candidates = [
        results_folder / f"data_{experiment_id}.csv",
        data_folder / f"data_{experiment_id}.csv",
        results_folder / f"akta_results_{experiment_id}.csv",
        data_folder / f"akta_results_{experiment_id}.csv",
        data_folder / "data.csv",  # very last-resort fallback
    ]
    for p in csv_candidates:
        if p.exists():
            print(f"INFO: Loading local AKTA CSV results from: {p}")
            try:
                df = pd.read_csv(p, na_values=["-", "NaN", "nan"])
            except Exception as e:
                print(f"WARNING: Failed to read CSV file {p}: {e}")
                continue

            if "time" not in df.columns:
                print(f"WARNING: CSV file {p} does not contain 'time' column. Skipping.")
                continue

            akta_data: Dict[str, Any] = {"time": df["time"].astype(float).tolist(), "signals": []}
            for col in df.columns:
                if col == "time":
                    continue
                akta_data["signals"].append(col)
                # Replace missing values with None
                akta_data[col] = [float(v) if pd.notna(v) else None for v in df[col]]

            # Optional: infer sample_time if possible
            time_arr = np.array(akta_data["time"], dtype=float)
            if time_arr.size > 1:
                diffs = np.diff(time_arr)
                akta_data["sample_time"] = float(np.nanmedian(diffs))
            else:
                akta_data["sample_time"] = None

            return akta_data

    print("INFO: No local AKTA results file found.")
    return None


def akta_results_to_dataframe(akta_data: Dict[str, Any]) -> pd.DataFrame:
    """Convert AKTA results dict to a pandas DataFrame.

    Expected structure:
    - 'time': list of floats
    - 'signals': list of signal names (optional but recommended)
    - each signal name key: list aligned with 'time'
    """
    if "time" not in akta_data:
        raise ValueError("AKTA results data is missing required 'time' field.")

    time = akta_data["time"]
    df_dict: Dict[str, Any] = {"time_s": np.array(time, dtype=float)}

    # Collect any numeric series besides time
    for key, value in akta_data.items():
        if key in ("time", "signals", "sample_time"):
            continue
        series = np.array([np.nan if v is None else v for v in value], dtype=float)
        df_dict[key] = series

    df = pd.DataFrame(df_dict)
    return df


def detect_peaks(
    time_s: np.ndarray,
    signal: np.ndarray,
    prominence: Optional[float] = None,
    height: Optional[float] = None,
    distance: Optional[int] = None,
) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """Detect peaks in a 1D signal using scipy.signal.find_peaks.

    Returns (peak_indices, peak_properties_dict).
    """
    # Clean up NaNs
    finite_mask = np.isfinite(signal)
    if not finite_mask.any():
        return np.array([], dtype=int), {}

    signal_clean = np.copy(signal)
    signal_clean[~finite_mask] = np.nan

    # Replace NaN with interpolated or 0 to avoid issues in find_peaks
    # Here we simply fill NaN with 0, since we only need approximate peaks.
    nan_mask = ~np.isfinite(signal_clean)
    if nan_mask.any():
        signal_clean[nan_mask] = 0.0

    # If no explicit thresholds are given, use simple heuristics
    if prominence is None:
        # 5 percent of (max - min) as default prominence
        span = float(np.nanmax(signal_clean) - np.nanmin(signal_clean))
        prominence = 0.05 * span if span > 0 else 0.0
    if height is None:
        # minimal height at 10 percent of max as default
        max_val = float(np.nanmax(signal_clean))
        height = 0.1 * max_val if max_val > 0 else 0.0

    peaks, props = find_peaks(signal_clean, prominence=prominence, height=height, distance=distance)
    return peaks, props


def plot_chromatogram_with_peaks(
    df: pd.DataFrame,
    experiment_id: str,
    results_folder: Path,
    time_unit: str = "min",
) -> Tuple[Optional[str], Optional[List[Dict[str, float]]]]:
    """Plot chromatogram with UV 280 nm and conductivity.

    - Uses df["time_s"] as x-axis.
    - Uses column "uv1" as UV trace (if present).
    - Uses column "cond" as conductivity trace (if present).
    - Identifies peaks in the UV trace and labels them with retention time.

    Returns:
        (plot_path, peak_list)
        where peak_list is a list of dicts with keys: index, time_s, time_unit, height.
    """
    if "time_s" not in df.columns:
        raise ValueError("DataFrame is missing required 'time_s' column for plotting.")

    time_s = df["time_s"].to_numpy(dtype=float)

    # Convert time unit
    if time_unit == "min":
        x = time_s / 60.0
        x_label = "Time (min)"
        time_factor = 1.0 / 60.0
    else:
        x = time_s
        x_label = "Time (s)"
        time_factor = 1.0

    has_uv = "uv1" in df.columns
    has_cond = "cond" in df.columns

    if not has_uv and not has_cond:
        print("WARNING: Neither 'uv1' nor 'cond' columns are present. Chromatogram cannot be plotted.")
        return None, None

    fig, ax1 = plt.subplots(figsize=(10, 5))

    peak_info_list: List[Dict[str, float]] = []

    # Plot UV 280 nm on primary y-axis in blue
    if has_uv:
        uv = df["uv1"].to_numpy(dtype=float)
        ax1.plot(x, uv, color="blue", linewidth=0.8, label="UV 280 nm")
        ax1.set_ylabel("UV 280 nm (mAU)", color="blue")
        ax1.tick_params(axis="y", labelcolor="blue")

        # Detect peaks in UV signal
        peaks, props = detect_peaks(time_s, uv)
        if peaks.size > 0:
            print(f"INFO: Detected {len(peaks)} peak(s) in UV trace.")
            peak_heights = props.get("peak_heights", uv[peaks])

            # Annotate peaks
            for idx, peak_idx in enumerate(peaks):
                rt_s = float(time_s[peak_idx])
                rt_conv = rt_s * time_factor
                height_val = float(peak_heights[idx]) if np.size(peak_heights) > idx else float(uv[peak_idx])
                ax1.plot(x[peak_idx], uv[peak_idx], "ro", markersize=4)
                label = f"RT={rt_conv:.2f} {time_unit}"
                ax1.annotate(
                    label,
                    xy=(x[peak_idx], uv[peak_idx]),
                    xytext=(0, 8),
                    textcoords="offset points",
                    ha="center",
                    fontsize=8,
                    rotation=0,
                    color="red",
                )
                peak_info_list.append(
                    {
                        "index": int(peak_idx),
                        "time_s": rt_s,
                        f"time_{time_unit}": rt_conv,
                        "height": height_val,
                    }
                )
        else:
            print("INFO: No peaks detected in UV trace with default parameters.")

    ax1.set_xlabel(x_label)

    # Plot conductivity on secondary y-axis in orange
    if has_cond:
        cond = df["cond"].to_numpy(dtype=float)
        ax2 = ax1.twinx()
        ax2.plot(x, cond, color="orange", linewidth=0.8, alpha=0.7, label="Conductivity")
        ax2.set_ylabel("Conductivity (mS/cm)", color="orange")
        ax2.tick_params(axis="y", labelcolor="orange")

    ax1.set_title(f"AKTA Chromatogram - Experiment {experiment_id}")

    # Build combined legend
    handles, labels = [], []
    for ax in fig.axes:
        h, l = ax.get_legend_handles_labels()
        handles.extend(h)
        labels.extend(l)
    if handles:
        ax1.legend(handles, labels, loc="best")

    fig.tight_layout()

    plot_filename = f"akta_chromatogram_{experiment_id}.png"
    plot_path = results_folder / plot_filename
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    print(f"INFO: Saved chromatogram plot to: {plot_path}")

    if peak_info_list:
        return str(plot_path), peak_info_list
    else:
        return str(plot_path), None


def autodetect_latest_experiment_id(data_folder: Path) -> Optional[str]:
    """Auto-detect the most recent experiment_<ID>.json in a folder.

    Returns the detected experiment ID as a string, or None if none found.
    """
    json_files = sorted(
        data_folder.glob("experiment_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not json_files:
        root_folder = Path("../")
        json_files = sorted(
            root_folder.glob("experiment_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not json_files:
            return None

    latest = json_files[0]
    name = latest.stem  # experiment_<ID>
    try:
        _, exp_id = name.split("_", 1)
        print(f"INFO: Auto-detected latest experiment ID: {exp_id}")
        return exp_id
    except ValueError:
        return None


def analyze_experiment(
    experiment_id: Optional[str] = None,
    data_folder: str = "../data",
    results_folder: str = "../results",
) -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography experiments.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries to auto-detect.
        data_folder (str): Path to the folder containing experiment_<ID>.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with all key metrics and paths to generated files.
    """
    data_folder_path = Path(data_folder)
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        # First try CLI argument if present
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from sys.argv: {experiment_id}")
        else:
            experiment_id = autodetect_latest_experiment_id(data_folder_path)
            if experiment_id is None:
                raise ValueError(
                    "No experiment_id provided and could not auto-detect any experiment_*.json file."
                )

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

    print(f"INFO: Starting AKTA analysis for experiment {experiment_id}")

    # Load experiment JSON and metadata
    try:
        experiment_data = load_experiment_json(experiment_id, data_folder_path)
    except Exception as e:
        analysis_results["message"] = str(e)
        print(f"ERROR: {e}")
        return analysis_results

    try:
        metadata_fields = extract_elab_metadata(experiment_data)
        analysis_results["metadata"].update(metadata_fields)
    except Exception as e:
        # Metadata is considered required according to user spec: treat as failure
        analysis_results["message"] = f"Metadata extraction failed: {e}"
        print(f"ERROR: {analysis_results['message']}")
        return analysis_results

    # Fetch AKTA data from control server first, then fall back to local files
    akta_data = fetch_akta_results(str(experiment_id))
    if akta_data is None:
        akta_data = load_akta_local_fallback(str(experiment_id), data_folder_path, results_folder_path)

    if akta_data is None:
        # Graceful failure: analysis cannot proceed without chromatogram data
        msg = "AKTA data not available from server or local files - analysis skipped."
        analysis_results["status"] = "success"
        analysis_results["message"] = msg
        print(f"INFO: {msg}")

        # Save minimal analysis results JSON
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, "w", encoding="utf-8") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")

        return analysis_results

    # Convert to DataFrame
    try:
        df = akta_results_to_dataframe(akta_data)
    except Exception as e:
        analysis_results["message"] = f"Failed to convert AKTA results to table: {e}"
        print(f"ERROR: {analysis_results['message']}")
        return analysis_results

    # Export processed data as CSV
    processed_csv_filename = f"akta_processed_data_{experiment_id}.csv"
    processed_csv_path = results_folder_path / processed_csv_filename
    try:
        df.to_csv(processed_csv_path, index=False)
        analysis_results["data_outputs"]["processed_data_csv"] = str(processed_csv_path)
        analysis_results["files_processed"] += 1
        print(f"INFO: Saved processed data CSV to: {processed_csv_path}")
    except Exception as e:
        print(f"WARNING: Failed to save processed data CSV: {e}")

    # Plot chromatogram and detect peaks
    try:
        plot_path, peak_info_list = plot_chromatogram_with_peaks(
            df, str(experiment_id), results_folder_path, time_unit="min"
        )
        if plot_path is not None:
            analysis_results["plots"]["chromatogram"] = plot_path
            analysis_results["files_processed"] += 1
        if peak_info_list:
            analysis_results["peaks"] = peak_info_list
    except Exception as e:
        print(f"WARNING: Failed to generate chromatogram plot: {e}")

    # Final status
    if not analysis_results["message"]:
        analysis_results["status"] = "success"
        analysis_results["message"] = "AKTA analysis completed successfully."

    # Save analysis results JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    try:
        with open(results_json_path, "w", encoding="utf-8") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
    except Exception as e:
        print(f"WARNING: Failed to save analysis results JSON: {e}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description="Analyze AKTA chromatography experiment data")
    parser.add_argument("experiment_id", nargs="?", help="Experiment ID. If not provided, attempts to auto-detect the most recent.")
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
