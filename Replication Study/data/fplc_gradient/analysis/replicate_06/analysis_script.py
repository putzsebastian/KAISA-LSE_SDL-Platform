#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

This script loads experiment metadata from an experiment_<ID>.json file,
retrieves AKTA chromatogram results (via AKTA control server or local
fallback), performs basic peak detection on the UV 280 nm trace, and
plots the chromatogram with optional peak annotations.

All outputs (plots, processed CSV, JSON summary) are written to the
results folder and include the experiment ID in their filenames.
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

import requests

# ---------------------------------------------------------------------------
# AKTA control server configuration (can be overridden by environment vars)
# ---------------------------------------------------------------------------

AKTA_CONTROL_SERVER = os.getenv("AKTA_CONTROL_SERVER", "http://localhost:5001")
AKTA_API_KEY = os.getenv("AKTA_API_KEY", "akta-control-key")


def fetch_akta_results(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results from the control server.

    Returns:
        dict with keys similar to akta_results.json (signals, time, uv1, cond, ...),
        or None if not available / unreachable.
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
                print("INFO: AKTA results found on control server.")
            else:
                print("WARNING: AKTA response did not contain 'results' field or it was empty.")
            return results or None
        else:
            print(f"WARNING: AKTA results not found on server (status {response.status_code}).")
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _auto_detect_latest_experiment_id(data_folder: Path) -> str:
    """Auto-detect most recent experiment_<ID>.json file in data_folder.

    Returns the detected experiment ID (string).

    Raises FileNotFoundError if no matching file is found.
    """
    json_files = sorted(
        data_folder.glob("experiment_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not json_files:
        raise FileNotFoundError(
            f"No experiment_*.json files found in data folder: {data_folder}"
        )
    latest = json_files[0]
    name = latest.stem  # 'experiment_1234'
    try:
        exp_id = name.split("experiment_")[1]
    except Exception:
        raise ValueError(f"Could not parse experiment ID from filename: {latest.name}")
    print(f"INFO: Auto-detected latest experiment ID: {exp_id}")
    return exp_id


def _ensure_results_folder(results_folder: Path) -> None:
    results_folder.mkdir(parents=True, exist_ok=True)


def _load_experiment_json(experiment_id: str, data_folder: Path) -> Dict[str, Any]:
    """Load experiment_<ID>.json with proper error handling."""
    # First try root-level JSON as per global instructions
    root_json_path = Path("../") / f"experiment_{experiment_id}.json"
    data_file_path = root_json_path
    if not data_file_path.exists():
        data_file_path = data_folder / f"experiment_{experiment_id}.json"

    print(f"INFO: Loading experiment JSON from: {data_file_path}")
    try:
        with open(data_file_path, "r", encoding="utf-8") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    return experiment_data


def _extract_elab_metadata(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract relevant eLabFTW extra_fields with validation.

    Returns a dict with raw values for the known fields. Missing fields
    are not fatal for this AKTA analysis, but are logged.
    """
    needed_keys = [
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

    extracted: Dict[str, Any] = {}

    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        raise KeyError(f"Missing expected metadata field structure: {e}")

    for key in needed_keys:
        field = metadata.get(key)
        if field is None:
            print(f"WARNING: Missing expected metadata field: {key}")
            continue
        value = field.get("value") if isinstance(field, dict) else None
        extracted[key] = value

    return extracted


def _prepare_akta_data(
    experiment_id: str,
    experiment_data: Dict[str, Any],
    results_folder: Path,
) -> Optional[Dict[str, Any]]:
    """Obtain AKTA data from server or local fallback.

    Returns:
        Dict with keys: 'time', 'uv1', 'cond', 'signals', 'sample_time', ...
        or None if no data is available (graceful condition).
    """

    # 1) Try AKTA control server
    akta_data = fetch_akta_results(experiment_id)

    # 2) Local fallbacks
    if akta_data is None:
        # Primary local JSON fallback: ../results/akta_results_<ID>.json
        results_root = Path("../results")
        local_json_path = results_root / f"akta_results_{experiment_id}.json"

        # Alternate: within results_folder
        alt_json_path = results_folder / f"akta_results_{experiment_id}.json"

        local_csv_path = Path("data.csv")  # typical Orbit working-dir path

        if local_json_path.exists():
            print(f"INFO: Loading local AKTA JSON from {local_json_path}")
            with open(local_json_path, "r", encoding="utf-8") as f:
                akta_data = json.load(f)
        elif alt_json_path.exists():
            print(f"INFO: Loading local AKTA JSON from {alt_json_path}")
            with open(alt_json_path, "r", encoding="utf-8") as f:
                akta_data = json.load(f)
        elif local_csv_path.exists():
            print(f"INFO: Loading local AKTA CSV from {local_csv_path}")
            df = pd.read_csv(local_csv_path, na_values=["-"], encoding="utf-8")
            akta_data = {col: df[col].dropna().tolist() for col in df.columns}
        else:
            print("INFO: No AKTA data available from server or local files. Skipping AKTA analysis.")
            return None

    # Basic validation
    if "time" not in akta_data:
        print("WARNING: AKTA data does not contain 'time' key. Skipping AKTA analysis.")
        return None

    return akta_data


def _detect_peaks(
    time_s: np.ndarray,
    uv: np.ndarray,
    min_height: Optional[float] = None,
    min_distance_s: float = 30.0,
) -> List[Tuple[float, float]]:
    """Simple peak detection on UV trace.

    Args:
        time_s: 1D array of time values (seconds).
        uv: 1D array of UV signal (mAU).
        min_height: minimal required peak height (mAU). If None, use 10 %
            of the dynamic range as threshold.
        min_distance_s: minimal distance between peaks in seconds.

    Returns:
        List of (t_peak_s, uv_peak) tuples.
    """
    if len(time_s) < 3 or len(uv) < 3:
        return []

    # Determine threshold
    uv_clean = uv[np.isfinite(uv)]
    if uv_clean.size == 0:
        return []

    uv_min = float(np.nanmin(uv_clean))
    uv_max = float(np.nanmax(uv_clean))
    dynamic_range = uv_max - uv_min
    if dynamic_range <= 0:
        return []

    if min_height is None:
        min_height = uv_min + 0.1 * dynamic_range

    # Convert min_distance from seconds to number of points using median dt
    dt = np.median(np.diff(time_s)) if len(time_s) > 1 else 1.0
    if not np.isfinite(dt) or dt <= 0:
        dt = 1.0
    min_distance_pts = max(int(round(min_distance_s / dt)), 1)

    peaks: List[Tuple[float, float]] = []
    last_peak_idx = -min_distance_pts

    for i in range(1, len(uv) - 1):
        if not np.isfinite(uv[i]):
            continue
        # Simple local maximum condition
        if uv[i] > uv[i - 1] and uv[i] >= uv[i + 1] and uv[i] >= min_height:
            if i - last_peak_idx >= min_distance_pts:
                peaks.append((float(time_s[i]), float(uv[i])))
                last_peak_idx = i

    return peaks


def _plot_chromatogram(
    experiment_id: str,
    time_s: np.ndarray,
    uv: np.ndarray,
    cond: Optional[np.ndarray],
    peaks: List[Tuple[float, float]],
    results_folder: Path,
) -> Dict[str, str]:
    """Generate chromatogram plot with optional peak annotations.

    Returns dict with keys 'png' and 'pdf' pointing to saved files.
    """
    # Convert to minutes for display
    time_min = time_s / 60.0

    fig, ax1 = plt.subplots(figsize=(10, 5))

    # UV trace in blue
    ax1.plot(time_min, uv, color="blue", linewidth=0.8, label="UV 280 nm")
    ax1.set_xlabel("Time (min)")
    ax1.set_ylabel("UV 280 nm (mAU)", color="blue")
    ax1.tick_params(axis="y", labelcolor="blue")

    # Conductivity on secondary axis if available
    ax2 = None
    if cond is not None and np.any(np.isfinite(cond)):
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond, color="red", linewidth=0.8, alpha=0.7, label="Conductivity")
        ax2.set_ylabel("Conductivity (mS/cm)", color="red")
        ax2.tick_params(axis="y", labelcolor="red")

    # Peak annotations
    if peaks:
        for t_peak_s, uv_peak in peaks:
            t_peak_min = t_peak_s / 60.0
            ax1.axvline(t_peak_min, color="gray", linestyle="--", linewidth=0.7)
            ax1.annotate(
                f"{t_peak_min:.2f} min",
                xy=(t_peak_min, uv_peak),
                xytext=(0, 5),
                textcoords="offset points",
                rotation=90,
                va="bottom",
                ha="center",
                fontsize=8,
                color="black",
            )

    # Legend handling
    lines_labels = []
    line1, = ax1.get_lines(),
    for line in line1:
        lines_labels.append((line, line.get_label()))
    if ax2 is not None:
        for line in ax2.get_lines():
            lines_labels.append((line, line.get_label()))

    if lines_labels:
        lines, labels = zip(*lines_labels)
        fig.legend(lines, labels, loc="upper right")

    fig.tight_layout()

    png_path = results_folder / f"chromatogram_{experiment_id}.png"
    pdf_path = results_folder / f"chromatogram_{experiment_id}.pdf"

    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"INFO: Saved chromatogram PNG to: {png_path}")
    print(f"INFO: Saved chromatogram PDF to: {pdf_path}")

    return {"png": str(png_path), "pdf": str(pdf_path)}


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------


def analyze_experiment(
    experiment_id: Optional[str] = None,
    data_folder: str = "../data",
    results_folder: str = "../results",
) -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography runs.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with all key metrics and file paths.
    """

    data_folder_path = Path(data_folder)
    results_folder_path = Path(results_folder)
    _ensure_results_folder(results_folder_path)

    # Initialize results structure
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
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            print("INFO: No experiment ID provided. Attempting auto-detection from data folder...")
            experiment_id = _auto_detect_latest_experiment_id(data_folder_path)

    analysis_results["experiment_id"] = experiment_id

    # Validate data folder
    if not data_folder_path.exists() or not data_folder_path.is_dir():
        raise FileNotFoundError(f"Data folder does not exist or is not a directory: {data_folder_path}")

    # Load experiment JSON
    experiment_data = _load_experiment_json(experiment_id, data_folder_path)
    analysis_results["metadata"]["experiment_json_source"] = experiment_id

    # Extract eLab metadata (non-fatal if some fields missing)
    try:
        elab_meta = _extract_elab_metadata(experiment_data)
        analysis_results["metadata"]["elab_extra_fields"] = elab_meta
    except KeyError as e:
        # This is a real failure according to user requirements
        analysis_results["message"] = str(e)
        print(f"ERROR: {e}")
        return analysis_results

    # Obtain AKTA data
    akta_data = _prepare_akta_data(experiment_id, experiment_data, results_folder_path)
    if akta_data is None:
        # Graceful success: no AKTA data available
        analysis_results["status"] = "success"
        analysis_results["message"] = "No AKTA data available - analysis skipped."

        # Save analysis_results JSON and return
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, "w", encoding="utf-8") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")

        return analysis_results

    # Convert AKTA data to arrays
    time = np.array(akta_data.get("time", []), dtype=float)

    # Primary UV detector: uv1
    uv_raw = akta_data.get("uv1")
    if uv_raw is None:
        print("WARNING: 'uv1' not present in AKTA data. Attempting to find alternative UV signal.")
        # Try alternative naming: 'uv'
        uv_raw = akta_data.get("uv")

    if uv_raw is None:
        analysis_results["message"] = "AKTA data does not contain a UV 280 nm signal (uv1)."
        print("ERROR: " + analysis_results["message"])
        return analysis_results

    uv = np.array([np.nan if v is None else float(v) for v in uv_raw], dtype=float)

    # Conductivity if available
    cond_raw = akta_data.get("cond")
    cond_array = None
    if cond_raw is not None:
        try:
            cond_array = np.array([np.nan if v is None else float(v) for v in cond_raw], dtype=float)
        except Exception:
            print("WARNING: Failed to convert 'cond' signal to float. Ignoring conductivity trace.")
            cond_array = None

    # Basic alignment check
    if len(time) != len(uv):
        min_len = min(len(time), len(uv))
        print(
            f"WARNING: Length mismatch between time ({len(time)}) and uv ({len(uv)}). "
            f"Truncating to {min_len} points."
        )
        time = time[:min_len]
        uv = uv[:min_len]
        if cond_array is not None and len(cond_array) >= min_len:
            cond_array = cond_array[:min_len]

    # Save processed data to CSV
    processed_df = pd.DataFrame({"time_s": time, "time_min": time / 60.0, "uv1_mAU": uv})
    if cond_array is not None:
        processed_df["cond_mS_cm"] = cond_array

    csv_path = results_folder_path / f"akta_processed_{experiment_id}.csv"
    processed_df.to_csv(csv_path, index=False)
    print(f"INFO: Saved processed AKTA data CSV to: {csv_path}")

    analysis_results["data_outputs"]["processed_chromatogram_csv"] = str(csv_path)
    analysis_results["files_processed"] = int(len(processed_df))

    # Peak detection on UV
    peaks = _detect_peaks(time, uv)
    analysis_results["metadata"]["num_peaks_detected"] = len(peaks)
    analysis_results["metadata"]["peaks"] = [
        {"time_s": float(t), "time_min": float(t) / 60.0, "uv_mAU": float(h)} for t, h in peaks
    ]

    # Plot chromatogram with peak annotations (only if peaks exist)
    plot_paths = _plot_chromatogram(experiment_id, time, uv, cond_array, peaks, results_folder_path)
    analysis_results["plots"].update(plot_paths)

    analysis_results["status"] = "success"
    analysis_results["message"] = "AKTA chromatogram analysis completed successfully."

    # Save analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, "w", encoding="utf-8") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Saved analysis results JSON to: {results_json_path}")

    return analysis_results


# ---------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------


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
            print(f"ERROR: Analysis did not complete successfully: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
