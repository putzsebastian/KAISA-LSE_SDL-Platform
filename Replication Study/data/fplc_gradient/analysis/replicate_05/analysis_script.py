#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.
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

# AKTA control server configuration (can be overridden by environment)
import requests

AKTA_CONTROL_SERVER = os.getenv('AKTA_CONTROL_SERVER', 'http://localhost:5001')
AKTA_API_KEY = os.getenv('AKTA_API_KEY', 'akta-control-key')


def fetch_akta_results_from_server(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results JSON from the AKTA control server.

    Returns a dict with the "results" payload or None if not available.
    This function must NOT raise on network issues; instead it should
    log a warning and return None so the caller can fall back to local files.
    """
    try:
        headers = {'X-API-Key': AKTA_API_KEY}
        url = f'{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}'
        print(f"INFO: Requesting AKTA results from server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            results = data.get('results') or data
            print("INFO: AKTA results successfully retrieved from server.")
            return results
        else:
            print(f"WARNING: AKTA results not found on server (status {response.status_code}).")
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def load_experiment_json(experiment_id: str, data_folder: str) -> Dict[str, Any]:
    """Load experiment JSON, first from project root ../experiment_ID.json,
    then from data_folder/experiment_ID.json.
    """
    root_path = Path('..') / f'experiment_{experiment_id}.json'
    if root_path.exists():
        data_file_path = root_path
    else:
        data_file_path = Path(data_folder) / f'experiment_{experiment_id}.json'

    print(f"INFO: Loading experiment JSON from: {data_file_path}")
    try:
        with open(data_file_path, 'r') as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    return experiment_data


def extract_elab_metadata(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract required eLabFTW extra fields with error handling.

    Returns a dict with the raw extra_fields content under key "extra_fields".
    Raises KeyError if the metadata_decoded / extra_fields structure is missing.
    """
    try:
        metadata = experiment_data['metadata_decoded']['extra_fields']
        if not isinstance(metadata, dict):
            raise TypeError("metadata_decoded.extra_fields is not a dict")
    except KeyError as e:
        raise KeyError(f"Missing expected metadata field structure: {e}")
    except TypeError as e:
        raise TypeError(f"Invalid metadata structure: {e}")

    return {"extra_fields": metadata}


def ensure_results_folder(results_folder: str) -> Path:
    """Ensure that the results folder exists and return its Path."""
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)
    return results_folder_path


def save_analysis_results_json(analysis_results: Dict[str, Any], results_folder_path: Path) -> Path:
    """Save analysis_results dict as JSON in the results folder."""
    experiment_id = analysis_results.get("experiment_id", "unknown")
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, 'w') as f:
        json.dump(analysis_results, f, indent=4)
    print(f"INFO: Saved analysis results JSON to: {results_json_path}")
    return results_json_path


