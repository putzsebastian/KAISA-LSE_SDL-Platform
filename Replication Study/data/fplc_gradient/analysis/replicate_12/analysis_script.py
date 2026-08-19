#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

Features:
- Load experiment metadata from experiment_{ID}.json
- Fetch or load AKTA chromatography results (JSON preferred, CSV fallback)
- Plot chromatogram with UV 280 nm and conductivity in different colors
- Identify peaks in the UV trace and calculate their retention times
- Label peaks with retention time on the plot (only if peaks were found)
- Save processed data and analysis results as CSV/JSON
- Robust error handling for external (CLI or programmatic) use

Note: This script uses only ASCII characters in prints and messages.
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
    """Fetch AKTA results for an experiment from the control server.

    Returns a dict with the contents of akta_results.json or None if
    results are not available on the server.
    """
    try:
        headers = {"X-API-Key": AKTA_API_KEY}
        url = f"{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}"
        print(f"INFO: Requesting AKTA results from server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", {})
            if results:
                print("INFO: AKTA results successfully fetched from server.")
                return results
            print("WARNING: AKTA server response did not contain 'results' field or it was empty.")
            return None
        else:
            print(f"WARNING: AKTA results not found on server (status {response.status_code}).")
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def load_akta_results_locally(experiment_id: str, data_folder: str, results_folder: str) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local files.

    Looks for:
    - ../results/akta_results_{experiment_id}.json
    - data.csv in the data folder

    Returns a dict with AKTA-style keys (time, uv1, cond, ...), or None if
    no suitable file is found.
    """
    results_folder_path = Path(results_folder)
    data_folder_path = Path(data_folder)

    akta_json = results_folder_path / f"akta_results_{experiment_id}.json"
    akta_csv = data_folder_path / "data.csv"

    if akta_json.exists():
        try:
            print(f"INFO: Loading AKTA JSON results from local file: {akta_json}")
            with open(akta_json, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA JSON file '{akta_json}': {e}")

    if akta_csv.exists():
        try:
            print(f"INFO: Loading AKTA CSV data from local file: {akta_csv}")
            df = pd.read_csv(akta_csv, na_values=["-"])
            # Convert to AKTA-style dict: each column to list (dropping NaNs)
            akta_data: Dict[str, Any] = {}
            for col in df.columns:
                series = df[col]
                if series.isna().all():
                    akta_data[col] = []
                else:
                    akta_data[col] = series.tolist()
            return akta_data
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA CSV file '{akta_csv}': {e}")

    print("INFO: No local AKTA results file found (JSON or CSV).")
    return None


def ensure_results_folder(path: str) -> Path:
    """Ensure results folder exists and return Path object."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def smooth_signal(signal: np.ndarray, window_size: int = 5) -> np.ndarray:
    """Simple moving-average smoothing.

    Parameters:
        signal: 1D numpy array of signal values
        window_size: odd integer window size; if < 3, no smoothing applied
    """
    if signal.size == 0 or window_size < 3:
        return signal
    if window_size % 2 == 0:
        window_size += 1
    pad = window_size // 2
    padded = np.pad(signal, pad_width=pad, mode="edge")
    kernel = np.ones(window_size) / float(window_size)
    smoothed = np.convolve(padded, kernel, mode="valid")
    return smoothed


