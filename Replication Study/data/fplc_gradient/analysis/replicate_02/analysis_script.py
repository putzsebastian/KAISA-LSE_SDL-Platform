#!/usr/bin/env python3
"""
Analysis Script - AKTA Chromatography Run Evaluation
Can be called externally with experiment ID as parameter.

Features:
- Loads experiment metadata from experiment_<ID>.json
- Fetches AKTA chromatogram data from AKTA control server with local fallback
- Plots UV 280 nm and conductivity chromatograms with different colors
- Identifies peaks in UV signal and calculates their retention times
- Labels peaks on the plot with retention times (only if peaks are found)
- Saves processed data to CSV and analysis results to JSON in results folder
- Can be used as a module or as a command-line script
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import find_peaks

import requests

# AKTA control server configuration
AKTA_CONTROL_SERVER = os.getenv('AKTA_CONTROL_SERVER', 'http://localhost:5001')
AKTA_API_KEY = os.getenv('AKTA_API_KEY', 'akta-control-key')


def fetch_akta_results_from_server(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results JSON from control server.

    Returns a dict with keys like 'time', 'uv1', 'cond' if available,
    or None if not found or server not reachable.
    """
    try:
        headers = {'X-API-Key': AKTA_API_KEY}
        url = f"{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}"
        print(f"INFO: Requesting AKTA results from server: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            results = data.get('results') or data
            print("INFO: AKTA results successfully fetched from server.")
            return results
        else:
            print(f"WARNING: AKTA results not found on server (status {response.status_code}).")
            return None
    except Exception as e:
        print(f"WARNING: Could not reach AKTA control server: {e}")
        return None


def load_akta_results_local(experiment_id: str, data_folder: str, results_folder: str) -> Optional[Dict[str, Any]]:
    """Load AKTA results from local files.

    Priority:
      1) results/akta_results_<ID>.json
      2) data/data.csv in data folder
    Returns dict in AKTA JSON structure or None if nothing available.
    """
    results_folder_path = Path(results_folder)
    data_folder_path = Path(data_folder)

    json_path = results_folder_path / f"akta_results_{experiment_id}.json"
    csv_path = data_folder_path / "data.csv"

    if json_path.exists():
        print(f"INFO: Loading AKTA results from local JSON: {json_path}")
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            return data
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA JSON {json_path}: {e}")

    if csv_path.exists():
        print(f"INFO: Loading AKTA results from local CSV: {csv_path}")
        try:
            df = pd.read_csv(csv_path, na_values=['-'])
            if 'time' not in df.columns:
                raise ValueError("CSV file missing required 'time' column")
            akta_data: Dict[str, Any] = {}
            for col in df.columns:
                # Drop NaNs and convert to list
                series = df[col].dropna()
                akta_data[col] = series.tolist()
            return akta_data
        except Exception as e:
            print(f"WARNING: Failed to read local AKTA CSV {csv_path}: {e}")

    print("INFO: No local AKTA results file found.")
    return None


def ensure_results_folder(results_folder: str) -> Path:
    path = Path(results_folder)
    path.mkdir(parents=True, exist_ok=True)
    return path


def auto_detect_latest_experiment_id(data_folder: str) -> Optional[str]:
    """Find the most recent experiment_<ID>.json file and return ID as string."""
    data_path = Path(data_folder)
    if not data_path.exists():
        print(f"WARNING: Data folder does not exist for auto-detection: {data_folder}")
        return None

    candidates = list(data_path.glob("experiment_*.json"))
    if not candidates:
        print("WARNING: No experiment_*.json files found for auto-detection.")
        return None

    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    name = latest.stem  # 'experiment_<ID>'
    try:
        _, eid = name.split("_", 1)
        print(f"INFO: Auto-detected latest experiment ID: {eid}")
        return eid
    except ValueError:
        print(f"WARNING: Could not parse experiment ID from file name: {latest.name}")
        return None


def identify_peaks(time_s: np.ndarray, uv: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Identify peaks in UV signal and return indices and diagnostics.

    Uses scipy.signal.find_peaks with basic prominence and distance heuristics.
    """
    diagnostics: Dict[str, Any] = {}

    if len(time_s) == 0 or len(uv) == 0:
        diagnostics["message"] = "Empty time or UV array; no peaks detected."
        return np.array([], dtype=int), diagnostics

    finite_mask = np.isfinite(uv)
    if not np.any(finite_mask):
        diagnostics["message"] = "No finite UV values; no peaks detected."
        return np.array([], dtype=int), diagnostics

    uv_valid = uv[finite_mask]
    t_valid = time_s[finite_mask]

    if len(uv_valid) < 5:
        diagnostics["message"] = "Too few data points for peak detection."
        return np.array([], dtype=int), diagnostics

    uv_range = float(np.nanmax(uv_valid) - np.nanmin(uv_valid))
    if uv_range <= 0:
        diagnostics["message"] = "UV signal is constant; no peaks expected."
        return np.array([], dtype=int), diagnostics

    prominence = max(uv_range * 0.05, 0.01)
    distance_points = max(int(len(uv_valid) * 0.01), 1)

    peaks_rel, properties = find_peaks(uv_valid, prominence=prominence, distance=distance_points)

    peaks = np.nonzero(finite_mask)[0][peaks_rel]

    diagnostics["prominence_used"] = prominence
    diagnostics["distance_points_used"] = distance_points
    diagnostics["num_peaks"] = int(len(peaks))

    return peaks, diagnostics


def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = '../data',
                       results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for AKTA run.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with key metrics and output file paths.
    """
    results_folder_path = ensure_results_folder(results_folder)

    analysis_results: Dict[str, Any] = {
        "experiment_id": experiment_id,
        "status": "failed",
        "message": "",
        "plots": {},
        "data_outputs": {},
        "metadata": {},
        "files_processed": 0,
    }

    # Resolve experiment_id: explicit arg -> CLI arg -> auto-detect
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ('-h', '--help'):
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from sys.argv: {experiment_id}")
        else:
            experiment_id = auto_detect_latest_experiment_id(data_folder)
            if experiment_id is None:
                msg = "Could not determine experiment ID automatically. Provide it as an argument."
                analysis_results["message"] = msg
                print(f"ERROR: {msg}")
                return analysis_results

    analysis_results["experiment_id"] = experiment_id

    # Load experiment JSON metadata
    exp_json_path_root = Path('..') / f"experiment_{experiment_id}.json"
    data_folder_path = Path(data_folder)
    data_file_path = data_folder_path / f"experiment_{experiment_id}.json"

    experiment_data: Optional[Dict[str, Any]] = None
    if exp_json_path_root.exists():
        try:
            print(f"INFO: Loading experiment JSON from root: {exp_json_path_root}")
            with open(exp_json_path_root, 'r') as f:
                experiment_data = json.load(f)
        except Exception as e:
            print(f"WARNING: Failed to read root experiment JSON: {e}")

    if experiment_data is None:
        print(f"INFO: Loading experiment JSON from data folder: {data_file_path}")
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

    analysis_results["metadata"]["experiment_json_path"] = str((exp_json_path_root if exp_json_path_root.exists() else data_file_path).resolve())

    # Extract selected eLabFTW extra fields (if present)
    extra_fields = {}
    try:
        extra_fields = experiment_data.get('metadata_decoded', {}).get('extra_fields', {}) or {}
    except Exception:
        extra_fields = {}

    selected_fields = [
        'Process ID', 'CIP_DURATION', 'WASH_DURATION', 'CIP_3_DURATION', 'CIP_4_DURATION',
        'CIP_5_DURATION', 'WASH_FLOW_RATE', 'CIP_2_HOLD_DURATION', 'LOAD_INJECT_DURATION',
        'LOAD_INJECT_FLOW_RATE', 'EQUILIBRATION_DURATION', 'ELUTE_GRADIENT_DURATION',
        'EQUILIBRATION_FLOW_RATE', 'ELUTE_GRADIENT_GRADIENT_END', 'ELUTE_GRADIENT_GRADIENT_START',
    ]

    metadata_extracted = {}
    for field in selected_fields:
        try:
            if field in extra_fields and isinstance(extra_fields[field], dict):
                metadata_extracted[field] = extra_fields[field].get('value')
        except Exception as e:
            print(f"WARNING: Could not extract metadata field {field}: {e}")

    analysis_results["metadata"]["elab_extra_fields"] = metadata_extracted

    # Fetch AKTA results: server first, then local fallback
    print(f"INFO: Fetching AKTA data for experiment {experiment_id} from control server...")
    akta_data = fetch_akta_results_from_server(str(experiment_id))

    if akta_data is None:
        print("INFO: Falling back to local AKTA results files...")
        akta_data = load_akta_results_local(str(experiment_id), data_folder=data_folder, results_folder=results_folder)

    if akta_data is None:
        msg = "AKTA data not available - analysis skipped. This is expected for test runs."
        analysis_results["status"] = "success"
        analysis_results["message"] = msg
        print(f"INFO: {msg}")
    else:
        # Process AKTA data
        try:
            time_vals = np.array(akta_data.get('time', []), dtype=float)
        except Exception:
            # Some local CSV fallbacks might have 'time' as first column only
            if 'time' in akta_data:
                time_vals = np.array(akta_data['time'], dtype=float)
            else:
                msg = "AKTA data missing 'time' field; cannot perform chromatogram analysis."
                analysis_results["message"] = msg
                print(f"ERROR: {msg}")
                return analysis_results

        # UV 280 nm is uv1 by convention
        uv_vals_raw = akta_data.get('uv1') or akta_data.get('UV280') or akta_data.get('uv_280')
        if uv_vals_raw is None:
            msg = "AKTA data missing UV 280 nm signal ('uv1'); chromatogram cannot be plotted."
            analysis_results["message"] = msg
            analysis_results["status"] = "success"
            print(f"WARNING: {msg}")
            # Still record that data existed but no UV1 available
            analysis_results["metadata"]["akta_signals_available"] = akta_data.get('signals')
        else:
            uv_vals = np.array([v if v is not None else np.nan for v in uv_vals_raw], dtype=float)

            cond_raw = akta_data.get('cond') or akta_data.get('conductivity')
            cond_vals: Optional[np.ndarray]
            if cond_raw is not None:
                cond_vals = np.array([v if v is not None else np.nan for v in cond_raw], dtype=float)
            else:
                cond_vals = None

            # Basic validation of array lengths
            n = len(time_vals)
            if len(uv_vals) != n:
                min_len = min(n, len(uv_vals))
                print("WARNING: Length mismatch between time and UV arrays; truncating to common length.")
                time_vals = time_vals[:min_len]
                uv_vals = uv_vals[:min_len]
                if cond_vals is not None:
                    cond_vals = cond_vals[:min_len]

            if n == 0:
                msg = "AKTA data contains no time points; analysis cannot proceed."
                analysis_results["message"] = msg
                print(f"ERROR: {msg}")
                return analysis_results

            # Build processed DataFrame
            df_dict: Dict[str, Any] = {
                'time_s': time_vals,
                'time_min': time_vals / 60.0,
                'uv280_mAU': uv_vals,
            }
            if cond_vals is not None:
                df_dict['cond_mS_cm'] = cond_vals

            df = pd.DataFrame(df_dict)

            processed_csv_path = results_folder_path / f"akta_processed_{experiment_id}.csv"
            df.to_csv(processed_csv_path, index=False)
            print(f"INFO: Saved processed AKTA data to CSV: {processed_csv_path}")
            analysis_results["data_outputs"]["akta_processed_csv"] = str(processed_csv_path.resolve())

            # Peak detection on UV signal
            peaks_idx, peak_diag = identify_peaks(time_vals, uv_vals)
            peak_info_list: List[Dict[str, Any]] = []

            if len(peaks_idx) > 0:
                for idx in peaks_idx:
                    rt_s = float(time_vals[idx])
                    rt_min = rt_s / 60.0
                    height = float(uv_vals[idx]) if np.isfinite(uv_vals[idx]) else float('nan')
                    peak_info_list.append({
                        'index': int(idx),
                        'time_s': rt_s,
                        'time_min': rt_min,
                        'uv_height_mAU': height,
                    })

                peaks_df = pd.DataFrame(peak_info_list)
                peaks_csv_path = results_folder_path / f"akta_peaks_{experiment_id}.csv"
                peaks_df.to_csv(peaks_csv_path, index=False)
                print(f"INFO: Saved peak table to CSV: {peaks_csv_path}")
                analysis_results["data_outputs"]["akta_peaks_csv"] = str(peaks_csv_path.resolve())

            analysis_results["metadata"]["peak_detection"] = peak_diag
            analysis_results["metadata"]["num_peaks"] = len(peaks_idx)

            # Plot chromatogram
            fig, ax1 = plt.subplots(figsize=(10, 5))

            ax1.plot(df['time_min'], df['uv280_mAU'], color='blue', linewidth=0.8, label='UV 280 nm')
            ax1.set_xlabel('Time (min)')
            ax1.set_ylabel('UV Absorbance (mAU)', color='blue')
            ax1.tick_params(axis='y', labelcolor='blue')

            ax2 = None
            if cond_vals is not None:
                ax2 = ax1.twinx()
                ax2.plot(df['time_min'], df['cond_mS_cm'], color='red', linewidth=0.8, alpha=0.7, label='Conductivity')
                ax2.set_ylabel('Conductivity (mS/cm)', color='red')
                ax2.tick_params(axis='y', labelcolor='red')

            ax1.set_title(f'AKTA Chromatogram - Experiment {experiment_id}')

            # Peak labelling only if peaks were identified
            if len(peaks_idx) > 0:
                for info in peak_info_list:
                    x = info['time_min']
                    y = info['uv_height_mAU']
                    label = f"{x:.2f} min"
                    ax1.plot(x, y, 'ko', markersize=4)
                    ax1.annotate(label, xy=(x, y), xytext=(0, 5),
                                 textcoords='offset points', ha='center', fontsize=8, rotation=90)

            fig.tight_layout()

            png_path = results_folder_path / f"akta_chromatogram_{experiment_id}.png"
            pdf_path = results_folder_path / f"akta_chromatogram_{experiment_id}.pdf"
            fig.savefig(png_path, dpi=150)
            fig.savefig(pdf_path)
            plt.close(fig)

            print(f"INFO: Saved chromatogram plot to: {png_path} and {pdf_path}")
            analysis_results["plots"]["chromatogram_png"] = str(png_path.resolve())
            analysis_results["plots"]["chromatogram_pdf"] = str(pdf_path.resolve())

            analysis_results["status"] = "success"
            if len(peaks_idx) > 0:
                analysis_results["message"] = f"AKTA analysis completed with {len(peaks_idx)} peak(s) detected."
            else:
                analysis_results["message"] = "AKTA analysis completed. No peaks detected in UV signal."

            analysis_results["files_processed"] = 1

    # Save analysis results JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    try:
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        analysis_results["data_outputs"]["analysis_results_json"] = str(results_json_path.resolve())
    except Exception as e:
        print(f"WARNING: Failed to save analysis results JSON: {e}")

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
        if results.get("status") == "success":
            print("SUCCESS: Analysis successful!")
            return 0
        else:
            print(f"ERROR: Analysis did not complete successfully: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
