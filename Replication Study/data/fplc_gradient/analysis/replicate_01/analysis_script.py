#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

This script:
- Loads experiment metadata from experiment_<ID>.json
- Fetches or loads AKTA chromatography results (JSON or CSV)
- Generates a chromatogram plot (UV 280 nm and conductivity)
- Detects peaks in the UV trace and annotates them with retention times
- Exports processed data and a structured JSON summary

Designed for both CLI usage and import as a module.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import find_peaks

import requests

# AKTA control server configuration (can be overridden by environment)
AKTA_CONTROL_SERVER = os.getenv("AKTA_CONTROL_SERVER", "http://localhost:5001")
AKTA_API_KEY = os.getenv("AKTA_API_KEY", "akta-control-key")


def fetch_akta_results(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results from the control server.

    Returns the "results" dict or None if not available.
    This function is best-effort: it logs warnings but does not raise.
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
                print("INFO: AKTA results retrieved from server.")
            else:
                print("WARNING: AKTA server responded but no 'results' field found.")
            return results or None
        else:
            print(f"WARNING: AKTA results not found on server (status {response.status_code}).")
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def load_local_akta_results(experiment_id: str, results_folder: Path, data_folder: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local files.

    Priority:
    1) akta_results_<experiment_id>.json in results folder
    2) data.csv in data folder
    Returns a dict with keys: time, uv1, cond (if available).
    Returns None if nothing usable is found.
    """
    # 1) JSON results in results folder
    akta_json = results_folder / f"akta_results_{experiment_id}.json"
    if akta_json.exists():
        print(f"INFO: Loading AKTA JSON results from {akta_json}")
        try:
            with open(akta_json, "r") as f:
                data = json.load(f)
            # Expect a flat dict with time, uv1, cond etc.
            return data
        except Exception as e:
            print(f"WARNING: Failed to load local AKTA JSON: {e}")

    # 2) CSV results in data folder (Orbit native format)
    akta_csv = data_folder / "data.csv"
    if akta_csv.exists():
        print(f"INFO: Loading AKTA CSV results from {akta_csv}")
        try:
            df = pd.read_csv(akta_csv, na_values=["-"])
        except Exception as e:
            print(f"WARNING: Failed to read AKTA CSV file: {e}")
            return None

        if "time" not in df.columns:
            print("WARNING: AKTA CSV file missing 'time' column. Cannot use.")
            return None

        result: Dict[str, Any] = {"time": df["time"].dropna().tolist()}
        # Optional signals
        for col in ("uv1", "cond", "uv2", "ph"):
            if col in df.columns:
                result[col] = df[col].tolist()
        print("INFO: AKTA CSV successfully parsed.")
        return result

    print("INFO: No local AKTA results (JSON or CSV) found.")
    return None


def ensure_results_folder(path: Path) -> None:
    """Ensure that the results folder exists."""
    path.mkdir(parents=True, exist_ok=True)


def autodetect_latest_experiment_id(data_folder: Path) -> Optional[str]:
    """Auto-detect the most recent experiment_<ID>.json file and return ID as string.

    Returns None if no matching file is found.
    """
    json_files = sorted(
        data_folder.glob("experiment_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not json_files:
        return None
    latest = json_files[0]
    name = latest.stem  # e.g. 'experiment_3914'
    prefix = "experiment_"
    if not name.startswith(prefix):
        return None
    return name[len(prefix) :]


def extract_metadata_fields(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract known eLabFTW extra_fields with robust error handling.

    Missing fields are logged and omitted from the returned dict, but do not
    cause the analysis to fail.
    """
    extracted: Dict[str, Any] = {}
    extra = None

    try:
        extra = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError:
        print("WARNING: metadata_decoded.extra_fields not found in experiment JSON.")
        return extracted

    # List of known fields (key in extra_fields)
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

    for field in known_fields:
        try:
            value = extra[field]["value"]
            extracted[field] = value
        except KeyError:
            print(f"INFO: Optional metadata field missing: {field}")
        except TypeError:
            print(f"WARNING: Unexpected structure for metadata field: {field}")

    return extracted


def akta_dict_to_dataframe(akta_data: Dict[str, Any]) -> pd.DataFrame:
    """Convert AKTA results dict into a tidy DataFrame.

    Expects at minimum a 'time' key. Optional keys: 'uv1', 'cond', etc.
    """
    if "time" not in akta_data:
        raise ValueError("AKTA results data is missing required 'time' field.")

    time = np.asarray(akta_data["time"], dtype=float)
    df = pd.DataFrame({"time_s": time})

    # Map known signals, if present
    signal_map = {
        "uv1": "uv280_mAU",
        "uv2": "uv260_mAU",
        "cond": "cond_mS_cm",
        "ph": "pH",
    }
    for key, col_name in signal_map.items():
        if key in akta_data:
            values = np.asarray([
                np.nan if v is None else v for v in akta_data.get(key, [])
            ], dtype=float)
            if len(values) != len(df):
                # Align by truncation to shortest length
                min_len = min(len(values), len(df))
                print(
                    f"WARNING: Signal '{key}' length ({len(values)}) does not match time length ({len(df)}). Truncating to {min_len}."
                )
                df = df.iloc[:min_len].copy()
                values = values[:min_len]
            df[col_name] = values

    return df


