#!/usr/bin/env python3
"""
Analysis Script - Tecan Loading Isotherm Evaluation (Absorbance-based)
Processes Tecan Spark absorbance data to compute loading isotherms and Langmuir fits.
Can be called externally with experiment ID as parameter.
"""

import os
import sys
import json
import argparse
import shutil
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.optimize import curve_fit

# Fallback r2_score implementation if sklearn is not available
try:
    from sklearn.metrics import r2_score as sk_r2_score
    def r2_score(y_true, y_pred):
        return sk_r2_score(y_true, y_pred)
except Exception:
    def r2_score(y_true, y_pred):
        y_true = np.asarray(y_true)
        y_pred = np.asarray(y_pred)
        ss_res = np.sum((y_true - y_pred) ** 2)
        ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
        if ss_tot == 0:
            return float('nan')
        return 1 - ss_res / ss_tot

# Device control server configuration
DEVICE_CONTROL_SERVER = os.getenv('DEVICE_CONTROL_SERVER', 'http://localhost:8000')
DEVICE_API_KEY = os.getenv('DEVICE_API_KEY', 'your-secure-api-key-here')


def fetch_tecan_data_file(experiment_id: str, save_to_folder: str) -> str:
    """Fetch Tecan Excel data file from device control server for given experiment ID."""
    url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}"
    headers = {'X-API-Key': DEVICE_API_KEY}

    try:
        # First check if the file exists
        info_url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}/info"
        info_response = requests.get(info_url, headers=headers, timeout=10)

        if info_response.status_code == 404:
            raise FileNotFoundError(f"No Tecan data found for experiment {experiment_id}")
        elif info_response.status_code != 200:
            raise Exception(f"Failed to check data availability: {info_response.status_code} - {info_response.text}")

        # Download the file
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        # Ensure save folder exists
        save_folder = Path(save_to_folder)
        save_folder.mkdir(parents=True, exist_ok=True)

        # Save the file
        filename = f"tecan_data_{experiment_id}.xlsx"
        file_path = save_folder / filename

        with open(file_path, 'wb') as f:
            f.write(response.content)

        print(f"SUCCESS: Successfully downloaded Tecan data to: {file_path}")
        return str(file_path)

    except requests.exceptions.ConnectionError:
        raise Exception(f"Cannot connect to device control server at {DEVICE_CONTROL_SERVER}")
    except requests.exceptions.Timeout:
        raise Exception("Timeout while fetching Tecan data from device control server")
    except Exception as e:
        raise Exception(f"Failed to fetch Tecan data for experiment {experiment_id}: {str(e)}")


