#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatogram Evaluation
Can be called externally with experiment ID as parameter.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

try:
    from scipy.signal import find_peaks
except Exception:
    find_peaks = None

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


def auto_detect_experiment_id(data_folder: str) -> str:
    """Auto-detect the most recent experiment ID from root or data folder."""
    candidates = []

    search_dirs = [Path('..'), Path(data_folder)]
    for base_dir in search_dirs:
        if not base_dir.exists() or not base_dir.is_dir():
            continue
        for path in base_dir.glob('experiment_*.json'):
            try:
                candidates.append((path.stat().st_mtime, path))
            except Exception:
                continue

    if not candidates:
        raise FileNotFoundError('No experiment JSON files found for auto-detection in root or data folder.')

    candidates.sort(key=lambda x: x[0], reverse=True)
    latest_path = candidates[0][1]
    name = latest_path.stem
    if not name.startswith('experiment_'):
        raise ValueError(f'Unexpected experiment filename format: {latest_path.name}')
    experiment_id = name[len('experiment_'):]
    print(f'INFO: Auto-detected experiment ID: {experiment_id}')
    return experiment_id


def load_experiment_json(experiment_id: str, data_folder: str) -> Tuple[dict, Path]:
    """Load experiment JSON, checking root folder first, then data folder."""
    root_path = Path(f'../experiment_{experiment_id}.json')
    fallback_path = Path(data_folder) / f'experiment_{experiment_id}.json'

    chosen_path = None
    if root_path.exists():
        chosen_path = root_path
    elif fallback_path.exists():
        chosen_path = fallback_path
    else:
        raise FileNotFoundError(f'Data file not found. Checked: {root_path} and {fallback_path}')

    try:
        with open(chosen_path, 'r', encoding='utf-8') as f:
            experiment_data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f'Invalid JSON format in {chosen_path}: {e}')

    print(f'INFO: Loaded experiment JSON from: {chosen_path}')
    return experiment_data, chosen_path


def extract_metadata_fields(experiment_data: dict) -> Dict[str, Optional[str]]:
    """Extract expected eLabFTW extra fields with validation."""
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
        'EQUILIBRATION_FLOW_RATE',
        'ELUTE_ISOCRATIC_DURATION',
        'ELUTE_ISOCRATIC_PERCENT_B',
        'ELUTE_ISOCRATIC_2_DURATION',
        'ELUTE_ISOCRATIC_2_PERCENT_B'
    ]

    metadata_out = {}
    extra_fields = None

    try:
        extra_fields = experiment_data['metadata_decoded']['extra_fields']
    except KeyError:
        print('WARNING: metadata_decoded.extra_fields not present in experiment JSON. Continuing without eLabFTW metadata.')
        return metadata_out

    if not isinstance(extra_fields, dict):
        raise ValueError('metadata_decoded.extra_fields is present but is not a dictionary.')

    for field in expected_fields:
        if field in extra_fields:
            value_container = extra_fields[field]
            if isinstance(value_container, dict) and 'value' in value_container:
                metadata_out[field] = value_container.get('value')
            else:
                print(f'WARNING: Metadata field {field} exists but does not contain a value key.')
                metadata_out[field] = None
        else:
            metadata_out[field] = None

    return metadata_out


def save_akta_results_locally(akta_results: dict, results_folder_path: Path, experiment_id: str) -> Path:
    """Save fetched AKTA JSON results to results folder."""
    target = results_folder_path / f'akta_results_{experiment_id}.json'
    with open(target, 'w', encoding='utf-8') as f:
        json.dump(akta_results, f, indent=4)
    print(f'INFO: Saved AKTA server results to: {target}')
    return target


def load_local_akta_data(experiment_id: str, results_folder_path: Path) -> Optional[dict]:
    """Load AKTA data from local fallback locations."""
    akta_json = results_folder_path / f'akta_results_{experiment_id}.json'
    local_json_alt = Path(f'akta_results_{experiment_id}.json')
    akta_csv = Path('data.csv')

    if akta_json.exists():
        print(f'INFO: Loading local AKTA JSON from: {akta_json}')
        with open(akta_json, 'r', encoding='utf-8') as f:
            return json.load(f)

    if local_json_alt.exists():
        print(f'INFO: Loading local AKTA JSON from: {local_json_alt}')
        with open(local_json_alt, 'r', encoding='utf-8') as f:
            return json.load(f)

    if akta_csv.exists():
        print(f'INFO: Loading local AKTA CSV from: {akta_csv}')
        df = pd.read_csv(akta_csv, na_values=['-'])
        return dataframe_to_akta_dict(df)

    return None