def load_akta_results_local(experiment_id: str, results_folder_path: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local files.

    Priority:
      1) ../results/akta_results_{experiment_id}.json
      2) data.csv in the current working directory
    """
    # 1) Try dedicated akta_results json in ../results or in provided results_folder
    candidate_paths: List[Path] = []
    candidate_paths.append(Path('../results') / f'akta_results_{experiment_id}.json')
    candidate_paths.append(results_folder_path / f'akta_results_{experiment_id}.json')

    for p in candidate_paths:
        if p.exists():
            try:
                print(f"INFO: Loading AKTA JSON results from: {p}")
                with open(p, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"WARNING: Failed to read AKTA JSON file {p}: {e}")

    # 2) Fallback: data.csv in current working directory
    csv_path = Path('data.csv')
    if csv_path.exists():
        try:
            print(f"INFO: Loading AKTA CSV results from: {csv_path}")
            df = pd.read_csv(csv_path, na_values=['-'])
            # Convert to dict-of-lists, dropping NaN where appropriate
            akta_data: Dict[str, Any] = {}
            for col in df.columns:
                series = df[col]
                if series.dtype.kind in 'biufc':
                    akta_data[col] = series.where(series.notna()).tolist()
                else:
                    akta_data[col] = series.astype(object).where(series.notna()).tolist()
            return akta_data
        except Exception as e:
            print(f"WARNING: Failed to read AKTA CSV file {csv_path}: {e}")

    print("INFO: No local AKTA results file found.")
    return None


def prepare_akta_timeseries(akta_data: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Convert raw AKTA data to numpy arrays for time, UV280, and conductivity.

    Returns (time_min, uv1, cond) where time_min is time in minutes,
    uv1 is an array of floats (mAU), cond is an array of floats (mS/cm) or None
    if conductivity is not available.
    """
    if 'time' not in akta_data:
        raise KeyError("AKTA data does not contain 'time' field.")

    time_raw = np.array(akta_data['time'], dtype=float)
    time_min = time_raw / 60.0

    # UV at 280 nm is typically under key 'uv1'
    if 'uv1' not in akta_data:
        raise KeyError("AKTA data does not contain 'uv1' (UV 280 nm) field.")

    uv_raw = np.array([
        np.nan if v is None else v for v in akta_data.get('uv1', [])
    ], dtype=float)

    cond = None
    if 'cond' in akta_data:
        cond = np.array([
            np.nan if v is None else v for v in akta_data.get('cond', [])
        ], dtype=float)

    return time_min, uv_raw, cond


def detect_peaks(time_min: np.ndarray, uv: np.ndarray) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """Detect peaks in UV chromatogram.

    Uses scipy.signal.find_peaks with basic prominence-based detection.
    Returns (peak_indices, properties) where peak_indices is an array of
    indices into time_min / uv and properties is the dict returned by
    find_peaks.
    """
    if time_min.size == 0 or uv.size == 0:
        return np.array([], dtype=int), {}

    # Basic preprocessing: replace NaN with median or zero
    uv_clean = uv.copy()
    if np.isnan(uv_clean).all():
        return np.array([], dtype=int), {}
    median_val = np.nanmedian(uv_clean)
    uv_clean[np.isnan(uv_clean)] = median_val

    # Set prominence relative to the dynamic range to avoid spurious peaks
    dynamic_range = float(np.nanmax(uv_clean) - np.nanmin(uv_clean))
    if dynamic_range <= 0:
        return np.array([], dtype=int), {}

    prominence = max(dynamic_range * 0.05, 0.1)  # 5 percent of range or at least 0.1 mAU
    distance_points = max(int(len(uv_clean) * 0.01), 1)  # at least 1 percent of points apart

    peak_indices, properties = find_peaks(uv_clean, prominence=prominence, distance=distance_points)
    return peak_indices, properties


def plot_chromatogram(
    experiment_id: str,
    time_min: np.ndarray,
    uv: np.ndarray,
    cond: Optional[np.ndarray],
    peak_indices: np.ndarray,
    results_folder_path: Path,
) -> Dict[str, str]:
    """Plot chromatogram with UV and conductivity.

    Peaks are marked and labeled with retention time if any are detected.
    Returns dict with paths to generated plot files.
    """
    plot_paths: Dict[str, str] = {}

    if time_min.size == 0 or uv.size == 0:
        print("WARNING: Empty chromatogram data. No plot generated.")
        return plot_paths

    fig, ax1 = plt.subplots(figsize=(10, 5))

    ax1.plot(time_min, uv, color='blue', linewidth=0.8, label='UV 280 nm')
    ax1.set_xlabel('Time (min)')
    ax1.set_ylabel('UV Absorbance (mAU)', color='blue')
    ax1.tick_params(axis='y', labelcolor='blue')

    # Secondary axis for conductivity if available
    if cond is not None and cond.size == time_min.size:
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond, color='red', linewidth=0.8, alpha=0.7, label='Conductivity')
        ax2.set_ylabel('Conductivity (mS/cm)', color='red')
        ax2.tick_params(axis='y', labelcolor='red')
    else:
        ax2 = None

    # Peak markers and labels, only if peaks were detected
    if peak_indices.size > 0:
        peak_times = time_min[peak_indices]
        peak_heights = uv[peak_indices]
        ax1.scatter(peak_times, peak_heights, color='black', s=30, zorder=5, label='Peaks')

        # Label peaks with retention time
        for t, h in zip(peak_times, peak_heights):
            ax1.annotate(
                f"{t:.2f} min",
                xy=(t, h),
                xytext=(0, 6),
                textcoords='offset points',
                ha='center',
                fontsize=8,
                rotation=90,
            )

    ax1.set_title(f'AKTA Chromatogram - Experiment {experiment_id}')
    fig.tight_layout()

    png_path = results_folder_path / f'chromatogram_{experiment_id}.png'
    pdf_path = results_folder_path / f'chromatogram_{experiment_id}.pdf'

    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"INFO: Saved chromatogram plot PNG to: {png_path}")
    print(f"INFO: Saved chromatogram plot PDF to: {pdf_path}")

    plot_paths['chromatogram_png'] = str(png_path.resolve())
    plot_paths['chromatogram_pdf'] = str(pdf_path.resolve())

    return plot_paths