def detect_uv_peaks(df: pd.DataFrame, prominence: float = 10.0, height: Optional[float] = None) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """Detect peaks in the UV 280 nm signal using scipy.signal.find_peaks.

    Parameters
    ----------
    df : DataFrame with columns 'time_s' and 'uv280_mAU'.
    prominence : Minimum prominence of peaks (in mAU).
    height : Optional minimum peak height (in mAU). If None, estimated from data.

    Returns
    -------
    peaks_idx : ndarray of indices of detected peaks.
    properties : dict of peak properties from find_peaks.
    """
    if "uv280_mAU" not in df.columns:
        print("INFO: No UV 280 nm signal present. Peak detection skipped.")
        return np.array([]), {}

    y = df["uv280_mAU"].values.astype(float)

    if np.all(~np.isfinite(y)):
        print("INFO: UV 280 nm signal contains no finite values. Peak detection skipped.")
        return np.array([]), {}

    # Replace NaN with interpolated or zero for peak finding; simple fill for robustness
    y_proc = y.copy()
    mask = ~np.isfinite(y_proc)
    if np.any(mask):
        y_proc[mask] = 0.0

    if height is None:
        finite_y = y_proc[np.isfinite(y_proc)]
        if finite_y.size == 0:
            print("INFO: No valid UV values for peak height estimation.")
            return np.array([]), {}
        baseline = np.percentile(finite_y, 10)
        max_val = np.max(finite_y)
        # Set min height as mid-point between baseline and max, but not less than baseline + 0.1 * range
        min_height = baseline + 0.1 * (max_val - baseline)
        height = max(min_height, baseline)

    print(
        f"INFO: Running peak detection with prominence={prominence:.2f} mAU, height>={height:.2f} mAU"
    )
    peaks_idx, properties = find_peaks(y_proc, prominence=prominence, height=height)
    print(f"INFO: Detected {len(peaks_idx)} peak(s) in UV 280 nm trace.")
    return peaks_idx, properties