def find_peaks(
    time: np.ndarray,
    uv: np.ndarray,
    min_prominence: float = 10.0,
    min_distance_s: float = 0.5,
) -> List[Dict[str, Any]]:
    """Identify peaks in a UV chromatogram.

    This is a lightweight peak picker that does not rely on scipy.

    Parameters:
        time: 1D numpy array of times in seconds
        uv: 1D numpy array of UV signal (mAU), same length as time
        min_prominence: minimal peak height above local baseline
        min_distance_s: minimal time between consecutive peaks in seconds

    Returns:
        List of dicts with keys: index, time_s, time_min, height
    """
    if time.size == 0 or uv.size == 0 or time.size != uv.size:
        return []

    # Basic local-maximum detection
    peaks_indices: List[int] = []
    for i in range(1, len(uv) - 1):
        if uv[i] > uv[i - 1] and uv[i] > uv[i + 1]:
            peaks_indices.append(i)

    if not peaks_indices:
        return []

    # Filter by prominence relative to a simple baseline estimate
    baseline = np.median(uv)
    candidates = [i for i in peaks_indices if uv[i] - baseline >= min_prominence]

    if not candidates:
        return []

    # Enforce minimal distance between peaks (in time)
    peaks: List[Dict[str, Any]] = []
    last_peak_time = None
    for idx in candidates:
        t = time[idx]
        if last_peak_time is not None and (t - last_peak_time) < min_distance_s:
            # Keep the higher of the two peaks in this neighborhood
            if peaks and uv[idx] > peaks[-1]["height"]:
                peaks[-1] = {
                    "index": idx,
                    "time_s": float(time[idx]),
                    "time_min": float(time[idx] / 60.0),
                    "height": float(uv[idx]),
                }
            continue
        peaks.append(
            {
                "index": idx,
                "time_s": float(time[idx]),
                "time_min": float(time[idx] / 60.0),
                "height": float(uv[idx]),
            }
        )
        last_peak_time = t

    return peaks