def save_processed_chromatogram_data(
    experiment_id: str,
    time_min: np.ndarray,
    uv: np.ndarray,
    cond: Optional[np.ndarray],
    peak_indices: np.ndarray,
    results_folder_path: Path,
) -> str:
    """Save processed chromatogram data, including peak annotations, as CSV.

    Returns absolute path to the saved CSV file.
    """
    data: Dict[str, Any] = {
        'time_min': time_min,
        'uv_mAU': uv,
    }
    if cond is not None and cond.size == time_min.size:
        data['cond_mS_cm'] = cond

    df = pd.DataFrame(data)

    # Add a boolean and retention_time_min column for peaks
    is_peak = np.zeros(len(df), dtype=bool)
    is_peak[peak_indices] = True
    df['is_peak'] = is_peak

    # Retention time is simply the time_min at peaks; others NaN
    retention_time = np.full(len(df), np.nan, dtype=float)
    if peak_indices.size > 0:
        retention_time[peak_indices] = time_min[peak_indices]
    df['retention_time_min'] = retention_time

    csv_path = results_folder_path / f'chromatogram_processed_{experiment_id}.csv'
    df.to_csv(csv_path, index=False)
    print(f"INFO: Saved processed chromatogram data to: {csv_path}")

    return str(csv_path.resolve())


def auto_detect_latest_experiment_id(data_folder: str) -> Optional[str]:
    """Auto-detect the most recent experiment_ID.json file in data_folder or project root.

    Searches both ../ for experiment_*.json and the provided data_folder.
    Returns the detected experiment ID as string, or None if no files are found.
    """
    candidates: List[Path] = []
    # Search in project root
    root = Path('..')
    candidates.extend(root.glob('experiment_*.json'))
    # Search in specified data_folder
    data_path = Path(data_folder)
    if data_path.exists():
        candidates.extend(data_path.glob('experiment_*.json'))

    if not candidates:
        return None

    # Pick most recently modified
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    name = latest.stem  # experiment_1234
    if '_' in name:
        return name.split('_', 1)[1]
    return None


