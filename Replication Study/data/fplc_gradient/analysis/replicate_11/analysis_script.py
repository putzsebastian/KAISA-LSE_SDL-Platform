#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Data Evaluation
Can be called externally with experiment ID as parameter.

This script loads experiment metadata from an experiment_<ID>.json file
and AKTA chromatography results either from the AKTA control server or
from local result files. It then:

- Extracts relevant timing and flow information from eLabFTW extra fields
- Loads AKTA time-series data (uv1 / cond vs time)
- Detects chromatographic peaks on the UV 280 nm signal
- Calculates peak retention times (in minutes)
- Plots the chromatogram with UV and conductivity overlaid; if peaks
  are detected, they are labelled with their retention times
- Exports processed data and summary tables as CSV
- Writes a structured JSON summary with paths to all outputs

The script is designed for both CLI usage and import as a module.
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

# AKTA control server configuration (can be overridden via env vars)
AKTA_CONTROL_SERVER = os.getenv('AKTA_CONTROL_SERVER', 'http://localhost:5001')
AKTA_API_KEY = os.getenv('AKTA_API_KEY', 'akta-control-key')


def fetch_akta_results_from_server(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results JSON from the control server.

    Returns the value under key "results" if present, or None if not
    available or on any non-200 HTTP code.
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


def load_akta_results_local(experiment_id: str, results_folder: Path, data_folder: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local files.

    Priority:
      1) akta_results_<ID>.json in results_folder
      2) akta_results_<ID>.json in data_folder
      3) data.csv in data_folder (Orbit native CSV)

    Returns a dict with at least keys: "time", and optionally "uv1", "cond".
    """
    # 1) JSON in results folder
    akta_json_results = results_folder / f'akta_results_{experiment_id}.json'
    if akta_json_results.exists():
        print(f"INFO: Loading AKTA JSON results from: {akta_json_results}")
        with open(akta_json_results, 'r') as f:
            return json.load(f)

    # 2) JSON in data folder
    akta_json_data = data_folder / f'akta_results_{experiment_id}.json'
    if akta_json_data.exists():
        print(f"INFO: Loading AKTA JSON results from data folder: {akta_json_data}")
        with open(akta_json_data, 'r') as f:
            return json.load(f)

    # 3) data.csv in data folder
    akta_csv = data_folder / 'data.csv'
    if akta_csv.exists():
        print(f"INFO: Loading AKTA CSV results from: {akta_csv}")
        try:
            df = pd.read_csv(akta_csv, na_values=['-'])
        except Exception as e:
            print(f"WARNING: Failed to read AKTA CSV file: {e}")
            return None
        if 'time' not in df.columns:
            print("WARNING: AKTA CSV file missing 'time' column.")
            return None
        # Build a minimal AKTA-like dict
        akta_data: Dict[str, Any] = {
            'time': df['time'].dropna().tolist()
        }
        # Optionally include uv1 and cond if present
        if 'uv1' in df.columns:
            akta_data['uv1'] = df['uv1'].where(pd.notnull(df['uv1'])).tolist()
        if 'cond' in df.columns:
            akta_data['cond'] = df['cond'].where(pd.notnull(df['cond'])).tolist()
        print("INFO: AKTA CSV data loaded successfully.")
        return akta_data

    print("INFO: No local AKTA results file found.")
    return None


def detect_peaks(time_s: np.ndarray, signal: np.ndarray,
                 height_threshold_factor: float = 0.1,
                 min_distance_points: int = 5) -> List[int]:
    """Simple peak detection on a 1D chromatographic signal.

    Parameters
    ----------
    time_s : np.ndarray
        Time axis in seconds.
    signal : np.ndarray
        Signal values (e.g. UV in mAU).
    height_threshold_factor : float
        Peaks must be at least this fraction of (max - baseline).
    min_distance_points : int
        Minimum number of points between consecutive peaks.

    Returns
    -------
    List[int]
        Indices of detected peaks in the signal array.
    """
    if signal.size == 0 or time_s.size == 0 or signal.size != time_s.size:
        return []

    finite_mask = np.isfinite(signal)
    if not np.any(finite_mask):
        return []

    sig = signal.astype(float)
    t = time_s.astype(float)

    # Rough baseline as median of signal
    baseline = np.nanmedian(sig[finite_mask])
    max_val = np.nanmax(sig[finite_mask])
    dynamic_range = max_val - baseline
    if not np.isfinite(dynamic_range) or dynamic_range <= 0:
        return []

    height_threshold = baseline + height_threshold_factor * dynamic_range

    peak_indices: List[int] = []
    n = len(sig)
    last_peak_idx = -min_distance_points - 1

    for i in range(1, n - 1):
        if not np.isfinite(sig[i]):
            continue
        # Local maximum condition
        if sig[i] > sig[i - 1] and sig[i] >= sig[i + 1] and sig[i] >= height_threshold:
            if i - last_peak_idx >= min_distance_points:
                peak_indices.append(i)
                last_peak_idx = i

    return peak_indices


def ensure_results_folder(path: Path) -> Path:
    """Ensure that the results folder exists and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_experiment_json(experiment_id: str, data_folder: Path) -> Dict[str, Any]:
    """Load experiment_<ID>.json from the given data folder with error handling."""
    # Primary location: root-level ../experiment_ID.json
    root_json = Path('..') / f'experiment_{experiment_id}.json'
    if root_json.exists():
        data_file_path = root_json
    else:
        data_file_path = data_folder / f'experiment_{experiment_id}.json'

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
    """Extract selected eLabFTW extra fields with validation.

    Missing fields are reported but do not cause a hard failure, since they
    may not be required for basic chromatogram plotting.
    """
    meta_out: Dict[str, Any] = {}
    missing_fields: List[str] = []

    try:
        extra = experiment_data['metadata_decoded']['extra_fields']
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    field_names = [
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

    for name in field_names:
        try:
            if name in extra and isinstance(extra[name], dict) and 'value' in extra[name]:
                meta_out[name] = extra[name]['value']
            else:
                missing_fields.append(name)
        except Exception:
            missing_fields.append(name)

    if missing_fields:
        print("WARNING: Some expected metadata fields are missing or malformed: "
              + ", ".join(missing_fields))

    return meta_out


def plot_chromatogram(
    time_s: np.ndarray,
    uv_mau: Optional[np.ndarray],
    cond_mscm: Optional[np.ndarray],
    peak_indices: List[int],
    experiment_id: str,
    results_folder: Path,
) -> Dict[str, str]:
    """Generate chromatogram plot with UV and conductivity.

    Peaks (if any) are annotated with retention times.

    Returns a dict with paths to generated plot files.
    """
    if time_s.size == 0:
        raise ValueError("Chromatogram plot requested but time array is empty.")

    time_min = time_s / 60.0

    plt.figure(figsize=(10, 6))
    ax1 = plt.gca()

    # UV signal on primary y-axis
    if uv_mau is not None and np.any(np.isfinite(uv_mau)):
        ax1.plot(time_min, uv_mau, color='blue', linewidth=1.0, label='UV 280 nm')
        ax1.set_ylabel('UV Absorbance (mAU)', color='blue')
        ax1.tick_params(axis='y', labelcolor='blue')
    else:
        ax1.set_ylabel('Signal (a.u.)')

    ax1.set_xlabel('Time (min)')

    # Conductivity on secondary axis
    ax2 = None
    if cond_mscm is not None and np.any(np.isfinite(cond_mscm)):
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond_mscm, color='red', linewidth=1.0, alpha=0.7,
                 label='Conductivity')
        ax2.set_ylabel('Conductivity (mS/cm)', color='red')
        ax2.tick_params(axis='y', labelcolor='red')

    # Peak annotations (if any)
    if uv_mau is not None and peak_indices:
        for idx in peak_indices:
            if 0 <= idx < len(time_min) and np.isfinite(uv_mau[idx]):
                t_rt = time_min[idx]
                y_val = uv_mau[idx]
                label = f"RT={t_rt:.2f} min"
                ax1.plot(time_min[idx], uv_mau[idx], 'ko', markersize=4)
                ax1.annotate(
                    label,
                    xy=(time_min[idx], uv_mau[idx]),
                    xytext=(0, 10),
                    textcoords='offset points',
                    ha='center',
                    fontsize=8,
                    rotation=0,
                )

    ax1.set_title(f'AKTA Chromatogram - Experiment {experiment_id}')
    plt.tight_layout()

    results_paths: Dict[str, str] = {}

    png_path = results_folder / f'chromatogram_{experiment_id}.png'
    pdf_path = results_folder / f'chromatogram_{experiment_id}.pdf'
    plt.savefig(png_path, dpi=150)
    plt.savefig(pdf_path)
    plt.close()

    print(f"INFO: Chromatogram plots saved to: {png_path} and {pdf_path}")

    results_paths['chromatogram_png'] = str(png_path.resolve())
    results_paths['chromatogram_pdf'] = str(pdf_path.resolve())

    return results_paths