def plot_chromatogram(
    time: np.ndarray,
    uv: np.ndarray,
    cond: Optional[np.ndarray],
    peaks: List[Dict[str, Any]],
    experiment_id: str,
    results_folder_path: Path,
) -> Dict[str, str]:
    """Create chromatogram plot with UV and conductivity.

    UV 280 nm: blue line
    Conductivity: red line on secondary y-axis (if provided)
    Peaks (if any): labeled with retention time in minutes

    Returns a dict with paths to generated plot files.
    """
    if time.size == 0 or uv.size == 0:
        print("WARNING: Empty time or UV array, skipping chromatogram plotting.")
        return {}

    fig, ax1 = plt.subplots(figsize=(10, 5))

    # UV trace
    ax1.plot(time / 60.0, uv, "b-", linewidth=0.8, label="UV 280 nm")
    ax1.set_xlabel("Time (min)")
    ax1.set_ylabel("UV Absorbance (mAU)", color="b")
    ax1.tick_params(axis="y", labelcolor="b")

    # Conductivity on secondary axis if available
    if cond is not None and cond.size == time.size:
        ax2 = ax1.twinx()
        ax2.plot(time / 60.0, cond, "r-", linewidth=0.8, alpha=0.7, label="Conductivity")
        ax2.set_ylabel("Conductivity (mS/cm)", color="r")
        ax2.tick_params(axis="y", labelcolor="r")
    else:
        ax2 = None

    # Peak annotation if any peaks were found
    if peaks:
        for pk in peaks:
            t_min = pk["time_min"]
            height = pk["height"]
            ax1.plot(t_min, height, "ko", markersize=4)
            ax1.text(
                t_min,
                height,
                f"{t_min:.2f} min",
                fontsize=8,
                rotation=90,
                va="bottom",
                ha="center",
            )

    ax1.set_title(f"AKTA Chromatogram - Experiment {experiment_id}")

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
    """Auto-detect the most recent experiment_*.json file and return its ID.

    Returns None if no such file exists.
    """
    folder = Path(data_folder)
    if not folder.exists() or not folder.is_dir():
        return None

    candidates: List[Tuple[float, str]] = []
    for p in folder.glob("experiment_*.json"):
        try:
            mtime = p.stat().st_mtime
            name = p.stem  # experiment_ID
            if "_" in name:
                exp_id = name.split("_", 1)[1]
                candidates.append((mtime, exp_id))
        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = "../data", results_folder: str = "../results") -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography runs.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder containing experiment_{ID}.json
        results_folder (str): Path to results folder where outputs will be saved

    Returns:
        dict: Analysis results with all key metrics and file paths
    """
    results_folder_path = ensure_results_folder(results_folder)

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

    # Determine experiment_id if not provided
    if experiment_id is None:
        auto_id = auto_detect_latest_experiment_id(data_folder)
        if auto_id is None:
            msg = "No experiment ID provided and no experiment_*.json file found for auto-detection."
            analysis_results["message"] = msg
            print(f"ERROR: {msg}")
            return analysis_results
        experiment_id = auto_id
        analysis_results["experiment_id"] = experiment_id
        print(f"INFO: Auto-detected latest experiment ID: {experiment_id}")

    # Load experiment JSON metadata
    data_folder_path = Path(data_folder)
    data_file_root_first = Path("../") / f"experiment_{experiment_id}.json"
    data_file_path = data_folder_path / f"experiment_{experiment_id}.json"

    experiment_data: Dict[str, Any]
    experiment_json_path: Optional[Path] = None

    try:
        if data_file_root_first.exists():
            experiment_json_path = data_file_root_first
        elif data_file_path.exists():
            experiment_json_path = data_file_path
        else:
            raise FileNotFoundError(f"Data file not found in root or data folder for experiment {experiment_id}.")

        print(f"INFO: Loading experiment JSON from: {experiment_json_path}")
        with open(experiment_json_path, "r") as f:
            experiment_data = json.load(f)
        analysis_results["files_processed"] += 1
        analysis_results["metadata"]["experiment_json_path"] = str(experiment_json_path.resolve())
    except FileNotFoundError as e:
        msg = str(e)
        analysis_results["message"] = msg
        print(f"ERROR: {msg}")
        return analysis_results
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON format in experiment file: {e}"
        analysis_results["message"] = msg
        print(f"ERROR: {msg}")
        return analysis_results

    # Access eLabFTW extra fields (optional but validated when present)
    try:
        metadata_decoded = experiment_data.get("metadata_decoded") or {}
        extra_fields = metadata_decoded.get("extra_fields") or {}
        analysis_results["metadata"]["extra_fields_keys"] = list(extra_fields.keys())

        # Safely extract known fields if they exist
        def _get_extra(name: str) -> Optional[Any]:
            field = extra_fields.get(name)
            if isinstance(field, dict) and "value" in field:
                return field["value"]
            return None

        known_fields = [
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
        extracted_meta: Dict[str, Any] = {}
        for key in known_fields:
            value = _get_extra(key)
            if value is not None:
                extracted_meta[key] = value
        analysis_results["metadata"]["elab_extra_fields"] = extracted_meta
    except Exception as e:
        # Metadata issues should not be fatal for chromatography analysis
        print(f"WARNING: Error while accessing eLabFTW extra fields: {e}")

    # Fetch AKTA data from server first, then fall back to local files
    print(f"INFO: Fetching AKTA data for experiment {experiment_id}...")
    akta_data = fetch_akta_results_from_server(str(experiment_id))
    data_source = "server"

    if akta_data is None:
        print("INFO: Falling back to local AKTA result files...")
        akta_data = load_akta_results_locally(str(experiment_id), data_folder, results_folder)
        data_source = "local" if akta_data is not None else "none"

    if akta_data is None:
        msg = "AKTA data not available - analysis skipped. This is expected for dry runs or metadata-only experiments."
        analysis_results["status"] = "success"
        analysis_results["message"] = msg
        analysis_results["metadata"]["data_source"] = "none"
        print(f"INFO: {msg}")
    else:
        analysis_results["metadata"]["data_source"] = data_source

        # Normalize AKTA data structure
        time_arr: Optional[np.ndarray] = None
        uv_arr: Optional[np.ndarray] = None
        cond_arr: Optional[np.ndarray] = None

        # Prefer 'time' key if present
        if "time" in akta_data:
            try:
                time_arr = np.asarray(akta_data["time"], dtype=float)
            except Exception as e:
                print(f"ERROR: Failed to convert 'time' field to numeric array: {e}")
        # UV 280nm is typically 'uv1'
        if "uv1" in akta_data:
            try:
                uv_arr = np.asarray(
                    [np.nan if v is None else v for v in akta_data["uv1"]], dtype=float
                )
            except Exception as e:
                print(f"ERROR: Failed to convert 'uv1' field to numeric array: {e}")
        # Conductivity if present
        if "cond" in akta_data:
            try:
                cond_arr = np.asarray(
                    [np.nan if v is None else v for v in akta_data["cond"]], dtype=float
                )
            except Exception as e:
                print(f"WARNING: Failed to convert 'cond' field to numeric array: {e}")
                cond_arr = None

        # If time or uv arr is missing, try to construct from CSV-style dict
        if time_arr is None or uv_arr is None:
            try:
                df = pd.DataFrame(akta_data)
                if "time" in df.columns and "uv1" in df.columns:
                    time_arr = df["time"].astype(float).to_numpy()
                    uv_arr = df["uv1"].astype(float).to_numpy()
                    if "cond" in df.columns:
                        cond_arr = pd.to_numeric(df["cond"], errors="coerce").to_numpy()
                else:
                    raise ValueError("AKTA data does not contain required columns 'time' and 'uv1'.")
            except Exception as e:
                msg = f"Failed to interpret AKTA data structure: {e}"
                analysis_results["message"] = msg
                print(f"ERROR: {msg}")
                # Even though AKTA data exists, we cannot analyze it -> failed
                return analysis_results

        if time_arr is None or uv_arr is None or time_arr.size == 0 or uv_arr.size == 0:
            msg = "AKTA data is empty or missing required 'time'/'uv1' fields; analysis skipped."
            analysis_results["status"] = "success"
            analysis_results["message"] = msg
            print(f"WARNING: {msg}")
        else:
            # Ensure same length
            n = min(time_arr.size, uv_arr.size)
            time_arr = time_arr[:n]
            uv_arr = uv_arr[:n]
            if cond_arr is not None and cond_arr.size >= n:
                cond_arr = cond_arr[:n]
            elif cond_arr is not None and cond_arr.size < n:
                cond_arr = None

            # Basic cleaning: replace NaNs in UV with baseline (median of finite values)
            finite_mask = np.isfinite(uv_arr)
            if not finite_mask.any():
                msg = "UV trace contains no finite values; analysis skipped."
                analysis_results["status"] = "success"
                analysis_results["message"] = msg
                print(f"WARNING: {msg}")
            else:
                baseline = float(np.median(uv_arr[finite_mask]))
                uv_clean = uv_arr.copy()
                uv_clean[~finite_mask] = baseline

                # Smoothing for peak detection
                uv_smooth = smooth_signal(uv_clean, window_size=7)

                # Peak detection
                peaks = find_peaks(time_arr, uv_smooth, min_prominence=10.0, min_distance_s=10.0)
                analysis_results["peaks"] = peaks

                if peaks:
                    print(f"INFO: Identified {len(peaks)} peak(s) in UV trace.")
                    for i, pk in enumerate(peaks, start=1):
                        print(
                            f"  Peak {i}: retention time = {pk['time_min']:.2f} min, height = {pk['height']:.1f} mAU"
                        )
                else:
                    print("INFO: No peaks passed the detection criteria.")

                # Save processed time-series data as CSV
                processed_df = pd.DataFrame({
                    "time_s": time_arr,
                    "time_min": time_arr / 60.0,
                    "uv1_mAU": uv_arr,
                    "uv1_smooth_mAU": uv_smooth,
                })
                if cond_arr is not None:
                    processed_df["cond_mS_cm"] = cond_arr

                processed_csv_path = results_folder_path / f"akta_processed_{experiment_id}.csv"
                processed_df.to_csv(processed_csv_path, index=False)
                print(f"INFO: Saved processed AKTA data CSV to: {processed_csv_path}")
                analysis_results["data_outputs"]["processed_csv"] = str(processed_csv_path.resolve())
                analysis_results["files_processed"] += 1

                # Plot chromatogram with peak labels (if any peaks)
                plots_dict = plot_chromatogram(
                    time=time_arr,
                    uv=uv_smooth,
                    cond=cond_arr,
                    peaks=peaks,
                    experiment_id=str(experiment_id),
                    results_folder_path=results_folder_path,
                )
                analysis_results["plots"].update(plots_dict)

                analysis_results["status"] = "success"
                if peaks:
                    analysis_results["message"] = (
                        f"AKTA analysis completed with {len(peaks)} identified peak(s)."
                    )
                else:
                    analysis_results["message"] = "AKTA analysis completed; no peaks detected with current criteria."

    # Save analysis results JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    try:
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        analysis_results["data_outputs"]["results_json"] = str(results_json_path.resolve())
    except Exception as e:
        print(f"WARNING: Failed to save analysis results JSON: {e}")

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
            print(f"ERROR: Analysis failed or incomplete: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
