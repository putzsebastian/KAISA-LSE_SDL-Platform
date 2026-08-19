#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

This script analyzes AKTA Pure chromatography runs:
- Loads experiment metadata from experiment_<ID>.json
- Fetches AKTA chromatogram data from AKTA control server or local files
- Plots UV280 and conductivity vs time
- Detects peaks on the UV trace and determines retention times
- Labels detected peaks (with retention time) on the chromatogram plot
- Saves processed data and analysis results to the results folder

Conforms to Orbit automation conventions:
- ASCII-only logging output
- Robust error handling with informative messages
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

# Device control server configuration for AKTA
AKTA_CONTROL_SERVER = os.getenv('AKTA_CONTROL_SERVER', 'http://localhost:5001')
AKTA_API_KEY = os.getenv('AKTA_API_KEY', 'akta-control-key')


def fetch_akta_results(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results from the control server.

    Returns a dict with keys similar to akta_results.json or None if unavailable.
    This function should NEVER raise for typical connectivity / 404 issues; it
    logs a warning and returns None instead so that the caller can fall back
    to local files gracefully.
    """
    try:
        headers = {'X-API-Key': AKTA_API_KEY}
        url = f'{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}'
        print(f"INFO: Fetching AKTA results from control server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return data.get('results', {}) or {}
        else:
            print(f'WARNING: AKTA results not found on server (status {response.status_code})')
            return None
    except Exception as e:
        print(f'WARNING: Could not reach AKTA control server: {e}')
        return None


def load_experiment_json(experiment_id: str, data_folder: str) -> Dict[str, Any]:
    """Load experiment_<ID>.json from the given data folder with error handling.

    Also supports the alternate location in the project root (../experiment_<ID>.json)
    as a first attempt, then falls back to data_folder.
    """
    root_candidate = Path('..') / f'experiment_{experiment_id}.json'
    data_candidate = Path(data_folder) / f'experiment_{experiment_id}.json'

    if root_candidate.exists():
        data_file_path = root_candidate
    else:
        data_file_path = data_candidate

    print(f"INFO: Loading experiment JSON from: {data_file_path}")
    try:
        with open(data_file_path, 'r') as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    return experiment_data


def extract_metadata_fields(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract eLabFTW extra_fields from experiment_data with validation.

    Returns a flat dict with the parsed values where available.
    Missing fields are not fatal for this AKTA analysis but will be logged.
    """
    metadata_values: Dict[str, Any] = {}

    try:
        extra_fields = experiment_data['metadata_decoded']['extra_fields']
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    # List of expected fields (name in eLab extra_fields)
    expected_fields = [
        'Process ID',
        'CIP_DURATION',
        'WASH_DURATION',
        'CIP_3_DURATION',
        'CIP_4_DURATION',
        'CIP_5_DURATION',
        'WASH_FLOW_RATE',
        'CIP_2_HOLD_DURATION',
        'LOAD_INJECT_DURATION',
        'LOAD_INJECT_FLOW_RATE',
        'EQUILIBRATION_DURATION',
        'ELUTE_GRADIENT_DURATION',
        'EQUILIBRATION_FLOW_RATE',
        'ELUTE_GRADIENT_GRADIENT_END',
        'ELUTE_GRADIENT_GRADIENT_START',
    ]

    for field in expected_fields:
        field_entry = extra_fields.get(field)
        if field_entry is None:
            print(f"WARNING: Metadata field '{field}' is missing in extra_fields.")
            continue
        value = field_entry.get('value')
        metadata_values[field] = value

    return metadata_values


def ensure_results_folder(results_folder: str) -> Path:
    """Create results folder if it does not exist and return its Path."""
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)
    return results_folder_path