def dataframe_to_akta_dict(df: pd.DataFrame) -> dict:
    """Convert AKTA CSV dataframe to AKTA-like dict structure."""
    data = {'signals': [c for c in df.columns if c != 'time']}
    for col in df.columns:
        values = []
        for v in df[col].tolist():
            if pd.isna(v):
                values.append(None)
            else:
                try:
                    values.append(float(v))
                except Exception:
                    values.append(v)
        data[col] = values
    return data


def sanitize_numeric_array(values: List) -> np.ndarray:
    """Convert list with possible None values to float numpy array with NaN."""
    cleaned = []
    for v in values:
        if v is None:
            cleaned.append(np.nan)
        else:
            try:
                cleaned.append(float(v))
            except Exception:
                cleaned.append(np.nan)
    return np.array(cleaned, dtype=float)


def validate_akta_data(akta_data: dict) -> Dict[str, np.ndarray]:
    """Validate and normalize AKTA result structure."""
    if not isinstance(akta_data, dict):
        raise ValueError('AKTA data is not a dictionary.')

    if 'time' not in akta_data:
        raise KeyError('AKTA data missing required field: time')

    time = sanitize_numeric_array(akta_data.get('time', []))
    if time.size == 0:
        raise ValueError('AKTA data contains empty time array.')

    uv = sanitize_numeric_array(akta_data.get('uv1', [])) if 'uv1' in akta_data else np.array([])
    cond = sanitize_numeric_array(akta_data.get('cond', [])) if 'cond' in akta_data else np.array([])

    if uv.size == 0 and cond.size == 0:
        raise ValueError('AKTA data does not contain uv1 or cond signals.')

    min_len = len(time)
    for arr in [uv, cond]:
        if arr.size > 0:
            min_len = min(min_len, len(arr))

    time = time[:min_len]
    if uv.size > 0:
        uv = uv[:min_len]
    else:
        uv = np.full(min_len, np.nan)
    if cond.size > 0:
        cond = cond[:min_len]
    else:
        cond = np.full(min_len, np.nan)

    valid_time_mask = ~np.isnan(time)
    time = time[valid_time_mask]
    uv = uv[valid_time_mask]
    cond = cond[valid_time_mask]

    if len(time) == 0:
        raise ValueError('No valid numeric time points available after cleaning.')

    return {
        'time_s': time,
        'time_min': time / 60.0,
        'uv1': uv,
        'cond': cond
    }