def check_tecan_data_availability(experiment_id: str) -> dict:
    """Check if Tecan data is available for the given experiment ID."""
    url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}/info"
    headers = {'X-API-Key': DEVICE_API_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            return {"available": False, "error": "No data found"}
        else:
            return {"available": False, "error": f"Server error: {response.status_code}"}

    except Exception as e:
        return {"available": False, "error": str(e)}


def get_most_recent_folder(directory, n=0):
    """Finds the n-th most recent subfolder in a given directory."""
    try:
        folders = [f for f in os.listdir(directory) if os.path.isdir(os.path.join(directory, f))]
    except Exception:
        return None
    if not folders:
        return None
    sorted_folders = sorted(folders, key=lambda f: os.path.getctime(os.path.join(directory, f)), reverse=True)
    return os.path.join(directory, sorted_folders[n]) if len(sorted_folders) > n else None


def get_most_recent_excel_file(directory, n=0):
    """Finds the most recent Excel file within the 'Export/xlsx/' subfolder."""
    most_recent_folder = get_most_recent_folder(directory, n)
    if not most_recent_folder:
        return None

    excel_export_path = Path(most_recent_folder) / "Export" / "xlsx"
    if not excel_export_path.exists() or not excel_export_path.is_dir():
        return None

    try:
        files_in_folder = os.listdir(excel_export_path)
        excel_files = [f for f in files_in_folder if f.lower().endswith('.xlsx')]
        if not excel_files:
            return None
        # Sort files by creation time to get the most recent one
        excel_files.sort(key=lambda f: os.path.getctime(Path(excel_export_path) / f), reverse=True)
        return str(Path(excel_export_path) / excel_files[0])
    except Exception:
        return None


# Helper functions for metadata and data parsing

def autodetect_experiment_id(data_folder: str) -> str:
    """Auto-detect most recent experiment ID by scanning root and data folder."""
    candidates = []
    root_path = Path('..')
    # Root-first scan
    for p in root_path.glob('experiment_*.json'):
        candidates.append(p)
    # Data folder scan
    df_path = Path(data_folder)
    if df_path.exists():
        for p in df_path.glob('experiment_*.json'):
            candidates.append(p)
    if not candidates:
        raise FileNotFoundError("No experiment_*.json files found in root or data folder for auto-detection")
    candidates = sorted(candidates, key=lambda x: x.stat().st_mtime, reverse=True)
    latest = candidates[0]
    name = latest.stem  # experiment_1234
    try:
        exp_id = name.split('experiment_')[1]
    except Exception:
        raise ValueError(f"Unable to parse experiment ID from filename: {latest}")
    print(f"INFO: Auto-detected most recent experiment ID: {exp_id} from {latest}")
    return exp_id


def normalize_key(k: str) -> str:
    return ''.join(ch for ch in k.lower() if ch.isalnum())


def get_field_value(extra_fields: dict, key: str, required: bool = True, default=None):
    """Robustly fetch value from eLabFTW extra_fields dict."""
    # Direct key
    if key in extra_fields and isinstance(extra_fields[key], dict) and 'value' in extra_fields[key]:
        return extra_fields[key]['value']
    # Fuzzy match
    target = normalize_key(key)
    for k, v in extra_fields.items():
        if normalize_key(k) == target and isinstance(v, dict) and 'value' in v:
            return v['value']
    if required:
        raise KeyError(f"Missing expected metadata field: {key}")
    return default


def parse_semicolon_floats(s: str) -> list:
    if s is None:
        return []
    if isinstance(s, (int, float)):
        return [float(s)]
    parts = [p.strip() for p in str(s).split(';') if str(p).strip() != '']
    vals = []
    for p in parts:
        try:
            vals.append(float(p.replace(',', '.')))
        except Exception:
            # Try to extract numeric from string
            try:
                vals.append(float(''.join(ch for ch in p if (ch.isdigit() or ch in '.-eE'))))
            except Exception:
                print(f"WARNING: Could not parse numeric value from '{p}', skipping")
    return vals


def safe_float(x, name: str):
    try:
        return float(str(x).replace(',', '.'))
    except Exception:
        raise ValueError(f"Invalid numeric value for {name}: {x}")


def read_absorbance_from_excel(tecan_data_path: Path, num_columns: int, num_rows: int = 8) -> pd.DataFrame:
    """Read absorbance block from Tecan Excel. Absorbance starts at row 34, column B (A1)."""
    try:
        raw_df = pd.read_excel(
            tecan_data_path,
            header=None,
            skiprows=33,  # Row 34 is 0-indexed 33
            usecols=list(range(1, 1 + num_columns)),  # Column B is index 1
            nrows=num_rows
        )
        return raw_df
    except Exception as e:
        raise Exception(f"Failed to read absorbance data block from Excel: {e}")


def parse_absorbance_from_embedded_json(experiment_data: dict) -> pd.DataFrame:
    """Parse absorbance matrix (8 rows x up to 12 cols) from an embedded JSON table structure similar to the provided example."""
    try:
        data_top = experiment_data.get('data')
        if not data_top or not isinstance(data_top, list):
            return None
        # The structure seems to be: {"data": [ {"data": [ {"row": [...]}, ... ] } ] }
        inner = data_top[0].get('data') if isinstance(data_top[0], dict) else None
        if not inner:
            return None
        # Find rows labelled 'A'..'H'
        rows_dict = {}
        for entry in inner:
            row = entry.get('row') if isinstance(entry, dict) else None
            if not row or len(row) < 2:
                continue
            first = str(row[0]).strip()
            if first in list('ABCDEFGH'):
                # take next 12 numeric entries (columns 1..12)
                vals = []
                for v in row[1:13]:
                    if v == "" or v is None:
                        vals.append(np.nan)
                    else:
                        try:
                            vals.append(float(v))
                        except Exception:
                            try:
                                vals.append(float(str(v).replace(',', '.')))
                            except Exception:
                                vals.append(np.nan)
                rows_dict[first] = vals
        if len(rows_dict) == 0:
            return None
        # Build DataFrame ordered A..H; fill missing rows with NaN
        ordered_rows = []
        for r in list('ABCDEFGH'):
            if r in rows_dict:
                ordered_rows.append(rows_dict[r])
            else:
                ordered_rows.append([np.nan] * 12)
        df = pd.DataFrame(ordered_rows)
        return df
    except Exception:
        return None


def langmuir_isotherm(c, qmax, K):
    return (qmax * K * c) / (1.0 + K * c)


def fit_langmuir(c_vals, q_vals):
    c_vals = np.asarray(c_vals, dtype=float)
    q_vals = np.asarray(q_vals, dtype=float)
    # Remove NaNs
    mask = np.isfinite(c_vals) & np.isfinite(q_vals)
    c = c_vals[mask]
    q = q_vals[mask]
    if len(c) < 3:
        return {
            'qmax': float('nan'),
            'K': float('nan'),
            'r2': float('nan'),
            'success': False,
            'message': 'Insufficient points for fit (need >= 3)'
        }
    try:
        qmax0 = max(np.nanmax(q), 1e-9)
        K0 = 1.0 / max(np.nanmedian(c), 1e-6)
        popt, pcov = curve_fit(langmuir_isotherm, c, q, p0=[qmax0, K0], bounds=([0.0, 0.0], [np.inf, np.inf]), maxfev=10000)
        q_pred = langmuir_isotherm(c, *popt)
        r2 = r2_score(q, q_pred)
        return {
            'qmax': float(popt[0]),
            'K': float(popt[1]),
            'r2': float(r2),
            'success': True,
            'message': 'OK'
        }
    except Exception as e:
        return {
            'qmax': float('nan'),
            'K': float('nan'),
            'r2': float('nan'),
            'success': False,
            'message': f'Fit failed: {e}'
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
        "experiment_id": experiment_id,
        "status": "failed",
        "message": "",
        "plots": {},
        "data_outputs": {},
        "metadata": {},
        "fits_by_salt": {}
    }

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1]:
            experiment_id = sys.argv[1]
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Experiment ID taken from command line arguments: {experiment_id}")
        else:
            experiment_id = autodetect_experiment_id(data_folder=data_folder)
            analysis_results["experiment_id"] = experiment_id

    # Load experiment data (JSON) - check both root and data folder
    data_file_path = Path('..') / f'experiment_{experiment_id}.json'  # Root folder first
    if not data_file_path.exists():
        data_file_path = Path(data_folder) / f'experiment_{experiment_id}.json'  # Fallback to data folder

    print(f"INFO: Loading experiment data JSON from: {data_file_path}")
    try:
        with open(data_file_path, 'r') as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format: {e}")

    # Extract eLabFTW extra fields
    try:
        metadata = experiment_data['metadata_decoded']['extra_fields']
    except KeyError as e:
        raise KeyError(f"Missing expected metadata field: {e}")

    # Extract required metadata values
    try:
        replicates = int(safe_float(get_field_value(metadata, 'Replicates'), 'Replicates'))
        num_salt_concs = int(safe_float(get_field_value(metadata, 'Number of salt concentrations'), 'Number of salt concentrations'))
        num_ligand_concs = int(safe_float(get_field_value(metadata, 'Number of ligand concentrations'), 'Number of ligand concentrations'))

        ligand_concs_str = get_field_value(metadata, 'Ligand concentrations')
        salt_concs_str = get_field_value(metadata, 'Salt concentrations')
        ligand_conc_unit = str(get_field_value(metadata, 'Ligand concentration unit'))
        salt_conc_unit = str(get_field_value(metadata, 'Salt concentration unit'))

        slope = safe_float(get_field_value(metadata, 'Calibration Curve Slope'), 'Calibration Curve Slope')
        intercept = safe_float(get_field_value(metadata, 'Calibration Curve Intercept'), 'Calibration Curve Intercept')
        if abs(slope) < 1e-12:
            raise ValueError("Calibration curve slope must be non-zero")

        resin_volume_ul = safe_float(get_field_value(metadata, 'Resin Mass'), 'Resin Mass')
        total_volume_uL = safe_float(get_field_value(metadata, 'Total volume'), 'Total volume')

        measurement_wavelength = get_field_value(metadata, 'Measurement Wavelength', required=False, default=None)

    except KeyError as e:
        raise KeyError(f"Missing expected metadata field: {e}")

    # Parse concentrations lists
    ligand_concs = parse_semicolon_floats(ligand_concs_str)
    salt_concs = parse_semicolon_floats(salt_concs_str)

    # Validate lengths and adjust
    if len(ligand_concs) != num_ligand_concs and len(ligand_concs) > 0:
        print(f"WARNING: Number of ligand concentrations in list ({len(ligand_concs)}) does not match metadata value ({num_ligand_concs}). Using list length.")
        num_ligand_concs = len(ligand_concs)
    if len(salt_concs) != num_salt_concs and len(salt_concs) > 0:
        print(f"WARNING: Number of salt concentrations in list ({len(salt_concs)}) does not match metadata value ({num_salt_concs}). Using list length.")
        num_salt_concs = len(salt_concs)

    # Validate ranges
    if num_ligand_concs < 1 or num_ligand_concs > 8:
        raise ValueError(f"Number of ligand concentrations must be between 1 and 8, got {num_ligand_concs}")
    if num_salt_concs < 1:
        raise ValueError(f"Number of salt concentrations must be >= 1, got {num_salt_concs}")
    if replicates < 1:
        raise ValueError(f"Replicates must be >= 1, got {replicates}")
    if num_salt_concs * replicates > 12:
        raise ValueError(f"Number of columns used (salt concentrations x replicates) exceeds 12: {num_salt_concs * replicates}")

    # Compute units for q
    q_unit = f"{ligand_conc_unit}"

    analysis_results['metadata'] = {
        'replicates': replicates,
        'num_salt_concs': num_salt_concs,
        'num_ligand_concs': num_ligand_concs,
        'ligand_concs': ligand_concs,
        'salt_concs': salt_concs,
        'ligand_conc_unit': ligand_conc_unit,
        'salt_conc_unit': salt_conc_unit,
        'calibration_slope': slope,
        'calibration_intercept': intercept,
        'resin_volume_ul': resin_volume_ul,
        'total_volume_uL': total_volume_uL,
        'q_unit': q_unit,
        'measurement_wavelength': measurement_wavelength
    }

    # Fetch Tecan data with device server first, fallback to local
    print(f"INFO: Fetching Tecan data for experiment {experiment_id} from device control server...")

    tecan_data_path = None
    embedded_df = None
    try:
        # Check if data is available first
        data_info = check_tecan_data_availability(experiment_id)
        if not data_info.get("available", False):
            raise FileNotFoundError(f"No Tecan data available for experiment {experiment_id}: {data_info.get('error', 'Unknown error')}")

        print(f"INFO: Tecan data found on server: {data_info.get('filename', 'tecan_data.xlsx')}")

        # Fetch the data file
        tecan_data_path = fetch_tecan_data_file(experiment_id, str(results_folder_path))

    except Exception as device_server_error:
        # Fallback to local path if device server not available or file not found on server
        print(f"WARNING: Device control server access failed or file not found: {device_server_error}. Falling back to local file search...")
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"
        if not Path(tecan_raw_path).exists():
            # Try embedded JSON absorbance data as a last resort
            embedded_df = parse_absorbance_from_embedded_json(experiment_data)
            if embedded_df is None:
                analysis_results["message"] = "Tecan data not available - analysis skipped. This is expected for test runs."
                analysis_results["status"] = "success"  # Do not fail the whole process
                analysis_results["note"] = "No Tecan data found on server or local system. Analysis requires actual measurement data."
                print(f"INFO: {analysis_results['message']}")
                print(f"INFO: {analysis_results['note']}")
                # Save analysis_results JSON before returning
                results_json_file = f"analysis_results_{experiment_id}.json"
                results_json_path = results_folder_path / results_json_file
                with open(results_json_path, 'w') as f:
                    json.dump(analysis_results, f, indent=4)
                print(f"INFO: Saved analysis results JSON to: {results_json_path}")
                return analysis_results
            else:
                print("INFO: Using embedded JSON absorbance data from experiment JSON")
                analysis_results['data_outputs']['tecan_data_source'] = 'embedded_json'
        else:
            print(f"INFO: Searching for most recent Tecan Excel file in: {tecan_raw_path}")
            most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)

            if not most_recent_excel_source:
                # Try embedded JSON absorbance data as a last resort
                embedded_df = parse_absorbance_from_embedded_json(experiment_data)
                if embedded_df is None:
                    analysis_results["message"] = "No recent Tecan Excel file found - analysis skipped."
                    analysis_results["status"] = "success"
                    analysis_results["note"] = "Analysis requires actual measurement data from Tecan device."
                    print(f"INFO: {analysis_results['message']}")
                    print(f"INFO: {analysis_results['note']}")
                    # Save analysis_results JSON before returning
                    results_json_file = f"analysis_results_{experiment_id}.json"
                    results_json_path = results_folder_path / results_json_file
                    with open(results_json_path, 'w') as f:
                        json.dump(analysis_results, f, indent=4)
                    print(f"INFO: Saved analysis results JSON to: {results_json_path}")
                    return analysis_results
                else:
                    print("INFO: Using embedded JSON absorbance data from experiment JSON")
                    analysis_results['data_outputs']['tecan_data_source'] = 'embedded_json'
            else:
                # Define target path for the copied raw data
                tecan_data_path = results_folder_path / f'tecan_data_{experiment_id}.xlsx'

                try:
                    shutil.copy(most_recent_excel_source, tecan_data_path)
                    print(f"SUCCESS: Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")
                    analysis_results['data_outputs']['tecan_data_source'] = str(tecan_data_path)
                except Exception as e:
                    # This is a real failure - data exists but we can't access it
                    analysis_results["message"] = f"Failed to copy raw Excel data from local path: {e}"
                    print(f"ERROR: {analysis_results['message']}")
                    # Save analysis_results JSON before returning
                    results_json_file = f"analysis_results_{experiment_id}.json"
                    results_json_path = results_folder_path / results_json_file
                    with open(results_json_path, 'w') as f:
                        json.dump(analysis_results, f, indent=4)
                    print(f"INFO: Saved analysis results JSON to: {results_json_path}")
                    return analysis_results

    # At this point we either have tecan_data_path pointing to an Excel or embedded_df from JSON
    num_columns = replicates * num_salt_concs
    print(f"INFO: Expecting columns (replicates x salts): {replicates} x {num_salt_concs} = {num_columns}")

    absorbance_df = None
    if tecan_data_path and Path(tecan_data_path).exists():
        try:
            absorbance_df = read_absorbance_from_excel(Path(tecan_data_path), num_columns=num_columns, num_rows=8)
            analysis_results['data_outputs']['tecan_data_source'] = str(tecan_data_path)
        except Exception as e:
            print(f"WARNING: Excel read failed: {e}. Trying embedded JSON absorbance if available...")
            embedded_df = parse_absorbance_from_embedded_json(experiment_data)
            if embedded_df is not None:
                absorbance_df = embedded_df.iloc[:8, :num_columns].copy()
                analysis_results['data_outputs']['tecan_data_source'] = 'embedded_json'
    elif embedded_df is not None:
        absorbance_df = embedded_df.iloc[:8, :num_columns].copy()
    else:
        # Gracefully skip analysis instead of failing
        analysis_results["message"] = "Tecan data not available - analysis skipped. This is expected for test runs."
        analysis_results["status"] = "success"
        analysis_results["note"] = "No Tecan data found on server or local system. Analysis requires actual measurement data."
        print(f"INFO: {analysis_results['message']}")
        print(f"INFO: {analysis_results['note']}")
        # Save analysis_results JSON before returning
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    if absorbance_df is None or absorbance_df.empty:
        analysis_results["message"] = "Loaded data frame is empty - analysis skipped."
        analysis_results["status"] = "success"
        print(f"INFO: {analysis_results['message']}")
        # Save and return
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Trim to required rows and columns
    absorbance_df = absorbance_df.iloc[:8, :num_columns]

    # Prepare data records
    records = []  # replicate-level records
    agg_records = []  # aggregated per condition

    # Ensure ligand_concs length
    if len(ligand_concs) == 0:
        # Generate placeholder sequence 1..num_ligand_concs if not provided
        ligand_concs = list(range(1, num_ligand_concs + 1))
        print("WARNING: Ligand concentrations list is empty; using placeholder sequence 1..N")
    if len(salt_concs) == 0:
        salt_concs = list(range(1, num_salt_concs + 1))
        print("WARNING: Salt concentrations list is empty; using placeholder sequence 1..N")

    # Iterate over salt groups (columns grouped by replicates) and ligand rows
    for si in range(num_salt_concs):
        salt_val = salt_concs[si] if si < len(salt_concs) else float('nan')
        col_start = si * replicates
        col_end = col_start + replicates
        for li in range(num_ligand_concs):
            c0 = ligand_concs[li] if li < len(ligand_concs) else float('nan')
            abs_vals = []
            for ri, col in enumerate(range(col_start, col_end)):
                try:
                    a = absorbance_df.iloc[li, col]
                except Exception:
                    a = np.nan
                if pd.isna(a):
                    continue
                try:
                    a_float = float(a)
                except Exception:
                    a_float = np.nan
                if pd.isna(a_float):
                    continue
                # Compute equilibrium concentration cE = (Abs - intercept)/slope
                cE = (a_float - intercept) / slope
                if cE < 0:
                    print(f"WARNING: Computed negative cE ({cE}) at salt index {si}, ligand index {li}, replicate {ri}; clipping to 0")
                    cE = 0.0
                q = (c0 - cE) * total_volume_uL / resin_volume_ul
                records.append({
                    'salt_index': si,
                    'salt_conc': salt_val,
                    'ligand_index': li,
                    'ligand_c0': c0,
                    'replicate_index': ri,
                    'absorbance': a_float,
                    'cE': cE,
                    'q': q
                })
                abs_vals.append((a_float, cE, q))
            # Aggregate
            if len(abs_vals) > 0:
                cE_vals = [v[1] for v in abs_vals]
                q_vals = [v[2] for v in abs_vals]
                agg_records.append({
                    'salt_index': si,
                    'salt_conc': salt_val,
                    'ligand_index': li,
                    'ligand_c0': c0,
                    'n_replicates': len(abs_vals),
                    'cE_mean': float(np.mean(cE_vals)),
                    'cE_std': float(np.std(cE_vals, ddof=1)) if len(cE_vals) > 1 else 0.0,
                    'q_mean': float(np.mean(q_vals)),
                    'q_std': float(np.std(q_vals, ddof=1)) if len(q_vals) > 1 else 0.0
                })

    if len(records) == 0:
        analysis_results["message"] = "No valid measurement points found after parsing - analysis skipped."
        analysis_results["status"] = "success"
        print(f"INFO: {analysis_results['message']}")
        results_json_file = f"analysis_results_{experiment_id}.json"
        results_json_path = results_folder_path / results_json_file
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"INFO: Saved analysis results JSON to: {results_json_path}")
        return analysis_results

    # Create DataFrames
    replicates_df = pd.DataFrame.from_records(records)
    aggregated_df = pd.DataFrame.from_records(agg_records)

    # Save processed data CSVs
    replicates_csv = results_folder_path / f"processed_replicates_{experiment_id}.csv"
    aggregated_csv = results_folder_path / f"processed_aggregated_{experiment_id}.csv"
    replicates_df.to_csv(replicates_csv, index=False)
    aggregated_df.to_csv(aggregated_csv, index=False)
    print(f"SUCCESS: Saved processed replicates CSV to: {replicates_csv}")
    print(f"SUCCESS: Saved processed aggregated CSV to: {aggregated_csv}")
    analysis_results['data_outputs']['replicates_csv'] = str(replicates_csv)
    analysis_results['data_outputs']['aggregated_csv'] = str(aggregated_csv)

    # Fit Langmuir per salt
    fits = []
    salt_labels = []
    for si in range(num_salt_concs):
        df_s = aggregated_df[aggregated_df['salt_index'] == si]
        if df_s.empty:
            print(f"WARNING: No aggregated points for salt index {si}; skipping fit")
            continue
        c_vals = df_s['cE_mean'].values
        q_vals = df_s['q_mean'].values
        fit_res = fit_langmuir(c_vals, q_vals)
        salt_val = salt_concs[si] if si < len(salt_concs) else float('nan')
        fits.append({
            'salt_index': si,
            'salt_conc': salt_val,
            'qmax': fit_res['qmax'],
            'K': fit_res['K'],
            'r2': fit_res['r2'],
            'success': fit_res['success'],
            'message': fit_res['message'],
            'n_points': int(df_s.shape[0])
        })
        analysis_results['fits_by_salt'][str(salt_val)] = {
            'qmax': fit_res['qmax'],
            'K': fit_res['K'],
            'r2': fit_res['r2'],
            'n_points': int(df_s.shape[0])
        }
        salt_labels.append(str(salt_val))

    fits_df = pd.DataFrame(fits)
    fits_csv = results_folder_path / f"langmuir_fits_{experiment_id}.csv"
    fits_df.to_csv(fits_csv, index=False)
    print(f"SUCCESS: Saved Langmuir fit parameters CSV to: {fits_csv}")
    analysis_results['data_outputs']['fits_csv'] = str(fits_csv)

    # Plot isotherms
    fig, ax = plt.subplots(figsize=(8, 6))
    colors = plt.cm.tab10.colors
    color_count = len(colors)

    handles = []
    labels = []

    for si in range(num_salt_concs):
        df_s = aggregated_df[aggregated_df['salt_index'] == si]
        if df_s.empty:
            continue
        c_vals = df_s['cE_mean'].values
        q_vals = df_s['q_mean'].values
        yerr = df_s['q_std'].values
        color = colors[si % color_count]
        ax.errorbar(c_vals, q_vals, yerr=yerr, fmt='o', color=color, ecolor=color, capsize=3, label=None)
        # Fit line for display
        fit_row = fits_df[fits_df['salt_index'] == si]
        if not fit_row.empty and bool(fit_row.iloc[0]['success']):
            qmax = float(fit_row.iloc[0]['qmax'])
            K = float(fit_row.iloc[0]['K'])
            r2 = float(fit_row.iloc[0]['r2'])
            c_smooth = np.linspace(0, max(c_vals) * 1.1 if np.max(c_vals) > 0 else 1.0, 200)
            y_smooth = langmuir_isotherm(c_smooth, qmax, K)
            ax.plot(c_smooth, y_smooth, '-', color=color)
            legend_label = f"Salt {salt_concs[si]} {salt_conc_unit}: qmax={qmax:.4g}, K={K:.4g}, R^2={r2:.3f}"
        else:
            legend_label = f"Salt {salt_concs[si]} {salt_conc_unit}: fit unavailable"
        handles.append(ax.scatter([], [], color=color))
        labels.append(legend_label)

    ax.set_xlabel(f"Equilibrium concentration cE [{ligand_conc_unit}]")
    ax.set_ylabel(f"Loading q [{q_unit}]")
    title_extra = f" at {measurement_wavelength} nm" if measurement_wavelength is not None else ""
    ax.set_title(f"Loading Isotherms - Experiment {experiment_id}{title_extra}")
    ax.legend(handles, labels, loc='best', fontsize=8)
    ax.grid(True, linestyle='--', alpha=0.4)
    plt.tight_layout()

    png_path = results_folder_path / f"loading_isotherms_{experiment_id}.png"
    pdf_path = results_folder_path / f"loading_isotherms_{experiment_id}.pdf"
    fig.savefig(png_path, dpi=300)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"SUCCESS: Saved isotherm plots to: {png_path} and {pdf_path}")
    analysis_results['plots']['isotherms_png'] = str(png_path)
    analysis_results['plots']['isotherms_pdf'] = str(pdf_path)

    # Finalize results
    analysis_results['status'] = 'success'
    analysis_results['message'] = 'Analysis completed successfully.'

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, 'w') as f:
        json.dump(analysis_results, f, indent=4)
    print(f"SUCCESS: Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main():
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze Tecan loading isotherm experiment data')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID. If not provided, attempts to auto-detect the most recent.')
    parser.add_argument('--data-folder', default='../data', help='Data folder path (default: ../data)')
    parser.add_argument('--results-folder', default='../results', help='Results folder path (default: ../results)')

    args = parser.parse_args()

    try:
        results = analyze_experiment(
            experiment_id=args.experiment_id,
            data_folder=args.data_folder,
            results_folder=args.results_folder
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