def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = '../data',
                       results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography runs.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries
            to auto-detect the most recent experiment JSON file.
        data_folder (str): Path to the folder containing experiment_ID.json
            files and optional AKTA raw outputs.
        results_folder (str): Path to the folder where all analysis outputs
            will be saved.

    Returns:
        dict: Analysis results with all key metrics and paths to generated
              files.
    """
    results_folder_path = ensure_results_folder(Path(results_folder))
    data_folder_path = Path(data_folder)

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        print("INFO: No experiment_id provided. Attempting auto-detection from data folder...")
        if not data_folder_path.exists():
            raise FileNotFoundError(f"Data folder does not exist: {data_folder_path}")
        json_files = sorted(
            data_folder_path.glob('experiment_*.json'),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not json_files:
            raise FileNotFoundError(
                f"No experiment_*.json files found in data folder: {data_folder_path}"
            )
        latest = json_files[0]
        experiment_id = latest.stem.replace('experiment_', '')
        print(f"INFO: Auto-detected experiment_id: {experiment_id}")

    analysis_results: Dict[str, Any] = {
        'experiment_id': str(experiment_id),
        'status': 'failed',
        'message': '',
        'plots': {},
        'data_outputs': {},
        'metadata': {},
        'files_processed': 0,
    }

    try:
        # 1) Load experiment JSON metadata
        experiment_data = load_experiment_json(str(experiment_id), data_folder_path)
        analysis_results['metadata']['raw_experiment_json_path'] = str(
            (Path('..') / f'experiment_{experiment_id}.json').resolve()
            if (Path('..') / f'experiment_{experiment_id}.json').exists()
            else (data_folder_path / f'experiment_{experiment_id}.json').resolve()
        )

        # 2) Extract metadata fields
        try:
            meta_fields = extract_metadata_fields(experiment_data)
            analysis_results['metadata']['elab_extra_fields'] = meta_fields
        except KeyError as e:
            # Missing core structure is a real error
            raise

        # 3) Fetch AKTA results (server preferred, local fallback)
        print(f"INFO: Fetching AKTA data for experiment {experiment_id}...")
        akta_data = fetch_akta_results_from_server(str(experiment_id))
        if akta_data is None:
            print("INFO: Falling back to local AKTA results files...")
            akta_data = load_akta_results_local(str(experiment_id), results_folder_path, data_folder_path)

        if akta_data is None:
            analysis_results['status'] = 'success'
            analysis_results['message'] = (
                'AKTA data not available - analysis limited to metadata extraction. '
                'This is expected for dry runs or experiments without AKTA output.'
            )
            print("INFO: " + analysis_results['message'])
        else:
            # 4) Process AKTA data
            time_list = akta_data.get('time')
            if time_list is None:
                raise ValueError('AKTA data missing required "time" field.')

            time_s = np.array(time_list, dtype=float)

            uv_list = akta_data.get('uv1')
            uv_mau = None
            if uv_list is not None:
                uv_arr = np.array([
                    np.nan if v is None else float(v) for v in uv_list
                ], dtype=float)
                uv_mau = uv_arr

            cond_list = akta_data.get('cond')
            cond_mscm = None
            if cond_list is not None:
                cond_arr = np.array([
                    np.nan if v is None else float(v) for v in cond_list
                ], dtype=float)
                cond_mscm = cond_arr

            # Save processed raw time-series as CSV
            df_dict: Dict[str, Any] = {'time_s': time_s}
            if uv_mau is not None:
                df_dict['uv1_mAU'] = uv_mau
            if cond_mscm is not None:
                df_dict['cond_mS_cm'] = cond_mscm
            akta_df = pd.DataFrame(df_dict)

            ts_csv_path = results_folder_path / f'akta_timeseries_{experiment_id}.csv'
            akta_df.to_csv(ts_csv_path, index=False)
            analysis_results['data_outputs']['akta_timeseries_csv'] = str(ts_csv_path.resolve())
            analysis_results['files_processed'] += 1
            print(f"INFO: AKTA time-series data saved to: {ts_csv_path}")

            # 5) Peak detection on UV trace (if available)
            peak_indices: List[int] = []
            peak_retention_times_min: List[float] = []
            if uv_mau is not None and np.any(np.isfinite(uv_mau)):
                peak_indices = detect_peaks(time_s, uv_mau)
                peak_retention_times_min = [float(time_s[i]) / 60.0 for i in peak_indices]
                print(f"INFO: Detected {len(peak_indices)} peak(s) in UV 280 nm trace.")
            else:
                print("INFO: No valid UV 280 nm data available for peak detection.")

            # Save peaks summary as CSV (if any peaks)
            if peak_indices:
                peaks_df = pd.DataFrame({
                    'peak_index': peak_indices,
                    'time_s': [float(time_s[i]) for i in peak_indices],
                    'retention_time_min': peak_retention_times_min,
                    'uv1_mAU': [float(uv_mau[i]) for i in peak_indices] if uv_mau is not None else [np.nan] * len(peak_indices),
                })
                peaks_csv_path = results_folder_path / f'akta_peaks_{experiment_id}.csv'
                peaks_df.to_csv(peaks_csv_path, index=False)
                analysis_results['data_outputs']['akta_peaks_csv'] = str(peaks_csv_path.resolve())
                analysis_results['files_processed'] += 1
                analysis_results['metadata']['num_peaks_detected'] = int(len(peak_indices))
                analysis_results['metadata']['peak_retention_times_min'] = peak_retention_times_min
                print(f"INFO: AKTA peaks summary saved to: {peaks_csv_path}")
            else:
                analysis_results['metadata']['num_peaks_detected'] = 0
                analysis_results['metadata']['peak_retention_times_min'] = []

            # 6) Plot chromatogram with optional peak labels
            plot_paths = plot_chromatogram(
                time_s=time_s,
                uv_mau=uv_mau,
                cond_mscm=cond_mscm,
                peak_indices=peak_indices,
                experiment_id=str(experiment_id),
                results_folder=results_folder_path,
            )
            analysis_results['plots'].update(plot_paths)
            analysis_results['files_processed'] += len(plot_paths)

            analysis_results['status'] = 'success'
            if peak_indices:
                analysis_results['message'] = (
                    f'AKTA analysis completed with {len(peak_indices)} peak(s) '
                    f'identified in the UV 280 nm signal.'
                )
            else:
                analysis_results['message'] = (
                    'AKTA analysis completed. No peaks detected in the UV 280 nm signal.'
                )

    except Exception as e:
        analysis_results['status'] = 'failed'
        analysis_results['message'] = str(e)
        print(f"ERROR: Analysis failed: {e}")

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    try:
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        analysis_results['results_json_path'] = str(results_json_path.resolve())
    except Exception as e:
        print(f"WARNING: Failed to write analysis results JSON: {e}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze AKTA chromatography experiment data.')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID. If not provided, attempts auto-detection from data folder.')
    parser.add_argument('--data-folder', default='../data', help='Path to the folder containing experiment_ID.json files. Default: ../data')
    parser.add_argument('--results-folder', default='../results', help='Path to the folder where all analysis outputs will be saved. Default: ../results')

    args = parser.parse_args()

    try:
        results = analyze_experiment(
            experiment_id=args.experiment_id,
            data_folder=args.data_folder,
            results_folder=args.results_folder,
        )
        if results.get('status') == 'success':
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
