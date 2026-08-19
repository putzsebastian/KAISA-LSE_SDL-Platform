#!/usr/bin/env python3
"""
Analysis Script - Tecan Loading Isotherm Evaluation
Can be called externally with experiment ID as parameter.
"""

import os
import sys
import json
import argparse
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

# Device control server configuration for Tecan
DEVICE_CONTROL_SERVER = os.getenv('DEVICE_CONTROL_SERVER', 'http://localhost:8000')
DEVICE_API_KEY = os.getenv('DEVICE_API_KEY', 'your-secure-api-key-here')


def check_tecan_data_availability(experiment_id: str) -> dict:
    """Check if Tecan data is available for the given experiment ID."""
    url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}/list"
    headers = {'X-API-Key': DEVICE_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {"available": data.get('total_files', 0) > 0,
                    "total_files": data.get('total_files', 0),
                    "files": data.get('files', [])}
        elif response.status_code == 404:
            return {"available": False, "total_files": 0, "files": [], "error": "No data found"}
        else:
            return {"available": False, "total_files": 0, "files": [], "error": f"Server error: {response.status_code}"}
    except Exception as e:
        return {"available": False, "total_files": 0, "files": [], "error": str(e)}


def fetch_tecan_data_file(experiment_id: str, save_to_folder: str, file_index: int = 0) -> str:
    """Fetch a SINGLE Tecan Excel data file from device control server."""
    url = f"{DEVICE_CONTROL_SERVER}/api/tecan/data/{experiment_id}?file_index={file_index}"
    headers = {'X-API-Key': DEVICE_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        save_folder = Path(save_to_folder)
        save_folder.mkdir(parents=True, exist_ok=True)
        filename = f"tecan_data_{experiment_id}.xlsx"
        file_path = save_folder / filename
        with open(file_path, 'wb') as f:
            f.write(response.content)
        print(f"Successfully downloaded Tecan data to: {file_path}")
        return str(file_path)
    except requests.exceptions.ConnectionError:
        raise Exception(f"Cannot connect to device control server at {DEVICE_CONTROL_SERVER}")
    except requests.exceptions.Timeout:
        raise Exception("Timeout while fetching Tecan data from device control server")
    except Exception as e:
        raise Exception(f"Failed to fetch Tecan data for experiment {experiment_id}: {str(e)}")


def get_most_recent_folder(directory, n=0):
    """Finds the n-th most recent subfolder in a given directory."""
    folders = [f for f in os.listdir(directory) if os.path.isdir(os.path.join(directory, f))]
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
        excel_files.sort(key=lambda f: os.path.getctime(Path(excel_export_path) / f), reverse=True)
        return str(Path(excel_export_path) / excel_files[0])
    except Exception:
        return None


def parse_semicolon_floats(value: Any, field_name: str) -> List[float]:
    """Parse semicolon separated floats from metadata field.

    Handles comma as decimal separator and strips whitespace.
    """
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(f"Metadata field '{field_name}' must be string or number, got {type(value)}")
    parts = [p.strip() for p in value.split(';') if p.strip() != ""]
    floats: List[float] = []
    for p in parts:
        p_norm = p.replace(',', '.')
        try:
            floats.append(float(p_norm))
        except ValueError:
            raise ValueError(f"Cannot convert token '{p}' in field '{field_name}' to float")
    if not floats:
        raise ValueError(f"Metadata field '{field_name}' did not contain any numeric values")
    return floats


def parse_positive_int(value: Any, field_name: str) -> int:
    """Parse positive integer from metadata field."""
    if isinstance(value, int):
        iv = value
    else:
        if value is None:
            raise ValueError(f"Metadata field '{field_name}' is None")
        try:
            iv = int(str(value).strip())
        except Exception:
            raise ValueError(f"Metadata field '{field_name}' with value {value!r} cannot be converted to int")
    if iv <= 0:
        raise ValueError(f"Metadata field '{field_name}' must be positive, got {iv}")
    return iv


def langmuir_isotherm(c_e: np.ndarray, q_max: float, K: float) -> np.ndarray:
    """Langmuir isotherm: q = q_max * K * c_e / (1 + K * c_e)."""
    return q_max * K * c_e / (1.0 + K * c_e)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fit Langmuir isotherm to data.

    Returns (q_max, K, r2). If fit fails or parameters are not physically
    meaningful, returns (None, None, None).
    """
    # Remove NaNs
    mask = np.isfinite(c_e) & np.isfinite(q)
    c_e_fit = c_e[mask]
    q_fit = q[mask]
    if c_e_fit.size < 3:
        print("WARNING: Not enough points for Langmuir fit (need >=3)")
        return None, None, None

    # Initial guesses based on data
    max_q = float(np.nanmax(q_fit))
    median_c = float(np.nanmedian(c_e_fit)) if c_e_fit.size > 0 else 1.0
    if median_c <= 0:
        median_c = float(np.nanmax(c_e_fit)) if np.nanmax(c_e_fit) > 0 else 1.0
    # For Langmuir, q ~ qmax/2 at c_e = 1/K, so K ~ 1/median_c
    K0 = 1.0 / median_c if median_c > 0 else 1.0
    q0 = max_q if max_q > 0 else 1.0

    try:
        popt, pcov = curve_fit(
            langmuir_isotherm,
            c_e_fit,
            q_fit,
            p0=[q0, K0],
            bounds=([0.0, 0.0], [np.inf, np.inf]),
            maxfev=10000,
        )
        q_max, K = popt
        if not (np.isfinite(q_max) and np.isfinite(K)):
            print("WARNING: Non-finite Langmuir parameters")
            return None, None, None
        # Compute fitted values and R2
        q_pred = langmuir_isotherm(c_e_fit, q_max, K)
        ss_res = float(np.sum((q_fit - q_pred) ** 2))
        ss_tot = float(np.sum((q_fit - np.mean(q_fit)) ** 2))
        if ss_tot == 0:
            print("WARNING: Total sum of squares is zero; cannot compute R2")
            return None, None, None
        r2 = 1.0 - ss_res / ss_tot
        if r2 < 0.0:
            print("WARNING: Negative R2 for Langmuir fit; treating as failed fit")
            return None, None, None
        return float(q_max), float(K), float(r2)
    except Exception as e:
        print(f"WARNING: Langmuir fit failed: {e}")
        return None, None, None


def map_well_to_indices(well: str) -> Tuple[int, int]:
    """Map 96-well name (e.g. 'A1') to (row_index, col_index) zero-based.

    Row A-H -> 0-7, Column 1-12 -> 0-11.
    """
    if not isinstance(well, str) or len(well) < 2:
        raise ValueError(f"Invalid well identifier: {well!r}")
    row_char = well[0].upper()
    col_str = well[1:]
    if row_char < 'A' or row_char > 'H':
        raise ValueError(f"Row letter out of range in well '{well}'")
    try:
        col = int(col_str)
    except Exception:
        raise ValueError(f"Column not integer in well '{well}'")
    if col < 1 or col > 12:
        raise ValueError(f"Column out of range in well '{well}'")
    row_idx = ord(row_char) - ord('A')
    col_idx = col - 1
    return row_idx, col_idx


def build_plate_mapping(
    num_ligand_conc: int,
    num_salt_conc: int,
    num_replicates: int,
    ligand_concs: List[float],
    salt_concs: List[float],
) -> List[Dict[str, Any]]:
    """Build mapping from wells to (ligand_index, salt_index, replicate_index).

    Plate layout rules from user:
    - In a column of wells, ligand concentrations are ascending with row number.
    - Salt concentrations are ascending row-wise, but replicates are grouped together.
      Example for 4 salt concentrations and 3 replicates:
      columns 1-3: salt 0
      columns 4-6: salt 100
      columns 7-9: salt 200
      columns 10-12: salt 500
    """
    if len(ligand_concs) != num_ligand_conc:
        raise ValueError(
            f"Number of ligand concentrations ({len(ligand_concs)}) does not match "
            f"Number of ligand concentrations metadata ({num_ligand_conc})"
        )
    if len(salt_concs) != num_salt_conc:
        raise ValueError(
            f"Number of salt concentrations ({len(salt_concs)}) does not match "
            f"Number of salt concentrations metadata ({num_salt_conc})"
        )
    if num_salt_conc * num_replicates > 12:
        raise ValueError(
            "Product of number of salt concentrations and replicates exceeds 12 columns; "
            "layout does not fit on a 96 well plate."
        )

    mapping: List[Dict[str, Any]] = []
    rows = [chr(ord('A') + i) for i in range(8)]
    for salt_idx in range(num_salt_conc):
        for rep_idx in range(num_replicates):
            col_idx = salt_idx * num_replicates + rep_idx  # 0-based
            col_num = col_idx + 1  # 1-based
            for lig_idx in range(num_ligand_conc):
                row_char = rows[lig_idx]
                well_name = f"{row_char}{col_num}"
                mapping.append(
                    {
                        "well": well_name,
                        "row_index": lig_idx,
                        "col_index": col_idx,
                        "ligand_index": lig_idx,
                        "salt_index": salt_idx,
                        "replicate_index": rep_idx,
                        "c0": ligand_concs[lig_idx],
                        "salt_conc": salt_concs[salt_idx],
                    }
                )
    expected_groups = num_ligand_conc * num_salt_conc
    print(
        f"INFO: Plate mapping created with {len(mapping)} wells, "
        f"expecting {expected_groups} parameter combinations with {num_replicates} replicates each."
    )
    return mapping


def read_tecan_absorbance(tecan_data_path: Path, num_rows: int = 8, num_cols: int = 12) -> pd.DataFrame:
    """Read absorbance data from a Tecan Excel file.

    Absorbance block starts at row 34 (index 33) and column B (index 1).
    """
    print(f"INFO: Reading Tecan absorbance data from {tecan_data_path}")
    raw_df = pd.read_excel(
        tecan_data_path,
        header=None,
        skiprows=33,
        usecols=list(range(1, 1 + num_cols)),
        nrows=num_rows,
        engine="openpyxl",
    )
    # rows A-H correspond to plate rows, columns 1-12 to plate columns
    raw_df.index = [chr(ord('A') + i) for i in range(num_rows)]
    raw_df.columns = list(range(1, num_cols + 1))
    return raw_df


def extract_metadata_fields(experiment_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and validate required metadata fields from experiment data."""
    try:
        metadata = experiment_data['metadata_decoded']['extra_fields']
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure or field: {e}")

    def get_field(name: str) -> Any:
        if name not in metadata:
            raise KeyError(f"Missing expected metadata field: {name}")
        return metadata[name].get('value')

    fields: Dict[str, Any] = {}
    fields['Buffer'] = get_field('Buffer')
    fields['Salt Name'] = get_field('Salt Name')
    fields['Process ID'] = get_field('Process ID')
    fields['Pump Speed'] = get_field('Pump Speed')
    fields['Replicates_raw'] = get_field('Replicates')
    fields['Resin Mass_raw'] = get_field('Resin Mass')
    fields['Ligand Name'] = get_field('Ligand Name')
    fields['Total volume_raw'] = get_field('Total volume')
    fields['Venting time'] = get_field('Venting time')
    fields['Pump Duration'] = get_field('Pump Duration')
    fields['Incubation Time'] = get_field('Incubation Time')
    fields['Salt concentrations_raw'] = get_field('Salt concentrations')
    fields['Equilibration Cycles'] = get_field('Equilibration Cycles')
    fields['Equilibration Volume'] = get_field('Equilibration Volume')
    fields['Ligand concentrations_raw'] = get_field('Ligand concentrations')
    fields['Incubation Temperature'] = get_field('Incubation Temperature')
    fields['Measurement Wavelength'] = get_field('Measurement Wavelength')
    fields['Calibration Curve Slope_raw'] = get_field('Calibration Curve Slope')
    fields['Salt concentration unit'] = get_field('Salt concentration unit')
    fields['Shaker Speed Incubation'] = get_field('Shaker Speed Incubation')
    fields['Salt Stock Concentration'] = get_field('Salt Stock Concentration')
    fields['Ligand concentration unit'] = get_field('Ligand concentration unit')
    fields['Ligand Stock Concentration'] = get_field('Ligand Stock Concentration')
    fields['Calibration Curve Intercept_raw'] = get_field('Calibration Curve Intercept')
    fields['Equilibration Cycle Duration'] = get_field('Equilibration Cycle Duration')
    fields['Number of salt concentrations_raw'] = get_field('Number of salt concentrations')
    fields['Number of ligand concentrations_raw'] = get_field('Number of ligand concentrations')

    # Parse numeric fields
    fields['Replicates'] = parse_positive_int(fields['Replicates_raw'], 'Replicates')
    fields['Resin Mass_mg'] = float(str(fields['Resin Mass_raw']).replace(',', '.'))
    fields['Total volume_uL'] = float(str(fields['Total volume_raw']).replace(',', '.'))
    fields['Salt concentrations'] = parse_semicolon_floats(
        fields['Salt concentrations_raw'], 'Salt concentrations'
    )
    fields['Ligand concentrations'] = parse_semicolon_floats(
        fields['Ligand concentrations_raw'], 'Ligand concentrations'
    )
    fields['Num_salt_conc'] = parse_positive_int(
        fields['Number of salt concentrations_raw'], 'Number of salt concentrations'
    )
    fields['Num_ligand_conc'] = parse_positive_int(
        fields['Number of ligand concentrations_raw'], 'Number of ligand concentrations'
    )

    if fields['Num_salt_conc'] != len(fields['Salt concentrations']):
        raise ValueError(
            f"Metadata inconsistency: Number of salt concentrations ({fields['Num_salt_conc']}) "
            f"does not match length of Salt concentrations list ({len(fields['Salt concentrations'])})"
        )
    if fields['Num_ligand_conc'] != len(fields['Ligand concentrations']):
        raise ValueError(
            f"Metadata inconsistency: Number of ligand concentrations ({fields['Num_ligand_conc']}) "
            f"does not match length of Ligand concentrations list ({len(fields['Ligand concentrations'])})"
        )

    fields['Calibration_slope'] = float(
        str(fields['Calibration Curve Slope_raw']).replace(',', '.')
    )
    fields['Calibration_intercept'] = float(
        str(fields['Calibration Curve Intercept_raw']).replace(',', '.')
    )

    if fields['Calibration_slope'] == 0:
        raise ValueError("Calibration Curve Slope must not be zero")

    return fields


def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = '../data',
                       results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm data.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with all key metrics
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
        "warnings": [],
    }

    # Auto-detect experiment ID if not provided
    if experiment_id is None:
        if len(sys.argv) > 1 and sys.argv[1] not in ('', None):
            experiment_id = sys.argv[1]
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Using experiment ID from command line: {experiment_id}")
        else:
            # Auto-detect most recent experiment_*.json in data_folder
            data_path = Path(data_folder)
            json_files = list(data_path.glob('experiment_*.json'))
            if not json_files:
                raise FileNotFoundError(
                    f"No experiment_*.json files found in data folder '{data_folder}' for auto-detection."
                )
            json_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            latest = json_files[0]
            stem = latest.stem  # experiment_1234
            try:
                experiment_id = stem.split('_', 1)[1]
            except Exception:
                raise ValueError(f"Cannot parse experiment ID from filename '{latest.name}'")
            analysis_results["experiment_id"] = experiment_id
            print(f"INFO: Auto-detected most recent experiment ID: {experiment_id}")

    # Load experiment JSON: first try root ../experiment_ID.json, then data_folder
    data_folder_path = Path(data_folder)
    root_json_path = Path('..') / f'experiment_{experiment_id}.json'
    data_file_path = root_json_path
    if not data_file_path.exists():
        data_file_path = data_folder_path / f'experiment_{experiment_id}.json'

    try:
        with open(data_file_path, 'r') as f:
            experiment_data = json.load(f)
        print(f"INFO: Loaded experiment data from {data_file_path}")
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # Extract metadata fields
    meta_fields = extract_metadata_fields(experiment_data)
    analysis_results["metadata"]["Buffer"] = meta_fields.get("Buffer")
    analysis_results["metadata"]["Salt Name"] = meta_fields.get("Salt Name")
    analysis_results["metadata"]["Ligand Name"] = meta_fields.get("Ligand Name")
    analysis_results["metadata"]["Salt concentration unit"] = meta_fields.get("Salt concentration unit")
    analysis_results["metadata"]["Ligand concentration unit"] = meta_fields.get("Ligand concentration unit")
    analysis_results["metadata"]["Measurement Wavelength"] = meta_fields.get("Measurement Wavelength")
    analysis_results["metadata"]["Num_ligand_conc"] = meta_fields.get("Num_ligand_conc")
    analysis_results["metadata"]["Num_salt_conc"] = meta_fields.get("Num_salt_conc")
    analysis_results["metadata"]["Replicates"] = meta_fields.get("Replicates")

    num_ligand = meta_fields['Num_ligand_conc']
    num_salt = meta_fields['Num_salt_conc']
    num_reps = meta_fields['Replicates']

    # Fetch Tecan data (single file experiment)
    print(f"Fetching Tecan data for experiment {experiment_id} from device control server...")

    try:
        data_info = check_tecan_data_availability(experiment_id)
        if not data_info.get("available", False):
            raise FileNotFoundError(
                f"No Tecan data available for experiment {experiment_id}: "
                f"{data_info.get('error', 'Unknown error')}"
            )

        print(f"Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
        tecan_data_path_str = fetch_tecan_data_file(str(experiment_id), str(results_folder_path))
        tecan_data_path = Path(tecan_data_path_str)

    except Exception as device_server_error:
        print(f"Device control server access failed: {device_server_error}. Falling back to local file search...")
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            analysis_results["message"] = (
                "Tecan data not available - analysis skipped. "
                "No data found on server or local system."
            )
            analysis_results["status"] = "success"
            analysis_results["metadata"]["data_source"] = "none"
            print(f"INFO: {analysis_results['message']}")
            # Save results JSON
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, 'w') as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
        if not most_recent_excel_source:
            analysis_results["message"] = "No recent Tecan Excel file found - analysis skipped."
            analysis_results["status"] = "success"
            analysis_results["metadata"]["data_source"] = "none"
            print(f"INFO: {analysis_results['message']}")
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, 'w') as f:
                json.dump(analysis_results, f, indent=4)
            print(f"Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        tecan_data_path = results_folder_path / f'tecan_data_{experiment_id}.xlsx'
        shutil.copy(most_recent_excel_source, tecan_data_path)
        print(f"Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")

    analysis_results["metadata"]["data_source"] = "tecan_excel"
    analysis_results["data_outputs"]["raw_excel"] = str(Path(tecan_data_path).resolve())

    # Read absorbance data
    absorbance_df = read_tecan_absorbance(tecan_data_path, num_rows=8, num_cols=12)

    # Build plate mapping
    mapping = build_plate_mapping(
        num_ligand_conc=num_ligand,
        num_salt_conc=num_salt,
        num_replicates=num_reps,
        ligand_concs=meta_fields['Ligand concentrations'],
        salt_concs=meta_fields['Salt concentrations'],
    )

    # Prepare detailed dataset
    detailed_records: List[Dict[str, Any]] = []
    slope = meta_fields['Calibration_slope']
    intercept = meta_fields['Calibration_intercept']
    v_total = meta_fields['Total volume_uL']
    m_resin = meta_fields['Resin Mass_mg']

    max_c0 = max(meta_fields['Ligand concentrations']) if meta_fields['Ligand concentrations'] else 0.0

    for m in mapping:
        well = m['well']
        row_char = well[0]
        col_num = int(well[1:])
        try:
            absorbance = float(absorbance_df.loc[row_char, col_num])
        except KeyError:
            print(f"WARNING: Well {well} not present in data frame; skipping this well.")
            continue
        except Exception:
            print(f"WARNING: Invalid absorbance for well {well}; skipping this well.")
            continue

        # Calculate equilibrium concentration cE from calibration: cE = (Abs - intercept) / slope
        cE = (absorbance - intercept) / slope

        # Sanity checks for calibration
        if cE < 0 or cE > max_c0 * 1.05:  # allow small numerical tolerance
            msg = (
                f"Equilibrium concentration cE={cE:.4g} for well {well} outside expected range "
                f"[0, {max_c0}]; calibration may be wrong."
            )
            print("WARNING: " + msg)
            analysis_results["warnings"].append(msg)

        detailed_records.append(
            {
                "well": well,
                "row": row_char,
                "col": col_num,
                "ligand_index": m['ligand_index'],
                "salt_index": m['salt_index'],
                "replicate_index": m['replicate_index'],
                "c0": m['c0'],
                "salt_conc": m['salt_conc'],
                "absorbance": absorbance,
                "cE": cE,
            }
        )

    detailed_df = pd.DataFrame(detailed_records)
    if detailed_df.empty:
        raise RuntimeError("No valid well data extracted from Tecan file; cannot continue analysis.")

    # Check that cE varies along the column as c0 varies (rough heuristic)
    c0_var = detailed_df.groupby('col')['c0'].var().mean()
    cE_var = detailed_df.groupby('col')['cE'].var().mean()
    if cE_var < 0.1 * c0_var and c0_var > 0:
        msg = (
            "Equilibrium concentrations cE show much less variation across ligand "
            "concentrations than c0 within columns; calibration may be inverted or invalid."
        )
        print("ERROR: " + msg)
        raise RuntimeError(msg)

    # Aggregate replicates for each (ligand_index, salt_index)
    group_cols = ['ligand_index', 'salt_index', 'c0', 'salt_conc']
    grouped = detailed_df.groupby(group_cols)
    aggregated = grouped['cE'].agg(['mean', 'std', 'count']).reset_index()
    aggregated.rename(columns={'mean': 'cE_mean', 'std': 'cE_std', 'count': 'n_reps'}, inplace=True)

    expected_groups = num_ligand * num_salt
    if aggregated.shape[0] != expected_groups:
        msg = (
            f"Expected {expected_groups} parameter combinations (ligand x salt), "
            f"but found {aggregated.shape[0]}. Check plate layout mapping."
        )
        print("ERROR: " + msg)
        raise RuntimeError(msg)

    # Ensure each group has the expected number of replicates
    bad_groups = aggregated[aggregated['n_reps'] != num_reps]
    if not bad_groups.empty:
        msg = (
            "Some parameter combinations do not have the expected number of replicates. "
            "This indicates an issue with plate layout or missing wells."
        )
        print("WARNING: " + msg)
        analysis_results["warnings"].append(msg)

    # Calculate loading q for each parameter combination
    def compute_q(row):
        return (row['c0'] - row['cE_mean']) * v_total / m_resin

    aggregated['q'] = aggregated.apply(compute_q, axis=1)

    # Langmuir fits per salt concentration
    fit_results: List[Dict[str, Any]] = []

    for salt_value, subdf in aggregated.groupby('salt_conc'):
        cE_vals = subdf['cE_mean'].values.astype(float)
        q_vals = subdf['q'].values.astype(float)
        q_max, K, r2 = fit_langmuir(cE_vals, q_vals)
        fit_results.append(
            {
                "salt_conc": float(salt_value),
                "q_max": q_max,
                "K": K,
                "R2": r2,
                "n_points": int(len(subdf)),
            }
        )

    fit_results_df = pd.DataFrame(fit_results)

    # Plot isotherms with fits
    fig, ax = plt.subplots(figsize=(8, 6))
    colors = plt.cm.viridis(np.linspace(0, 1, len(aggregated['salt_conc'].unique())))

    for (salt_value, subdf), color in zip(aggregated.groupby('salt_conc'), colors):
        cE_vals = subdf['cE_mean'].values.astype(float)
        q_vals = subdf['q'].values.astype(float)
        salt_value_float = float(salt_value)
        label_base = f"Salt {salt_value_float} {meta_fields['Salt concentration unit']}"

        ax.scatter(cE_vals, q_vals, color=color, label=None, s=30)

        fit_row = fit_results_df[fit_results_df['salt_conc'] == salt_value_float]
        if not fit_row.empty and pd.notna(fit_row.iloc[0]['q_max']) and pd.notna(fit_row.iloc[0]['K']):
            q_max = fit_row.iloc[0]['q_max']
            K = fit_row.iloc[0]['K']
            r2 = fit_row.iloc[0]['R2']
            cE_fit_range = np.linspace(max(cE_vals.min(), 0), cE_vals.max() * 1.05, 200)
            q_fit_curve = langmuir_isotherm(cE_fit_range, q_max, K)
            legend_label = f"{label_base}: qmax={q_max:.3g}, K={K:.3g}, R2={r2:.3f}"
            ax.plot(cE_fit_range, q_fit_curve, color=color, label=legend_label)
        else:
            legend_label = f"{label_base}: fit failed"
            ax.plot([], [], color=color, label=legend_label)

    ax.set_xlabel(f"Equilibrium ligand concentration cE ({meta_fields['Ligand concentration unit']})")
    ax.set_ylabel(
        f"Loading q ({meta_fields['Ligand concentration unit']} * uL / mg resin)"
    )
    ax.set_title("Loading isotherms from Tecan plate reader")
    ax.legend(fontsize=8)
    ax.grid(True, linestyle='--', alpha=0.3)
    plt.tight_layout()

    plot_png = results_folder_path / f"isotherms_{experiment_id}.png"
    plot_pdf = results_folder_path / f"isotherms_{experiment_id}.pdf"
    fig.savefig(plot_png, dpi=300)
    fig.savefig(plot_pdf)
    plt.close(fig)

    analysis_results["plots"]["isotherms_png"] = str(plot_png.resolve())
    analysis_results["plots"]["isotherms_pdf"] = str(plot_pdf.resolve())

    # Save detailed and aggregated data
    detailed_csv = results_folder_path / f"detailed_loading_data_{experiment_id}.csv"
    aggregated_csv = results_folder_path / f"aggregated_loading_data_{experiment_id}.csv"
    fits_csv = results_folder_path / f"langmuir_fit_parameters_{experiment_id}.csv"

    detailed_df.to_csv(detailed_csv, index=False)
    aggregated_df.to_csv(aggregated_csv, index=False)
    fit_results_df.to_csv(fits_csv, index=False)

    analysis_results["data_outputs"]["detailed_csv"] = str(detailed_csv.resolve())
    analysis_results["data_outputs"]["aggregated_csv"] = str(aggregated_csv.resolve())
    analysis_results["data_outputs"]["fit_parameters_csv"] = str(fits_csv.resolve())

    analysis_results["status"] = "success"
    analysis_results["message"] = "Analysis completed successfully."
    analysis_results["files_processed"] = 1

    # Save results JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, 'w') as f:
        json.dump(analysis_results, f, indent=4)
    print(f"Saved analysis results JSON to: {results_json_path}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze Tecan loading isotherm experiment data.')
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
            print(f"ERROR: Analysis failed: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
