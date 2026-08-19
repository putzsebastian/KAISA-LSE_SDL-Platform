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

import requests

# Device usage context for dispatcher (filled by workflow wizard in real runs)
MS_SAMPLER = 'none'  # 'ESI' | 'M5' | 'none'
MS_BATCH_GRANULARITY = 'none'  # 'per_well' | 'all_in_one' | 'none'
MS_BATCH_CONFIG = 'none (no MS acquisition in this workflow)'
DEVICES_USED = ['AKTA Pure']

# AKTA control server configuration (see section 5.2 of spec)
AKTA_CONTROL_SERVER = os.getenv('AKTA_CONTROL_SERVER', 'http://localhost:5001')
AKTA_API_KEY = os.getenv('AKTA_API_KEY', 'akta-control-key')


def fetch_akta_results(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results from the control server.

    Returns a dict with key 'results' structure or None if not available.
    """
    try:
        headers = {'X-API-Key': AKTA_API_KEY}
        url = f"{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}"
        print(f"INFO: Requesting AKTA results from {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', {})
            if results:
                print("INFO: AKTA results retrieved from control server.")
            else:
                print("WARNING: AKTA server response did not contain 'results' field or it was empty.")
            return results
        else:
            print(f"WARNING: AKTA results not found on server (status {response.status_code}).")
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def _safe_get_metadata_field(metadata: Dict[str, Any], field: str) -> Optional[Any]:
    """Safely get a value from eLabFTW extra_fields dict.

    Returns None if field or 'value' key is missing.
    """
    try:
        entry = metadata.get(field, None)
        if entry is None:
            return None
        return entry.get('value', None)
    except Exception:
        return None


def _load_experiment_json(experiment_id: str, data_folder: str) -> Dict[str, Any]:
    """Load experiment JSON from root first, then fallback to data_folder.

    Raises FileNotFoundError or ValueError with informative messages.
    """
    # Root-first lookup
    root_path = Path('..') / f"experiment_{experiment_id}.json"
    data_folder_path = Path(data_folder) / f"experiment_{experiment_id}.json"

    candidates = [root_path, data_folder_path]
    last_err: Optional[Exception] = None

    for path in candidates:
        try:
            if path.is_file():
                print(f"INFO: Loading experiment JSON from {path}")
                with open(path, 'r') as f:
                    return json.load(f)
        except json.JSONDecodeError as e:
            last_err = ValueError(f"Invalid JSON format in file {path}: {e}")
            break
        except Exception as e:  # unexpected I/O
            last_err = e

    if last_err is not None:
        raise last_err

    raise FileNotFoundError(f"Experiment JSON not found in root or data folder for experiment_id={experiment_id}.")


def _auto_detect_experiment_id(data_folder: str) -> str:
    """Auto-detect the most recent experiment_ID.json in data_folder.

    Looks in root ('..') and data_folder, returns experiment id string.
    Raises RuntimeError if no JSON files are found.
    """
    candidates: List[Path] = []
    for base in (Path('..'), Path(data_folder)):
        if base.exists() and base.is_dir():
            candidates.extend(base.glob('experiment_*.json'))

    if not candidates:
        raise RuntimeError("No experiment_*.json files found for auto-detection.")

    candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
    latest = candidates[0]
    name = latest.stem  # 'experiment_1234'
    try:
        exp_id = name.split('_', 1)[1]
    except Exception:
        raise RuntimeError(f"Cannot parse experiment id from filename {latest.name}.")

    print(f"INFO: Auto-detected experiment ID {exp_id} from {latest}")
    return exp_id


def _prepare_results_dict(experiment_id: Optional[str]) -> Dict[str, Any]:
    """Initialize the analysis_results dict."""
    return {
        "experiment_id": experiment_id,
        "status": "failed",
        "message": "",
        "plots": {},
        "data_outputs": {},
        "metadata": {},
        "files_processed": 0,
        "warnings": [],
    }


def _parse_akta_data_from_server_or_local(
    experiment_id: str,
    results_folder_path: Path,
) -> Optional[Dict[str, Any]]:
    """Obtain AKTA data, preferring control server, then local JSON/CSV.

    Returns a dict-like data structure compatible with the AKTA JSON schema
    ({'time': [...], 'uv1': [...], 'cond': [...], 'signals': [...], ...})
    or None if no data available.
    """
    # Try device control server first
    akta_data = fetch_akta_results(experiment_id)
    if akta_data:
        # Save a copy in results folder for traceability
        akta_json_out = results_folder_path / f"akta_results_{experiment_id}.json"
        try:
            with open(akta_json_out, 'w') as f:
                json.dump(akta_data, f, indent=2)
            print(f"INFO: Saved AKTA JSON from server to {akta_json_out}")
        except Exception as e:
            print(f"WARNING: Failed to save AKTA JSON to {akta_json_out}: {e}")
        return akta_data

    # Fallback to local files
    print("INFO: Falling back to local AKTA result files.")
    results_folder_local = results_folder_path
    akta_json = results_folder_local / f"akta_results_{experiment_id}.json"
    akta_csv = Path('data.csv')  # Orbit copies to working dir

    if akta_json.exists():
        try:
            print(f"INFO: Loading local AKTA JSON: {akta_json}")
            with open(akta_json, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA JSON {akta_json}: {e}")

    if akta_csv.exists():
        try:
            print(f"INFO: Loading local AKTA CSV: {akta_csv}")
            df = pd.read_csv(akta_csv, na_values=['-'])
            akta_data_csv = {col: df[col].dropna().tolist() for col in df.columns}
            return akta_data_csv
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA CSV {akta_csv}: {e}")

    print("INFO: No AKTA data found on server or local files.")
    return None


def _prepare_akta_timeseries(akta_data: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Extract time, uv1, and conductivity arrays from AKTA data.

    Handles both JSON-structured and CSV-derived AKTA data.
    Returns (time_s, uv1_mau, cond_mscm_or_None).
    """
    # Time
    if 'time' not in akta_data:
        raise ValueError("AKTA data missing 'time' field.")

    time = np.array(akta_data['time'], dtype=float)

    # UV at 280 nm (uv1)
    if 'uv1' in akta_data:
        uv_raw = akta_data['uv1']
    elif 'uv' in akta_data:
        uv_raw = akta_data['uv']
    else:
        raise ValueError("AKTA data missing 'uv1' (or 'uv') field for UV signal.")

    uv = np.array([np.nan if v is None else float(v) for v in uv_raw], dtype=float)

    # Conductivity
    cond_arr: Optional[np.ndarray] = None
    if 'cond' in akta_data:
        cond_raw = akta_data['cond']
        cond_arr = np.array([np.nan if v is None else float(v) for v in cond_raw], dtype=float)
    elif 'conductivity' in akta_data:
        cond_raw = akta_data['conductivity']
        cond_arr = np.array([np.nan if v is None else float(v) for v in cond_raw], dtype=float)

    # Ensure matching lengths
    min_len = min(len(time), len(uv))
    if cond_arr is not None:
        min_len = min(min_len, len(cond_arr))

    time = time[:min_len]
    uv = uv[:min_len]
    if cond_arr is not None:
        cond_arr = cond_arr[:min_len]

    return time, uv, cond_arr


def _detect_peaks(time_s: np.ndarray, uv: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Detect peaks in the UV chromatogram and return peak indices and details.

    Uses scipy.signal.find_peaks with simple prominence and height heuristics.
    Returns indices and a dict with retention times, peak_heights, etc.
    """
    # Basic cleaning: ignore NaNs
    mask = ~np.isnan(uv)
    time_clean = time_s[mask]
    uv_clean = uv[mask]

    if len(time_clean) < 5:
        return np.array([], dtype=int), {"retention_times_s": [], "peak_heights": []}

    # Heuristic thresholds: height relative to baseline and max
    baseline = np.nanpercentile(uv_clean, 10)
    max_height = np.nanmax(uv_clean)
    if not np.isfinite(max_height):
        return np.array([], dtype=int), {"retention_times_s": [], "peak_heights": []}

    rel_height = max(0.05 * (max_height - baseline), 0.0)
    prominence = max(0.02 * (max_height - baseline), 0.0)

    peaks, _ = find_peaks(uv_clean, height=baseline + rel_height, prominence=prominence)

    if peaks.size == 0:
        return peaks, {"retention_times_s": [], "peak_heights": []}

    peak_times_s = time_clean[peaks]
    peak_heights = uv_clean[peaks]

    peak_info = {
        "retention_times_s": peak_times_s.tolist(),
        "retention_times_min": (peak_times_s / 60.0).tolist(),
        "peak_heights": peak_heights.tolist(),
        "n_peaks": int(len(peak_times_s)),
    }
    return peaks, peak_info


def _plot_chromatogram(
    experiment_id: str,
    time_s: np.ndarray,
    uv: np.ndarray,
    cond: Optional[np.ndarray],
    peaks_idx: np.ndarray,
    peak_info: Dict[str, Any],
    results_folder_path: Path,
) -> Dict[str, str]:
    """Generate chromatogram plot with UV and conductivity.

    UV (280 nm) in blue, conductivity in red (second y-axis) if available.
    Peaks are labeled with retention time in minutes only if any peaks exist.
    Returns a dict with paths to generated plot files.
    """
    plot_paths: Dict[str, str] = {}

    if time_s.size == 0 or uv.size == 0:
        print("WARNING: Empty time or UV arrays, skipping plot generation.")
        return plot_paths

    time_min = time_s / 60.0

    fig, ax1 = plt.subplots(figsize=(10, 5))

    ax1.plot(time_min, uv, color='blue', linewidth=0.8, label='UV 280 nm')
    ax1.set_xlabel('Time (min)')
    ax1.set_ylabel('UV Absorbance (mAU)', color='blue')
    ax1.tick_params(axis='y', labelcolor='blue')

    ax2 = None
    if cond is not None and cond.size == time_s.size:
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond, color='red', linewidth=0.8, alpha=0.7, label='Conductivity')
        ax2.set_ylabel('Conductivity (mS/cm)', color='red')
        ax2.tick_params(axis='y', labelcolor='red')

    title = f'AKTA Chromatogram - Experiment {experiment_id}'
    ax1.set_title(title)

    # Peak labeling if peaks were detected
    if peaks_idx is not None and peaks_idx.size > 0 and peak_info.get('retention_times_min'):
        print(f"INFO: Labeling {len(peak_info['retention_times_min'])} peaks on chromatogram.")
        for i, (rt_min, height) in enumerate(zip(peak_info['retention_times_min'], peak_info['peak_heights'])):
            label = f"P{i+1}: {rt_min:.2f} min"
            # Find approximate y-value at this retention time for labeling
            # Use UV height as y
            ax1.annotate(
                label,
                xy=(rt_min, height),
                xytext=(0, 5),
                textcoords='offset points',
                ha='center',
                fontsize=8,
                rotation=90,
                color='black',
            )

    fig.tight_layout()

    png_path = results_folder_path / f'chromatogram_{experiment_id}.png'
    pdf_path = results_folder_path / f'chromatogram_{experiment_id}.pdf'
    try:
        fig.savefig(png_path, dpi=150)
        fig.savefig(pdf_path)
        print(f"INFO: Saved chromatogram plots to {png_path} and {pdf_path}")
        plot_paths['chromatogram_png'] = str(png_path.resolve())
        plot_paths['chromatogram_pdf'] = str(pdf_path.resolve())
    except Exception as e:
        print(f"WARNING: Failed to save chromatogram plots: {e}")

    plt.close(fig)
    return plot_paths


def _parse_akta_timeseries_from_sources(experiment_id: str, results_folder_path: Path) -> Optional[Dict[str, Any]]:
    """Wrapper calling _parse_akta_data_from_server_or_local.

    Split out to keep analyze_experiment readable.
    """
    try:
        akta_data = _parse_akta_data_from_server_or_local(experiment_id, results_folder_path)
        return akta_data
    except Exception as e:
        print(f"WARNING: Unexpected error while obtaining AKTA data: {e}")
        return None


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = '../data', results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography runs.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, attempts auto-detection.
        data_folder (str): Path to data folder containing experiment_*.json.
        results_folder (str): Path to results folder where outputs will be written.

    Returns:
        dict: Analysis results with key metrics and file paths.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    # Auto-detect experiment ID if needed
    if experiment_id is None:
        try:
            experiment_id = _auto_detect_experiment_id(data_folder)
        except Exception as e:
            analysis_results = _prepare_results_dict(experiment_id)
            analysis_results['message'] = f"Failed to auto-detect experiment ID: {e}"
            analysis_results['status'] = 'failed'
            print(f"ERROR: {analysis_results['message']}")
            # Save results JSON even on failure
            results_json_path = results_folder_path / 'analysis_results_unknown.json'
            with open(results_json_path, 'w') as f:
                json.dump(analysis_results, f, indent=4)
            print(f"INFO: Saved analysis results JSON to: {results_json_path}")
            return analysis_results

    analysis_results = _prepare_results_dict(experiment_id)

    # Load experiment JSON (for metadata and traceability)
    try:
        experiment_data = _load_experiment_json(experiment_id, data_folder)
        analysis_results['metadata']['experiment_json_loaded'] = True
    except Exception as e:
        msg = f"Failed to load experiment JSON for experiment {experiment_id}: {e}"
        analysis_results['status'] = 'failed'
        analysis_results['message'] = msg
        print(f"ERROR: {msg}")
        # Save partial results
        results_json_path = results_folder_path / f'analysis_results_{experiment_id}.json'
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Extract eLabFTW extra fields when available, but do not fail hard if missing.
    metadata_extra = {}
    try:
        metadata_extra = experiment_data.get('metadata_decoded', {}).get('extra_fields', {}) or {}
    except Exception:
        metadata_extra = {}

    # Optionally collect specific expected fields into metadata for reporting
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
    extracted_metadata_fields = {}
    for field in expected_fields:
        value = _safe_get_metadata_field(metadata_extra, field)
        if value is not None:
            extracted_metadata_fields[field] = value
    analysis_results['metadata']['elab_extra_fields'] = extracted_metadata_fields

    # Get AKTA data from device server or local
    akta_data = _parse_akta_timeseries_from_sources(experiment_id, results_folder_path)

    if akta_data is None:
        msg = "AKTA data not available - analysis skipped. This is expected for dry runs or experiments without AKTA output."
        analysis_results['status'] = 'success'
        analysis_results['message'] = msg
        analysis_results['metadata']['data_source'] = 'none'
        print(f"INFO: {msg}")
    else:
        try:
            time_s, uv, cond = _prepare_akta_timeseries(akta_data)
        except Exception as e:
            msg = f"Failed to parse AKTA time series data: {e}"
            analysis_results['status'] = 'failed'
            analysis_results['message'] = msg
            print(f"ERROR: {msg}")
        else:
            # Detect peaks
            peaks_idx, peak_info = _detect_peaks(time_s, uv)

            # Generate chromatogram plot with peak labels if peaks exist
            plot_paths = _plot_chromatogram(
                experiment_id=experiment_id,
                time_s=time_s,
                uv=uv,
                cond=cond,
                peaks_idx=peaks_idx,
                peak_info=peak_info,
                results_folder_path=results_folder_path,
            )

            # Save processed time series as CSV
            df_dict = {
                'time_s': time_s,
                'time_min': time_s / 60.0,
                'uv1_mau': uv,
            }
            if cond is not None:
                df_dict['cond_mscm'] = cond
            ts_df = pd.DataFrame(df_dict)
            ts_csv_path = results_folder_path / f'akta_timeseries_{experiment_id}.csv'
            try:
                ts_df.to_csv(ts_csv_path, index=False)
                print(f"INFO: Saved processed AKTA time series to {ts_csv_path}")
            except Exception as e:
                print(f"WARNING: Failed to save time series CSV: {e}")

            # Populate analysis_results
            analysis_results['status'] = 'success'
            analysis_results['message'] = 'AKTA analysis completed successfully.'
            analysis_results['metadata']['data_source'] = 'device_or_local'
            analysis_results['plots'].update(plot_paths)
            analysis_results['data_outputs']['akta_timeseries_csv'] = str(ts_csv_path.resolve())
            analysis_results['data_outputs']['peaks'] = peak_info
            analysis_results['files_processed'] = 1

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    try:
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
    except Exception as e:
        print(f"WARNING: Failed to save analysis results JSON: {e}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze AKTA experiment data')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID')
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
