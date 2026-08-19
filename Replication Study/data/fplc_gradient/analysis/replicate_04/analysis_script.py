#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

This script loads experiment metadata from eLabFTW-style JSON files and
AKTA chromatography results either from an AKTA control server or from
local result files. It generates chromatogram plots, performs simple
peak detection on the UV 280 nm signal, and calculates peak retention
times. All results and generated files are written to a results folder.
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


def fetch_akta_results(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results from the control server.

    Returns a dict corresponding to the contents of akta_results.json,
    or None if the server has no data or is unreachable.
    """
    try:
        headers = {"X-API-Key": AKTA_API_KEY}
        url = f"{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}"
        print(f"INFO: Requesting AKTA results from {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", {})
            if results:
                print("INFO: AKTA results received from control server.")
            else:
                print("WARNING: Empty 'results' field in AKTA server response.")
            return results
        else:
            print(
                f"WARNING: AKTA results not found on server for experiment {experiment_id} "
                f"(status {response.status_code})."
            )
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def ensure_results_folder(path: Path) -> Path:
    """Ensure that the results folder exists and return its Path object."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_experiment_json(experiment_id: str, data_folder: str) -> Dict[str, Any]:
    """Load experiment JSON data with robust error handling."""
    data_folder_path = Path(data_folder)

    # Preferred path according to global instructions: root ../experiment_ID.json
    preferred_path = Path("../") / f"experiment_{experiment_id}.json"
    if preferred_path.exists():
        data_file_path = preferred_path
    else:
        data_file_path = data_folder_path / f"experiment_{experiment_id}.json"

    print(f"INFO: Loading experiment data from: {data_file_path}")

    try:
        with open(data_file_path, "r", encoding="utf-8") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # Validate expected metadata structure where possible
    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
        # Touch expected fields if present, but do not fail if absent
        for key in [
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
        ]:
            if key in metadata and isinstance(metadata[key], dict):
                _ = metadata[key].get("value", None)
    except KeyError as e:
        # For this analysis, missing eLabFTW extra fields are not fatal
        print(f"WARNING: Missing expected metadata field: {e}")

    return experiment_data


def find_local_akta_results(experiment_id: str, results_folder: Path) -> Optional[Dict[str, Any]]:
    """Look for local AKTA results JSON or CSV and return a standardized dict.

    Priority:
    1) akta_results_{experiment_id}.json in results_folder
    2) data.csv in current working directory

    Returns a dict with keys matching akta_results.json structure where possible,
    or None if no local data is found.
    """
    akta_json = results_folder / f"akta_results_{experiment_id}.json"
    akta_csv = Path("data.csv")

    if akta_json.exists():
        print(f"INFO: Found AKTA JSON results file: {akta_json}")
        try:
            with open(akta_json, "r", encoding="utf-8") as f:
                akta_data = json.load(f)
            return akta_data
        except Exception as e:
            print(f"WARNING: Failed to load AKTA JSON file {akta_json}: {e}")

    if akta_csv.exists():
        print(f"INFO: Found AKTA CSV data file: {akta_csv}")
        try:
            df = pd.read_csv(akta_csv, na_values=["-"], encoding="utf-8")
            akta_data: Dict[str, Any] = {}
            for col in df.columns:
                # Drop NaNs and convert to native Python lists
                series = df[col].dropna()
                akta_data[col] = series.tolist()
            # If this is a plain CSV, ensure at least 'time' is present
            if "time" not in akta_data:
                print(
                    "WARNING: Local AKTA CSV file does not contain a 'time' column. "
                    "Cannot use this file as chromatography input."
                )
                return None
            return akta_data
        except Exception as e:
            print(f"WARNING: Failed to read AKTA CSV file {akta_csv}: {e}")

    print(
        f"INFO: No local AKTA results file found for experiment {experiment_id} "
        "(neither JSON nor CSV)."
    )
    return None


def standardize_akta_data(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Standardize raw AKTA data (JSON-like) to a unified dict.

    Expected structure (primary):
        {
          "signals": ["uv1", "cond"],
          "sample_time": 1.0,
          "time": [...],
          "uv1": [...],
          "cond": [...]
        }

    Fallback for CSV-like dicts:
        {
          "time": [...],
          "uv1": [...],
          "cond": [...],
          ...
        }
    """
    if not isinstance(raw, dict):
        print("WARNING: AKTA raw data is not a dict; cannot standardize.")
        return None

    # If data already has a 'signals' list, assume proper akta_results.json
    if "signals" in raw and "time" in raw:
        signals = raw.get("signals") or []
        # Ensure at least uv1 is present for analysis
        if "uv1" not in signals and "uv1" not in raw:
            print("WARNING: AKTA data does not contain 'uv1' signal; cannot analyze.")
            return None
        return raw

    # Fallback: derive 'signals' from available keys (except 'time')
    if "time" in raw:
        time = raw["time"]
        if not isinstance(time, list) or len(time) == 0:
            print("WARNING: AKTA 'time' array is empty or invalid.")
            return None
        signals: List[str] = []
        for key, value in raw.items():
            if key == "time":
                continue
            if isinstance(value, list) and len(value) == len(time):
                signals.append(key)
        if "uv1" not in signals:
            print("WARNING: Derived AKTA data does not contain 'uv1' signal; cannot analyze.")
            return None
        return {
            "signals": signals,
            "sample_time": None,
            **{k: v for k, v in raw.items()},
        }

    print("WARNING: AKTA raw data does not contain a 'time' field.")
    return None


def detect_peaks(
    time_s: np.ndarray,
    uv: np.ndarray,
    min_height_fraction: float = 0.1,
    min_distance_points: int = 5,
) -> List[Dict[str, Any]]:
    """Simple peak detection on UV trace.

    Args:
        time_s: 1D numpy array of time in seconds.
        uv: 1D numpy array of UV signal (mAU).
        min_height_fraction: Fraction of (max - min) used as minimum peak height.
        min_distance_points: Minimum distance between peaks in index points.

    Returns:
        List of dicts with keys: index, time_s, retention_min, height.
    """
    if time_s.size == 0 or uv.size == 0:
        return []

    # Replace NaNs with local interpolation where possible
    if np.isnan(uv).any():
        print("INFO: UV trace contains NaN values; performing linear interpolation.")
        nans = np.isnan(uv)
        if (~nans).sum() >= 2:
            uv_interp = uv.copy()
            uv_interp[nans] = np.interp(time_s[nans], time_s[~nans], uv[~nans])
            uv = uv_interp
        else:
            uv = np.nan_to_num(uv, nan=0.0)

    uv_min = float(np.min(uv))
    uv_max = float(np.max(uv))
    dynamic_range = uv_max - uv_min
    if dynamic_range <= 0:
        print("INFO: UV trace has no dynamic range; skipping peak detection.")
        return []

    threshold = uv_min + min_height_fraction * dynamic_range

    # Very simple local maximum detection
    peaks: List[int] = []
    for i in range(1, len(uv) - 1):
        if uv[i] > uv[i - 1] and uv[i] >= uv[i + 1] and uv[i] >= threshold:
            if peaks and (i - peaks[-1]) < min_distance_points:
                # Keep the higher of the two close peaks
                if uv[i] > uv[peaks[-1]]:
                    peaks[-1] = i
            else:
                peaks.append(i)

    peak_list: List[Dict[str, Any]] = []
    for idx in peaks:
        t_s = float(time_s[idx])
        peak_list.append(
            {
                "index": int(idx),
                "time_s": t_s,
                "retention_min": t_s / 60.0,
                "height": float(uv[idx]),
            }
        )

    return peak_list


def plot_chromatogram(
    experiment_id: str,
    data: Dict[str, Any],
    peaks: List[Dict[str, Any]],
    results_folder: Path,
) -> Dict[str, str]:
    """Plot chromatogram with UV 280 nm and conductivity.

    If peaks are provided, label them with their retention time in the plot.

    Returns a dict with paths to generated plot files.
    """
    time = np.array(data.get("time", []), dtype=float)
    if time.size == 0:
        raise ValueError("AKTA data does not contain a valid 'time' array.")

    uv_raw = data.get("uv1", None)
    if uv_raw is None:
        raise ValueError("AKTA data does not contain 'uv1' signal for UV 280 nm.")
    uv = np.array([np.nan if v is None else v for v in uv_raw], dtype=float)

    cond = None
    if "cond" in data:
        cond_raw = data.get("cond")
        cond = np.array([np.nan if v is None else v for v in cond_raw], dtype=float)

    # Prepare figure
    fig, ax1 = plt.subplots(figsize=(10, 5))

    # UV trace in blue
    ax1.plot(time / 60.0, uv, "b-", linewidth=0.8, label="UV 280 nm")
    ax1.set_xlabel("Time (min)")
    ax1.set_ylabel("UV Absorbance (mAU)", color="b")
    ax1.tick_params(axis="y", labelcolor="b")

    # Conductivity on secondary axis if available
    ax2 = None
    if cond is not None:
        ax2 = ax1.twinx()
        ax2.plot(time / 60.0, cond, "r-", linewidth=0.8, alpha=0.7, label="Conductivity")
        ax2.set_ylabel("Conductivity (mS/cm)", color="r")
        ax2.tick_params(axis="y", labelcolor="r")

    ax1.set_title(f"AKTA Chromatogram - Experiment {experiment_id}")

    # Label peaks if any were detected
    if peaks:
        print(f"INFO: Labelling {len(peaks)} peak(s) on chromatogram plot.")
        for peak in peaks:
            t_min = peak["retention_min"]
            height = peak["height"]
            ax1.plot(t_min, height, "ko", markersize=4)
            label = f"{t_min:.2f} min"
            ax1.annotate(
                label,
                xy=(t_min, height),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                rotation=90,
            )
    else:
        print("INFO: No peaks detected; chromatogram will be plotted without labels.")

    fig.tight_layout()

    plot_paths: Dict[str, str] = {}
    png_name = f"chromatogram_{experiment_id}.png"
    pdf_name = f"chromatogram_{experiment_id}.pdf"
    png_path = results_folder / png_name
    pdf_path = results_folder / pdf_name

    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"INFO: Saved chromatogram PNG to: {png_path}")
    print(f"INFO: Saved chromatogram PDF to: {pdf_path}")

    plot_paths["chromatogram_png"] = str(png_path.resolve())
    plot_paths["chromatogram_pdf"] = str(pdf_path.resolve())

    return plot_paths


def auto_detect_latest_experiment_id(data_folder: str) -> Optional[str]:
    """Auto-detect the most recent experiment ID from JSON files in data_folder.

    Looks for files named experiment_*.json and picks the most recently
    modified one. Returns the extracted experiment ID as string, or None
    if no matching files are found.
    """
    folder = Path(data_folder)
    if not folder.exists():
        print(f"WARNING: Data folder does not exist: {folder}")
        return None

    candidates: List[Path] = list(folder.glob("experiment_*.json"))
    if not candidates:
        print(f"WARNING: No experiment_*.json files found in data folder: {folder}")
        return None

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest = candidates[0]
    name = latest.stem  # experiment_<id>
    prefix = "experiment_"
    if not name.startswith(prefix):
        return None
    exp_id = name[len(prefix) :]
    print(f"INFO: Auto-detected latest experiment ID: {exp_id} from file {latest}")
    return exp_id


def analyze_experiment(
    experiment_id: Optional[str] = None,
    data_folder: str = "../data",
    results_folder: str = "../results",
) -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography runs.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries to auto-detect.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with all key metrics and paths to generated files.
    """
    results_folder_path = ensure_results_folder(Path(results_folder))

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

    # Determine experiment ID if not provided
    if experiment_id is None:
        # Try CLI argument first (for programmatic calls without args to analyze_experiment)
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            # Auto-detect most recent experiment
            experiment_id = auto_detect_latest_experiment_id(data_folder)
            if experiment_id is None:
                raise ValueError(
                    "Could not determine experiment ID: no ID provided and "
                    "no experiment_*.json files found for auto-detection."
                )
            print(f"INFO: Auto-detected experiment ID: {experiment_id}")

    analysis_results["experiment_id"] = experiment_id

    # Load experiment JSON (for metadata; AKTA signal data comes separately)
    try:
        experiment_data = load_experiment_json(experiment_id, data_folder)
        analysis_results["metadata"]["experiment_json_loaded"] = True
    except Exception as e:
        # For this analysis, experiment JSON is helpful but not strictly required
        print(f"WARNING: Failed to load experiment JSON for ID {experiment_id}: {e}")
        analysis_results["metadata"]["experiment_json_loaded"] = False
        experiment_data = {}

    # Optionally store some basic metadata if available
    process_id = None
    try:
        metadata = experiment_data.get("metadata_decoded", {}).get("extra_fields", {})
        if "Process ID" in metadata and isinstance(metadata["Process ID"], dict):
            process_id = metadata["Process ID"].get("value")
            analysis_results["metadata"]["process_id"] = process_id
    except Exception:
        pass

    # Fetch AKTA results from control server first
    print(f"INFO: Fetching AKTA results for experiment {experiment_id} from control server...")
    akta_results = fetch_akta_results(str(experiment_id))

    # Fall back to local files if needed
    if akta_results is None:
        print("INFO: Falling back to local AKTA results search...")
        akta_results = find_local_akta_results(str(experiment_id), results_folder_path)

    if akta_results is None:
        # Graceful success: no AKTA data available
        msg = (
            "AKTA data not available - analysis skipped. "
            "This is expected for test runs or experiments without AKTA output."
        )
        print(f"INFO: {msg}")
        analysis_results["status"] = "success"
        analysis_results["message"] = msg

        # Save analysis results JSON
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, "w", encoding="utf-8") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Standardize AKTA data structure
    akta_data = standardize_akta_data(akta_results)
    if akta_data is None:
        raise ValueError("AKTA data structure is invalid or missing required fields.")

    # Convert to DataFrame and save as CSV for further analysis
    time_arr = np.array(akta_data.get("time", []), dtype=float)
    if time_arr.size == 0:
        raise ValueError("AKTA data does not contain non-empty 'time' array.")

    df_dict: Dict[str, Any] = {"time_s": time_arr}
    for sig in akta_data.get("signals", []):
        values = akta_data.get(sig)
        if values is None:
            continue
        arr = np.array([np.nan if v is None else v for v in values], dtype=float)
        if arr.size != time_arr.size:
            print(
                f"WARNING: Signal '{sig}' length {arr.size} does not match time length {time_arr.size}; skipping."
            )
            continue
        df_dict[sig] = arr

    chromatogram_df = pd.DataFrame(df_dict)
    csv_name = f"akta_chromatogram_{experiment_id}.csv"
    csv_path = results_folder_path / csv_name
    chromatogram_df.to_csv(csv_path, index=False)
    print(f"INFO: Saved processed chromatogram data CSV to: {csv_path}")

    analysis_results["data_outputs"]["chromatogram_csv"] = str(csv_path.resolve())
    analysis_results["files_processed"] += 1

    # Peak detection on UV 280 nm (uv1)
    uv_arr = chromatogram_df.get("uv1", None)
    peaks: List[Dict[str, Any]] = []
    if uv_arr is not None:
        uv_np = uv_arr.to_numpy(dtype=float)
        peaks = detect_peaks(time_arr, uv_np)
        if peaks:
            print(f"INFO: Detected {len(peaks)} peak(s) in UV 280 nm trace.")
        else:
            print("INFO: No peaks detected in UV 280 nm trace.")
    else:
        print("WARNING: UV 280 nm signal 'uv1' not found; peak detection skipped.")

    analysis_results["peaks"] = peaks

    # Create chromatogram plot with optional peak labels
    plots = plot_chromatogram(experiment_id, akta_data, peaks, results_folder_path)
    analysis_results["plots"].update(plots)

    # Summarize peak metrics in a table (if peaks exist)
    if peaks:
        peaks_df = pd.DataFrame(peaks)
        peaks_csv_name = f"akta_peaks_{experiment_id}.csv"
        peaks_csv_path = results_folder_path / peaks_csv_name
        peaks_df.to_csv(peaks_csv_path, index=False)
        analysis_results["data_outputs"]["peaks_csv"] = str(peaks_csv_path.resolve())
        print(f"INFO: Saved peaks table CSV to: {peaks_csv_path}")

    analysis_results["status"] = "success"
    analysis_results["message"] = "AKTA chromatogram analysis completed successfully."

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, "w", encoding="utf-8") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description="Analyze AKTA chromatography experiment data")
    parser.add_argument("experiment_id", nargs="?", help="Experiment ID")
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
            print(f"ERROR: Analysis failed: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
