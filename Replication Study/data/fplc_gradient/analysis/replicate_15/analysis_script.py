#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Run Evaluation
Can be called externally with experiment ID as parameter.

Features:
- Loads experiment_[ID].json from a data folder
- Fetches AKTA results via control server first, then falls back to local files
- Parses AKTA chromatogram data (time, UV 280 nm, conductivity)
- Detects peaks in the UV trace and calculates their retention time
- Generates a chromatogram plot with dual y-axes (UV and conductivity)
- Annotates peaks with their retention time if any are found
- Saves processed data and analysis results to a results folder
- Can be used both as a CLI tool and as an importable module
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
from scipy.signal import find_peaks

import requests


# AKTA control server configuration
AKTA_CONTROL_SERVER = os.getenv("AKTA_CONTROL_SERVER", "http://localhost:5001")
AKTA_API_KEY = os.getenv("AKTA_API_KEY", "akta-control-key")


def fetch_akta_results(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results from the control server.

    Returns:
        dict with AKTA results data, or None if not available.
    """
    try:
        headers = {"X-API-Key": AKTA_API_KEY}
        url = f"{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}"
        print(f"INFO: Fetching AKTA results from control server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", {})
            if results:
                print("INFO: AKTA results successfully fetched from server.")
                return results
            else:
                print("WARNING: AKTA results response did not contain 'results' field or it was empty.")
                return None
        else:
            print(f"WARNING: AKTA results not found on server (status {response.status_code}).")
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def load_local_akta_results(experiment_id: str, results_folder_path: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local JSON or CSV fallback.

    Primary: akta_results_{experiment_id}.json in results folder.
    Fallback: data.csv in current working directory.
    """
    akta_json = results_folder_path / f"akta_results_{experiment_id}.json"
    akta_csv = Path("data.csv")

    if akta_json.exists():
        print(f"INFO: Loading local AKTA JSON results from: {akta_json}")
        try:
            with open(akta_json, "r") as f:
                data = json.load(f)
            return data
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA JSON file '{akta_json}': {e}")

    if akta_csv.exists():
        print(f"INFO: Loading local AKTA CSV results from: {akta_csv}")
        try:
            df = pd.read_csv(akta_csv, na_values=["-"])
            akta_data: Dict[str, Any] = {col: df[col].dropna().tolist() for col in df.columns}
            return akta_data
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA CSV file '{akta_csv}': {e}")

    print("INFO: No local AKTA results file found.")
    return None


def parse_akta_data(raw_data: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Parse raw AKTA data into numpy arrays.

    Args:
        raw_data: Dictionary with keys like 'time', 'uv1', 'cond'.

    Returns:
        time_s: np.ndarray of time in seconds.
        uv_mau: np.ndarray of UV at 280 nm (mAU).
        cond: np.ndarray of conductivity (mS/cm) or None if not present.

    Raises:
        ValueError: if required fields are missing or have invalid format.
    """
    if "time" not in raw_data:
        raise ValueError("AKTA data missing required field 'time'.")

    # Convert time to numpy array of float
    time_vals = raw_data["time"]
    time_s = np.array([float(t) for t in time_vals], dtype=float)

    # UV signal: expecting key 'uv1' as primary UV 280 nm trace
    if "uv1" not in raw_data:
        raise ValueError("AKTA data missing required field 'uv1' for UV 280 nm.")

    uv_vals = raw_data["uv1"]
    uv_mau = np.array([
        np.nan if (v is None or (isinstance(v, str) and v.strip() == "")) else float(v)
        for v in uv_vals
    ], dtype=float)

    cond = None
    if "cond" in raw_data:
        cond_vals = raw_data["cond"]
        cond = np.array([
            np.nan if (v is None or (isinstance(v, str) and v.strip() == "")) else float(v)
            for v in cond_vals
        ], dtype=float)

    if time_s.shape[0] != uv_mau.shape[0]:
        raise ValueError("AKTA time and UV arrays must have the same length.")

    if cond is not None and cond.shape[0] != time_s.shape[0]:
        print("WARNING: Conductivity array length does not match time array. Ignoring conductivity.")
        cond = None

    return time_s, uv_mau, cond


def detect_peaks(time_s: np.ndarray, uv_mau: np.ndarray) -> pd.DataFrame:
    """Detect peaks in the UV trace and compute their retention times.

    Peak detection heuristics:
    - Uses scipy.signal.find_peaks
    - Height threshold set to max(uv) * 0.05 (5 percent of maximum) to avoid noise
    - Minimum distance between peaks of 5 data points

    Returns:
        DataFrame with columns: 'peak_index', 'time_s', 'time_min', 'height_mau'
        Empty DataFrame if no peaks are found.
    """
    if len(uv_mau) == 0:
        return pd.DataFrame(columns=["peak_index", "time_s", "time_min", "height_mau"])

    # Replace NaNs for peak detection
    uv_clean = np.copy(uv_mau)
    if np.all(np.isnan(uv_clean)):
        print("WARNING: All UV values are NaN; no peaks can be detected.")
        return pd.DataFrame(columns=["peak_index", "time_s", "time_min", "height_mau"])

    # Fill NaNs by interpolation for peak detection purposes
    nans = np.isnan(uv_clean)
    if np.any(nans):
        not_nan = ~nans
        uv_clean[nans] = np.interp(time_s[nans], time_s[not_nan], uv_clean[not_nan])

    uv_max = np.nanmax(uv_clean)
    if uv_max <= 0:
        print("WARNING: Maximum UV signal is not positive; no peaks will be detected.")
        return pd.DataFrame(columns=["peak_index", "time_s", "time_min", "height_mau"])

    height_threshold = uv_max * 0.05
    distance_points = max(5, len(uv_clean) // 200)

    peaks, properties = find_peaks(uv_clean, height=height_threshold, distance=distance_points)

    if peaks.size == 0:
        print("INFO: No peaks detected above the threshold.")
        return pd.DataFrame(columns=["peak_index", "time_s", "time_min", "height_mau"])

    peak_heights = properties.get("peak_heights", uv_clean[peaks])

    peak_times_s = time_s[peaks]
    peak_times_min = peak_times_s / 60.0

    peaks_df = pd.DataFrame({
        "peak_index": peaks,
        "time_s": peak_times_s,
        "time_min": peak_times_min,
        "height_mau": peak_heights,
    })

    print(f"INFO: Detected {len(peaks_df)} peak(s) in UV trace.")
    return peaks_df


def plot_chromatogram(
    experiment_id: str,
    time_s: np.ndarray,
    uv_mau: np.ndarray,
    cond: Optional[np.ndarray],
    peaks_df: pd.DataFrame,
    results_folder_path: Path,
) -> Dict[str, str]:
    """Generate chromatogram plot with UV and conductivity.

    UV 280 nm is plotted in blue on the left y-axis.
    Conductivity (if available) is plotted in red on the right y-axis.
    Peaks, if present, are annotated with their retention times.

    Returns:
        dict with keys 'png' and 'pdf' pointing to saved plot paths.
    """
    time_min = time_s / 60.0

    fig, ax1 = plt.subplots(figsize=(10, 5))

    ax1.plot(time_min, uv_mau, color="blue", linewidth=0.9, label="UV 280 nm")
    ax1.set_xlabel("Time (min)")
    ax1.set_ylabel("UV Absorbance (mAU)", color="blue")
    ax1.tick_params(axis="y", labelcolor="blue")

    ax2 = None
    if cond is not None:
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond, color="red", linewidth=0.8, alpha=0.8, label="Conductivity")
        ax2.set_ylabel("Conductivity (mS/cm)", color="red")
        ax2.tick_params(axis="y", labelcolor="red")

    title = f"AKTA Chromatogram - Experiment {experiment_id}"
    ax1.set_title(title)

    # Annotate peaks with retention times if peaks are present
    if not peaks_df.empty:
        for _, row in peaks_df.iterrows():
            t = row["time_min"]
            h = row["height_mau"]
            ax1.plot(t, h, "ko", markersize=4)
            label = f"{t:.2f} min"
            ax1.annotate(
                label,
                xy=(t, h),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                rotation=0,
            )

    fig.tight_layout()

    plot_paths: Dict[str, str] = {}
    png_path = results_folder_path / f"chromatogram_{experiment_id}.png"
    pdf_path = results_folder_path / f"chromatogram_{experiment_id}.pdf"

    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"INFO: Saved chromatogram plot PNG to: {png_path}")
    print(f"INFO: Saved chromatogram plot PDF to: {pdf_path}")

    plot_paths["png"] = str(png_path.resolve())
    plot_paths["pdf"] = str(pdf_path.resolve())

    return plot_paths


def load_experiment_metadata(experiment_id: str, data_folder: str) -> Dict[str, Any]:
    """Load experiment_[ID].json with error handling and return parsed data."""
    data_folder_path = Path(data_folder)

    # Try root folder first: ../experiment_{id}.json
    root_json_path = Path("../") / f"experiment_{experiment_id}.json"
    if root_json_path.exists():
        data_file_path = root_json_path
    else:
        data_file_path = data_folder_path / f"experiment_{experiment_id}.json"

    print(f"INFO: Loading experiment metadata from: {data_file_path}")

    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # Optional: validate eLabFTW extra fields structure if present
    try:
        _ = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError:
        # Not all experiments must have metadata_decoded; this is not fatal
        print("INFO: 'metadata_decoded.extra_fields' not found in experiment JSON. Continuing without extra fields.")

    return experiment_data


def auto_detect_most_recent_experiment_id(data_folder: str) -> Optional[str]:
    """Auto-detect the most recent experiment_[ID].json file and return its ID as string.

    Returns None if no experiment JSON files are found.
    """
    data_folder_path = Path(data_folder)
    if not data_folder_path.exists():
        print(f"WARNING: Data folder does not exist: {data_folder_path}")
        return None

    json_files = sorted(
        data_folder_path.glob("experiment_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not json_files:
        print(f"WARNING: No experiment_*.json files found in data folder: {data_folder_path}")
        return None

    most_recent = json_files[0]
    name = most_recent.stem  # experiment_<id>
    try:
        exp_id = name.split("experiment_")[1]
    except Exception:
        print(f"WARNING: Could not parse experiment ID from filename: {most_recent.name}")
        return None

    print(f"INFO: Auto-detected most recent experiment ID: {exp_id}")
    return exp_id


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = "../data", results_folder: str = "../results") -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography runs.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries to auto-detect.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with key metrics and paths to generated files.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            print("INFO: No experiment ID provided. Attempting auto-detection from data folder.")
            experiment_id = auto_detect_most_recent_experiment_id(data_folder)
            if experiment_id is None:
                raise ValueError("Could not auto-detect experiment ID: no experiment JSON files found.")

    analysis_results: Dict[str, Any] = {
        "experiment_id": experiment_id,
        "status": "failed",
        "message": "",
        "plots": {},
        "data_outputs": {},
        "metadata": {},
        "files_processed": 0,
    }

    print(f"INFO: Starting analysis for experiment ID: {experiment_id}")

    # Load experiment metadata (mainly for traceability and potential future use)
    try:
        experiment_data = load_experiment_metadata(experiment_id, data_folder)
        analysis_results["metadata"]["experiment_json_loaded"] = True
    except Exception as e:
        analysis_results["metadata"]["experiment_json_loaded"] = False
        analysis_results["message"] = str(e)
        print(f"ERROR: {e}")
        raise

    # Fetch or load AKTA chromatogram data
    akta_data = fetch_akta_results(experiment_id)
    if akta_data is None:
        print("INFO: Falling back to local AKTA result files.")
        akta_data = load_local_akta_results(experiment_id, results_folder_path)

    if akta_data is None:
        msg = "AKTA data not available from control server or local files - analysis skipped."
        analysis_results["status"] = "success"
        analysis_results["message"] = msg
        print(f"INFO: {msg}")

        # Save minimal analysis results JSON
        results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")

        return analysis_results

    # Parse AKTA data
    try:
        time_s, uv_mau, cond = parse_akta_data(akta_data)
    except Exception as e:
        analysis_results["message"] = f"Failed to parse AKTA data: {e}"
        print(f"ERROR: {analysis_results['message']}")
        raise

    # Store raw parsed data into CSV for further analysis
    df = pd.DataFrame({
        "time_s": time_s,
        "time_min": time_s / 60.0,
        "uv_280_mau": uv_mau,
    })
    if cond is not None:
        df["cond_mS_cm"] = cond

    raw_csv_path = results_folder_path / f"akta_chromatogram_raw_{experiment_id}.csv"
    df.to_csv(raw_csv_path, index=False)
    analysis_results["data_outputs"]["chromatogram_csv"] = str(raw_csv_path.resolve())
    analysis_results["files_processed"] += 1
    print(f"INFO: Saved parsed AKTA chromatogram data to: {raw_csv_path}")

    # Peak detection
    peaks_df = detect_peaks(time_s, uv_mau)
    if not peaks_df.empty:
        peaks_csv_path = results_folder_path / f"akta_peaks_{experiment_id}.csv"
        peaks_df.to_csv(peaks_csv_path, index=False)
        analysis_results["data_outputs"]["peaks_csv"] = str(peaks_csv_path.resolve())
        analysis_results["metadata"]["num_peaks"] = int(len(peaks_df))
        analysis_results["files_processed"] += 1
        print(f"INFO: Saved peak table to: {peaks_csv_path}")
    else:
        analysis_results["metadata"]["num_peaks"] = 0
        print("INFO: No peaks detected; skipping peak table export.")

    # Plot chromatogram with peak annotations (if any)
    plot_paths = plot_chromatogram(experiment_id, time_s, uv_mau, cond, peaks_df, results_folder_path)
    analysis_results["plots"]["chromatogram_png"] = plot_paths.get("png")
    analysis_results["plots"]["chromatogram_pdf"] = plot_paths.get("pdf")
    analysis_results["files_processed"] += 2

    # Finalize analysis results
    analysis_results["status"] = "success"
    if analysis_results.get("message") == "":
        if analysis_results["metadata"].get("num_peaks", 0) > 0:
            analysis_results["message"] = "Analysis completed successfully with peak detection."
        else:
            analysis_results["message"] = "Analysis completed successfully; no peaks detected."

    # Save analysis results JSON
    results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
    with open(results_json_path, "w") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Saved analysis results JSON to: {results_json_path}")

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
            print("SUCCESS: Analysis completed successfully.")
            return 0
        else:
            print(f"ERROR: Analysis did not complete successfully: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
