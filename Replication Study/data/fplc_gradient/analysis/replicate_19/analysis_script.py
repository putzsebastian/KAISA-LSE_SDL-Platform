#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

Features:
- Loads experiment JSON (eLabFTW-style) for metadata and ID handling
- Fetches AKTA chromatography results from control server with local fallback
- Plots chromatogram (UV 280 nm and conductivity) with automatic peak detection
- Exports peak table and underlying time series as CSV
- Saves a structured JSON summary of the analysis
- Usable as CLI tool or imported analyze_experiment() function
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


# -----------------------------------------------------------------------------
# AKTA control server configuration (from prompt blueprint)
# -----------------------------------------------------------------------------

AKTA_CONTROL_SERVER = os.getenv("AKTA_CONTROL_SERVER", "http://localhost:5001")
AKTA_API_KEY = os.getenv("AKTA_API_KEY", "akta-control-key")


def fetch_akta_results(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results from the control server.

    Returns
    -------
    dict or None
        Parsed JSON results dict when available, otherwise None.
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
                print("INFO: AKTA results retrieved from control server.")
            else:
                print("WARNING: AKTA server responded without 'results' payload.")
            return results or None
        else:
            print(
                f"WARNING: AKTA results not found on server for experiment {experiment_id} "
                f"(status {response.status_code})."
            )
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------


def _ensure_results_folder(results_folder: str) -> Path:
    p = Path(results_folder)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_experiment_json(experiment_id: str, data_folder: str) -> Dict[str, Any]:
    """Load experiment JSON with required error handling.

    First tries the root-level ../experiment_<id>.json as required by
    higher-level platform, then falls back to data_folder/experiment_<id>.json.
    """
    # Primary location: root relative to analysis folder
    root_candidate = Path("../") / f"experiment_{experiment_id}.json"
    if root_candidate.exists():
        data_file_path = root_candidate
    else:
        data_file_path = Path(data_folder) / f"experiment_{experiment_id}.json"

    print(f"INFO: Loading experiment data from: {data_file_path}")
    try:
        with open(data_file_path, "r") as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    return experiment_data


def _extract_metadata_fields(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract known eLabFTW extra fields with robust error handling.

    Missing fields are reported but do not cause the whole analysis to fail,
    because AKTA analysis can run without them. They are returned when present.
    """
    fields_of_interest = [
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

    collected: Dict[str, Any] = {}

    try:
        metadata = experiment_data["metadata_decoded"]["extra_fields"]
    except KeyError as e:
        # This is informative but not fatal for AKTA analysis
        print(f"WARNING: Missing expected metadata structure in experiment JSON: {e}")
        return collected

    for field in fields_of_interest:
        try:
            value = metadata[field]["value"]
            collected[field] = value
        except KeyError:
            print(f"INFO: Optional metadata field missing: {field}")

    return collected


def _load_akta_from_local(experiment_id: str, results_folder_path: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local files when server is unavailable.

    Search order (per blueprint):
    1) ../results/akta_results_<id>.json
    2) data.csv in current working directory
    """
    # Try dedicated JSON file first
    local_json = results_folder_path / f"akta_results_{experiment_id}.json"
    if local_json.exists():
        print(f"INFO: Loading local AKTA JSON results from: {local_json}")
        try:
            with open(local_json, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA JSON results: {e}")

    # Fallback to Orbit-native CSV
    akta_csv = Path("data.csv")
    if akta_csv.exists():
        print(f"INFO: Loading local AKTA CSV results from: {akta_csv}")
        try:
            df = pd.read_csv(akta_csv, na_values=["-"])
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA CSV results: {e}")
            return None

        # Convert to dictionary-of-lists structure similar to JSON blueprint
        akta_data: Dict[str, Any] = {}
        for col in df.columns:
            akta_data[col] = df[col].where(pd.notnull(df[col]), None).tolist()
        # Derive signals list if possible (all columns except time)
        signals = [c for c in df.columns if c != "time"]
        akta_data.setdefault("signals", signals)
        return akta_data

    print("WARNING: No local AKTA result files found (akta_results_*.json or data.csv).")
    return None


def _prepare_akta_timeseries(akta_data: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Extract time, UV 280 nm (uv1) and conductivity arrays from AKTA dict.

    Returns
    -------
    time_s : np.ndarray
    uv_mau : np.ndarray
    cond_mscm : np.ndarray or None
    """
    # Time
    if "time" not in akta_data:
        raise ValueError("AKTA data does not contain 'time' array.")
    time_vals = np.array(akta_data["time"], dtype=float)

    # UV 280 nm (primary detector signal is 'uv1')
    if "uv1" not in akta_data:
        raise ValueError("AKTA data does not contain 'uv1' (UV 280 nm) signal.")

    uv_raw = np.array([
        np.nan if v is None else float(v) for v in akta_data.get("uv1", [])
    ], dtype=float)

    # Conductivity, optional
    cond_vals: Optional[np.ndarray] = None
    if "cond" in akta_data:
        cond_vals = np.array([
            np.nan if v is None else float(v) for v in akta_data.get("cond", [])
        ], dtype=float)

    return time_vals, uv_raw, cond_vals


def _detect_peaks(time_s: np.ndarray, uv_mau: np.ndarray) -> pd.DataFrame:
    """Detect peaks in UV trace and compute retention times.

    Uses scipy.signal.find_peaks with simple heuristics:
    - Prominence based on a fraction of the UV dynamic range
    - Minimum distance between peaks to avoid over-detection

    Returns
    -------
    pandas.DataFrame
        Columns: peak_index, retention_time_s, retention_time_min,
                 height_mau, prominence_mau
    """
    if len(time_s) == 0 or len(uv_mau) == 0:
        return pd.DataFrame(columns=[
            "peak_index",
            "retention_time_s",
            "retention_time_min",
            "height_mau",
            "prominence_mau",
        ])

    # Basic cleaning: replace NaN in UV with minimal value to avoid missing data breaks
    finite_uv = uv_mau[np.isfinite(uv_mau)]
    if finite_uv.size == 0:
        return pd.DataFrame(columns=[
            "peak_index",
            "retention_time_s",
            "retention_time_min",
            "height_mau",
            "prominence_mau",
        ])

    uv_clean = uv_mau.copy()
    min_uv = np.nanmin(finite_uv)
    uv_clean[~np.isfinite(uv_clean)] = min_uv

    # Heuristic prominence: at least 5 percent of dynamic range
    uv_range = np.nanmax(finite_uv) - np.nanmin(finite_uv)
    if uv_range <= 0:
        # Flat trace; nothing to detect
        return pd.DataFrame(columns=[
            "peak_index",
            "retention_time_s",
            "retention_time_min",
            "height_mau",
            "prominence_mau",
        ])

    prominence = 0.05 * uv_range

    # Minimum distance between peaks: approximate 10 data points
    # (works with typical 1 Hz sampling; conservative for higher sampling)
    distance = max(1, len(time_s) // 500)

    peak_indices, props = find_peaks(uv_clean, prominence=prominence, distance=distance)

    if peak_indices.size == 0:
        return pd.DataFrame(columns=[
            "peak_index",
            "retention_time_s",
            "retention_time_min",
            "height_mau",
            "prominence_mau",
        ])

    heights = uv_clean[peak_indices]
    prominences = props.get("prominences", np.full_like(heights, np.nan))

    peak_table = pd.DataFrame({
        "peak_index": peak_indices,
        "retention_time_s": time_s[peak_indices],
        "retention_time_min": time_s[peak_indices] / 60.0,
        "height_mau": heights,
        "prominence_mau": prominences,
    })

    return peak_table


def _plot_chromatogram(
    experiment_id: str,
    time_s: np.ndarray,
    uv_mau: np.ndarray,
    cond_mscm: Optional[np.ndarray],
    peak_table: pd.DataFrame,
    results_folder_path: Path,
) -> str:
    """Create chromatogram plot and save as PNG.

    UV 280 nm and conductivity use different colors. Peaks, if present, are
    annotated with retention time.

    Returns
    -------
    str
        Absolute path to saved PNG file.
    """
    time_min = time_s / 60.0

    fig, ax1 = plt.subplots(figsize=(10, 5))

    # UV trace (primary)
    ax1.plot(time_min, uv_mau, color="blue", linewidth=1.0, label="UV 280 nm")
    ax1.set_xlabel("Time (min)")
    ax1.set_ylabel("UV Absorbance (mAU)", color="blue")
    ax1.tick_params(axis="y", labelcolor="blue")

    # Conductivity (secondary axis)
    if cond_mscm is not None:
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond_mscm, color="red", linewidth=1.0, alpha=0.7, label="Conductivity")
        ax2.set_ylabel("Conductivity (mS/cm)", color="red")
        ax2.tick_params(axis="y", labelcolor="red")
    else:
        ax2 = None

    # Peak annotations if any
    if not peak_table.empty:
        for _, row in peak_table.iterrows():
            rt_min = row["retention_time_min"]
            height = row["height_mau"]
            ax1.plot(rt_min, height, "ko", markersize=4)
            ax1.annotate(
                f"{rt_min:.2f} min",
                xy=(rt_min, height),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                rotation=0,
            )

    ax1.set_title(f"AKTA Chromatogram - Experiment {experiment_id}")

    # Legends: combine both axes if present
    lines_labels = []
    for ax in [ax1, ax2] if cond_mscm is not None else [ax1]:
        handles, labels = ax.get_legend_handles_labels()
        lines_labels.append((handles, labels))
    handles_combined: List[Any] = []
    labels_combined: List[str] = []
    for handles, labels in lines_labels:
        handles_combined.extend(handles)
        labels_combined.extend(labels)
    if handles_combined:
        ax1.legend(handles_combined, labels_combined, loc="best")

    fig.tight_layout()

    plot_filename = f"akta_chromatogram_{experiment_id}.png"
    plot_path = results_folder_path / plot_filename
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    print(f"INFO: Chromatogram plot saved to: {plot_path}")
    return str(plot_path.resolve())


# -----------------------------------------------------------------------------
# Main analysis function
# -----------------------------------------------------------------------------


def analyze_experiment(
    experiment_id: Optional[str] = None,
    data_folder: str = "../data",
    results_folder: str = "../results",
) -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography runs.

    Parameters
    ----------
    experiment_id : str, optional
        Experiment ID. If None, auto-detects from most recent experiment_*.json
        in the given data_folder or root folder.
    data_folder : str
        Path to the folder containing experiment_*.json files.
    results_folder : str
        Path to the folder where analysis outputs will be saved.

    Returns
    -------
    dict
        Analysis results summary including metrics, file paths and metadata.
    """
    results_folder_path = _ensure_results_folder(results_folder)

    analysis_results: Dict[str, Any] = {
        "experiment_id": experiment_id,
        "status": "failed",
        "message": "",
        "plots": {},
        "data_outputs": {},
        "metadata": {},
        "files_processed": 0,
    }

    # ------------------------------------------------------------------
    # Auto-detect experiment ID if not provided
    # ------------------------------------------------------------------
    if experiment_id is None:
        # Try CLI argument first (if any)
        if len(sys.argv) > 1 and sys.argv[1] not in ("-h", "--help"):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            # Auto-detect most recent experiment JSON
            print("INFO: No experiment ID provided. Attempting auto-detection from JSON files.")
            candidates: List[Path] = []
            # Root folder first (../experiment_*.json)
            root_path = Path("../")
            candidates.extend(root_path.glob("experiment_*.json"))
            # Then data folder
            data_folder_path = Path(data_folder)
            candidates.extend(data_folder_path.glob("experiment_*.json"))

            if not candidates:
                raise FileNotFoundError(
                    "No experiment_*.json files found for auto-detection. "
                    "Provide an experiment ID explicitly."
                )

            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            latest = candidates[0]
            experiment_id = latest.stem.replace("experiment_", "")
            print(f"INFO: Auto-detected most recent experiment ID: {experiment_id}")

    analysis_results["experiment_id"] = experiment_id

    # ------------------------------------------------------------------
    # Load experiment JSON
    # ------------------------------------------------------------------
    experiment_data = _load_experiment_json(experiment_id, data_folder)
    analysis_results["metadata"]["experiment_json_source"] = "root_or_data_folder"

    # Extract selected metadata fields from eLabFTW extra_fields
    extra_metadata = _extract_metadata_fields(experiment_data)
    analysis_results["metadata"]["extra_fields"] = extra_metadata

    # ------------------------------------------------------------------
    # Fetch AKTA data from control server with local fallback
    # ------------------------------------------------------------------
    print(f"INFO: Fetching AKTA results for experiment {experiment_id} from control server.")
    akta_data = fetch_akta_results(str(experiment_id))

    if akta_data is None:
        print("INFO: Falling back to local AKTA result search.")
        akta_data = _load_akta_from_local(str(experiment_id), results_folder_path)

    if akta_data is None:
        analysis_results["status"] = "success"
        analysis_results["message"] = (
            "AKTA data not available - analysis skipped. "
            "No results on server and no suitable local files found."
        )
        print(f"INFO: {analysis_results['message']}")
    else:
        # ------------------------------------------------------------------
        # Prepare time series and detect peaks
        # ------------------------------------------------------------------
        time_s, uv_mau, cond_mscm = _prepare_akta_timeseries(akta_data)

        # Collect raw time series into DataFrame and save
        ts_dict: Dict[str, Any] = {"time_s": time_s, "time_min": time_s / 60.0, "uv1_mau": uv_mau}
        if cond_mscm is not None:
            ts_dict["cond_mscm"] = cond_mscm

        ts_df = pd.DataFrame(ts_dict)
        ts_csv_name = f"akta_timeseries_{experiment_id}.csv"
        ts_csv_path = results_folder_path / ts_csv_name
        ts_df.to_csv(ts_csv_path, index=False)
        print(f"INFO: AKTA time series saved to: {ts_csv_path}")
        analysis_results["data_outputs"]["timeseries_csv"] = str(ts_csv_path.resolve())
        analysis_results["files_processed"] += 1

        # Peak detection
        peak_table = _detect_peaks(time_s, uv_mau)

        if peak_table.empty:
            print("INFO: No peaks detected in UV trace (after heuristic filtering).")
            analysis_results["metadata"]["peaks_detected"] = 0
        else:
            peak_csv_name = f"akta_peaks_{experiment_id}.csv"
            peak_csv_path = results_folder_path / peak_csv_name
            peak_table.to_csv(peak_csv_path, index=False)
            analysis_results["data_outputs"]["peaks_csv"] = str(peak_csv_path.resolve())
            analysis_results["metadata"]["peaks_detected"] = int(len(peak_table))
            print(f"INFO: Detected {len(peak_table)} peaks. Peak table saved to: {peak_csv_path}")
            analysis_results["files_processed"] += 1

        # ------------------------------------------------------------------
        # Plot chromatogram + annotate peaks if available
        # ------------------------------------------------------------------
        plot_path = _plot_chromatogram(
            experiment_id=str(experiment_id),
            time_s=time_s,
            uv_mau=uv_mau,
            cond_mscm=cond_mscm,
            peak_table=peak_table,
            results_folder_path=results_folder_path,
        )
        analysis_results["plots"]["chromatogram_png"] = plot_path
        analysis_results["files_processed"] += 1

        analysis_results["status"] = "success"
        analysis_results["message"] = "AKTA chromatogram analysis completed successfully."

    # ----------------------------------------------------------------------
    # Save analysis_results JSON
    # ----------------------------------------------------------------------
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, "w") as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Analysis results JSON saved to: {results_json_path}")

    return analysis_results


# -----------------------------------------------------------------------------
# CLI entry point
# -----------------------------------------------------------------------------


def main() -> int:
    """Command line interface."""
    parser = argparse.ArgumentParser(description="Analyze AKTA experiment data")
    parser.add_argument("experiment_id", nargs="?", help="Experiment ID. If omitted, auto-detects the most recent.")
    parser.add_argument(
        "--data-folder",
        default="../data",
        help="Path to the folder containing experiment_*.json files. Default: ../data",
    )
    parser.add_argument(
        "--results-folder",
        default="../results",
        help="Path to the folder where analysis outputs will be saved. Default: ../results",
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
        print(f"ERROR: Unhandled exception during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