def plot_chromatogram(
    df: pd.DataFrame,
    peaks_idx: Optional[np.ndarray],
    experiment_id: str,
    results_folder: Path,
) -> Dict[str, str]:
    """Generate chromatogram plot with UV 280 nm and conductivity.

    Peaks, if provided, are annotated with retention times.

    Returns dict with keys 'png' and 'pdf' for file paths.
    """
    time_min = df["time_s"].values / 60.0
    uv = df.get("uv280_mAU")
    cond = df.get("cond_mS_cm")

    fig, ax1 = plt.subplots(figsize=(10, 5))

    if uv is not None:
        ax1.plot(time_min, uv.values, color="blue", linewidth=0.8, label="UV 280 nm")
    ax1.set_xlabel("Time (min)")
    ax1.set_ylabel("UV Absorbance (mAU)", color="blue")
    ax1.tick_params(axis="y", labelcolor="blue")

    ax2 = None
    if cond is not None:
        ax2 = ax1.twinx()
        ax2.plot(
            time_min,
            cond.values,
            color="red",
            linewidth=0.8,
            alpha=0.8,
            label="Conductivity",
        )
        ax2.set_ylabel("Conductivity (mS/cm)", color="red")
        ax2.tick_params(axis="y", labelcolor="red")

    title = f"AKTA Chromatogram - Experiment {experiment_id}"
    ax1.set_title(title)

    # Peak annotation (if peaks provided and uv is present)
    if peaks_idx is not None and len(peaks_idx) > 0 and uv is not None:
        for idx in peaks_idx:
            rt_min = time_min[idx]
            height = uv.values[idx]
            ax1.plot(rt_min, height, "ko", markersize=4)
            ax1.annotate(
                f"{rt_min:.2f} min",
                xy=(rt_min, height),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                rotation=90,
            )

    fig.tight_layout()

    png_path = results_folder / f"chromatogram_{experiment_id}.png"
    pdf_path = results_folder / f"chromatogram_{experiment_id}.pdf"
    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"INFO: Saved chromatogram PNG to {png_path}")
    print(f"INFO: Saved chromatogram PDF to {pdf_path}")

    return {"png": str(png_path.resolve()), "pdf": str(pdf_path.resolve())}


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = "../data", results_folder: str = "../results") -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography experiments.

    Args:
        experiment_id (str): Experiment ID.
        data_folder (str): Path to folder containing experiment_<ID>.json files.
        results_folder (str): Path to folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with key metrics and paths to generated files.
    """
    data_folder_path = Path(data_folder)
    results_folder_path = Path(results_folder)
    ensure_results_folder(results_folder_path)

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        # Try CLI argument
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            experiment_id = autodetect_latest_experiment_id(data_folder_path)
            if experiment_id is None:
                raise FileNotFoundError(
                    f"Could not auto-detect experiment ID: no experiment_*.json found in {data_folder_path}"
                )
            print(
                f"INFO: Auto-detected most recent experiment ID from JSON files: {experiment_id}"
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

    # Load experiment data JSON
    # First: root-level JSON (../experiment_<id>.json), then data folder
    root_json_path = Path("..") / f"experiment_{experiment_id}.json"
    if root_json_path.exists():
        data_file_path = root_json_path
        print(f"INFO: Loading experiment JSON from root path: {data_file_path}")
    else:
        data_file_path = data_folder_path / f"experiment_{experiment_id}.json"
        print(f"INFO: Loading experiment JSON from data folder path: {data_file_path}")

    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # Extract selected metadata fields (best-effort)
    meta_fields = extract_metadata_fields(experiment_data)
    analysis_results["metadata"]["extra_fields"] = meta_fields

    # Fetch AKTA results from device server first
    print(f"INFO: Attempting to fetch AKTA data for experiment {experiment_id} from control server...")
    akta_data = fetch_akta_results(str(experiment_id))

    if akta_data is None:
        # Local fallback
        print("INFO: Falling back to local AKTA result files...")
        akta_data = load_local_akta_results(str(experiment_id), results_folder_path, data_folder_path)

    if akta_data is None:
        # No data available: graceful success
        msg = (
            "AKTA data not available - analysis skipped. This is expected for test runs "
            "or experiments without AKTA output."
        )
        print(f"INFO: {msg}")
        analysis_results["status"] = "success"
        analysis_results["message"] = msg
        analysis_results["metadata"]["data_source"] = "none"

        # Save analysis_results JSON before returning
        results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")

        return analysis_results

    analysis_results["metadata"]["data_source"] = "device_or_local"

    # Convert AKTA results to DataFrame
    try:
        df = akta_dict_to_dataframe(akta_data)
    except Exception as e:
        raise ValueError(f"Failed to convert AKTA results to DataFrame: {e}")

    # Save raw processed table
    processed_csv_path = results_folder_path / f"akta_processed_{experiment_id}.csv"
    try:
        df.to_csv(processed_csv_path, index=False)
        print(f"INFO: Saved processed AKTA data to {processed_csv_path}")
        analysis_results["data_outputs"]["processed_csv"] = str(
            processed_csv_path.resolve()
        )
        analysis_results["files_processed"] += 1
    except Exception as e:
        print(f"WARNING: Failed to save processed CSV: {e}")

    # Peak detection on UV trace
    peaks_idx, properties = detect_uv_peaks(df)

    peaks_info: List[Dict[str, Any]] = []
    if peaks_idx.size > 0 and "uv280_mAU" in df.columns:
        time_min = df["time_s"].values / 60.0
        heights = df["uv280_mAU"].values
        for idx in peaks_idx:
            peak_entry = {
                "index": int(idx),
                "retention_time_min": float(time_min[idx]),
                "height_mAU": float(heights[idx]),
            }
            peaks_info.append(peak_entry)
        analysis_results["peaks"] = peaks_info
        print(f"INFO: Recorded {len(peaks_info)} peak(s) with retention times.")
    else:
        print("INFO: No peaks detected in UV 280 nm trace.")

    # Plot chromatogram with peak annotations (if any)
    peaks_for_plot = peaks_idx if peaks_idx.size > 0 else None
    plot_paths = plot_chromatogram(df, peaks_for_plot, str(experiment_id), results_folder_path)
    analysis_results["plots"]["chromatogram"] = plot_paths

    # Overall status
    analysis_results["status"] = "success"
    analysis_results["message"] = "AKTA chromatography analysis completed successfully."

    # Save analysis_results JSON
    results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
    try:
        with open(results_json_path, "w") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        analysis_results["data_outputs"]["results_json"] = str(
            results_json_path.resolve()
        )
    except Exception as e:
        print(f"WARNING: Failed to save analysis results JSON: {e}")

    return analysis_results


def main() -> int:
    """Command line interface for AKTA chromatography analysis."""
    parser = argparse.ArgumentParser(description="Analyze AKTA chromatography experiment data")
    parser.add_argument("experiment_id", nargs="?", help="Experiment ID")
    parser.add_argument("--data-folder", default="../data", help="Path to the data folder (default: ../data)")
    parser.add_argument(
        "--results-folder", default="../results", help="Path to the results folder (default: ../results)"
    )

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
            print(f"ERROR: Analysis finished with status '{results.get('status')}'. Message: {results.get('message')}")
            return 1
    except Exception as e:
        print(f"ERROR: Analysis failed with unhandled exception: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