def load_akta_data_local(experiment_id: str, results_folder_path: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA data from local files as a fallback.

    Priority:
    1) ../results/akta_results_<ID>.json
    2) data.csv in current working directory

    Returns a dict in AKTA JSON structure or None if neither is available.
    """
    akta_json = results_folder_path / f'akta_results_{experiment_id}.json'
    akta_csv = Path('data.csv')

    if akta_json.exists():
        print(f"INFO: Loading AKTA JSON results from local file: {akta_json}")
        try:
            with open(akta_json, 'r') as f:
                akta_data = json.load(f)
            return akta_data
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA JSON {akta_json}: {e}")

    if akta_csv.exists():
        print(f"INFO: Loading AKTA CSV data from local file: {akta_csv}")
        try:
            df = pd.read_csv(akta_csv, na_values=['-'])
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA CSV {akta_csv}: {e}")
            return None

        akta_data: Dict[str, Any] = {}
        for col in df.columns:
            series = df[col]
            # Convert NaN to None for JSON-serializable output
            akta_data[col] = [None if pd.isna(v) else float(v) for v in series]
        # Align with expected JSON-like structure
        # Expect at least 'time' and maybe 'uv1', 'cond', etc.
        akta_data.setdefault('signals', [c for c in df.columns if c != 'time'])
        return akta_data

    print("INFO: No local AKTA results file found (neither akta_results_<ID>.json nor data.csv)")
    return None


def harmonize_akta_data(raw_data: Dict[str, Any]) -> Dict[str, np.ndarray]:
    """Convert raw AKTA dict (from server or local) into numpy arrays.

    Ensures presence of 'time' and at least one UV signal (uv1) if available.
    Missing vectors are represented as empty arrays.
    """
    time_list = raw_data.get('time') or raw_data.get('Time')
    if time_list is None:
        raise ValueError("AKTA data does not contain 'time' field.")

    time = np.array([np.nan if v is None else float(v) for v in time_list], dtype=float)

    def to_array(key: str) -> np.ndarray:
        vals = raw_data.get(key)
        if vals is None:
            return np.array([], dtype=float)
        return np.array([np.nan if v is None else float(v) for v in vals], dtype=float)

    uv1 = to_array('uv1')
    cond = to_array('cond')

    data_arrs: Dict[str, np.ndarray] = {
        'time': time,
        'uv1': uv1,
        'cond': cond,
    }

    return data_arrs


def detect_peaks(time: np.ndarray, signal: np.ndarray,
                 min_prominence: float = 5.0,
                 min_distance_points: int = 10) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Detect peaks in the given signal using scipy.signal.find_peaks.

    Args:
        time: 1D array of time values (seconds).
        signal: 1D array of signal values (same length as time).
        min_prominence: Minimum prominence for a peak to be considered meaningful.
        min_distance_points: Minimum distance (in data points) between peaks.

    Returns:
        peaks_indices: Indices of detected peaks.
        properties: Dict returned by find_peaks (heights, prominences, etc.).
    """
    if time.size == 0 or signal.size == 0 or time.size != signal.size:
        return np.array([], dtype=int), {}

    # Replace NaN with median (or zero if all NaN) to avoid breaking find_peaks
    clean_signal = signal.copy()
    if np.all(np.isnan(clean_signal)):
        return np.array([], dtype=int), {}
    median_val = np.nanmedian(clean_signal)
    clean_signal[np.isnan(clean_signal)] = median_val

    peaks_indices, properties = find_peaks(
        clean_signal,
        prominence=min_prominence,
        distance=min_distance_points,
    )

    return peaks_indices, properties


def plot_chromatogram(
    experiment_id: str,
    time: np.ndarray,
    uv1: np.ndarray,
    cond: np.ndarray,
    peaks_indices: Optional[np.ndarray] = None,
    results_folder_path: Optional[Path] = None,
) -> Dict[str, str]:
    """Create chromatogram plot with UV280 and conductivity.

    If peaks_indices is provided and non-empty, label peaks with retention time.

    Returns:
        Dict with keys 'png' and 'pdf' mapping to saved file paths.
    """
    if results_folder_path is None:
        results_folder_path = Path('.')

    # Prepare time in minutes for plotting
    time_min = time / 60.0

    fig, ax1 = plt.subplots(figsize=(10, 5))

    # Plot UV280 (uv1) in blue
    if uv1.size > 0:
        ax1.plot(time_min, uv1, 'b-', linewidth=0.8, label='UV 280 nm')
    ax1.set_xlabel('Time (min)')
    ax1.set_ylabel('UV Absorbance (mAU)', color='b')
    ax1.tick_params(axis='y', labelcolor='b')

    # Plot conductivity on secondary axis in red
    ax2 = None
    if cond.size > 0:
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond, 'r-', linewidth=0.8, alpha=0.7, label='Conductivity')
        ax2.set_ylabel('Conductivity (mS/cm)', color='r')
        ax2.tick_params(axis='y', labelcolor='r')

    # Peak labeling
    if peaks_indices is not None and peaks_indices.size > 0 and uv1.size > 0:
        for idx in peaks_indices:
            if idx < 0 or idx >= time_min.size:
                continue
            rt_min = time_min[idx]
            y_val = uv1[idx]
            ax1.plot(rt_min, y_val, 'ko', markersize=3)
            label = f"{rt_min:.2f} min"
            ax1.annotate(
                label,
                xy=(rt_min, y_val),
                xytext=(0, 5),
                textcoords='offset points',
                ha='center',
                fontsize=7,
                rotation=90,
            )

    ax1.set_title(f'AKTA Chromatogram - Experiment {experiment_id}')
    fig.tight_layout()

    plots: Dict[str, str] = {}
    png_path = results_folder_path / f'chromatogram_{experiment_id}.png'
    pdf_path = results_folder_path / f'chromatogram_{experiment_id}.pdf'

    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"INFO: Saved chromatogram plot PNG to: {png_path}")
    print(f"INFO: Saved chromatogram plot PDF to: {pdf_path}")

    plots['png'] = str(png_path.resolve())
    plots['pdf'] = str(pdf_path.resolve())

    return plots


