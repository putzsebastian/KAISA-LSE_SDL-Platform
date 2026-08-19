#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

This script processes AKTA Pure chromatography runs, loading experiment metadata
from an experiment_<ID>.json file and AKTA time-series data either from the
AKTA control server or from local result files. It generates a chromatogram
plot with UV 280 nm and conductivity (if available), identifies peaks in the
UV trace, reports their retention times, and saves all results into a JSON
summary and CSV files in the results folder.
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

# ---------------------------------------------------------------------------
# AKTA control server configuration
# ---------------------------------------------------------------------------

import requests

AKTA_CONTROL_SERVER = os.getenv("AKTA_CONTROL_SERVER", "http://localhost:5001")
AKTA_API_KEY = os.getenv("AKTA_API_KEY", "akta-control-key")


def fetch_akta_results_from_server(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results JSON from the control server.

    Returns a dict with the AKTA results payload, or None if not available.
    Does not raise on missing data; only on connection problems.
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
                print("INFO: AKTA results successfully retrieved from server.")
                return results
            print("WARNING: AKTA server responded with 200 but no 'results' payload.")
            return None
        else:
            print(
                f"WARNING: AKTA results not found on server for experiment {experiment_id} "
                f"(status {response.status_code})."
            )
            return None
    except requests.exceptions.RequestException as e:
        # Connection or timeout problems are considered expected in some runs
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def auto_detect_latest_experiment_id(data_folder: Path) -> str:
    """Auto-detect the most recent experiment_<ID>.json file in data_folder.

    Returns the detected experiment ID as a string.
    Raises FileNotFoundError if no suitable file is found.
    """
    if not data_folder.exists() or not data_folder.is_dir():
        raise FileNotFoundError(f"Data folder does not exist or is not a directory: {data_folder}")

    candidates: List[Path] = list(data_folder.glob("experiment_*.json"))
    if not candidates:
        raise FileNotFoundError(f"No experiment_*.json files found in data folder: {data_folder}")

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest = candidates[0]
    # Extract ID between 'experiment_' and '.json'
    stem = latest.stem  # e.g. 'experiment_3914'
    exp_id = stem.replace("experiment_", "", 1)
    print(f"INFO: Auto-detected latest experiment ID: {exp_id} from file {latest.name}")
    return exp_id


def load_experiment_json(experiment_id: str, data_folder: Path) -> Dict[str, Any]:
    """Load experiment_<ID>.json with robust error handling."""
    # Per global rules: first look in root ../experiment_<ID>.json, then data folder
    root_json_path = Path("..") / f"experiment_{experiment_id}.json"
    data_file_path = data_folder / f"experiment_{experiment_id}.json"

    if root_json_path.exists():
        selected_path = root_json_path
    else:
        selected_path = data_file_path

    print(f"INFO: Loading experiment JSON from: {selected_path}")
    try:
        with open(selected_path, "r", encoding="utf-8") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {selected_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {selected_path}: {e}")

    return experiment_data


def extract_elab_metadata_fields(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract selected eLabFTW extra fields with error handling.

    Returns a dict of metadata values. Missing fields are reported but do not
    stop the AKTA analysis, because chromatogram plotting does not strictly
    require them. However, the user is informed about missing fields.
    """
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

    meta_values: Dict[str, Any] = {}
    missing_fields: List[str] = []

    try:
        extra_fields = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        # If the entire structure is missing, raise a clear error
        raise KeyError(f"Missing expected metadata structure in experiment JSON: {e}")

    for field in required_fields:
        try:
            value = extra_fields[field]["value"]
            meta_values[field] = value
        except KeyError:
            missing_fields.append(field)

    if missing_fields:
        print(
            "WARNING: The following expected eLabFTW metadata fields are missing: "
            + ", ".join(missing_fields)
        )

    return meta_values


def load_akta_data_local(experiment_id: str, results_folder: Path, working_dir: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local JSON or CSV fallbacks.

    Priority:
      1) akta_results_<ID>.json in results folder
      2) akta_results.json in working directory
      3) data.csv (Orbit default) in working directory
    Returns a dict like the AKTA JSON structure or None if nothing found.
    """
    # 1) results/akta_results_<ID>.json
    akta_results_path = results_folder / f"akta_results_{experiment_id}.json"
    if akta_results_path.exists():
        print(f"INFO: Loading AKTA results from local JSON: {akta_results_path}")
        try:
            with open(akta_results_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA JSON {akta_results_path}: {e}")

    # 2) working_dir/akta_results.json
    akta_json = working_dir / "akta_results.json"
    if akta_json.exists():
        print(f"INFO: Loading AKTA results from working directory JSON: {akta_json}")
        try:
            with open(akta_json, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"WARNING: Failed to read working directory AKTA JSON {akta_json}: {e}")

    # 3) working_dir/data.csv
    akta_csv = working_dir / "data.csv"
    if akta_csv.exists():
        print(f"INFO: Loading AKTA data from CSV fallback: {akta_csv}")
        try:
            df = pd.read_csv(akta_csv, na_values=["-"], encoding="utf-8")
        except Exception as e:
            print(f"WARNING: Failed to read AKTA CSV {akta_csv}: {e}")
            return None

        # Convert to AKTA-like structure
        akta_data: Dict[str, Any] = {}
        for col in df.columns:
            # Drop NaNs for each column, convert to native Python types
            akta_data[col] = [None if pd.isna(v) else float(v) for v in df[col]]
        # Derive signals list: all columns except 'time'
        signals = [c for c in df.columns if c != "time"]
        akta_data.setdefault("signals", signals)
        return akta_data

    print("INFO: No local AKTA results (JSON or CSV) found.")
    return None


def prepare_akta_timeseries(akta_data: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Prepare time, UV280 (uv1), and conductivity arrays from AKTA results.

    Returns (time_min, uv_mau, cond_mscm or None).
    Missing values are converted to NaN.
    """
    if "time" not in akta_data:
        raise ValueError("AKTA data does not contain 'time' array.")

    time_raw = np.array(akta_data["time"], dtype=float)
    time_min = time_raw / 60.0  # convert seconds to minutes

    # UV 280 nm: in AKTA JSON this is usually 'uv1'
    if "uv1" in akta_data:
        uv_values = np.array([
            np.nan if v is None else float(v) for v in akta_data["uv1"]
        ], dtype=float)
    else:
        raise ValueError("AKTA data does not contain 'uv1' signal (UV 280 nm).")

    cond_values: Optional[np.ndarray] = None
    if "cond" in akta_data:
        cond_values = np.array([
            np.nan if v is None else float(v) for v in akta_data["cond"]
        ], dtype=float)

    return time_min, uv_values, cond_values


def detect_peaks(time_min: np.ndarray, uv_values: np.ndarray,
                 height_threshold_factor: float = 0.1,
                 prominence_factor: float = 0.05,
                 min_distance_points: int = 5) -> List[Dict[str, Any]]:
    """Simple peak detection in UV trace.

    Parameters
    ----------
    time_min : np.ndarray
        Time in minutes.
    uv_values : np.ndarray
        UV signal (mAU).
    height_threshold_factor : float
        Fraction of (max - baseline) to use as minimum peak height.
    prominence_factor : float
        Fraction of (max - baseline) to use as minimum peak prominence.
    min_distance_points : int
        Minimum distance between peaks in points.

    Returns
    -------
    List of dicts with keys: 'index', 'time_min', 'height'.
    """
    # Import here to avoid hard dependency if used in a minimal environment
    from scipy.signal import find_peaks

    if len(time_min) != len(uv_values):
        raise ValueError("Time and UV arrays must have the same length for peak detection.")

    finite_uv = uv_values[np.isfinite(uv_values)]
    if finite_uv.size == 0:
        print("WARNING: No finite UV values available for peak detection.")
        return []

    baseline = float(np.nanmin(finite_uv))
    max_val = float(np.nanmax(finite_uv))
    dynamic_range = max_val - baseline
    if dynamic_range <= 0:
        print("WARNING: UV signal has no dynamic range; skipping peak detection.")
        return []

    height_threshold = baseline + height_threshold_factor * dynamic_range
    prominence_threshold = prominence_factor * dynamic_range

    print(
        "INFO: Peak detection thresholds - "
        f"height >= {height_threshold:.3f}, prominence >= {prominence_threshold:.3f}"
    )

    peaks, props = find_peaks(
        uv_values,
        height=height_threshold,
        prominence=prominence_threshold,
        distance=min_distance_points,
    )

    peak_list: List[Dict[str, Any]] = []
    for idx in peaks:
        peak_list.append(
            {
                "index": int(idx),
                "time_min": float(time_min[idx]),
                "height": float(uv_values[idx]),
            }
        )

    print(f"INFO: Detected {len(peak_list)} peak(s) in UV trace.")
    return peak_list


def plot_chromatogram(
    experiment_id: str,
    time_min: np.ndarray,
    uv_values: np.ndarray,
    cond_values: Optional[np.ndarray],
    peaks: List[Dict[str, Any]],
    results_folder: Path,
) -> Dict[str, str]:
    """Generate chromatogram plot with UV and conductivity.

    Uses blue for UV 280 nm and red for conductivity. If peaks are present,
    annotate them with their retention times.

    Returns dict with keys 'png' and 'pdf' pointing to saved file paths.
    """
    fig, ax1 = plt.subplots(figsize=(10, 5))

    # Plot UV 280 nm
    ax1.plot(time_min, uv_values, color="blue", linewidth=1.0, label="UV 280 nm")
    ax1.set_xlabel("Time (min)")
    ax1.set_ylabel("UV Absorbance (mAU)", color="blue")
    ax1.tick_params(axis="y", labelcolor="blue")

    # Plot conductivity if available
    ax2 = None
    if cond_values is not None:
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond_values, color="red", linewidth=1.0, alpha=0.7, label="Conductivity")
        ax2.set_ylabel("Conductivity (mS/cm)", color="red")
        ax2.tick_params(axis="y", labelcolor="red")

    # Peak annotations, if any
    if peaks:
        for peak in peaks:
            t = peak["time_min"]
            h = peak["height"]
            ax1.plot(t, h, "ko", markersize=4)
            ax1.annotate(
                f"{t:.2f} min",
                xy=(t, h),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                rotation=0,
            )

    title = f"AKTA Chromatogram - Experiment {experiment_id}"
    ax1.set_title(title)

    # Combine legends if conductivity present
    lines_labels = []
    line, label = ax1.get_legend_handles_labels()
    lines_labels.append((line, label))
    if ax2 is not None:
        line2, label2 = ax2.get_legend_handles_labels()
        lines_labels.append((line2, label2))
    lines = sum((l for l, _ in lines_labels), [])
    labels = sum((lab for _, lab in lines_labels), [])
    if lines:
        ax1.legend(lines, labels, loc="best")

    fig.tight_layout()

    png_path = results_folder / f"chromatogram_{experiment_id}.png"
    pdf_path = results_folder / f"chromatogram_{experiment_id}.pdf"

    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"INFO: Saved chromatogram PNG to: {png_path}")
    print(f"INFO: Saved chromatogram PDF to: {pdf_path}")

    return {"png": str(png_path.resolve()), "pdf": str(pdf_path.resolve())}


def save_peaks_csv(
    experiment_id: str,
    peaks: List[Dict[str, Any]],
    results_folder: Path,
) -> Optional[str]:
    """Save detected peaks with retention times to CSV.

    Returns the CSV path or None if no peaks.
    """
    if not peaks:
        print("INFO: No peaks to save; skipping peak CSV export.")
        return None

    df = pd.DataFrame(peaks)
    csv_path = results_folder / f"akta_peaks_{experiment_id}.csv"
    df.to_csv(csv_path, index=False)
    print(f"INFO: Saved peaks CSV to: {csv_path}")
    return str(csv_path.resolve())


def save_timeseries_csv(
    experiment_id: str,
    time_min: np.ndarray,
    uv_values: np.ndarray,
    cond_values: Optional[np.ndarray],
    results_folder: Path,
) -> str:
    """Save full time-series data (time, UV, cond) to CSV."""
    data = {
        "time_min": time_min,
        "uv280_mAU": uv_values,
    }
    if cond_values is not None:
        data["cond_mS_cm"] = cond_values

    df = pd.DataFrame(data)
    csv_path = results_folder / f"akta_timeseries_{experiment_id}.csv"
    df.to_csv(csv_path, index=False)
    print(f"INFO: Saved time-series CSV to: {csv_path}")
    return str(csv_path.resolve())


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
        experiment_id (str): Experiment ID.
        data_folder (str): Path to data folder containing experiment_<ID>.json.
        results_folder (str): Path to results folder where outputs will be saved.

    Returns:
        dict: Analysis results with all key metrics and file paths.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    data_folder_path = Path(data_folder)

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
        # Try CLI arg if available
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            print("INFO: No experiment ID provided; auto-detecting latest experiment JSON...")
            experiment_id = auto_detect_latest_experiment_id(data_folder_path)

    analysis_results["experiment_id"] = experiment_id

    # Load experiment JSON metadata
    experiment_data = load_experiment_json(experiment_id, data_folder_path)
    analysis_results["metadata"]["experiment_json_loaded"] = True

    # Extract eLab metadata (non-fatal if some fields missing)
    try:
        meta_values = extract_elab_metadata_fields(experiment_data)
        analysis_results["metadata"]["elab_extra_fields"] = meta_values
    except KeyError as e:
        # This is considered a real failure because the user asked for
        # validation of eLabFTW field existence.
        msg = f"Missing expected metadata field or structure: {e}"
        print(f"ERROR: {msg}")
        analysis_results["message"] = msg
        analysis_results["status"] = "failed"
        # Save partial results JSON
        results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
        with open(results_json_path, "w", encoding="utf-8") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON (with error) to: {results_json_path}")
        return analysis_results

    # ------------------------------------------------------------------
    # AKTA data acquisition: try control server first, then local files
    # ------------------------------------------------------------------
    working_dir = Path(".")  # AKTA CSV fallback location

    print(f"INFO: Fetching AKTA data for experiment {experiment_id} from control server...")
    akta_data = fetch_akta_results_from_server(str(experiment_id))

    if akta_data is None:
        print("INFO: Falling back to local AKTA result files...")
        akta_data = load_akta_data_local(str(experiment_id), results_folder_path, working_dir)

    if akta_data is None:
        msg = (
            "AKTA data not available on control server or local files. "
            "Analysis skipped for chromatogram. This can be expected for test runs."
        )
        print(f"WARNING: {msg}")
        analysis_results["status"] = "success"
        analysis_results["message"] = msg
        results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
        with open(results_json_path, "w", encoding="utf-8") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # ------------------------------------------------------------------
    # Prepare time-series data
    # ------------------------------------------------------------------
    try:
        time_min, uv_values, cond_values = prepare_akta_timeseries(akta_data)
    except Exception as e:
        msg = f"Failed to prepare AKTA time-series data: {e}"
        print(f"ERROR: {msg}")
        analysis_results["message"] = msg
        analysis_results["status"] = "failed"
        results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
        with open(results_json_path, "w", encoding="utf-8") as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON (with error) to: {results_json_path}")
        return analysis_results

    # Save raw-like time-series CSV
    timeseries_csv_path = save_timeseries_csv(experiment_id, time_min, uv_values, cond_values, results_folder_path)
    analysis_results["data_outputs"]["timeseries_csv"] = timeseries_csv_path
    analysis_results["files_processed"] += 1

    # ------------------------------------------------------------------
    # Peak detection on UV trace
    # ------------------------------------------------------------------
    try:
        peaks = detect_peaks(time_min, uv_values)
    except Exception as e:
        msg = f"Peak detection failed: {e}"
        print(f"WARNING: {msg}")
        peaks = []
        analysis_results.setdefault("warnings", []).append(msg)

    peaks_csv_path = save_peaks_csv(experiment_id, peaks, results_folder_path)
    if peaks_csv_path is not None:
        analysis_results["data_outputs"]["peaks_csv"] = peaks_csv_path
        analysis_results["files_processed"] += 1

    # ------------------------------------------------------------------
    # Plot chromatogram with UV and conductivity, annotate peaks if any
    # ------------------------------------------------------------------
    plot_paths = plot_chromatogram(experiment_id, time_min, uv_values, cond_values, peaks, results_folder_path)
    analysis_results["plots"]["chromatogram"] = plot_paths

    # Store peak summary metrics
    analysis_results["peaks"] = peaks
    analysis_results["metadata"]["num_peaks"] = len(peaks)

    analysis_results["status"] = "success"
    analysis_results["message"] = "AKTA chromatogram analysis completed successfully."

    # Save final analysis results JSON
    results_json_path = results_folder_path / f"analysis_results_{experiment_id}.json"
    with open(results_json_path, "w", encoding="utf-8") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Saved analysis results JSON to: {results_json_path}")

    return analysis_results


# ---------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------


def main() -> int:
    """Command line interface."""
    parser = argparse.ArgumentParser(description="Analyze AKTA chromatography experiment data")
    parser.add_argument("experiment_id", nargs="?", help="Experiment ID")
    parser.add_argument("--data-folder", default="../data", help="Data folder path (default: ../data)")
    parser.add_argument("--results-folder", default="../results", help="Results folder path (default: ../results)")

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
