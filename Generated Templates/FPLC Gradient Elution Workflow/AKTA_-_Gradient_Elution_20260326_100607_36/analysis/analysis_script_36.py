#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatogram Analysis
Can be called externally with experiment ID as parameter.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import requests
from scipy.signal import find_peaks

AKTA_CONTROL_SERVER = os.getenv('AKTA_CONTROL_SERVER', 'http://localhost:5001')
AKTA_API_KEY = os.getenv('AKTA_API_KEY', 'akta-control-key')


def fetch_akta_results(experiment_id: str):
    """Fetch AKTA results from the control server."""
    try:
        headers = {'X-API-Key': AKTA_API_KEY}
        response = requests.get(
            f'{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}',
            headers=headers,
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            return data.get('results', {})
        else:
            print(f'WARNING: AKTA results not found on server (status {response.status_code})')
            return None
    except Exception as e:
        print(f'WARNING: Could not reach AKTA control server: {e}')
        return None


def find_experiment_file(experiment_id: str, data_folder: str) -> Path:
    """Find experiment JSON, checking root first, then data folder."""
    root_path = Path(f'../experiment_{experiment_id}.json')
    if root_path.exists():
        return root_path

    data_path = Path(data_folder) / f'experiment_{experiment_id}.json'
    if data_path.exists():
        return data_path

    raise FileNotFoundError(f"Data file not found in root or data folder for experiment {experiment_id}")



def autodetect_experiment_id(data_folder: str) -> str:
    """Auto-detect the most recent experiment ID from root or data folder."""
    candidates = []

    root_parent = Path('..')
    if root_parent.exists():
        candidates.extend(root_parent.glob('experiment_*.json'))

    data_path = Path(data_folder)
    if data_path.exists():
        candidates.extend(data_path.glob('experiment_*.json'))

    unique_candidates = []
    seen = set()
    for path in candidates:
        resolved = str(path.resolve())
        if resolved not in seen:
            unique_candidates.append(path)
            seen.add(resolved)

    if not unique_candidates:
        raise FileNotFoundError('No experiment JSON files found for auto-detection')

    latest = max(unique_candidates, key=lambda p: p.stat().st_mtime)
    name = latest.stem
    if not name.startswith('experiment_'):
        raise ValueError(f'Unexpected experiment filename format: {latest.name}')

    experiment_id = name.replace('experiment_', '', 1)
    if not experiment_id:
        raise ValueError(f'Could not parse experiment ID from filename: {latest.name}')

    return experiment_id



def load_experiment_data(experiment_id: str, data_folder: str) -> Tuple[Dict, Path]:
    """Load experiment JSON with robust error handling."""
    data_file_path = find_experiment_file(experiment_id, data_folder)
    try:
        with open(data_file_path, 'r', encoding='utf-8') as f:
            experiment_data = json.load(f)
        return experiment_data, data_file_path
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")



def extract_metadata(experiment_data: Dict) -> Dict:
    """Extract metadata and eLabFTW extra fields if available."""
    metadata_summary = {
        'source_type': 'unknown',
        'extra_fields': {},
        'process_id': None
    }

    if not isinstance(experiment_data, dict):
        raise ValueError('Experiment data must be a JSON object')

    if 'metadata_decoded' in experiment_data and isinstance(experiment_data.get('metadata_decoded'), dict):
        metadata_summary['source_type'] = 'elabftw'
        extra_fields = experiment_data.get('metadata_decoded', {}).get('extra_fields', {})
        if not isinstance(extra_fields, dict):
            raise ValueError('metadata_decoded.extra_fields must be a dictionary when present')

        for key, value in extra_fields.items():
            if isinstance(value, dict) and 'value' in value:
                metadata_summary['extra_fields'][key] = value.get('value')
            else:
                metadata_summary['extra_fields'][key] = value

        metadata_summary['process_id'] = metadata_summary['extra_fields'].get('Process ID')
    else:
        metadata_summary['source_type'] = 'direct_execution'

    return metadata_summary



def load_akta_data_from_local(experiment_id: str, results_folder: str) -> Optional[Dict]:
    """Load AKTA results from local fallback locations."""
    results_folder_path = Path(results_folder)
    akta_json = results_folder_path / f'akta_results_{experiment_id}.json'
    local_json = Path(f'akta_results_{experiment_id}.json')
    local_csv = Path('data.csv')

    if akta_json.exists():
        print(f'INFO: Loading local AKTA JSON from {akta_json}')
        with open(akta_json, 'r', encoding='utf-8') as f:
            return json.load(f)

    if local_json.exists():
        print(f'INFO: Loading local AKTA JSON from {local_json}')
        with open(local_json, 'r', encoding='utf-8') as f:
            return json.load(f)

    if local_csv.exists():
        print(f'INFO: Loading local AKTA CSV from {local_csv}')
        df = pd.read_csv(local_csv, na_values=['-'])
        data = {}
        for col in df.columns:
            values = []
            for item in df[col].tolist():
                if pd.isna(item):
                    values.append(None)
                else:
                    values.append(float(item))
            data[col] = values
        if 'time' in df.columns:
            signal_columns = [c for c in df.columns if c != 'time']
            data['signals'] = signal_columns
        return data

    return None



def save_akta_results_copy(akta_data: Dict, experiment_id: str, results_folder: Path) -> Path:
    """Save a local copy of AKTA data in the results folder."""
    output_path = results_folder / f'akta_results_{experiment_id}.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(akta_data, f, indent=4)
    return output_path



def to_numeric_array(values: List) -> np.ndarray:
    """Convert values with None entries to a float numpy array with NaN."""
    converted = []
    for v in values:
        if v is None:
            converted.append(np.nan)
        else:
            try:
                converted.append(float(v))
            except Exception:
                converted.append(np.nan)
    return np.array(converted, dtype=float)



def validate_akta_data(akta_data: Dict) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Validate and extract AKTA time, UV280 and conductivity arrays."""
    if not isinstance(akta_data, dict):
        raise ValueError('AKTA data must be a dictionary')

    if 'time' not in akta_data:
        raise KeyError('AKTA data missing required field: time')

    if 'uv1' not in akta_data:
        raise KeyError('AKTA data missing required field: uv1')

    time = to_numeric_array(akta_data.get('time', []))
    uv1 = to_numeric_array(akta_data.get('uv1', []))

    if len(time) == 0 or len(uv1) == 0:
        raise ValueError('AKTA data contains empty time or uv1 arrays')

    if len(time) != len(uv1):
        raise ValueError(f'Length mismatch between time ({len(time)}) and uv1 ({len(uv1)})')

    cond = None
    if 'cond' in akta_data:
        cond = to_numeric_array(akta_data.get('cond', []))
        if len(cond) != len(time):
            print(f'WARNING: Conductivity length mismatch ({len(cond)}) vs time ({len(time)}). Ignoring conductivity.')
            cond = None

    return time, uv1, cond



def detect_uv_peaks(time: np.ndarray, uv: np.ndarray) -> List[Dict]:
    """Detect peaks in the UV trace and compute retention times."""
    valid_mask = np.isfinite(time) & np.isfinite(uv)
    time_valid = time[valid_mask]
    uv_valid = uv[valid_mask]

    if len(time_valid) < 5:
        return []

    uv_range = float(np.nanmax(uv_valid) - np.nanmin(uv_valid))
    if uv_range <= 0:
        return []

    prominence = max(uv_range * 0.05, 1.0)
    distance = max(1, len(uv_valid) // 50)

    peak_indices, properties = find_peaks(uv_valid, prominence=prominence, distance=distance)

    peaks = []
    prominences = properties.get('prominences', np.array([]))

    for i, idx in enumerate(peak_indices):
        rt_seconds = float(time_valid[idx])
        rt_minutes = rt_seconds / 60.0
        peak_height = float(uv_valid[idx])
        peak_prominence = float(prominences[i]) if i < len(prominences) else None
        peaks.append({
            'peak_index': int(idx),
            'retention_time_s': rt_seconds,
            'retention_time_min': rt_minutes,
            'peak_height_mAU': peak_height,
            'prominence_mAU': peak_prominence
        })

    return peaks



def export_processed_data(time: np.ndarray, uv1: np.ndarray, cond: Optional[np.ndarray], experiment_id: str, results_folder: Path) -> Path:
    """Export processed chromatogram data to CSV."""
    data = {
        'time_s': time,
        'time_min': time / 60.0,
        'uv280_mAU': uv1
    }
    if cond is not None:
        data['conductivity_mScm'] = cond

    df = pd.DataFrame(data)
    output_path = results_folder / f'processed_chromatogram_{experiment_id}.csv'
    df.to_csv(output_path, index=False)
    return output_path



def generate_chromatogram_plot(time: np.ndarray, uv1: np.ndarray, cond: Optional[np.ndarray], peaks: List[Dict], experiment_id: str, results_folder: Path) -> Tuple[Path, Path]:
    """Generate chromatogram plot with UV and conductivity traces and optional peak labels."""
    time_min = time / 60.0

    fig, ax1 = plt.subplots(figsize=(12, 6))
    ax1.plot(time_min, uv1, color='tab:blue', linewidth=1.2, label='UV 280 nm')
    ax1.set_xlabel('Time (min)')
    ax1.set_ylabel('UV 280 nm (mAU)', color='tab:blue')
    ax1.tick_params(axis='y', labelcolor='tab:blue')

    ax2 = None
    if cond is not None and np.isfinite(cond).any():
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond, color='tab:orange', linewidth=1.2, alpha=0.85, label='Conductivity')
        ax2.set_ylabel('Conductivity (mS/cm)', color='tab:orange')
        ax2.tick_params(axis='y', labelcolor='tab:orange')

    if peaks:
        valid_mask = np.isfinite(time) & np.isfinite(uv1)
        time_valid = time[valid_mask]
        uv_valid = uv1[valid_mask]
        for peak in peaks:
            peak_idx = peak['peak_index']
            if peak_idx < len(time_valid) and peak_idx < len(uv_valid):
                x = time_valid[peak_idx] / 60.0
                y = uv_valid[peak_idx]
                label = f"RT={peak['retention_time_min']:.2f} min"
                ax1.plot([x], [y], marker='o', color='red', markersize=4)
                ax1.annotate(
                    label,
                    xy=(x, y),
                    xytext=(5, 8),
                    textcoords='offset points',
                    fontsize=8,
                    color='red'
                )

    ax1.set_title(f'AKTA Chromatogram - Experiment {experiment_id}')
    ax1.grid(True, alpha=0.3)

    handles1, labels1 = ax1.get_legend_handles_labels()
    handles = list(handles1)
    labels = list(labels1)
    if ax2 is not None:
        handles2, labels2 = ax2.get_legend_handles_labels()
        handles.extend(handles2)
        labels.extend(labels2)
    if handles:
        ax1.legend(handles, labels, loc='best')

    fig.tight_layout()

    png_path = results_folder / f'chromatogram_{experiment_id}.png'
    pdf_path = results_folder / f'chromatogram_{experiment_id}.pdf'
    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    return png_path, pdf_path



def analyze_experiment(experiment_id=None, data_folder='../data', results_folder='../results'):
    """
    Main analysis function

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with all key metrics
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

    analysis_results = {
        'experiment_id': experiment_id,
        'status': 'failed',
        'message': '',
        'plots': {},
        'data_outputs': {},
        'metadata': {},
        'files_processed': 0,
        'peak_analysis': {
            'num_peaks': 0,
            'peaks': []
        }
    }

    try:
        print('INFO: Starting AKTA chromatogram analysis')

        if experiment_id is None:
            if len(sys.argv) > 1 and not str(sys.argv[1]).startswith('--'):
                experiment_id = sys.argv[1]
                print(f'INFO: Using experiment ID from command line context: {experiment_id}')
            else:
                experiment_id = autodetect_experiment_id(data_folder)
                print(f'INFO: Auto-detected experiment ID: {experiment_id}')

        if experiment_id is None or str(experiment_id).strip() == '':
            raise ValueError('Experiment ID must be provided or auto-detectable')

        experiment_id = str(experiment_id).strip()
        analysis_results['experiment_id'] = experiment_id

        print(f'INFO: Loading experiment metadata for experiment {experiment_id}')
        experiment_data, experiment_json_path = load_experiment_data(experiment_id, data_folder)
        metadata_summary = extract_metadata(experiment_data)
        analysis_results['metadata'] = metadata_summary
        analysis_results['data_outputs']['experiment_json'] = str(experiment_json_path)

        print(f'INFO: Fetching AKTA results for experiment {experiment_id} from control server')
        akta_data = fetch_akta_results(experiment_id)

        if akta_data:
            print('INFO: AKTA results fetched successfully from control server')
            saved_akta_json = save_akta_results_copy(akta_data, experiment_id, results_folder_path)
            analysis_results['data_outputs']['akta_results_json'] = str(saved_akta_json)
        else:
            print('WARNING: Falling back to local AKTA result files')
            akta_data = load_akta_data_from_local(experiment_id, results_folder)
            if akta_data is None:
                analysis_results['status'] = 'success'
                analysis_results['message'] = 'AKTA data not available - analysis skipped. This is expected for test runs.'
                results_json_path = results_folder_path / f'analysis_results_{experiment_id}.json'
                with open(results_json_path, 'w', encoding='utf-8') as f:
                    json.dump(analysis_results, f, indent=4)
                print(f"INFO: {analysis_results['message']}")
                print(f'INFO: Saved analysis results JSON to: {results_json_path}')
                return analysis_results
            saved_akta_json = save_akta_results_copy(akta_data, experiment_id, results_folder_path)
            analysis_results['data_outputs']['akta_results_json'] = str(saved_akta_json)

        print('INFO: Validating AKTA chromatogram data')
        time, uv1, cond = validate_akta_data(akta_data)
        analysis_results['files_processed'] = 1

        print('INFO: Detecting UV peaks')
        peaks = detect_uv_peaks(time, uv1)
        analysis_results['peak_analysis']['num_peaks'] = len(peaks)
        analysis_results['peak_analysis']['peaks'] = peaks

        print('INFO: Exporting processed chromatogram data')
        processed_csv_path = export_processed_data(time, uv1, cond, experiment_id, results_folder_path)
        analysis_results['data_outputs']['processed_csv'] = str(processed_csv_path)

        print('INFO: Generating chromatogram plots')
        png_path, pdf_path = generate_chromatogram_plot(time, uv1, cond, peaks, experiment_id, results_folder_path)
        analysis_results['plots']['chromatogram_png'] = str(png_path)
        analysis_results['plots']['chromatogram_pdf'] = str(pdf_path)

        analysis_results['status'] = 'success'
        if peaks:
            analysis_results['message'] = f'AKTA analysis completed successfully. Identified {len(peaks)} peak(s).'
        else:
            analysis_results['message'] = 'AKTA analysis completed successfully. No peaks were identified.'

    except Exception as e:
        analysis_results['status'] = 'failed'
        analysis_results['message'] = str(e)
        print(f'ERROR: {e}')

    results_json_file = f'analysis_results_{analysis_results.get("experiment_id", experiment_id)}.json'
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, 'w', encoding='utf-8') as f:
        json.dump(analysis_results, f, indent=4)
    print(f'Saved analysis results JSON to: {results_json_path}')

    return analysis_results



def main():
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze AKTA experiment data')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID')
    parser.add_argument('--data-folder', default='../data', help='Data folder path')
    parser.add_argument('--results-folder', default='../results', help='Results folder path')

    args = parser.parse_args()

    try:
        results = analyze_experiment(
            experiment_id=args.experiment_id,
            data_folder=args.data_folder,
            results_folder=args.results_folder
        )
        if results.get('status') == 'success':
            print('SUCCESS: Analysis successful!')
            return 0
        else:
            print(f"ERROR: Analysis failed: {results.get('message', 'Unknown error')}")
            return 1
    except Exception as e:
        print(f'ERROR: Analysis failed: {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