def detect_peaks(time_min: np.ndarray, uv: np.ndarray) -> List[dict]:
    """Detect peaks in UV trace and compute retention time metrics."""
    peaks_output = []

    valid_mask = (~np.isnan(time_min)) & (~np.isnan(uv))
    x = time_min[valid_mask]
    y = uv[valid_mask]

    if len(x) < 5 or len(y) < 5:
        print('INFO: Not enough UV points for peak detection.')
        return peaks_output

    y_range = float(np.nanmax(y) - np.nanmin(y))
    if not np.isfinite(y_range) or y_range <= 0:
        print('INFO: UV signal range is too small for peak detection.')
        return peaks_output

    if find_peaks is None:
        print('WARNING: scipy.signal.find_peaks is unavailable. Peak detection skipped.')
        return peaks_output

    prominence = max(y_range * 0.05, 1e-9)
    distance = max(1, len(y) // 50)

    try:
        peak_indices, properties = find_peaks(y, prominence=prominence, distance=distance)
    except Exception as e:
        print(f'WARNING: Peak detection failed: {e}')
        return peaks_output

    for idx_in_detected, peak_idx in enumerate(peak_indices):
        rt = float(x[peak_idx])
        height = float(y[peak_idx])
        peak_info = {
            'peak_number': idx_in_detected + 1,
            'retention_time_min': rt,
            'peak_height_mAU': height
        }
        if 'prominences' in properties and len(properties['prominences']) > idx_in_detected:
            peak_info['prominence_mAU'] = float(properties['prominences'][idx_in_detected])
        peaks_output.append(peak_info)

    print(f'INFO: Identified {len(peaks_output)} peak(s).')
    return peaks_output


def export_processed_data(time_s: np.ndarray, time_min: np.ndarray, uv: np.ndarray, cond: np.ndarray, results_folder_path: Path, experiment_id: str) -> Path:
    """Export processed AKTA signals to CSV."""
    df = pd.DataFrame({
        'time_s': time_s,
        'time_min': time_min,
        'uv1_mAU': uv,
        'cond_mS_cm': cond
    })
    csv_path = results_folder_path / f'processed_akta_data_{experiment_id}.csv'
    df.to_csv(csv_path, index=False)
    print(f'INFO: Saved processed data CSV to: {csv_path}')
    return csv_path


def export_peaks_csv(peaks: List[dict], results_folder_path: Path, experiment_id: str) -> Optional[Path]:
    """Export peak table to CSV if peaks exist."""
    if not peaks:
        return None
    df = pd.DataFrame(peaks)
    csv_path = results_folder_path / f'peak_table_{experiment_id}.csv'
    df.to_csv(csv_path, index=False)
    print(f'INFO: Saved peak table CSV to: {csv_path}')
    return csv_path


def plot_chromatogram(time_min: np.ndarray, uv: np.ndarray, cond: np.ndarray, peaks: List[dict], results_folder_path: Path, experiment_id: str) -> Dict[str, str]:
    """Plot chromatogram with UV and conductivity, and label peaks if present."""
    fig, ax1 = plt.subplots(figsize=(12, 6))

    uv_color = 'tab:blue'
    cond_color = 'tab:orange'

    uv_has_data = np.any(~np.isnan(uv))
    cond_has_data = np.any(~np.isnan(cond))

    if uv_has_data:
        ax1.plot(time_min, uv, color=uv_color, linewidth=1.2, label='UV 280 nm')
    ax1.set_xlabel('Time (min)')
    ax1.set_ylabel('UV Absorbance (mAU)', color=uv_color)
    ax1.tick_params(axis='y', labelcolor=uv_color)

    ax2 = None
    if cond_has_data:
        ax2 = ax1.twinx()
        ax2.plot(time_min, cond, color=cond_color, linewidth=1.2, alpha=0.85, label='Conductivity')
        ax2.set_ylabel('Conductivity (mS/cm)', color=cond_color)
        ax2.tick_params(axis='y', labelcolor=cond_color)

    if peaks and uv_has_data:
        valid_mask = (~np.isnan(time_min)) & (~np.isnan(uv))
        x = time_min[valid_mask]
        y = uv[valid_mask]
        for peak in peaks:
            rt = peak['retention_time_min']
            nearest_idx = int(np.argmin(np.abs(x - rt)))
            peak_x = float(x[nearest_idx])
            peak_y = float(y[nearest_idx])
            ax1.plot(peak_x, peak_y, 'o', color='red', markersize=5)
            ax1.annotate(
                f"RT={rt:.2f} min",
                xy=(peak_x, peak_y),
                xytext=(0, 8),
                textcoords='offset points',
                ha='center',
                fontsize=8,
                color='red'
            )

    title = f'AKTA Chromatogram - Experiment {experiment_id}'
    ax1.set_title(title)
    ax1.grid(True, alpha=0.3)

    lines = []
    labels = []
    l1, lab1 = ax1.get_legend_handles_labels()
    lines.extend(l1)
    labels.extend(lab1)
    if ax2 is not None:
        l2, lab2 = ax2.get_legend_handles_labels()
        lines.extend(l2)
        labels.extend(lab2)
    if lines:
        ax1.legend(lines, labels, loc='best')

    fig.tight_layout()

    png_path = results_folder_path / f'chromatogram_{experiment_id}.png'
    pdf_path = results_folder_path / f'chromatogram_{experiment_id}.pdf'
    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f'INFO: Saved chromatogram PNG to: {png_path}')
    print(f'INFO: Saved chromatogram PDF to: {pdf_path}')

    return {
        'png': str(png_path),
        'pdf': str(pdf_path)
    }


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
        'peak_count': 0,
        'peaks': []
    }

    try:
        if experiment_id is None:
            if len(sys.argv) > 1 and not str(sys.argv[1]).startswith('--'):
                experiment_id = sys.argv[1]
            else:
                experiment_id = auto_detect_experiment_id(data_folder)

        experiment_id = str(experiment_id)
        analysis_results['experiment_id'] = experiment_id

        if not data_folder:
            raise ValueError('data_folder must be a non-empty path string.')
        if not results_folder:
            raise ValueError('results_folder must be a non-empty path string.')

        experiment_data, experiment_json_path = load_experiment_json(experiment_id, data_folder)
        analysis_results['data_outputs']['experiment_json'] = str(experiment_json_path)

        metadata = extract_metadata_fields(experiment_data)
        analysis_results['metadata'] = metadata

        print(f'INFO: Fetching AKTA data for experiment {experiment_id} from control server...')
        akta_data = fetch_akta_results(experiment_id)

        if akta_data:
            saved_json_path = save_akta_results_locally(akta_data, results_folder_path, experiment_id)
            analysis_results['data_outputs']['akta_json'] = str(saved_json_path)
        else:
            print('INFO: Falling back to local AKTA data sources...')
            akta_data = load_local_akta_data(experiment_id, results_folder_path)
            if akta_data is None:
                analysis_results['status'] = 'success'
                analysis_results['message'] = 'AKTA data not available - analysis skipped. No server or local chromatography data found.'
                results_json_path = results_folder_path / f'analysis_results_{experiment_id}.json'
                with open(results_json_path, 'w', encoding='utf-8') as f:
                    json.dump(analysis_results, f, indent=4)
                print(f"INFO: {analysis_results['message']}")
                print(f'Saved analysis results JSON to: {results_json_path}')
                return analysis_results

        normalized = validate_akta_data(akta_data)
        time_s = normalized['time_s']
        time_min = normalized['time_min']
        uv = normalized['uv1']
        cond = normalized['cond']
        analysis_results['files_processed'] = 1

        processed_csv = export_processed_data(time_s, time_min, uv, cond, results_folder_path, experiment_id)
        analysis_results['data_outputs']['processed_csv'] = str(processed_csv)

        peaks = detect_peaks(time_min, uv)
        analysis_results['peaks'] = peaks
        analysis_results['peak_count'] = len(peaks)

        peaks_csv = export_peaks_csv(peaks, results_folder_path, experiment_id)
        if peaks_csv is not None:
            analysis_results['data_outputs']['peaks_csv'] = str(peaks_csv)

        plot_paths = plot_chromatogram(time_min, uv, cond, peaks, results_folder_path, experiment_id)
        analysis_results['plots'] = plot_paths

        analysis_results['signal_summary'] = {
            'time_start_min': float(np.nanmin(time_min)) if len(time_min) else None,
            'time_end_min': float(np.nanmax(time_min)) if len(time_min) else None,
            'uv_max_mAU': float(np.nanmax(uv)) if np.any(~np.isnan(uv)) else None,
            'uv_min_mAU': float(np.nanmin(uv)) if np.any(~np.isnan(uv)) else None,
            'cond_max_mS_cm': float(np.nanmax(cond)) if np.any(~np.isnan(cond)) else None,
            'cond_min_mS_cm': float(np.nanmin(cond)) if np.any(~np.isnan(cond)) else None
        }

        if peaks:
            analysis_results['message'] = f'AKTA chromatogram analyzed successfully. Identified {len(peaks)} peak(s).'
        else:
            analysis_results['message'] = 'AKTA chromatogram analyzed successfully. No peaks were identified.'
        analysis_results['status'] = 'success'

    except Exception as e:
        analysis_results['status'] = 'failed'
        analysis_results['message'] = f'{type(e).__name__}: {e}'
        print(f'ERROR: {analysis_results["message"]}')

    results_json_file = f'analysis_results_{analysis_results.get("experiment_id", "unknown")}.json'
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, 'w', encoding='utf-8') as f:
        json.dump(analysis_results, f, indent=4)
    print(f'Saved analysis results JSON to: {results_json_path}')

    if analysis_results['status'] != 'success':
        raise RuntimeError(analysis_results['message'])

    return analysis_results


def main():
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze AKTA experiment data')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID')
    parser.add_argument('--data-folder', default='../data', help='Data folder path')
    parser.add_argument('--results-folder', default='../results', help='Results folder path')

    args = parser.parse_args()

    try:
        analyze_experiment(
            experiment_id=args.experiment_id,
            data_folder=args.data_folder,
            results_folder=args.results_folder
        )
        print('SUCCESS: Analysis successful!')
        return 0
    except Exception as e:
        print(f'ERROR: Analysis failed: {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
