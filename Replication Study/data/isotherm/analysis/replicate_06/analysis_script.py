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

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
import requests

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
    """Parse semicolon-separated string of numbers into list of floats."""
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(f"Metadata field '{field_name}' must be a string or number, got {type(value)}")
    parts = [p.strip() for p in value.replace(',', '.').split(';') if p.strip() != ""]
    floats: List[float] = []
    for p in parts:
        try:
            floats.append(float(p))
        except ValueError:
            raise ValueError(f"Cannot convert value '{p}' in metadata field '{field_name}' to float")
    return floats


def langmuir_isotherm(c_e: np.ndarray, q_max: float, K: float) -> np.ndarray:
    """Langmuir isotherm: q = q_max * K * c_e / (1 + K * c_e)."""
    return q_max * K * c_e / (1.0 + K * c_e)


def fit_langmuir(c_e: np.ndarray, q: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fit Langmuir isotherm to data.

    Returns (q_max, K, R2). If fit fails or parameters not sensible, returns (None, None, None).
    """
    mask = np.isfinite(c_e) & np.isfinite(q)
    c_e_fit = c_e[mask]
    q_fit = q[mask]
    if c_e_fit.size < 3:
        print("WARNING: Not enough points for Langmuir fit (need at least 3)")
        return None, None, None

    q_max_guess = float(np.nanmax(q_fit)) if np.nanmax(q_fit) > 0 else 1.0
    if np.nanmax(c_e_fit) > 0:
        K_guess = 1.0 / np.nanmax(c_e_fit)
    else:
        K_guess = 1.0

    def r2_score_manual(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        y_true = np.asarray(y_true, dtype=float)
        y_pred = np.asarray(y_pred, dtype=float)
        ss_res = np.nansum((y_true - y_pred) ** 2)
        ss_tot = np.nansum((y_true - np.nanmean(y_true)) ** 2)
        if ss_tot == 0:
            return float('nan')
        return 1.0 - ss_res / ss_tot

    try:
        popt, _ = curve_fit(
            langmuir_isotherm,
            c_e_fit,
            q_fit,
            p0=[q_max_guess, K_guess],
            bounds=([0.0, 0.0], [np.inf, np.inf]),
            maxfev=10000,
        )
        q_max, K = float(popt[0]), float(popt[1])
        q_pred = langmuir_isotherm(c_e_fit, q_max, K)
        r2 = r2_score_manual(q_fit, q_pred)
        if not np.isfinite(q_max) or not np.isfinite(K) or q_max <= 0 or K < 0:
            print("WARNING: Non-physical Langmuir parameters obtained, discarding fit")
            return None, None, None
        return q_max, K, float(r2)
    except Exception as e:
        print(f"WARNING: Langmuir fit failed: {e}")
        return None, None, None


def map_well_to_indices(row_idx: int, col_idx: int,
                        n_ligand: int, n_salt: int, n_repl: int) -> Tuple[int, int, int]:
    """Map plate indices (0-based row, 0-based column) to (ligand_index, salt_index, replicate_index).

    Rows: ligand concentrations ascending with row number (0..n_ligand-1)
    Columns: salt concentrations grouped by replicates, ascending group index with column number.
    """
    ligand_index = row_idx
    group_index = col_idx // n_repl
    repl_index = col_idx % n_repl
    if ligand_index >= n_ligand:
        raise IndexError("Row index exceeds number of ligand concentrations")
    if group_index >= n_salt:
        raise IndexError("Column mapping produced salt index out of range")
    if repl_index >= n_repl:
        raise IndexError("Replicate index out of range")
    return ligand_index, group_index, repl_index


def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = '../data',
                       results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm experiments.

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

    try:
        if experiment_id is None:
            if len(sys.argv) > 1 and sys.argv[1] not in ('', None):
                experiment_id = sys.argv[1]
                analysis_results["experiment_id"] = experiment_id
            else:
                data_path = Path(data_folder)
                if not data_path.exists():
                    raise FileNotFoundError(f"Data folder does not exist: {data_folder}")
                json_files = sorted(
                    data_path.glob('experiment_*.json'),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if not json_files:
                    raise FileNotFoundError("No experiment_*.json files found for auto-detection")
                latest = json_files[0]
                name = latest.stem
                if not name.startswith('experiment_'):
                    raise RuntimeError(f"Unexpected file name format for auto-detected JSON: {name}")
                experiment_id = name.split('experiment_', 1)[1]
                analysis_results["experiment_id"] = experiment_id
                print(f"INFO: Auto-detected experiment_id = {experiment_id}")

        if experiment_id is None or str(experiment_id).strip() == "":
            raise ValueError("experiment_id is required and could not be determined")

        data_folder_path = Path(data_folder)
        if not data_folder_path.exists():
            raise FileNotFoundError(f"Data folder not found: {data_folder_path}")

        exp_json_root = Path('..') / f'experiment_{experiment_id}.json'
        if exp_json_root.exists():
            data_file_path = exp_json_root
            print(f"INFO: Using experiment JSON from root folder: {data_file_path}")
        else:
            data_file_path = data_folder_path / f'experiment_{experiment_id}.json'
            print(f"INFO: Using experiment JSON from data folder: {data_file_path}")

        try:
            with open(data_file_path, 'r') as f:
                experiment_data = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Data file not found: {data_file_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

        try:
            metadata = experiment_data['metadata_decoded']['extra_fields']
        except KeyError as e:
            raise KeyError(f"Missing expected metadata field structure: {e}")

        def get_meta(field: str) -> Any:
            if field not in metadata or 'value' not in metadata[field]:
                raise KeyError(f"Missing expected metadata field: {field}")
            return metadata[field]['value']

        n_ligand = int(get_meta('Number of ligand concentrations'))
        n_salt = int(get_meta('Number of salt concentrations'))
        n_repl = int(get_meta('Replicates'))

        ligand_concs = parse_semicolon_floats(get_meta('Ligand concentrations'), 'Ligand concentrations')
        salt_concs = parse_semicolon_floats(get_meta('Salt concentrations'), 'Salt concentrations')

        if len(ligand_concs) != n_ligand:
            raise ValueError(
                f"Number of ligand concentrations ({n_ligand}) does not match list length ({len(ligand_concs)})"
            )
        if len(salt_concs) != n_salt:
            raise ValueError(
                f"Number of salt concentrations ({n_salt}) does not match list length ({len(salt_concs)})"
            )

        ligand_unit = str(get_meta('Ligand concentration unit'))
        salt_unit = str(get_meta('Salt concentration unit'))

        slope = float(str(get_meta('Calibration Curve Slope')).replace(',', '.'))
        intercept = float(str(get_meta('Calibration Curve Intercept')).replace(',', '.'))

        if slope == 0:
            raise ValueError("Calibration curve slope is zero; cannot invert calibration.")

        resin_mass = float(str(get_meta('Resin Mass')).replace(',', '.'))
        total_volume = float(str(get_meta('Total volume')).replace(',', '.'))

        analysis_results['metadata']['n_ligand'] = n_ligand
        analysis_results['metadata']['n_salt'] = n_salt
        analysis_results['metadata']['n_replicates'] = n_repl
        analysis_results['metadata']['ligand_concentrations'] = ligand_concs
        analysis_results['metadata']['salt_concentrations'] = salt_concs
        analysis_results['metadata']['ligand_unit'] = ligand_unit
        analysis_results['metadata']['salt_unit'] = salt_unit
        analysis_results['metadata']['calibration_slope'] = slope
        analysis_results['metadata']['calibration_intercept'] = intercept
        analysis_results['metadata']['resin_mass_mg'] = resin_mass
        analysis_results['metadata']['total_volume_uL'] = total_volume

        results_folder_path.mkdir(parents=True, exist_ok=True)

        print(f"Fetching Tecan data for experiment {experiment_id} from device control server...")
        try:
            data_info = check_tecan_data_availability(experiment_id)
            if not data_info.get("available", False):
                raise FileNotFoundError(
                    f"No Tecan data available for experiment {experiment_id}: "
                    f"{data_info.get('error', 'Unknown error')}"
                )

            print(f"Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
            tecan_data_path_str = fetch_tecan_data_file(experiment_id, str(results_folder_path))
            tecan_data_path = Path(tecan_data_path_str)
        except Exception as device_server_error:
            print(f"Device control server access failed: {device_server_error}. Falling back to local file search...")
            tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

            if not Path(tecan_raw_path).exists():
                msg = "Tecan data not available - analysis skipped. This is expected for test runs."
                analysis_results["message"] = msg
                analysis_results["status"] = "success"
                analysis_results["note"] = "No Tecan data found on server or local system."
                print(f"INFO: {msg}")
                results_json_file = f"analysis_results_{experiment_id}.json"
                results_json_path = results_folder_path / results_json_file
                with open(results_json_path, 'w') as f:
                    json.dump(analysis_results, f, indent=4)
                print(f"Saved analysis results JSON to: {results_json_path}")
                return analysis_results

            most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
            if not most_recent_excel_source:
                msg = "No recent Tecan Excel file found - analysis skipped."
                analysis_results["message"] = msg
                analysis_results["status"] = "success"
                print(f"INFO: {msg}")
                results_json_file = f"analysis_results_{experiment_id}.json"
                results_json_path = results_folder_path / results_json_file
                with open(results_json_path, 'w') as f:
                    json.dump(analysis_results, f, indent=4)
                print(f"Saved analysis results JSON to: {results_json_path}")
                return analysis_results

            tecan_data_path = results_folder_path / f'tecan_data_{experiment_id}.xlsx'
            shutil.copy(most_recent_excel_source, tecan_data_path)
            print(f"Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")

        analysis_results["data_outputs"]["tecan_raw_excel"] = str(tecan_data_path.resolve())

        num_columns = 12
        num_rows = 8

        print(f"INFO: Reading absorbance data from Excel file {tecan_data_path}")
        try:
            raw_df = pd.read_excel(
                tecan_data_path,
                header=None,
                skiprows=33,
                usecols=list(range(1, 1 + num_columns)),
                nrows=num_rows,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to read Tecan Excel data: {e}")

        if raw_df.shape[0] < n_ligand:
            raise ValueError(
                f"Excel data has only {raw_df.shape[0]} rows, but {n_ligand} ligand concentrations are expected."
            )
        if raw_df.shape[1] < n_salt * n_repl:
            raise ValueError(
                f"Excel data has only {raw_df.shape[1]} columns, "
                f"but {n_salt * n_repl} columns are required for salt/replicate mapping."
            )

        absorbance = raw_df.to_numpy(dtype=float)

        print("INFO: Converting absorbance to equilibrium concentrations using calibration curve")
        c_e_all = (absorbance - intercept) / slope

        c_e_max_allowed = max(ligand_concs)
        if np.any(c_e_all < -1e-9):
            msg = "Calibration produced negative equilibrium concentrations; check calibration parameters."
            print("WARNING: " + msg)
            analysis_results['warnings'].append(msg)

        if np.any(c_e_all - c_e_max_allowed > 1e-6):
            msg = (
                "Calibration produced equilibrium concentrations larger than maximum initial concentration; "
                "this suggests flipped calibration."
            )
            print("WARNING: " + msg)
            analysis_results['warnings'].append(msg)

        expected_rows = []
        for i in range(n_ligand):
            ligand_c0 = ligand_concs[i]
            row_vals = c_e_all[i, :n_salt * n_repl]
            spread = np.nanmax(row_vals) - np.nanmin(row_vals)
            if not np.isfinite(spread):
                continue
            expected_rows.append((ligand_c0, spread))
        if expected_rows:
            max_spread = max(s for _, s in expected_rows)
            if max_spread < c_e_max_allowed * 0.1:
                msg = (
                    "Equilibrium concentrations vary only weakly across ligand levels; "
                    "this suggests that the calibration may have been applied in the wrong direction."
                )
                print("WARNING: " + msg)
                analysis_results['warnings'].append(msg)

        records: List[Dict[str, Any]] = []
        for row_idx in range(n_ligand):
            for col_idx in range(n_salt * n_repl):
                try:
                    ligand_index, salt_index, repl_index = map_well_to_indices(
                        row_idx, col_idx, n_ligand, n_salt, n_repl
                    )
                except IndexError as e:
                    msg = f"Skipping well at row {row_idx}, col {col_idx}: {e}"
                    print("WARNING: " + msg)
                    analysis_results['warnings'].append(msg)
                    continue

                c0 = float(ligand_concs[ligand_index])
                c_e_val = float(c_e_all[row_idx, col_idx])
                salt_c = float(salt_concs[salt_index])

                if not np.isfinite(c_e_val):
                    continue

                if c_e_val < -1e-6 or c_e_val - c0 > 1e-6:
                    msg = (
                        f"Equilibrium concentration {c_e_val:.6g} outside [0, c0={c0:.6g}] for "
                        f"ligand index {ligand_index}, salt index {salt_index}, replicate {repl_index}."
                    )
                    print("WARNING: " + msg)
                    analysis_results['warnings'].append(msg)

                records.append(
                    {
                        'ligand_index': ligand_index,
                        'salt_index': salt_index,
                        'replicate': repl_index,
                        'c0': c0,
                        'c_e': c_e_val,
                        'salt_conc': salt_c,
                    }
                )

        if not records:
            raise RuntimeError("No valid data records were extracted from the plate.")

        df_records = pd.DataFrame.from_records(records)
        processed_csv_path = results_folder_path / f'processed_well_data_{experiment_id}.csv'
        df_records.to_csv(processed_csv_path, index=False)
        analysis_results['data_outputs']['processed_well_data_csv'] = str(processed_csv_path.resolve())
        analysis_results['files_processed'] += 1

        print("INFO: Aggregating replicates per (ligand, salt) combination")
        group_cols = ['ligand_index', 'salt_index']
        agg_df = df_records.groupby(group_cols).agg(
            c0_mean=('c0', 'mean'),
            c_e_mean=('c_e', 'mean'),
            c_e_std=('c_e', 'std'),
            salt_conc=('salt_conc', 'mean'),
            n_points=('c_e', 'count'),
        ).reset_index()

        if not np.all(agg_df['n_points'].values == n_repl):
            msg = (
                "Replicate count per condition does not match expected number. "
                "Check plate layout and mapping."
            )
            print("ERROR: " + msg)
            raise RuntimeError(msg)

        expected_groups = n_ligand * n_salt
        actual_groups = agg_df.shape[0]
        if actual_groups != expected_groups:
            msg = (
                f"Number of aggregated groups ({actual_groups}) does not match expected "
                f"{expected_groups}. Well-to-condition mapping is likely wrong."
            )
            print("ERROR: " + msg)
            raise RuntimeError(msg)

        agg_df['q'] = (agg_df['c0_mean'] - agg_df['c_e_mean']) * total_volume / resin_mass

        agg_df['ligand_conc'] = agg_df['ligand_index'].apply(lambda i: ligand_concs[int(i)])
        agg_df['salt_conc_exact'] = agg_df['salt_index'].apply(lambda j: salt_concs[int(j)])

        aggregated_csv_path = results_folder_path / f'aggregated_isotherm_data_{experiment_id}.csv'
        agg_df.to_csv(aggregated_csv_path, index=False)
        analysis_results['data_outputs']['aggregated_isotherm_csv'] = str(aggregated_csv_path.resolve())
        analysis_results['files_processed'] += 1

        print("INFO: Performing Langmuir fits per salt concentration")
        fit_results: List[Dict[str, Any]] = []

        fig, ax = plt.subplots(figsize=(8, 6))
        colors = plt.cm.viridis(np.linspace(0, 1, n_salt))

        for salt_idx in range(n_salt):
            sub = agg_df[agg_df['salt_index'] == salt_idx].copy()
            if sub.empty:
                msg = f"No data for salt index {salt_idx}; skipping fit."
                print("WARNING: " + msg)
                analysis_results['warnings'].append(msg)
                continue

            c_e_vals = sub['c_e_mean'].to_numpy(dtype=float)
            q_vals = sub['q'].to_numpy(dtype=float)

            q_max, K, r2 = fit_langmuir(c_e_vals, q_vals)

            color = colors[salt_idx]
            label = f"Salt {salt_concs[salt_idx]} {salt_unit}"

            ax.errorbar(
                c_e_vals,
                q_vals,
                yerr=sub['c_e_std'].to_numpy(dtype=float),
                fmt='o',
                color=color,
                label=None,
                capsize=3,
                markersize=4,
            )

            if q_max is not None and K is not None and r2 is not None:
                c_e_plot = np.linspace(0, max(c_e_vals) * 1.1 if max(c_e_vals) > 0 else 1.0, 200)
                q_plot = langmuir_isotherm(c_e_plot, q_max, K)
                fit_label = (
                    f"{label}: q_max={q_max:.3g}, K={K:.3g}, R2={r2:.3f}"
                )
                ax.plot(c_e_plot, q_plot, '-', color=color, label=fit_label)
                fit_results.append(
                    {
                        'salt_index': salt_idx,
                        'salt_conc': float(salt_concs[salt_idx]),
                        'q_max': q_max,
                        'K': K,
                        'R2': r2,
                    }
                )
            else:
                msg = f"Langmuir fit not available for salt concentration {salt_concs[salt_idx]} {salt_unit}."
                print("WARNING: " + msg)
                analysis_results['warnings'].append(msg)
                ax.plot([], [], ' ', label=f"{label}: fit failed")

        ax.set_xlabel(f"Equilibrium ligand concentration c_E [{ligand_unit}]")
        ax.set_ylabel(f"Loading q [{ligand_unit} * uL / mg]")
        ax.set_title("Loading isotherms at different salt concentrations")
        ax.legend(fontsize=8)
        ax.grid(True, linestyle='--', linewidth=0.5, alpha=0.7)

        plt.tight_layout()
        plot_path_png = results_folder_path / f'isotherms_{experiment_id}.png'
        plot_path_pdf = results_folder_path / f'isotherms_{experiment_id}.pdf'
        fig.savefig(plot_path_png, dpi=300)
        fig.savefig(plot_path_pdf)
        plt.close(fig)

        analysis_results['plots']['isotherms_png'] = str(plot_path_png.resolve())
        analysis_results['plots']['isotherms_pdf'] = str(plot_path_pdf.resolve())
        analysis_results['files_processed'] += 2

        fit_df = pd.DataFrame.from_records(fit_results)
        fit_csv_path = results_folder_path / f'langmuir_fit_parameters_{experiment_id}.csv'
        fit_df.to_csv(fit_csv_path, index=False)
        analysis_results['data_outputs']['langmuir_fit_parameters_csv'] = str(fit_csv_path.resolve())
        analysis_results['files_processed'] += 1

        analysis_results['status'] = 'success'
        analysis_results['message'] = 'Analysis completed successfully.'

    except Exception as e:
        analysis_results['status'] = 'failed'
        analysis_results['message'] = str(e)
        print(f"ERROR: Analysis failed: {e}")

    results_json_file = f"analysis_results_{analysis_results.get('experiment_id', 'unknown')}.json"
    results_json_path = results_folder_path / results_json_file
    try:
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
    except Exception as e:
        print(f"ERROR: Failed to save analysis results JSON: {e}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze Tecan loading isotherm experiment data.')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID. If not provided, attempts to auto-detect the most recent.')
    parser.add_argument('--data-folder', default='../data', help='Data folder path (default: ../data)')
    parser.add_argument('--results-folder', default='../results', help='Results folder path (default: ../results)')

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
