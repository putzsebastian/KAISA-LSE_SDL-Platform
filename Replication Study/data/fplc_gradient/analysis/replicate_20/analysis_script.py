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

# AKTA control server configuration (can be overridden via environment variables)
AKTA_CONTROL_SERVER = os.getenv('AKTA_CONTROL_SERVER', 'http://localhost:5001')
AKTA_API_KEY = os.getenv('AKTA_API_KEY', 'akta-control-key')


def fetch_akta_results_from_server(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results JSON from the AKTA control server.

    Returns the 'results' dict on success, or None if not available.
    Does not raise on missing data; only on network-level problems.
    """
    try:
        headers = {'X-API-Key': AKTA_API_KEY}
        url = f'{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}'
        print(f"INFO: Fetching AKTA results from control server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', {})
            if results:
                print("INFO: AKTA results retrieved from server.")
                return results
            print("WARNING: AKTA server responded but no 'results' field found.")
            return None
        else:
            print(f"WARNING: AKTA results not found on server (status {response.status_code}).")
            return None
    except requests.exceptions.RequestException as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def load_experiment_json(experiment_id: str, data_folder: str) -> Dict[str, Any]:
    """Load experiment JSON, checking root first then data subfolder.

    Raises FileNotFoundError or ValueError with clear messages.
    """
    # First try root-level JSON as required
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


def get_elab_metadata_fields(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract eLabFTW extra_fields dict with error handling."""
    try:
        metadata = experiment_data['metadata_decoded']['extra_fields']
        if not isinstance(metadata, dict):
            raise TypeError("metadata_decoded.extra_fields is not a dict")
        return metadata
    except KeyError as e:
        raise KeyError(f"Missing expected metadata field structure: {e}")


def find_most_recent_experiment_id(data_folder: str) -> Optional[str]:
    """Auto-detect most recent experiment_*.json in the data folder.

    Returns the experiment ID as string, or None if none found.
    """
    folder = Path(data_folder)
    if not folder.exists():
        return None

    candidates: List[Path] = sorted(
        folder.glob('experiment_*.json'),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None

    latest = candidates[0]
    name = latest.stem  # experiment_1234
    if '_' in name:
        return name.split('_', 1)[1]
    return None


def prepare_akta_data_from_json(akta_data: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Convert AKTA results dict into numpy arrays.

    Returns (time_min, uv1, cond_or_None).
    Missing values (None) are converted to np.nan.
    """
    if 'time' not in akta_data:
        raise ValueError("AKTA data missing 'time' field.")
    if 'uv1' not in akta_data:
        raise ValueError("AKTA data missing 'uv1' field (UV 280 nm signal).")

    time = np.array(akta_data['time'], dtype=float)
    # Convert to minutes for plotting and retention times
    time_min = time / 60.0

    def _to_nan_array(values: List[Any]) -> np.ndarray:
        out = []
        for v in values:
            if v is None:
                out.append(np.nan)
            else:
                try:
                    out.append(float(v))
                except (TypeError, ValueError):
                    out.append(np.nan)
        return np.array(out, dtype=float)

    uv1 = _to_nan_array(akta_data.get('uv1', []))
    cond = None
    if 'cond' in akta_data:
        cond = _to_nan_array(akta_data.get('cond', []))

    if len(time_min) != len(uv1):
        raise ValueError("Length mismatch between time and uv1 arrays.")
    if cond is not None and len(cond) != len(time_min):
        raise ValueError("Length mismatch between time and cond arrays.")

    return time_min, uv1, cond


def prepare_akta_data_from_csv(csv_path: Path) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Load AKTA data from Orbit-style CSV.

    Expects at least 'time' and 'uv1' columns; optional 'cond' column.
    Missing values are represented as '-' and parsed as NaN.
    """
    print(f"INFO: Loading AKTA CSV data from: {csv_path}")
    if not csv_path.exists():
        raise FileNotFoundError(f"AKTA CSV file not found: {csv_path}")

    df = pd.read_csv(csv_path, na_values=['-'])
    if 'time' not in df.columns or 'uv1' not in df.columns:
        raise ValueError("AKTA CSV must contain at least 'time' and 'uv1' columns.")

    time_sec = pd.to_numeric(df['time'], errors='coerce').to_numpy()
    time_min = time_sec / 60.0
    uv1 = pd.to_numeric(df['uv1'], errors='coerce').to_numpy()

    cond = None
    if 'cond' in df.columns:
        cond = pd.to_numeric(df['cond'], errors='coerce').to_numpy()

    return time_min, uv1, cond


def detect_peaks(time_min: np.ndarray, uv1: np.ndarray) -> List[Dict[str, float]]:
    """Detect peaks in the UV trace and return list of peaks with retention times.

    Uses scipy.signal.find_peaks with simple prominence and height heuristics.
    Returns a list of dicts: [{'index': int, 'time_min': float, 'height': float}, ...]
    """
    # Remove NaNs for peak detection (but keep original indices for mapping)
    finite_mask = np.isfinite(uv1)
    if not np.any(finite_mask):
        print("WARNING: No finite UV data available for peak detection.")
        return []

    uv_finite = uv1[finite_mask]

    if uv_finite.size < 5:
        print("WARNING: Too few points for reliable peak detection.")
        return []

    # Basic heuristic for prominence and height
    # Use standard deviation to set a dynamic threshold
    uv_std = np.nanstd(uv_finite)
    uv_max = np.nanmax(uv_finite)

    if np.isnan(uv_std) or uv_std == 0:
        print("WARNING: UV signal has zero or undefined variance; skipping peak detection.")
        return []

    prominence = max(uv_std, 0.05 * uv_max)
    height = np.nanmean(uv_finite) + 0.5 * uv_std

    print(f"INFO: Peak detection parameters - prominence: {prominence:.3f}, height: {height:.3f}")

    # find_peaks returns indices into the array passed; we need to map back
    indices_all = np.arange(len(uv1))
    indices_finite = indices_all[finite_mask]

    peaks_finite, properties = find_peaks(uv_finite, prominence=prominence, height=height)

    peaks: List[Dict[str, float]] = []
    for idx_finite in peaks_finite:
        original_index = int(indices_finite[idx_finite])
        rt = float(time_min[original_index])
        height_val = float(uv1[original_index])
        peaks.append({
            'index': original_index,
            'time_min': rt,
            'height': height_val,
        })

    print(f"INFO: Detected {len(peaks)} peak(s) in UV trace.")
    return peaks


def plot_chromatogram(
    experiment_id: str,
    time_min: np.ndarray,
    uv1: np.ndarray,
    cond: Optional[np.ndarray],
    peaks: List[Dict[str, float]],
    results_folder: str,
) -> Dict[str, str]:
    """Generate chromatogram plot with UV and conductivity.

    If peaks are provided, label them with retention time.
    Returns dict with paths to generated plot files.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    fig, ax1 = plt.subplots(figsize=(10, 5))

    # Plot UV 280 nm
    ax1.plot(time_min, uv1, color='blue', linewidth=0.8, label='UV 280 nm')
    ax1.set_xlabel('Time (min)')
    ax1.set_ylabel('UV Absorbance (mAU)', color='blue')
    ax1.tick_params(axis='y', labelcolor='blue')

    # Conductivity on secondary axis if available
    ax2 = None
    if cond is not None:
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond, color='red', linewidth=0.8, alpha=0.7, label='Conductivity')
        ax2.set_ylabel('Conductivity (mS/cm)', color='red')
        ax2.tick_params(axis='y', labelcolor='red')

    # Peak labeling
    if peaks:
        for peak in peaks:
            idx = peak['index']
            rt = peak['time_min']
            height = peak['height']
            ax1.plot(time_min[idx], uv1[idx], 'ko', markersize=4)
            ax1.annotate(
                f"{rt:.2f} min",
                xy=(time_min[idx], uv1[idx]),
                xytext=(0, 8),
                textcoords='offset points',
                ha='center',
                fontsize=8,
                rotation=0,
            )

    ax1.set_title(f'AKTA Chromatogram - Experiment {experiment_id}')

    # Build a combined legend
    handles1, labels1 = ax1.get_legend_handles_labels()
    handles = handles1
    labels = labels1
    if ax2 is not None:
        handles2, labels2 = ax2.get_legend_handles_labels()
        handles += handles2
        labels += labels2
    if handles:
        ax1.legend(handles, labels, loc='best')

    fig.tight_layout()

    png_path = results_folder_path / f'chromatogram_{experiment_id}.png'
    pdf_path = results_folder_path / f'chromatogram_{experiment_id}.pdf'

    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"INFO: Saved chromatogram plots to: {png_path} and {pdf_path}")

    return {
        'chromatogram_png': str(png_path.resolve()),
        'chromatogram_pdf': str(pdf_path.resolve()),
    }


def export_processed_data(
    experiment_id: str,
    time_min: np.ndarray,
    uv1: np.ndarray,
    cond: Optional[np.ndarray],
    peaks: List[Dict[str, float]],
    results_folder: str,
) -> Dict[str, str]:
    """Export processed time-series and peak tables as CSV.

    Returns dict mapping logical names to file paths.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    # Time-series data
    data = {
        'time_min': time_min,
        'uv1_mAU': uv1,
    }
    if cond is not None:
        data['cond_mS_cm'] = cond

    ts_df = pd.DataFrame(data)
    ts_csv_path = results_folder_path / f'akta_timeseries_{experiment_id}.csv'
    ts_df.to_csv(ts_csv_path, index=False)
    print(f"INFO: Saved processed time-series data to: {ts_csv_path}")

    outputs = {
        'timeseries_csv': str(ts_csv_path.resolve()),
    }

    # Peaks table
    if peaks:
        peaks_df = pd.DataFrame(peaks)
        peaks_csv_path = results_folder_path / f'akta_peaks_{experiment_id}.csv'
        peaks_df.to_csv(peaks_csv_path, index=False)
        print(f"INFO: Saved peak table to: {peaks_csv_path}")
        outputs['peaks_csv'] = str(peaks_csv_path.resolve())

    return outputs


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = '../data', results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography data.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, attempts auto-detection.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with key metrics and file paths.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    analysis_results: Dict[str, Any] = {
        'experiment_id': experiment_id,
        'status': 'failed',
        'message': '',
        'plots': {},
        'data_outputs': {},
        'metadata': {},
        'files_processed': 0,
    }

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ('', None):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            print("INFO: No experiment ID provided, attempting auto-detection from most recent JSON file.")
            detected = find_most_recent_experiment_id(data_folder)
            if not detected:
                analysis_results['message'] = 'No experiment ID provided and no experiment_*.json files found.'
                analysis_results['status'] = 'failed'
                print(f"ERROR: {analysis_results['message']}")
                return analysis_results
            experiment_id = detected
            print(f"INFO: Auto-detected most recent experiment ID: {experiment_id}")

    analysis_results['experiment_id'] = experiment_id

    # Load experiment JSON (for metadata and traceability)
    try:
        experiment_data = load_experiment_json(experiment_id, data_folder)
        analysis_results['metadata']['experiment_json_loaded'] = True
    except Exception as e:
        # Hard fail on missing or invalid JSON, as this indicates configuration issues
        analysis_results['message'] = str(e)
        analysis_results['status'] = 'failed'
        print(f"ERROR: {e}")
        return analysis_results

    # Try to access eLabFTW extra fields (for logging only here)
    try:
        extra_fields = get_elab_metadata_fields(experiment_data)
        # Example: store some known fields if present
        for field_name in [
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
        ]:
            if field_name in extra_fields and isinstance(extra_fields[field_name], dict):
                analysis_results['metadata'][field_name] = extra_fields[field_name].get('value')
    except KeyError as e:
        # For this AKTA analysis, missing metadata is not fatal; log and continue
        print(f"WARNING: {e}")
        analysis_results['metadata']['elab_metadata_warning'] = str(e)

    # Fetch AKTA results from control server first
    akta_data = fetch_akta_results_from_server(str(experiment_id))

    time_min: np.ndarray
    uv1: np.ndarray
    cond: Optional[np.ndarray]

    if akta_data:
        try:
            time_min, uv1, cond = prepare_akta_data_from_json(akta_data)
            analysis_results['metadata']['akta_data_source'] = 'server_json'
            analysis_results['files_processed'] += 1
        except Exception as e:
            analysis_results['message'] = f'Error parsing AKTA JSON from server: {e}'
            analysis_results['status'] = 'failed'
            print(f"ERROR: {analysis_results['message']}")
            return analysis_results
    else:
        # Fallback to local files
        print("INFO: Falling back to local AKTA result files.")
        akta_json_path = results_folder_path / f'akta_results_{experiment_id}.json'
        akta_csv_path = Path('data.csv')

        if akta_json_path.exists():
            print(f"INFO: Found local AKTA JSON file: {akta_json_path}")
            try:
                with open(akta_json_path, 'r') as f:
                    local_akta_data = json.load(f)
                time_min, uv1, cond = prepare_akta_data_from_json(local_akta_data)
                analysis_results['metadata']['akta_data_source'] = 'local_json'
                analysis_results['files_processed'] += 1
            except Exception as e:
                analysis_results['message'] = f'Error loading local AKTA JSON ({akta_json_path}): {e}'
                analysis_results['status'] = 'failed'
                print(f"ERROR: {analysis_results['message']}")
                return analysis_results
        elif akta_csv_path.exists():
            print(f"INFO: Found local AKTA CSV file: {akta_csv_path}")
            try:
                time_min, uv1, cond = prepare_akta_data_from_csv(akta_csv_path)
                analysis_results['metadata']['akta_data_source'] = 'local_csv'
                analysis_results['files_processed'] += 1
            except Exception as e:
                analysis_results['message'] = f'Error loading local AKTA CSV ({akta_csv_path}): {e}'
                analysis_results['status'] = 'failed'
                print(f"ERROR: {analysis_results['message']}")
                return analysis_results
        else:
            analysis_results['message'] = (
                'AKTA data not available from server or local files; analysis skipped.'
            )
            analysis_results['status'] = 'success'
            print(f"INFO: {analysis_results['message']}")
            return analysis_results

    # At this point we have valid time_min, uv1, cond (optional)
    # Peak detection
    peaks = detect_peaks(time_min, uv1)

    # Plot chromatogram with optional peak labels
    plot_paths = plot_chromatogram(
        experiment_id=str(experiment_id),
        time_min=time_min,
        uv1=uv1,
        cond=cond,
        peaks=peaks,
        results_folder=results_folder,
    )

    # Export processed data and peaks
    data_output_paths = export_processed_data(
        experiment_id=str(experiment_id),
        time_min=time_min,
        uv1=uv1,
        cond=cond,
        peaks=peaks,
        results_folder=results_folder,
    )

    analysis_results['plots'].update(plot_paths)
    analysis_results['data_outputs'].update(data_output_paths)

    # Add peak summary to metadata
    analysis_results['metadata']['num_peaks_detected'] = len(peaks)
    if peaks:
        analysis_results['metadata']['peaks'] = [
            {'retention_time_min': p['time_min'], 'height': p['height']} for p in peaks
        ]

    analysis_results['status'] = 'success'
    analysis_results['message'] = 'AKTA chromatogram analysis completed successfully.'

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
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID')
    parser.add_argument('--data-folder', default='../data', help='Data folder path')
    parser.add_argument('--results-folder', default='../results', help='Results folder path')

    args = parser.parse_args()

    try:
        results = analyze_experiment(
            experiment_id=args.experiment_id,
            data_folder=args.data_folder,
            results_folder=args.results_folder,
        )
        if results.get('status') == 'success':
            print('SUCCESS: Analysis successful!')
            return 0
        else:
            print(f"ERROR: Analysis failed: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
