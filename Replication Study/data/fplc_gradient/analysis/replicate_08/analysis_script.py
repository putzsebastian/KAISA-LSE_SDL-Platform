#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

This script analyzes AKTA Pure chromatography runs:
- Loads experiment metadata from experiment_<ID>.json
- Fetches AKTA results from the AKTA control server or local fallback
- Builds a time-series DataFrame of UV 280 nm and conductivity
- Detects peaks in the UV trace and calculates their retention times
- Plots chromatogram with UV and conductivity (dual y-axis)
- Labels detected peaks with retention times on the plot
- Saves processed data and plots to the results folder
- Returns a structured JSON-like dict with key metrics and file paths
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import find_peaks


# AKTA control server configuration
AKTA_CONTROL_SERVER = os.getenv("AKTA_CONTROL_SERVER", "http://localhost:5001")
AKTA_API_KEY = os.getenv("AKTA_API_KEY", "akta-control-key")


def fetch_akta_results(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results from the control server.

    Returns a dict with keys similar to akta_results.json or None if not found.
    This function is designed to fail gracefully: network and HTTP errors are
    reported via print, and the caller can decide on fallback behavior.
    """
    try:
        headers = {"X-API-Key": AKTA_API_KEY}
        url = f"{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}"
        print(f"INFO: Attempting to fetch AKTA results from server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", {})
            if results:
                print("INFO: AKTA results successfully retrieved from server.")
                return results
            print("WARNING: Server responded with status 200 but no 'results' field present.")
            return None
        else:
            print(f"WARNING: AKTA results not found on server (status {response.status_code}).")
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def load_local_akta_results(experiment_id: str, results_folder_path: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local files as fallback.

    Priority:
    1) akta_results_<experiment_id>.json in results folder
    2) data.csv in current working directory
    """
    # 1) JSON fallback in results folder
    akta_json = results_folder_path / f"akta_results_{experiment_id}.json"
    if akta_json.exists():
        try:
            print(f"INFO: Loading local AKTA JSON results from: {akta_json}")
            with open(akta_json, "r") as f:
                data = json.load(f)
            return data
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA JSON file '{akta_json}': {e}")

    # 2) CSV fallback in working directory (Orbit native output)
    akta_csv = Path("data.csv")
    if akta_csv.exists():
        try:
            print(f"INFO: Loading local AKTA CSV results from: {akta_csv}")
            df = pd.read_csv(akta_csv, na_values=["-"], engine="python")
            # Convert to akta_results-like dict
            akta_data: Dict[str, Any] = {}
            for col in df.columns:
                # Drop NaNs and convert to python list
                series = df[col]
                values = [None if pd.isna(v) else float(v) for v in series]
                akta_data[col] = values
            # If time column exists, add signals list
            signals: List[str] = [c for c in df.columns if c != "time"]
            if signals:
                akta_data["signals"] = signals
            return akta_data
        except Exception as e:
            print(f"WARNING: Failed to read AKTA CSV file '{akta_csv}': {e}")

    print("INFO: No local AKTA results file found.")
    return None


def build_akta_dataframe(raw_data: Dict[str, Any]) -> pd.DataFrame:
    """Convert AKTA raw dict into a tidy pandas DataFrame.

    Expected structure (for JSON-like data):
        {
          "signals": ["uv1", "cond"],
          "sample_time": 1.0,
          "time": [...],
          "uv1": [...],
          "cond": [...]
        }
    For CSV-derived fallback data, keys will be column names.

    Returns a DataFrame with at least a 'time' column (seconds) and any
    available signals: 'uv1' for UV 280 nm, 'cond' for conductivity.
    """
    if not isinstance(raw_data, dict):
        raise ValueError("AKTA raw data must be a dict-like structure.")

    # Determine time vector
    time = raw_data.get("time")
    if time is None:
        # Try some common fallbacks / variants
        for key in ["Time", "Time (s)", "time_s", "t"]:
            if key in raw_data:
                time = raw_data[key]
                break
    if time is None:
        raise ValueError("AKTA data does not contain a 'time' column.")

    # Build DataFrame
    df = pd.DataFrame({"time": time})

    # Add known signals if present
    for signal_key in ["uv1", "uv_280", "uv", "UV", "UV280", "cond", "conductivity", "Cond"]:
        if signal_key in raw_data and signal_key not in df.columns:
            df[signal_key] = raw_data[signal_key]

    # Normalize to standard column names where possible
    # Map UV 280 to 'uv1', conductivity to 'cond'
    column_map = {}
    # UV candidates
    uv_candidates = ["uv1", "uv_280", "uv", "UV", "UV280"]
    uv_col = None
    for cand in uv_candidates:
        if cand in df.columns:
            uv_col = cand
            break
    if uv_col is not None and uv_col != "uv1":
        column_map[uv_col] = "uv1"

    # Conductivity candidates
    cond_candidates = ["cond", "conductivity", "Cond"]
    cond_col = None
    for cand in cond_candidates:
        if cand in df.columns:
            cond_col = cand
            break
    if cond_col is not None and cond_col != "cond":
        column_map[cond_col] = "cond"

    if column_map:
        df = df.rename(columns=column_map)

    # Ensure numeric types and handle None/NaN
    df["time"] = pd.to_numeric(df["time"], errors="coerce")

    for sig in ["uv1", "cond"]:
        if sig in df.columns:
            df[sig] = pd.to_numeric(df[sig], errors="coerce")

    # Drop rows where time is NaN
    df = df.dropna(subset=["time"])  # keep only rows with valid time
    df = df.sort_values("time").reset_index(drop=True)

    return df


def detect_uv_peaks(df: pd.DataFrame,
                    height: Optional[float] = None,
                    prominence: Optional[float] = None,
                    distance: Optional[int] = None) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Detect peaks in the UV 280 nm signal using scipy.signal.find_peaks.

    Args:
        df: DataFrame containing at least 'time' and 'uv1' columns.
        height: Optional minimum peak height. If None, determined heuristically.
        prominence: Optional minimum prominence. If None, determined heuristically.
        distance: Optional minimum horizontal distance (in points) between peaks.

    Returns:
        peaks_idx: Numpy array of indices of detected peaks (in df).
        properties: Dict with properties returned by find_peaks.
    """
    if "uv1" not in df.columns:
        raise ValueError("UV column 'uv1' not found in DataFrame for peak detection.")

    y = df["uv1"].values.astype(float)

    # Remove NaNs for heuristic determination
    y_valid = y[~np.isnan(y)]
    if y_valid.size == 0:
        print("WARNING: UV signal contains only NaN values - no peaks can be detected.")
        return np.array([], dtype=int), {}

    # Heuristic thresholds if not provided
    if height is None:
        baseline = np.nanpercentile(y_valid, 10)
        max_val = np.nanmax(y_valid)
        dynamic_range = max_val - baseline
        if dynamic_range <= 0:
            # Essentially flat signal
            print("INFO: UV signal has no dynamic range - no peaks will be detected.")
            return np.array([], dtype=int), {}
        height = baseline + 0.1 * dynamic_range

    if prominence is None:
        prominence = 0.05 * (np.nanmax(y_valid) - np.nanmin(y_valid))
        if prominence <= 0:
            prominence = None

    if distance is None:
        # Rough guess: 0.1% of the number of points, at least 1
        n_points = len(y_valid)
        distance = max(1, int(0.001 * n_points))

    print(
        "INFO: Peak detection parameters - height >= {:.3g}, prominence >= {}, distance >= {}".format(
            height,
            "auto" if prominence is None else f"{prominence:.3g}",
            distance,
        )
    )

    # Use find_peaks on the full array (including NaNs); it can handle them as non-peaks
    peaks_idx, properties = find_peaks(y, height=height, prominence=prominence, distance=distance)

    print(f"INFO: Detected {len(peaks_idx)} peak(s) in UV trace.")
    return peaks_idx, properties


def plot_chromatogram(df: pd.DataFrame,
                      peaks_idx: Optional[np.ndarray],
                      experiment_id: str,
                      results_folder_path: Path) -> Dict[str, str]:
    """Plot chromatogram with UV 280 nm and conductivity.

    Uses a dual y-axis plot (UV on left, conductivity on right). If peaks are
    provided (indices into df), labels them on the UV trace with retention time
    in minutes.

    Returns a dict with paths to generated plot files.
    """
    if "uv1" not in df.columns and "cond" not in df.columns:
        raise ValueError("DataFrame must contain at least 'uv1' or 'cond' for plotting.")

    time_min = df["time"].values / 60.0

    fig, ax1 = plt.subplots(figsize=(10, 5))

    # Plot UV 280 nm
    uv_label = "UV 280 nm (mAU)"
    if "uv1" in df.columns:
        ax1.plot(time_min, df["uv1"].values, "b-", linewidth=0.9, label=uv_label)
        ax1.set_ylabel(uv_label, color="b")
        ax1.tick_params(axis="y", labelcolor="b")

    ax1.set_xlabel("Time (min)")

    # Conductivity on secondary axis
    ax2 = None
    if "cond" in df.columns:
        ax2 = ax1.twinx()
        cond_label = "Conductivity (mS/cm)"
        ax2.plot(time_min, df["cond"].values, "r-", linewidth=0.9, alpha=0.7, label=cond_label)
        ax2.set_ylabel(cond_label, color="r")
        ax2.tick_params(axis="y", labelcolor="r")

    # Peak labeling, if peaks were provided and any exist
    if peaks_idx is not None and len(peaks_idx) > 0 and "uv1" in df.columns:
        y = df["uv1"].values
        for idx in peaks_idx:
            if 0 <= idx < len(df):
                t_min = time_min[idx]
                y_val = y[idx]
                label = f"{t_min:.2f} min"
                ax1.plot(t_min, y_val, "ko", markersize=4)
                ax1.annotate(
                    label,
                    xy=(t_min, y_val),
                    xytext=(0, 8),
                    textcoords="offset points",
                    ha="center",
                    fontsize=8,
                    rotation=0,
                )

    title = f"AKTA Chromatogram - Experiment {experiment_id}"
    ax1.set_title(title)

    fig.tight_layout()

    plot_paths: Dict[str, str] = {}
    png_path = results_folder_path / f"chromatogram_{experiment_id}.png"
    pdf_path = results_folder_path / f"chromatogram_{experiment_id}.pdf"

    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"INFO: Saved chromatogram PNG to: {png_path}")
    print(f"INFO: Saved chromatogram PDF to: {pdf_path}")

    plot_paths["chromatogram_png"] = str(png_path.resolve())
    plot_paths["chromatogram_pdf"] = str(pdf_path.resolve())

    return plot_paths


def auto_detect_latest_experiment_id(data_folder: str) -> Optional[str]:
    """Auto-detect the most recent experiment_<ID>.json in data_folder.

    Returns the detected experiment ID as string, or None if no file found.
    """
    folder = Path(data_folder)
    if not folder.exists() or not folder.is_dir():
        print(f"WARNING: Data folder does not exist for auto-detection: {folder}")
        return None

    candidates = list(folder.glob("experiment_*.json"))
    if not candidates:
        print("WARNING: No experiment_*.json files found for auto-detection.")
        return None

    # Sort by modification time, newest first
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest = candidates[0]
    name = latest.stem  # 'experiment_<ID>'
    parts = name.split("_", 1)
    if len(parts) == 2 and parts[1]:
        print(f"INFO: Auto-detected latest experiment ID: {parts[1]}")
        return parts[1]

    print(f"WARNING: Could not parse experiment ID from filename: {latest}")
    return None


def load_experiment_json(experiment_id: str, data_folder: str) -> Dict[str, Any]:
    """Load experiment_<ID>.json from the specified data folder.

    Raises FileNotFoundError or ValueError with descriptive messages.
    """
    # First look in root folder ../experiment_<ID>.json (as per global rules)
    root_json_path = Path("../") / f"experiment_{experiment_id}.json"
    data_json_path = Path(data_folder) / f"experiment_{experiment_id}.json"

    if root_json_path.exists():
        data_file_path = root_json_path
    else:
        data_file_path = data_json_path

    print(f"INFO: Loading experiment JSON from: {data_file_path}")
    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    return experiment_data


def extract_elab_metadata(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract eLabFTW extra_fields metadata with robust error handling.

    Only a subset of fields are used here, but the function attempts to read
    all known fields for completeness and potential future use.
    """
    meta_out: Dict[str, Any] = {}

    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    # Helper to safely extract a field's value
    def get_field(name: str) -> Any:
        try:
            field = metadata[name]
            value = field.get("value")
            return value
        except KeyError:
            # For this script, missing individual fields are not fatal; we log them.
            print(f"WARNING: Metadata field '{name}' not found in extra_fields.")
            return None

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
        meta_out[name] = get_field(name)

    return meta_out


def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = "../data",
                       results_folder: str = "../results") -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography runs.

    Args:
        experiment_id: Experiment ID string. If None, attempts auto-detection.
        data_folder: Path to folder containing experiment_<ID>.json.
        results_folder: Path to folder where analysis outputs will be saved.

    Returns:
        dict: Analysis results with key metrics and file paths.
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
        "peaks": [],  # list of {index, time_s, time_min, height}
    }

    # Handle experiment ID: explicit argument, CLI arg, or auto-detect
    if experiment_id is None:
        # If script is run directly and sys.argv contains an ID, use it
        if len(sys.argv) > 1 and sys.argv[0].endswith(".py"):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from command line args: {experiment_id}")
        else:
            # Auto-detect from most recent experiment_<ID>.json
            experiment_id = auto_detect_latest_experiment_id(data_folder)
            if experiment_id is None:
                raise ValueError(
                    "Experiment ID not provided and auto-detection failed. "
                    "Please pass an experiment ID explicitly."
                )

    analysis_results["experiment_id"] = experiment_id

    # Load experiment JSON metadata
    experiment_data = load_experiment_json(experiment_id, data_folder)
    analysis_results["metadata"]["experiment_json_source"] = experiment_id

    # Extract eLabFTW metadata (non-fatal warnings on missing individual fields)
    try:
        extra_meta = extract_elab_metadata(experiment_data)
        analysis_results["metadata"]["elab_extra_fields"] = extra_meta
    except KeyError as e:
        # Missing top-level metadata_decoded/extra_fields is considered an error
        raise KeyError(f"Missing expected metadata field: {e}")

    # Fetch AKTA results: device server first, then local fallback
    print(f"INFO: Fetching AKTA data for experiment {experiment_id}...")
    akta_raw = fetch_akta_results(experiment_id)

    if akta_raw is None:
        print("INFO: Falling back to local AKTA result files...")
        akta_raw = load_local_akta_results(experiment_id, results_folder_path)

    if akta_raw is None:
        # Graceful success: experiment might be a dry run or no AKTA run executed
        msg = (
            "AKTA data not available - analysis skipped. "
            "No data from control server or local files."
        )
        print(f"INFO: {msg}")
        analysis_results["status"] = "success"
        analysis_results["message"] = msg
        # Save analysis_results JSON
        results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Build AKTA DataFrame
    df = build_akta_dataframe(akta_raw)

    # Ensure at least one signal is present
    if "uv1" not in df.columns and "cond" not in df.columns:
        raise ValueError("AKTA data does not contain 'uv1' or 'cond' signals for analysis.")

    # Save processed time-series data to CSV
    processed_csv_path = results_folder_path / f"akta_timeseries_{experiment_id}.csv"
    df.to_csv(processed_csv_path, index=False)
    print(f"INFO: Saved processed AKTA time-series data to: {processed_csv_path}")
    analysis_results["data_outputs"]["akta_timeseries_csv"] = str(processed_csv_path.resolve())
    analysis_results["files_processed"] += 1

    # Peak detection in UV trace, if available
    peaks_idx: np.ndarray = np.array([], dtype=int)
    peak_properties: Dict[str, Any] = {}
    if "uv1" in df.columns:
        peaks_idx, peak_properties = detect_uv_peaks(df)

        # Build peaks summary list
        peaks_list: List[Dict[str, Any]] = []
        if len(peaks_idx) > 0:
            uv_values = df["uv1"].values
            time_s = df["time"].values
            for idx in peaks_idx:
                if 0 <= idx < len(df):
                    t_s = float(time_s[idx])
                    t_min = t_s / 60.0
                    height = float(uv_values[idx]) if not np.isnan(uv_values[idx]) else float("nan")
                    peak_info = {
                        "index": int(idx),
                        "time_s": t_s,
                        "time_min": t_min,
                        "height": height,
                    }
                    peaks_list.append(peak_info)
            analysis_results["peaks"] = peaks_list

    # Plot chromatogram with optional peak labeling
    plot_paths = plot_chromatogram(df, peaks_idx if len(peaks_idx) > 0 else None,
                                   experiment_id, results_folder_path)
    analysis_results["plots"].update(plot_paths)

    # Finalize
    analysis_results["status"] = "success"
    if analysis_results["peaks"]:
        analysis_results["message"] = (
            f"Analysis completed successfully. Detected {len(analysis_results['peaks'])} peak(s)."
        )
    else:
        analysis_results["message"] = (
            "Analysis completed successfully. No peaks were detected in the UV trace."
        )

    # Save analysis results as JSON
    results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
    with open(results_json_path, "w") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface."""
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
            print("SUCCESS: Analysis completed successfully.")
            return 0
        else:
            print(f"ERROR: Analysis completed with status '{results.get('status')}'. Message: {results.get('message')}")
            return 1
    except Exception as e:
        print(f"ERROR: Analysis failed with an unhandled exception: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
