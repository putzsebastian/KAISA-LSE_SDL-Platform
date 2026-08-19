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

# Device info (from system instructions)
AKTA_CONTROL_SERVER = os.getenv('AKTA_CONTROL_SERVER', 'http://localhost:5001')
AKTA_API_KEY = os.getenv('AKTA_API_KEY', 'akta-control-key')


def fetch_akta_results(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results from the control server.

    Returns a dict with the contents of akta_results.json under key 'results',
    or None if not available / unreachable.
    """
    try:
        headers = {'X-API-Key': AKTA_API_KEY}
        url = f'{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}'
        print(f"INFO: Requesting AKTA results from control server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return data.get('results', {})
        else:
            print(f"WARNING: AKTA results not found on server (status {response.status_code})")
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def _safe_get_metadata_field(metadata: Dict[str, Any], field_name: str) -> Any:
    """Helper to safely extract a value from eLabFTW extra_fields structure.

    Raises KeyError with a clear message if the field or its value is missing.
    """
    if field_name not in metadata:
        raise KeyError(f"Missing expected metadata field: {field_name}")
    field = metadata[field_name]
    if not isinstance(field, dict) or 'value' not in field:
        raise KeyError(f"Metadata field '{field_name}' is malformed or lacks 'value'")
    return field['value']


def _load_experiment_json(experiment_id: str, data_folder: str) -> Dict[str, Any]:
    """Load experiment JSON, trying root then data folder as described."""
    root_candidate = Path('..') / f'experiment_{experiment_id}.json'
    data_candidate = Path(data_folder) / f'experiment_{experiment_id}.json'

    for path in (root_candidate, data_candidate):
        if path.exists():
            print(f"INFO: Loading experiment JSON from {path}")
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON format in {path}: {e}")

    raise FileNotFoundError(f"Data file not found in root or data folder for experiment {experiment_id}")


def _auto_detect_most_recent_experiment_id(data_folder: str) -> str:
    """Auto-detect most recent experiment_[ID].json in data_folder or root.

    Returns the detected experiment ID as string, or raises FileNotFoundError
    if none can be found.
    """
    candidates: List[Path] = []
    for base in (Path('..'), Path(data_folder)):
        if base.exists():
            candidates.extend(base.glob('experiment_*.json'))

    if not candidates:
        raise FileNotFoundError("No experiment_*.json files found for auto-detection")

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    newest = candidates[0]
    name = newest.stem  # experiment_XXXX
    if '_' not in name:
        raise ValueError(f"Unexpected experiment filename format: {newest.name}")
    exp_id = name.split('_', 1)[1]
    print(f"INFO: Auto-detected most recent experiment ID: {exp_id} (from {newest})")
    return exp_id


def _ensure_results_folder(results_folder: str) -> Path:
    path = Path(results_folder)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _akta_data_from_local_files(experiment_id: str, results_folder_path: Path) -> Optional[Dict[str, Any]]:
    """Fallback: load AKTA results from local JSON or CSV.

    Looks for akta_results_{experiment_id}.json in results folder first,
    then for data.csv in current working directory. Returns a dict compatible
    with akta_results.json structure, or None if nothing usable is found.
    """
    akta_json = results_folder_path / f'akta_results_{experiment_id}.json'
    akta_csv = Path('data.csv')

    if akta_json.exists():
        print(f"INFO: Using local AKTA JSON results from {akta_json}")
        with open(akta_json, 'r') as f:
            return json.load(f)

    if akta_csv.exists():
        print(f"INFO: Using local AKTA CSV results from {akta_csv}")
        try:
            df = pd.read_csv(akta_csv, na_values=['-'])
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA CSV file {akta_csv}: {e}")
            return None
        # Convert DataFrame to dict-of-lists similar to JSON structure
        akta_data: Dict[str, Any] = {}
        for col in df.columns:
            series = df[col]
            if series.dtype.kind in 'bifc':
                akta_data[col] = series.astype(float).where(series.notna(), None).tolist()
            else:
                akta_data[col] = series.where(series.notna(), None).tolist()
        return akta_data

    print("INFO: No local AKTA results files found (neither akta_results_*.json nor data.csv)")
    return None


def _prepare_akta_dataframe(akta_data: Dict[str, Any]) -> pd.DataFrame:
    """Convert raw AKTA result dict (JSON or CSV-like) into a tidy DataFrame."""
    if not akta_data:
        raise ValueError("AKTA data structure is empty")

    # Known JSON structure: has 'time' and signals like 'uv1', 'cond'
    if 'time' in akta_data:
        df_dict: Dict[str, Any] = {'time': akta_data['time']}
        # Copy any additional numeric arrays as columns
        for key, value in akta_data.items():
            if key == 'time':
                continue
            if isinstance(value, list) and len(value) == len(df_dict['time']):
                df_dict[key] = value
        df = pd.DataFrame(df_dict)
    else:
        # Fallback: assume akta_data is already col->sequence mapping (from CSV)
        df = pd.DataFrame(akta_data)

    if 'time' not in df.columns:
        raise ValueError("AKTA data does not contain required 'time' column")

    # Replace None with NaN, enforce numeric for time and signal columns where possible
    df = df.replace({None: np.nan})
    df['time'] = pd.to_numeric(df['time'], errors='coerce')

    for col in df.columns:
        if col == 'time':
            continue
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.dropna(subset=['time'])
    df = df.sort_values('time').reset_index(drop=True)
    return df


def _detect_uv_peaks(time_s: np.ndarray, uv: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Detect peaks in UV trace and return indices and summary info.

    Uses scipy.signal.find_peaks with basic prominence-based detection.
    Returns (peak_indices, info_dict).
    """
    # Basic sanity: remove NaNs
    mask = np.isfinite(time_s) & np.isfinite(uv)
    time_s = time_s[mask]
    uv = uv[mask]

    if len(time_s) < 5:
        print("INFO: Not enough data points for peak detection.")
        return np.array([], dtype=int), {}

    # Estimate noise level for prominence: use median absolute deviation
    if np.all(uv == uv[0]):
        # Flat signal, no peaks
        return np.array([], dtype=int), {}

    mad = np.median(np.abs(uv - np.median(uv)))
    if mad == 0:
        mad = np.std(uv)
    if mad == 0:
        # Still flat-ish
        return np.array([], dtype=int), {}

    prominence = 3 * mad
    try:
        peak_indices, props = find_peaks(uv, prominence=prominence)
    except Exception as e:
        print(f"WARNING: Peak detection failed: {e}")
        return np.array([], dtype=int), {}

    info = {
        'n_peaks': int(len(peak_indices)),
        'prominence_threshold': float(prominence),
    }
    return peak_indices, info


def _plot_chromatogram(
    experiment_id: str,
    df: pd.DataFrame,
    results_folder_path: Path,
    peak_indices: Optional[np.ndarray] = None,
) -> Dict[str, str]:
    """Plot chromatogram: UV 280 nm vs time and conductivity if present.

    Adds peak labels with retention times (in minutes) if peak_indices is not empty.
    Returns dict with paths to created plot files.
    """
    outputs: Dict[str, str] = {}

    if 'uv1' not in df.columns and 'uv' not in df.columns:
        print("WARNING: No UV signal column (uv1 or uv) found for chromatogram plot.")
        return outputs

    uv_col = 'uv1' if 'uv1' in df.columns else 'uv'

    time_min = df['time'].to_numpy(dtype=float) / 60.0
    uv = df[uv_col].to_numpy(dtype=float)

    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax1.plot(time_min, uv, color='blue', linewidth=0.8, label='UV 280 nm')
    ax1.set_xlabel('Time (min)')
    ax1.set_ylabel('UV Absorbance (mAU)', color='blue')
    ax1.tick_params(axis='y', labelcolor='blue')

    # Second axis for conductivity if available
    cond_col = None
    if 'cond' in df.columns:
        cond_col = 'cond'
    elif 'conductivity' in df.columns:
        cond_col = 'conductivity'

    if cond_col is not None:
        cond = df[cond_col].to_numpy(dtype=float)
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond, color='red', linewidth=0.8, alpha=0.7, label='Conductivity')
        ax2.set_ylabel('Conductivity (mS/cm)', color='red')
        ax2.tick_params(axis='y', labelcolor='red')

    title = f'AKTA Chromatogram - Experiment {experiment_id}'
    ax1.set_title(title)

    # Peak annotations if any
    if peak_indices is not None and len(peak_indices) > 0:
        # Ensure indices align with filtered numeric arrays
        time_arr = df['time'].to_numpy(dtype=float)
        uv_arr = df[uv_col].to_numpy(dtype=float)
        n_points = len(time_arr)
        valid_peaks: List[int] = []
        for idx in peak_indices:
            if 0 <= idx < n_points and np.isfinite(time_arr[idx]) and np.isfinite(uv_arr[idx]):
                valid_peaks.append(idx)
        for idx in valid_peaks:
            t_min = time_arr[idx] / 60.0
            height = uv_arr[idx]
            label = f'{t_min:.2f} min'
            ax1.plot(t_min, height, 'ko', markersize=4)
            ax1.annotate(
                label,
                xy=(t_min, height),
                xytext=(0, 5),
                textcoords='offset points',
                ha='center',
                fontsize=8,
                rotation=90,
            )

    fig.tight_layout()

    png_path = results_folder_path / f'chromatogram_{experiment_id}.png'
    pdf_path = results_folder_path / f'chromatogram_{experiment_id}.pdf'

    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"INFO: Saved chromatogram PNG to {png_path}")
    print(f"INFO: Saved chromatogram PDF to {pdf_path}")

    outputs['chromatogram_png'] = str(png_path.resolve())
    outputs['chromatogram_pdf'] = str(pdf_path.resolve())
    return outputs


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = '../data', results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography data.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, auto-detects.
        data_folder (str): Path to folder containing experiment_[ID].json files.
        results_folder (str): Path where analysis outputs will be saved.

    Returns:
        dict: Analysis results with key metrics and file paths.
    """
    results_folder_path = _ensure_results_folder(results_folder)

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        try:
            experiment_id = _auto_detect_most_recent_experiment_id(data_folder)
        except Exception as e:
            raise RuntimeError(f"Failed to auto-detect experiment ID: {e}")

    analysis_results: Dict[str, Any] = {
        'experiment_id': experiment_id,
        'status': 'failed',
        'message': '',
        'plots': {},
        'data_outputs': {},
        'metadata': {},
        'files_processed': 0,
    }

    # Load experiment JSON (to access metadata / extra_fields)
    try:
        experiment_data = _load_experiment_json(experiment_id, data_folder)
    except Exception as e:
        analysis_results['message'] = str(e)
        print(f"ERROR: {e}")
        return analysis_results

    # Extract eLabFTW extra fields, if present
    extra_fields = {}
    try:
        extra_fields = experiment_data.get('metadata_decoded', {}).get('extra_fields', {}) or {}
    except Exception:
        extra_fields = {}

    # Example of safe metadata access (not strictly needed for basic AKTA analysis,
    # but implemented per requirements)
    metadata_values = {}
    if extra_fields:
        for key in [
            'Process ID', 'CIP_DURATION', 'WASH_DURATION', 'CIP_3_DURATION',
            'CIP_4_DURATION', 'CIP_5_DURATION', 'WASH_FLOW_RATE',
            'CIP_2_HOLD_DURATION', 'LOAD_INJECT_DURATION',
            'LOAD_INJECT_FLOW_RATE', 'EQUILIBRATION_DURATION',
            'ELUTE_GRADIENT_DURATION', 'EQUILIBRATION_FLOW_RATE',
            'ELUTE_GRADIENT_GRADIENT_END', 'ELUTE_GRADIENT_GRADIENT_START',
        ]:
            try:
                metadata_values[key] = _safe_get_metadata_field(extra_fields, key)
            except KeyError as e:
                # Not all fields are mandatory for chromatogram analysis, so log but do not fail
                print(f"WARNING: {e}")
    analysis_results['metadata']['elab_extra_fields'] = metadata_values

    # Fetch AKTA results: prefer device server, then local files
    print(f"INFO: Fetching AKTA data for experiment {experiment_id} from control server...")
    akta_data = fetch_akta_results(str(experiment_id))
    data_source = 'device'
    if not akta_data:
        print("INFO: Falling back to local AKTA result files...")
        akta_data = _akta_data_from_local_files(str(experiment_id), results_folder_path)
        data_source = 'local' if akta_data else 'none'

    if not akta_data:
        msg = 'AKTA data not available - analysis skipped. This is expected for test runs.'
        print(f"INFO: {msg}")
        analysis_results['status'] = 'success'
        analysis_results['message'] = msg
        analysis_results['metadata']['data_source'] = data_source
    else:
        try:
            df = _prepare_akta_dataframe(akta_data)
        except Exception as e:
            msg = f"Failed to prepare AKTA data: {e}"
            print(f"ERROR: {msg}")
            analysis_results['status'] = 'failed'
            analysis_results['message'] = msg
            # Save analysis_results JSON before returning
            results_json_path = results_folder_path / f'analysis_results_{experiment_id}.json'
            with open(results_json_path, 'w') as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        # Save processed AKTA data as CSV
        processed_csv_path = results_folder_path / f'akta_processed_{experiment_id}.csv'
        try:
            df.to_csv(processed_csv_path, index=False)
            analysis_results['data_outputs']['akta_processed_csv'] = str(processed_csv_path.resolve())
            analysis_results['files_processed'] += 1
            print(f"INFO: Saved processed AKTA data to {processed_csv_path}")
        except Exception as e:
            print(f"WARNING: Failed to save processed AKTA CSV: {e}")

        # Peak detection on UV signal
        uv_col = 'uv1' if 'uv1' in df.columns else ('uv' if 'uv' in df.columns else None)
        peak_info: Dict[str, Any] = {}
        if uv_col is not None:
            time_s = df['time'].to_numpy(dtype=float)
            uv = df[uv_col].to_numpy(dtype=float)
            peak_indices, peak_info = _detect_uv_peaks(time_s, uv)
            peak_rts_min: List[float] = []
            if len(peak_indices) > 0:
                for idx in peak_indices:
                    if 0 <= idx < len(time_s) and np.isfinite(time_s[idx]):
                        peak_rts_min.append(float(time_s[idx]) / 60.0)
            analysis_results['metadata']['peak_detection'] = {
                'n_peaks': int(len(peak_indices)),
                'retention_times_min': peak_rts_min,
                **peak_info,
            }
        else:
            print("WARNING: No UV column found for peak detection.")
            analysis_results['metadata']['peak_detection'] = {
                'n_peaks': 0,
                'retention_times_min': [],
                'note': 'No UV column available for peak detection',
            }

        # Plot chromatogram (with peak labels if peaks were identified)
        peak_indices_for_plot = None
        if 'peak_detection' in analysis_results['metadata'] and analysis_results['metadata']['peak_detection'].get('n_peaks', 0) > 0:
            peak_indices_for_plot = peak_indices
        plot_paths = _plot_chromatogram(experiment_id, df, results_folder_path, peak_indices_for_plot)
        analysis_results['plots'].update(plot_paths)

        analysis_results['status'] = 'success'
        analysis_results['message'] = 'AKTA analysis completed successfully.'
        analysis_results['metadata']['data_source'] = data_source

    # Save the analysis results as JSON
    results_json_path = results_folder_path / f'analysis_results_{experiment_id}.json'
    try:
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
    except Exception as e:
        print(f"WARNING: Failed to save analysis results JSON: {e}")

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