def auto_detect_latest_experiment_id(data_folder: str) -> Optional[str]:
    """Auto-detect the most recent experiment_<ID>.json in data_folder or project root.

    Returns the detected experiment_id as string, or None if none found.
    """
    candidates: List[Path] = []

    root_path = Path('..')
    data_path = Path(data_folder)

    # Collect from root and data folder
    candidates.extend(root_path.glob('experiment_*.json'))
    if data_path.exists():
        candidates.extend(data_path.glob('experiment_*.json'))

    if not candidates:
        return None

    # Sort by modification time, newest first
    candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
    latest = candidates[0]
    name = latest.stem  # experiment_<ID>
    try:
        experiment_id = name.split('_', 1)[1]
    except IndexError:
        return None
    print(f"INFO: Auto-detected latest experiment ID: {experiment_id} from file {latest}")
    return experiment_id


def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = '../data',
                       results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography experiments.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, attempts to auto-detect.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with all key metrics and paths to generated files.
    """
    results_folder_path = ensure_results_folder(results_folder)

    # Initialize results structure
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
        # Try sys.argv position 1 (CLI-like usage inside programmatic call)
        if len(sys.argv) > 1 and sys.argv[1] not in ('', '-'):  # simple guard
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from sys.argv[1]: {experiment_id}")
        else:
            experiment_id = auto_detect_latest_experiment_id(data_folder)
            if experiment_id is None:
                msg = "Could not auto-detect experiment ID: no experiment_*.json files found."
                analysis_results["message"] = msg
                analysis_results["status"] = "failed"
                print(f"ERROR: {msg}")
                return analysis_results
        analysis_results["experiment_id"] = experiment_id

    # Load experiment JSON
    try:
        experiment_data = load_experiment_json(experiment_id, data_folder)
    except Exception as e:
        msg = f"Failed to load experiment JSON: {e}"
        analysis_results["message"] = msg
        analysis_results["status"] = "failed"
        print(f"ERROR: {msg}")
        return analysis_results

    # Extract metadata fields (non-critical for AKTA analysis but useful)
    try:
        metadata_values = extract_metadata_fields(experiment_data)
        analysis_results["metadata"].update(metadata_values)
    except KeyError as e:
        # For this AKTA script we treat this as a hard error because the
        # specification requires validating the field existence.
        msg = f"Metadata extraction failed: {e}"
        analysis_results["message"] = msg
        analysis_results["status"] = "failed"
        print(f"ERROR: {msg}")
        return analysis_results

    # Try fetching AKTA results from device server first
    akta_raw = fetch_akta_results(str(experiment_id))

    # Fallback to local files if server data is unavailable or empty
    if not akta_raw:
        print("INFO: Using local AKTA data fallback (if available)...")
        akta_raw = load_akta_data_local(str(experiment_id), results_folder_path)

    if not akta_raw:
        msg = (
            "AKTA data not available on server or local files - analysis skipped. "
            "This can be expected for dry runs or configuration tests."
        )
        analysis_results["message"] = msg
        analysis_results["status"] = "success"
        print(f"INFO: {msg}")

        # Save analysis results JSON even when analysis is skipped
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Harmonize AKTA data into numpy arrays
    try:
        data_arrs = harmonize_akta_data(akta_raw)
    except Exception as e:
        msg = f"Failed to harmonize AKTA data: {e}"
        analysis_results["message"] = msg
        analysis_results["status"] = "failed"
        print(f"ERROR: {msg}")

        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    time = data_arrs['time']
    uv1 = data_arrs['uv1']
    cond = data_arrs['cond']

    # Basic validation
    if time.size == 0:
        msg = "AKTA data contains no time points - cannot analyze chromatogram."
        analysis_results["message"] = msg
        analysis_results["status"] = "failed"
        print(f"ERROR: {msg}")

        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Peak detection on UV signal (if present)
    peaks_indices = np.array([], dtype=int)
    peak_properties: Dict[str, Any] = {}
    if uv1.size > 0:
        print("INFO: Detecting peaks on UV 280 nm trace...")
        # Estimate a reasonable prominence threshold based on data spread
        finite_uv = uv1[np.isfinite(uv1)]
        if finite_uv.size > 0:
            approx_range = np.nanmax(finite_uv) - np.nanmin(finite_uv)
            # Set prominence to 5% of range, but not below a minimum absolute value
            auto_prominence = max(approx_range * 0.05, 5.0)
        else:
            auto_prominence = 5.0

        # Use about 1% of total points (min 5) as minimal distance
        min_distance_points = max(int(time.size * 0.01), 5)

        peaks_indices, peak_properties = detect_peaks(
            time=time,
            signal=uv1,
            min_prominence=auto_prominence,
            min_distance_points=min_distance_points,
        )
        print(f"INFO: Detected {peaks_indices.size} peak(s) on UV 280 nm trace.")
    else:
        print("WARNING: No UV 280 nm data available for peak detection.")

    # Compute retention times for detected peaks (in minutes)
    peak_info_list: List[Dict[str, Any]] = []
    if peaks_indices.size > 0:
        time_min = time / 60.0
        for idx in peaks_indices:
            if idx < 0 or idx >= time_min.size:
                continue
            rt_min = float(time_min[idx])
            height = float(uv1[idx]) if uv1.size > idx else float('nan')
            prominence = float(peak_properties.get('prominences', [np.nan] * len(peaks_indices))[list(peaks_indices).index(idx)]) if 'prominences' in peak_properties else float('nan')
            peak_info = {
                'index': int(idx),
                'retention_time_min': rt_min,
                'height_mAU': height,
                'prominence_mAU': prominence,
            }
            peak_info_list.append(peak_info)

    analysis_results['peaks'] = peak_info_list

    # Save processed chromatogram data as CSV
    processed_df = pd.DataFrame({
        'time_s': time,
        'time_min': time / 60.0,
        'uv1_mAU': uv1 if uv1.size == time.size else np.full_like(time, np.nan),
        'cond_mS_cm': cond if cond.size == time.size else np.full_like(time, np.nan),
    })

    chromatogram_csv_path = results_folder_path / f'akta_chromatogram_processed_{experiment_id}.csv'
    processed_df.to_csv(chromatogram_csv_path, index=False)
    print(f"INFO: Saved processed chromatogram CSV to: {chromatogram_csv_path}")

    analysis_results['data_outputs']['chromatogram_csv'] = str(chromatogram_csv_path.resolve())
    analysis_results['files_processed'] = 1

    # Generate chromatogram plot (with peak labels only if peaks exist)
    plots = plot_chromatogram(
        experiment_id=str(experiment_id),
        time=time,
        uv1=uv1,
        cond=cond,
        peaks_indices=peaks_indices if peaks_indices.size > 0 else None,
        results_folder_path=results_folder_path,
    )
    analysis_results['plots'].update({'chromatogram': plots})

    # Final status and message
    if peak_info_list:
        msg = f"Analysis complete. Detected {len(peak_info_list)} peak(s)."
    else:
        msg = "Analysis complete. No peaks detected above the threshold."
    analysis_results['message'] = msg
    analysis_results['status'] = 'success'

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, 'w') as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze AKTA chromatography experiment data')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID. If not provided, attempts to auto-detect the most recent.')
    parser.add_argument('--data-folder', default='../data', help='Path to the folder containing experiment_ID.json files. Default: ../data')
    parser.add_argument('--results-folder', default='../results', help='Path to the folder where all analysis outputs will be saved. Default: ../results')

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
