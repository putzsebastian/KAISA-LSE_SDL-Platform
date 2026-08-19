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

# Device / API configuration for AKTA control server
AKTA_CONTROL_SERVER = os.getenv('AKTA_CONTROL_SERVER', 'http://localhost:5001')
AKTA_API_KEY = os.getenv('AKTA_API_KEY', 'akta-control-key')


def fetch_akta_results_from_server(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results JSON from the control server.

    Returns the "results" dict or None if not available or on error.
    """
    try:
        headers = {'X-API-Key': AKTA_API_KEY}
        url = f'{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}'
        print(f"INFO: Fetching AKTA results from server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', {})
            if results:
                print("INFO: AKTA results retrieved from control server.")
                return results
            print("WARNING: AKTA server response did not contain 'results' field.")
            return None
        else:
            print(f"WARNING: AKTA results not found on server (status {response.status_code}).")
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def load_akta_local_results(experiment_id: str, results_folder_path: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local JSON or CSV as fallback.

    Priority:
      1) akta_results_{experiment_id}.json in results folder
      2) data.csv in current working directory
    Returns dict in AKTA JSON structure or None if not available.
    """
    json_path = results_folder_path / f'akta_results_{experiment_id}.json'
    if json_path.exists():
        try:
            print(f"INFO: Loading local AKTA JSON results from {json_path}")
            with open(json_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA JSON file {json_path}: {e}")

    csv_path = Path('data.csv')
    if csv_path.exists():
        try:
            print(f"INFO: Loading local AKTA CSV results from {csv_path}")
            df = pd.read_csv(csv_path, na_values=['-'])
            # Convert to AKTA-like structure
            akta_data: Dict[str, Any] = {}
            for col in df.columns:
                series = df[col]
                if col == 'time':
                    akta_data['time'] = series.dropna().astype(float).tolist()
                else:
                    akta_data[col] = series.astype(float).where(~series.isna(), None).tolist()
            # Infer signals list
            signals = [c for c in akta_data.keys() if c not in ('time', 'signals', 'sample_time')]
            akta_data['signals'] = signals
            # Try to estimate sample_time from median time diff if possible
            try:
                t = np.array(akta_data.get('time', []), dtype=float)
                if t.size >= 2:
                    diffs = np.diff(t)
                    positive_diffs = diffs[diffs > 0]
                    if positive_diffs.size > 0:
                        akta_data['sample_time'] = float(np.median(positive_diffs))
            except Exception:
                pass
            return akta_data
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA CSV file {csv_path}: {e}")

    print("INFO: No local AKTA results file found.")
    return None


def validate_and_extract_metadata(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and validate required eLabFTW metadata extra fields.

    Missing fields raise KeyError with a clear message.
    """
    try:
        metadata = experiment_data['metadata_decoded']['extra_fields']
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure in experiment JSON: {e}")

    required_fields = [
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

    extracted: Dict[str, Any] = {}
    for field in required_fields:
        if field not in metadata:
            raise KeyError(f"Missing expected metadata field: {field}")
        field_dict = metadata[field]
        if not isinstance(field_dict, dict) or 'value' not in field_dict:
            raise TypeError(f"Metadata field '{field}' is not in expected format with a 'value' key.")
        extracted[field] = field_dict['value']

    return extracted


def prepare_akta_dataframe(akta_data: Dict[str, Any]) -> pd.DataFrame:
    """Convert AKTA results dict to a pandas DataFrame with numeric columns.

    Ensures 'time' column exists and converts other known signals to float.
    """
    if 'time' not in akta_data:
        raise ValueError("AKTA results do not contain a 'time' field.")

    time_array = np.array(akta_data['time'], dtype=float)
    df_dict: Dict[str, Any] = {'time_s': time_array}

    # Handle signals
    signals = akta_data.get('signals', [])
    if not isinstance(signals, list):
        signals = []

    for signal_name in signals:
        if signal_name not in akta_data:
            continue
        arr = np.array([
            np.nan if v is None else v for v in akta_data.get(signal_name, [])
        ], dtype=float)
        df_dict[signal_name] = arr

    df = pd.DataFrame(df_dict)

    # Basic sanity cleaning: replace inf with nan and drop rows where time is nan
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df = df.dropna(subset=['time_s'])
    df = df.sort_values('time_s').reset_index(drop=True)

    return df


def detect_peaks(time_s: np.ndarray, signal: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Detect peaks in a chromatogram signal.

    Uses a simple prominence-based approach. Returns indices of peaks and
    a dictionary with peak properties.
    """
    # Remove NaNs for peak detection by simple interpolation / masking
    valid_mask = ~np.isnan(signal)
    if valid_mask.sum() < 3:
        return np.array([], dtype=int), {}

    sig_valid = signal.copy()
    # Simple forward-fill/backward-fill for NaNs
    n = len(sig_valid)
    for i in range(n):
        if np.isnan(sig_valid[i]):
            # look backward
            j = i - 1
            while j >= 0 and np.isnan(sig_valid[j]):
                j -= 1
            back_val = sig_valid[j] if j >= 0 else np.nan
            # look forward
            k = i + 1
            while k < n and np.isnan(sig_valid[k]):
                k += 1
            fwd_val = sig_valid[k] if k < n else np.nan
            if not np.isnan(back_val) and not np.isnan(fwd_val):
                sig_valid[i] = 0.5 * (back_val + fwd_val)
            elif not np.isnan(back_val):
                sig_valid[i] = back_val
            elif not np.isnan(fwd_val):
                sig_valid[i] = fwd_val

    # Estimate a height and prominence threshold relative to signal range
    sig_min = np.nanmin(sig_valid)
    sig_max = np.nanmax(sig_valid)
    dynamic_range = sig_max - sig_min
    if dynamic_range <= 0:
        return np.array([], dtype=int), {}

    height_thresh = sig_min + 0.1 * dynamic_range
    prominence_thresh = 0.05 * dynamic_range

    peaks, properties = find_peaks(
        sig_valid,
        height=height_thresh,
        prominence=prominence_thresh,
        distance=max(1, int(0.01 * len(sig_valid))),
    )
    return peaks, properties


def plot_chromatogram(
    df: pd.DataFrame,
    experiment_id: str,
    results_folder_path: Path,
    peaks_idx: Optional[np.ndarray] = None,
    peak_rts: Optional[np.ndarray] = None,
) -> Dict[str, str]:
    """Plot chromatogram with UV and conductivity, annotate peaks if present.

    Returns dict of generated plot file paths.
    """
    time_min = df['time_s'].values / 60.0
    uv = df.get('uv1', df.get('uv', None))
    cond = df.get('cond', None)

    if uv is None and cond is None:
        raise ValueError("Neither UV (uv1) nor conductivity (cond) signals are present in AKTA data.")

    fig, ax1 = plt.subplots(figsize=(10, 5))

    lines = []
    labels = []

    if uv is not None:
        uv_vals = uv.values.astype(float)
        l1, = ax1.plot(time_min, uv_vals, color='blue', linewidth=0.8, label='UV 280 nm')
        ax1.set_ylabel('UV absorbance (mAU)', color='blue')
        ax1.tick_params(axis='y', labelcolor='blue')
        lines.append(l1)
        labels.append('UV 280 nm')

    ax1.set_xlabel('Time (min)')

    ax2 = None
    if cond is not None:
        cond_vals = cond.values.astype(float)
        ax2 = ax1.twinx()
        l2, = ax2.plot(time_min, cond_vals, color='red', linewidth=0.8, alpha=0.7, label='Conductivity')
        ax2.set_ylabel('Conductivity (mS/cm)', color='red')
        ax2.tick_params(axis='y', labelcolor='red')
        lines.append(l2)
        labels.append('Conductivity')

    ax1.set_title(f'AKTA Chromatogram - Experiment {experiment_id}')

    # Peak annotation (if peaks were identified)
    if peaks_idx is not None and peak_rts is not None and len(peaks_idx) > 0:
        try:
            for idx, rt in zip(peaks_idx, peak_rts):
                x = time_min[idx]
                y = uv.values[idx] if uv is not None else None
                if y is None or np.isnan(y):
                    continue
                ax1.plot(x, y, 'ko', markersize=4)
                ax1.annotate(
                    f'{rt:.2f} min',
                    xy=(x, y),
                    xytext=(0, 8),
                    textcoords='offset points',
                    fontsize=8,
                    ha='center',
                    color='black',
                    rotation=0,
                )
        except Exception as e:
            print(f"WARNING: Failed to annotate peaks: {e}")

    if lines:
        ax1.legend(lines, labels, loc='best')

    fig.tight_layout()

    plot_paths: Dict[str, str] = {}
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


def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = '../data',
                       results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography runs.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries to auto-detect.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with key metrics and paths to generated files.
    """
    results_folder_path = Path(results_folder)
    results_folder_path.mkdir(parents=True, exist_ok=True)

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
        if len(sys.argv) > 1 and sys.argv[1]:
            experiment_id = sys.argv[1]
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            # Auto-detect most recent experiment JSON in data_folder
            data_path = Path(data_folder)
            json_files = sorted(
                data_path.glob('experiment_*.json'),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not json_files:
                msg = f"No experiment JSON files found in data folder: {data_folder}"
                analysis_results["message"] = msg
                print(f"ERROR: {msg}")
                return analysis_results
            latest = json_files[0]
            name = latest.stem  # experiment_<ID>
            try:
                experiment_id = name.split('_', 1)[1]
            except Exception:
                experiment_id = name
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Auto-detected most recent experiment ID: {experiment_id}")

    # Validate experiment_id
    if experiment_id is None or str(experiment_id).strip() == "":
        msg = "Experiment ID is required but could not be determined."
        analysis_results["message"] = msg
        print(f"ERROR: {msg}")
        return analysis_results

    experiment_id = str(experiment_id)
    analysis_results["experiment_id"] = experiment_id

    # Load experiment JSON data (root folder first, then data_folder as fallback)
    exp_json_path_root = Path('..') / f'experiment_{experiment_id}.json'
    if exp_json_path_root.exists():
        data_file_path = exp_json_path_root
    else:
        data_file_path = Path(data_folder) / f'experiment_{experiment_id}.json'

    print(f"INFO: Loading experiment data from: {data_file_path}")
    try:
        with open(data_file_path, 'r') as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        msg = f"Data file not found: {data_file_path}"
        analysis_results["message"] = msg
        print(f"ERROR: {msg}")
        return analysis_results
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON format in {data_file_path}: {e}"
        analysis_results["message"] = msg
        print(f"ERROR: {msg}")
        return analysis_results

    analysis_results["metadata"]["data_file_path"] = str(data_file_path.resolve())

    # Extract required metadata extra fields
    try:
        extra_meta = validate_and_extract_metadata(experiment_data)
        analysis_results["metadata"].update(extra_meta)
    except (KeyError, TypeError) as e:
        msg = f"Metadata validation error: {e}"
        analysis_results["message"] = msg
        print(f"ERROR: {msg}")
        return analysis_results

    # Fetch AKTA results: try device control server first, then local fallback
    print(f"INFO: Fetching AKTA results for experiment {experiment_id}...")
    akta_data = fetch_akta_results_from_server(experiment_id)
    data_source = "device"

    if akta_data is None:
        print("INFO: Falling back to local AKTA results files.")
        akta_data = load_akta_local_results(experiment_id, results_folder_path)
        data_source = "local" if akta_data is not None else "none"

    if akta_data is None:
        msg = "AKTA data not available - analysis skipped. This may be expected for dry runs or setup tests."
        analysis_results["status"] = "success"
        analysis_results["message"] = msg
        analysis_results["metadata"]["data_source"] = data_source
        print(f"INFO: {msg}")
        # Save results JSON even if no data
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    analysis_results["metadata"]["data_source"] = data_source

    # Prepare DataFrame from AKTA data
    try:
        df = prepare_akta_dataframe(akta_data)
    except Exception as e:
        msg = f"Failed to prepare AKTA DataFrame: {e}"
        analysis_results["message"] = msg
        print(f"ERROR: {msg}")
        return analysis_results

    analysis_results["files_processed"] = 1

    # Save processed data as CSV
    processed_csv_path = results_folder_path / f'akta_processed_{experiment_id}.csv'
    try:
        df.to_csv(processed_csv_path, index=False)
        analysis_results["data_outputs"]["processed_csv"] = str(processed_csv_path.resolve())
        print(f"INFO: Saved processed AKTA data to CSV: {processed_csv_path}")
    except Exception as e:
        print(f"WARNING: Failed to save processed data CSV: {e}")

    # Peak detection on UV signal if available
    peaks_idx: np.ndarray = np.array([], dtype=int)
    peak_rts_min: np.ndarray = np.array([], dtype=float)

    if 'uv1' in df.columns:
        uv_signal = df['uv1'].values.astype(float)
        time_s = df['time_s'].values.astype(float)
        try:
            peaks_idx, properties = detect_peaks(time_s, uv_signal)
            if peaks_idx.size > 0:
                peak_rts_min = time_s[peaks_idx] / 60.0
                analysis_results['metadata']['num_peaks'] = int(peaks_idx.size)
                analysis_results['metadata']['peak_retention_times_min'] = [
                    float(rt) for rt in peak_rts_min
                ]
                print(f"INFO: Detected {peaks_idx.size} peak(s) in UV signal.")
            else:
                analysis_results['metadata']['num_peaks'] = 0
                print("INFO: No peaks detected in UV signal.")
        except Exception as e:
            print(f"WARNING: Peak detection failed: {e}")
            analysis_results['metadata']['num_peaks'] = 0
    else:
        print("INFO: No 'uv1' column present; skipping peak detection.")
        analysis_results['metadata']['num_peaks'] = 0

    # Plot chromatogram (with peak labels only if peaks present)
    try:
        peaks_for_plot = peaks_idx if peaks_idx.size > 0 else None
        rts_for_plot = peak_rts_min if peak_rts_min.size > 0 else None
        plot_paths = plot_chromatogram(
            df=df,
            experiment_id=experiment_id,
            results_folder_path=results_folder_path,
            peaks_idx=peaks_for_plot,
            peak_rts=rts_for_plot,
        )
        analysis_results['plots'].update(plot_paths)
    except Exception as e:
        print(f"WARNING: Failed to generate chromatogram plots: {e}")

    analysis_results["status"] = "success"
    analysis_results["message"] = "AKTA chromatography analysis completed successfully."

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
            print("SUCCESS: Analysis completed successfully.")
            return 0
        else:
            print(f"ERROR: Analysis failed: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
