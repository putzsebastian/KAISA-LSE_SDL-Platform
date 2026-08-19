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

# AKTA control server configuration
AKTA_CONTROL_SERVER = os.getenv('AKTA_CONTROL_SERVER', 'http://localhost:5001')
AKTA_API_KEY = os.getenv('AKTA_API_KEY', 'akta-control-key')


def fetch_akta_results(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch AKTA results from the control server.

    Returns a dict with a "results" field or None if not available/accessible.
    """
    try:
        headers = {'X-API-Key': AKTA_API_KEY}
        url = f'{AKTA_CONTROL_SERVER}/api/akta/results/{experiment_id}'
        print(f"INFO: Requesting AKTA results from {url}")
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
    """Safely get a value from eLabFTW extra_fields metadata.

    Raises KeyError with a clear message if the field is missing.
    """
    if field_name not in metadata:
        raise KeyError(f"Missing expected metadata field: {field_name}")
    field = metadata[field_name]
    if not isinstance(field, dict) or 'value' not in field:
        raise KeyError(f"Metadata field '{field_name}' has unexpected structure (expected dict with 'value')")
    return field['value']


def _load_experiment_json(experiment_id: str, data_folder: str) -> Dict[str, Any]:
    """Load experiment JSON, trying root ../ first then data_folder.

    Raises FileNotFoundError or ValueError on error.
    """
    # First try root folder as per global rules
    root_candidate = Path('..') / f'experiment_{experiment_id}.json'
    data_candidate = Path(data_folder) / f'experiment_{experiment_id}.json'

    if root_candidate.exists():
        data_file_path = root_candidate
    else:
        data_file_path = data_candidate

    print(f"INFO: Loading experiment data from: {data_file_path}")

    try:
        with open(data_file_path, 'r') as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    return experiment_data


def _auto_detect_experiment_id(data_folder: str) -> str:
    """Auto-detect the most recent experiment ID from experiment_*.json in data_folder or root.

    Searches ../ first, then data_folder.
    """
    candidates: List[Path] = []
    for base in (Path('..'), Path(data_folder)):
        if base.exists() and base.is_dir():
            candidates.extend(base.glob('experiment_*.json'))

    if not candidates:
        raise FileNotFoundError("No experiment_*.json files found for auto-detection of experiment ID.")

    candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
    newest = candidates[0]
    name = newest.stem  # experiment_1234
    if '_' not in name:
        raise ValueError(f"Cannot parse experiment ID from filename: {newest.name}")
    experiment_id = name.split('_', 1)[1]
    print(f"INFO: Auto-detected experiment ID {experiment_id} from {newest}")
    return experiment_id


def _ensure_results_folder(results_folder: str) -> Path:
    path = Path(results_folder)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_akta_data(experiment_id: str, results_folder_path: Path) -> Optional[Dict[str, Any]]:
    """Load AKTA data.

    Priority:
    1) AKTA control server via fetch_akta_results
    2) Local JSON results file akta_results_{experiment_id}.json in results_folder
    3) Local CSV data.csv in working directory

    Returns a unified dict with keys: 'time', 'uv1', optional 'cond'.
    If nothing available, returns None.
    """
    # Try device server first
    akta_data = fetch_akta_results(experiment_id)
    if akta_data:
        print("INFO: Using AKTA data from control server.")
        return akta_data

    # Fallback to local JSON in results folder
    akta_json = results_folder_path / f'akta_results_{experiment_id}.json'
    akta_csv = Path('data.csv')

    if akta_json.exists():
        print(f"INFO: Loading AKTA JSON data from {akta_json}")
        try:
            with open(akta_json, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"WARNING: Failed to load local AKTA JSON {akta_json}: {e}")

    # Fallback to CSV
    if akta_csv.exists():
        print(f"INFO: Loading AKTA CSV data from {akta_csv}")
        try:
            df = pd.read_csv(akta_csv, na_values=['-'])
            data = {col: df[col].dropna().tolist() for col in df.columns}
            return data
        except Exception as e:
            print(f"WARNING: Failed to load local AKTA CSV {akta_csv}: {e}")

    print("INFO: No AKTA data available from server or local files.")
    return None


def _prepare_signals(akta_data: Dict[str, Any]) -> Tuple[np.ndarray, Optional[np.ndarray], Optional[np.ndarray]]:
    """Prepare time, uv1, cond arrays from AKTA data dict.

    Handles both JSON-style structure (signals + arrays) and flat dict.
    """
    # Handle JSON results structure
    if 'time' in akta_data and isinstance(akta_data.get('time'), list):
        time = np.array(akta_data['time'], dtype=float)
        uv = None
        cond = None
        if 'uv1' in akta_data:
            uv = np.array([v if v is not None else np.nan for v in akta_data['uv1']], dtype=float)
        if 'cond' in akta_data:
            cond = np.array([v if v is not None else np.nan for v in akta_data['cond']], dtype=float)
        return time, uv, cond

    # Handle CSV-style dict from data.csv
    if 'time' in akta_data:
        time = np.array(akta_data['time'], dtype=float)
        uv = None
        cond = None
        if 'uv1' in akta_data:
            uv = np.array(akta_data['uv1'], dtype=float)
        if 'cond' in akta_data:
            cond = np.array(akta_data['cond'], dtype=float)
        return time, uv, cond

    raise ValueError("AKTA data does not contain a 'time' field.")


def _detect_peaks(time: np.ndarray, uv: np.ndarray) -> List[Dict[str, Any]]:
    """Detect peaks in the UV signal.

    Uses scipy.signal.find_peaks with basic prominence threshold based on data.

    Returns list of dicts with keys: index, time, height
    """
    if uv is None or len(uv) == 0:
        return []

    # Replace NaNs for peak finding
    uv_clean = np.nan_to_num(uv, nan=np.nanmedian(uv) if not np.isnan(np.nanmedian(uv)) else 0.0)

    # Set a dynamic prominence threshold: fraction of signal range
    signal_range = np.nanmax(uv_clean) - np.nanmin(uv_clean)
    if signal_range <= 0:
        return []

    prominence = signal_range * 0.05  # 5 percent of range
    height = np.nanmin(uv_clean) + signal_range * 0.1  # at least 10 percent above min

    peaks, properties = find_peaks(uv_clean, prominence=prominence, height=height)

    peak_list: List[Dict[str, Any]] = []
    for idx in peaks:
        peak_list.append(
            {
                "index": int(idx),
                "time_s": float(time[idx]),
                "time_min": float(time[idx] / 60.0),
                "height": float(uv_clean[idx]),
            }
        )

    return peak_list


def _plot_chromatogram(
    experiment_id: str,
    time: np.ndarray,
    uv: Optional[np.ndarray],
    cond: Optional[np.ndarray],
    peaks: List[Dict[str, Any]],
    results_folder_path: Path,
) -> Dict[str, str]:
    """Generate chromatogram plot and save as PNG and PDF.

    Uses different colors for UV 280 nm and conductivity.
    Labels peaks with their retention time if peaks list is non-empty.

    Returns dict with keys 'png' and 'pdf' mapping to file paths.
    """
    if time is None or uv is None:
        raise ValueError("Time and UV data are required to plot chromatogram.")

    fig, ax1 = plt.subplots(figsize=(10, 5))

    # UV signal
    ax1.plot(time / 60.0, uv, color='blue', linewidth=0.8, label='UV 280 nm')
    ax1.set_xlabel('Time (min)')
    ax1.set_ylabel('UV Absorbance (mAU)', color='blue')
    ax1.tick_params(axis='y', labelcolor='blue')

    # Conductivity on secondary axis if available
    ax2 = None
    if cond is not None:
        ax2 = ax1.twinx()
        ax2.plot(time / 60.0, cond, color='orange', linewidth=0.8, alpha=0.7, label='Conductivity')
        ax2.set_ylabel('Conductivity (mS/cm)', color='orange')
        ax2.tick_params(axis='y', labelcolor='orange')

    # Peak annotations
    if peaks:
        for peak in peaks:
            t_min = peak['time_min']
            h = peak['height']
            ax1.axvline(x=t_min, color='gray', linestyle='--', linewidth=0.5, alpha=0.7)
            ax1.annotate(
                f"{t_min:.2f} min",
                xy=(t_min, h),
                xytext=(0, 5),
                textcoords='offset points',
                ha='center',
                fontsize=8,
                rotation=90,
            )

    ax1.set_title(f'AKTA Chromatogram - Experiment {experiment_id}')

    # Build legend manually from existing lines
    lines, labels = ax1.get_legend_handles_labels()
    if ax2 is not None:
        lines2, labels2 = ax2.get_legend_handles_labels()
        lines += lines2
        labels += labels2
    if lines:
        ax1.legend(lines, labels, loc='best')

    fig.tight_layout()

    png_path = results_folder_path / f'chromatogram_{experiment_id}.png'
    pdf_path = results_folder_path / f'chromatogram_{experiment_id}.pdf'
    fig.savefig(png_path, dpi=150)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"INFO: Saved chromatogram plot PNG to: {png_path}")
    print(f"INFO: Saved chromatogram plot PDF to: {pdf_path}")

    return {"png": str(png_path.resolve()), "pdf": str(pdf_path.resolve())}


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = '../data', results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for AKTA chromatography experiments.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries to auto-detect.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with all key metrics and paths to generated files.
    """
    results_folder_path = _ensure_results_folder(results_folder)

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1]:
            experiment_id = sys.argv[1]
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            print("INFO: No experiment ID provided, attempting auto-detection.")
            experiment_id = _auto_detect_experiment_id(data_folder)

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

    try:
        # Load experiment data JSON
        experiment_data = _load_experiment_json(experiment_id, data_folder)
        analysis_results["metadata"]["experiment_json_source"] = "root" if (Path('..') / f'experiment_{experiment_id}.json').exists() else "data_folder"

        # Access eLabFTW extra fields with error handling
        metadata = experiment_data.get('metadata_decoded', {}).get('extra_fields', {})
        if not isinstance(metadata, dict):
            raise KeyError("metadata_decoded.extra_fields is missing or not a dict")

        # Example access to a few fields to validate presence / structure
        for field_name in [
            'Process ID',
            'CIP_DURATION',
            'WASH_DURATION',
            'WASH_FLOW_RATE',
            'LOAD_INJECT_DURATION',
            'LOAD_INJECT_FLOW_RATE',
            'EQUILIBRATION_DURATION',
            'EQUILIBRATION_FLOW_RATE',
        ]:
            try:
                value = _safe_get_metadata_field(metadata, field_name)
                analysis_results["metadata"][field_name] = value
            except KeyError as e:
                # Log but do not fail the entire analysis for missing optional fields
                print(f"WARNING: {e}")

        # Load AKTA data
        akta_data = _load_akta_data(experiment_id, results_folder_path)
        if not akta_data:
            analysis_results["status"] = "success"
            analysis_results["message"] = "AKTA data not available - analysis limited to metadata extraction."
            print("INFO: " + analysis_results["message"])
        else:
            # Prepare time and signals
            time, uv, cond = _prepare_signals(akta_data)

            # Export processed time-series data to CSV
            proc_df = pd.DataFrame({"time_s": time})
            if uv is not None:
                proc_df["uv1_mAU"] = uv
            if cond is not None:
                proc_df["cond_mS_cm"] = cond

            csv_path = results_folder_path / f'akta_processed_{experiment_id}.csv'
            proc_df.to_csv(csv_path, index=False)
            print(f"INFO: Saved processed AKTA data CSV to: {csv_path}")
            analysis_results["data_outputs"]["processed_csv"] = str(csv_path.resolve())
            analysis_results["files_processed"] += 1

            # Detect peaks on UV signal
            peaks = []
            if uv is not None:
                peaks = _detect_peaks(time, uv)
                print(f"INFO: Detected {len(peaks)} peak(s) in UV signal.")
                analysis_results["peaks"] = peaks

                # Save peaks table if any
                if peaks:
                    peaks_df = pd.DataFrame(peaks)
                    peaks_csv_path = results_folder_path / f'akta_peaks_{experiment_id}.csv'
                    peaks_df.to_csv(peaks_csv_path, index=False)
                    print(f"INFO: Saved peaks CSV to: {peaks_csv_path}")
                    analysis_results["data_outputs"]["peaks_csv"] = str(peaks_csv_path.resolve())

            # Plot chromatogram with optional peak labels
            plots = _plot_chromatogram(experiment_id, time, uv, cond, peaks, results_folder_path)
            analysis_results["plots"] = plots

            analysis_results["status"] = "success"
            if peaks:
                analysis_results["message"] = f"AKTA analysis completed with {len(peaks)} detected peak(s)."
            else:
                analysis_results["message"] = "AKTA analysis completed. No significant peaks detected."

    except Exception as e:
        analysis_results["status"] = "failed"
        analysis_results["message"] = str(e)
        print(f"ERROR: {e}")

    # Save the analysis results as JSON
    if experiment_id is None:
        json_name = 'analysis_results_unknown_experiment.json'
    else:
        json_name = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / json_name
    try:
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        analysis_results["data_outputs"]["results_json"] = str(results_json_path.resolve())
    except Exception as e:
        print(f"ERROR: Failed to save analysis results JSON: {e}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze AKTA chromatography experiment data')
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