def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = '../data',
                       results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography runs.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries to auto-detect.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with all key metrics and paths to generated files.
    """
    results_folder_path = ensure_results_folder(results_folder)

    # Initialize analysis results structure
    analysis_results: Dict[str, Any] = {
        "experiment_id": experiment_id,
        "status": "failed",
        "message": "",
        "plots": {},
        "data_outputs": {},
        "metadata": {},
        "files_processed": 0,
    }

    # Determine experiment ID if not provided
    if experiment_id is None:
        # Try CLI argument first (when called without direct argument)
        if len(sys.argv) > 1 and sys.argv[1] not in ('', None):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            experiment_id = auto_detect_latest_experiment_id(data_folder)
            if experiment_id is None:
                analysis_results["message"] = (
                    "Could not auto-detect experiment ID: no experiment_*.json files found."
                )
                print(f"ERROR: {analysis_results['message']}")
                return analysis_results
            print(f"INFO: Auto-detected latest experiment ID: {experiment_id}")
        analysis_results["experiment_id"] = experiment_id
    else:
        analysis_results["experiment_id"] = experiment_id

    # Load experiment JSON to access metadata and for traceability
    try:
        experiment_data = load_experiment_json(experiment_id, data_folder)
        analysis_results["metadata"]["experiment_json_loaded"] = True
    except Exception as e:
        analysis_results["message"] = f"Failed to load experiment JSON: {e}"
        print(f"ERROR: {analysis_results['message']}")
        return analysis_results

    # Extract eLabFTW metadata extras (structure existence check)
    try:
        elab_meta = extract_elab_metadata(experiment_data)
        analysis_results["metadata"]["extra_fields_present"] = True
        analysis_results["metadata"]["extra_fields_keys"] = list(elab_meta["extra_fields"].keys())
    except Exception as e:
        # This is not fatal for chromatogram plotting; log and continue.
        print(f"WARNING: Could not extract eLabFTW extra fields: {e}")
        analysis_results["metadata"]["extra_fields_present"] = False
        analysis_results["metadata"]["extra_fields_error"] = str(e)

    # Try to get AKTA results from control server first
    akta_data = fetch_akta_results_from_server(experiment_id)

    # Fallback to local files if server did not provide data
    if akta_data is None:
        akta_data = load_akta_results_local(experiment_id, results_folder_path)

    if akta_data is None:
        analysis_results["status"] = "success"
        analysis_results["message"] = (
            "AKTA data not available from server or local files - chromatogram analysis skipped."
        )
        print(f"INFO: {analysis_results['message']}")
        save_analysis_results_json(analysis_results, results_folder_path)
        return analysis_results

    # Prepare time-series arrays
    try:
        time_min, uv, cond = prepare_akta_timeseries(akta_data)
    except Exception as e:
        analysis_results["message"] = f"Failed to prepare AKTA time-series data: {e}"
        print(f"ERROR: {analysis_results['message']}")
        save_analysis_results_json(analysis_results, results_folder_path)
        return analysis_results

    # Detect peaks in UV chromatogram
    peak_indices, peak_props = detect_peaks(time_min, uv)
    retention_times: List[float] = []
    if peak_indices.size > 0:
        retention_times = [float(t) for t in time_min[peak_indices]]
        print("INFO: Detected peaks at retention times (min): " + ", ".join(f"{t:.2f}" for t in retention_times))
    else:
        print("INFO: No significant peaks detected in UV chromatogram.")

    # Save processed chromatogram data
    processed_csv_path = save_processed_chromatogram_data(
        experiment_id=experiment_id,
        time_min=time_min,
        uv=uv,
        cond=cond,
        peak_indices=peak_indices,
        results_folder_path=results_folder_path,
    )

    # Plot chromatogram with UV and conductivity, label peaks if present
    plot_paths = plot_chromatogram(
        experiment_id=experiment_id,
        time_min=time_min,
        uv=uv,
        cond=cond,
        peak_indices=peak_indices,
        results_folder_path=results_folder_path,
    )

    # Populate analysis_results
    analysis_results["status"] = "success"
    analysis_results["message"] = "AKTA chromatogram analysis completed."
    analysis_results["data_outputs"]["processed_chromatogram_csv"] = processed_csv_path
    analysis_results["plots"].update(plot_paths)
    analysis_results["metadata"]["num_points"] = int(len(time_min))
    analysis_results["metadata"]["num_peaks"] = int(len(retention_times))
    analysis_results["metadata"]["retention_times_min"] = retention_times
    analysis_results["files_processed"] = 1

    # Save final analysis results JSON
    save_analysis_results_json(analysis_results, results_folder_path)

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze AKTA chromatography experiment data.')
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
            print("Analysis successful!")
            return 0
        else:
            print(f"Analysis failed: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
