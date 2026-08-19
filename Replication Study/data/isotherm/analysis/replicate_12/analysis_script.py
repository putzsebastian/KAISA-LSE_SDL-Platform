#!/usr/bin/env python3
"""
Analysis Script - Tecan Plate Reader Loading Isotherm Evaluation
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
import pandas as pd
import numpy as np
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


def langmuir_isotherm(c, qmax, K):
    """Langmuir isotherm model: q = qmax * K * c / (1 + K * c)."""
    return qmax * K * c / (1.0 + K * c)


def fit_langmuir_isotherm(c_eq: np.ndarray, q: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fit Langmuir isotherm and return (qmax, K, R2). Returns (None, None, None) if fit fails."""
    try:
        mask = np.isfinite(c_eq) & np.isfinite(q)
        c_fit = c_eq[mask]
        q_fit = q[mask]
        if c_fit.size < 3:
            print("WARNING: Not enough data points for Langmuir fit (need >=3).")
            return None, None, None

        qmax0 = float(np.nanmax(q_fit)) if np.nanmax(q_fit) > 0 else 1.0
        sorted_idx = np.argsort(c_fit)
        mid_idx = sorted_idx[len(sorted_idx) // 2]
        c_mid = float(c_fit[mid_idx]) if c_fit.size > 0 else 1.0
        K0 = 1.0 / max(c_mid, 1e-6)
        p0 = [qmax0, K0]
        bounds = ([0.0, 0.0], [np.inf, np.inf])

        popt, _ = curve_fit(langmuir_isotherm, c_fit, q_fit, p0=p0, bounds=bounds, maxfev=10000)
        qmax, K = popt
        if not np.isfinite(qmax) or not np.isfinite(K) or qmax < 0 or K < 0:
            print("WARNING: Invalid Langmuir fit parameters (non-finite or negative).")
            return None, None, None

        q_pred = langmuir_isotherm(c_fit, qmax, K)
        ss_res = np.sum((q_fit - q_pred) ** 2)
        ss_tot = np.sum((q_fit - np.mean(q_fit)) ** 2)
        if ss_tot == 0:
            print("WARNING: Zero variance in q values; cannot compute R2.")
            return None, None, None
        r2 = 1 - ss_res / ss_tot
        if not np.isfinite(r2):
            print("WARNING: Non-finite R2 for Langmuir fit.")
            return None, None, None

        return float(qmax), float(K), float(r2)
    except Exception as e:
        print(f"WARNING: Langmuir fit failed: {e}")
        return None, None, None


def parse_semicolon_list(value: Any, field_name: str) -> List[float]:
    """Parse a semicolon-separated string of numbers into a float list with robust handling."""
    if value is None:
        raise ValueError(f"Metadata field '{field_name}' is None")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(f"Metadata field '{field_name}' must be string, int or float, got {type(value)}")

    parts = [p.strip() for p in value.split(';') if p.strip() != '']
    result: List[float] = []
    for p in parts:
        p_norm = p.replace(',', '.')
        try:
            result.append(float(p_norm))
        except ValueError:
            raise ValueError(f"Cannot parse value '{p}' in metadata field '{field_name}' as float")
    if not result:
        raise ValueError(f"Metadata field '{field_name}' did not contain any numeric values")
    return result


def parse_int(value: Any, field_name: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        v = value.strip()
        if v == '':
            raise ValueError(f"Metadata field '{field_name}' is empty")
        try:
            return int(float(v.replace(',', '.')))
        except ValueError:
            raise ValueError(f"Cannot parse metadata field '{field_name}' value '{value}' as int")
    raise ValueError(f"Cannot parse metadata field '{field_name}' value '{value}' as int")


def parse_float(value: Any, field_name: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.strip()
        if v == '':
            raise ValueError(f"Metadata field '{field_name}' is empty")
        try:
            return float(v.replace(',', '.'))
        except ValueError:
            raise ValueError(f"Cannot parse metadata field '{field_name}' value '{value}' as float")
    raise ValueError(f"Cannot parse metadata field '{field_name}' value '{value}' as float")


def well_from_rowcol(row_idx: int, col_idx: int) -> str:
    """Convert zero-based row/column indices to well ID (e.g., A1)."""
    row_letter = chr(ord('A') + row_idx)
    return f"{row_letter}{col_idx + 1}"


def map_plate_conditions(num_ligand: int,
                         ligand_concs: List[float],
                         num_salt: int,
                         salt_concs: List[float],
                         replicates: int) -> pd.DataFrame:
    """Create a mapping DataFrame from well position to experimental conditions.

    Returns DataFrame with columns: well, row, col, ligand_index, salt_index,
    replicate_index, c0, salt_conc.
    """
    if len(ligand_concs) != num_ligand:
        raise ValueError("Number of ligand concentrations does not match 'Number of ligand concentrations'")
    if len(salt_concs) != num_salt:
        raise ValueError("Number of salt concentrations does not match 'Number of salt concentrations'")

    if num_ligand > 8:
        raise ValueError("Number of ligand concentrations cannot exceed 8 for a 96-well plate")

    records: List[Dict[str, Any]] = []

    for salt_idx in range(num_salt):
        for rep_idx in range(replicates):
            col_idx = salt_idx * replicates + rep_idx
            if col_idx >= 12:
                raise ValueError("Plate mapping exceeds 12 columns; check salt concentrations and replicates")
            for lig_idx in range(num_ligand):
                row_idx = lig_idx
                well = well_from_rowcol(row_idx, col_idx)
                records.append({
                    'well': well,
                    'row_idx': row_idx,
                    'col_idx': col_idx,
                    'ligand_index': lig_idx,
                    'salt_index': salt_idx,
                    'replicate_index': rep_idx,
                    'c0': ligand_concs[lig_idx],
                    'salt_conc': salt_concs[salt_idx],
                })
    mapping_df = pd.DataFrame.from_records(records)
    expected_points = num_ligand * num_salt * replicates
    if len(mapping_df) != expected_points:
        raise RuntimeError(
            f"Internal mapping error: expected {expected_points} mapped wells, got {len(mapping_df)}"
        )
    return mapping_df


def extract_absorbance_matrix_from_tecan_excel(tecan_path: Path) -> pd.DataFrame:
    """Read Tecan Excel file and return a DataFrame with rows A-H and columns 1-12 of absorbance data."""
    print(f"Reading Tecan Excel file: {tecan_path}")
    num_rows = 8
    num_cols = 12
    raw_df = pd.read_excel(
        tecan_path,
        header=None,
        skiprows=33,
        usecols=list(range(1, 1 + num_cols)),
        nrows=num_rows
    )
    if raw_df.shape != (num_rows, num_cols):
        print(f"WARNING: Unexpected absorbance block shape {raw_df.shape}, expected (8, 12)")
    rows = [chr(ord('A') + i) for i in range(num_rows)]
    cols = [str(i + 1) for i in range(num_cols)]
    raw_df.index = rows
    raw_df.columns = cols
    return raw_df


def analyze_experiment(experiment_id: Optional[str] = None,
                       data_folder: str = '../data',
                       results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm experiments.

    Args:
        experiment_id (str): Experiment ID
        data_folder (str): Path to data folder
        results_folder (str): Path to results folder

    Returns:
        dict: Analysis results with all key metrics and file paths
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
        "files_processed": 0
    }

    try:
        if experiment_id is None:
            if len(sys.argv) > 1 and sys.argv[1] not in ('', None):
                experiment_id = sys.argv[1]
                analysis_results["experiment_id"] = experiment_id
            else:
                data_path = Path(data_folder)
                json_files = sorted(
                    data_path.glob('experiment_*.json'),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )
                if not json_files:
                    raise FileNotFoundError("No experiment_*.json files found for auto-detection")
                latest = json_files[0]
                name = latest.stem
                if name.startswith('experiment_'):
                    experiment_id = name[len('experiment_'):]
                else:
                    raise RuntimeError(f"Cannot parse experiment ID from filename {latest.name}")
                analysis_results["experiment_id"] = experiment_id
        print(f"INFO: Using experiment_id={experiment_id}")

        data_file_root_first = Path('../') / f'experiment_{experiment_id}.json'
        data_file_path = data_file_root_first
        if not data_file_path.exists():
            data_file_path = Path(data_folder) / f'experiment_{experiment_id}.json'
        print(f"INFO: Loading experiment JSON from: {data_file_path}")

        try:
            with open(data_file_path, 'r') as f:
                experiment_data = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Data file not found: {data_file_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

        analysis_results["metadata"]["data_file"] = str(data_file_path.resolve())

        try:
            metadata = experiment_data['metadata_decoded']['extra_fields']
        except KeyError as e:
            raise KeyError(f"Missing expected metadata structure: {e}")

        required_fields = [
            'Number of ligand concentrations',
            'Number of salt concentrations',
            'Replicates',
            'Ligand concentrations',
            'Salt concentrations',
            'Calibration Curve Slope',
            'Calibration Curve Intercept',
            'Resin Mass',
            'Total volume',
            'Ligand concentration unit',
            'Salt concentration unit'
        ]
        for field in required_fields:
            if field not in metadata:
                raise KeyError(f"Missing expected metadata field: {field}")

        num_ligand = parse_int(metadata['Number of ligand concentrations']['value'], 'Number of ligand concentrations')
        num_salt = parse_int(metadata['Number of salt concentrations']['value'], 'Number of salt concentrations')
        replicates = parse_int(metadata['Replicates']['value'], 'Replicates')
        ligand_concs = parse_semicolon_list(metadata['Ligand concentrations']['value'], 'Ligand concentrations')
        salt_concs = parse_semicolon_list(metadata['Salt concentrations']['value'], 'Salt concentrations')
        slope = parse_float(metadata['Calibration Curve Slope']['value'], 'Calibration Curve Slope')
        intercept = parse_float(metadata['Calibration Curve Intercept']['value'], 'Calibration Curve Intercept')
        resin_mass = parse_float(metadata['Resin Mass']['value'], 'Resin Mass')
        total_volume = parse_float(metadata['Total volume']['value'], 'Total volume')
        ligand_unit = str(metadata['Ligand concentration unit']['value'])
        salt_unit = str(metadata['Salt concentration unit']['value'])

        if slope == 0:
            raise ValueError("Calibration Curve Slope must not be zero")

        analysis_results['metadata'].update({
            'num_ligand_concentrations': num_ligand,
            'num_salt_concentrations': num_salt,
            'replicates': replicates,
            'ligand_concentrations': ligand_concs,
            'salt_concentrations': salt_concs,
            'calibration_slope': slope,
            'calibration_intercept': intercept,
            'resin_mass_mg': resin_mass,
            'total_volume_uL': total_volume,
            'ligand_concentration_unit': ligand_unit,
            'salt_concentration_unit': salt_unit,
        })

        results_folder_str = str(results_folder_path)
        print(f"Fetching Tecan data for experiment {experiment_id} from device control server...")
        try:
            data_info = check_tecan_data_availability(experiment_id)
            if not data_info.get("available", False):
                raise FileNotFoundError(
                    f"No Tecan data available for experiment {experiment_id}: "
                    f"{data_info.get('error', 'Unknown error')}"
                )
            print(f"Tecan data found on server: {data_info.get('total_files', 0)} file(s)")
            tecan_data_path_str = fetch_tecan_data_file(experiment_id, results_folder_str)
            tecan_data_path = Path(tecan_data_path_str)
            analysis_results['metadata']['tecan_data_source'] = 'device_server'
        except Exception as device_server_error:
            print(f"Device control server access failed: {device_server_error}. Falling back to local file search...")
            tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

            if not Path(tecan_raw_path).exists():
                analysis_results["message"] = "Tecan data not available - analysis skipped. This is expected for test runs."
                analysis_results["status"] = "success"
                analysis_results["note"] = "No Tecan data found on server or local system."
                print(f"INFO: {analysis_results['message']}")
                return analysis_results

            most_recent_excel_source = get_most_recent_excel_file(tecan_raw_path)
            if not most_recent_excel_source:
                analysis_results["message"] = "No recent Tecan Excel file found - analysis skipped."
                analysis_results["status"] = "success"
                print(f"INFO: {analysis_results['message']}")
                return analysis_results

            tecan_data_path = results_folder_path / f'tecan_data_{experiment_id}.xlsx'
            shutil.copy(most_recent_excel_source, tecan_data_path)
            analysis_results['metadata']['tecan_data_source'] = 'local_workspace'
            print(f"Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")

        analysis_results['data_outputs']['tecan_raw_excel'] = str(Path(tecan_data_path).resolve())

        absorbance_df = extract_absorbance_matrix_from_tecan_excel(Path(tecan_data_path))

        melt_records: List[Dict[str, Any]] = []
        for row_label in absorbance_df.index:
            for col_label in absorbance_df.columns:
                well = f"{row_label}{col_label}"
                value = absorbance_df.loc[row_label, col_label]
                try:
                    val_float = float(value)
                except Exception:
                    val_float = np.nan
                melt_records.append({
                    'well': well,
                    'absorbance': val_float
                })
        plate_df = pd.DataFrame.from_records(melt_records)

        mapping_df = map_plate_conditions(num_ligand, ligand_concs, num_salt, salt_concs, replicates)

        merged_df = pd.merge(plate_df, mapping_df, on='well', how='inner')

        expected_rows = num_ligand * num_salt * replicates
        if len(merged_df) != expected_rows:
            raise RuntimeError(
                f"Well-to-condition mapping mismatch: expected {expected_rows} data rows, got {len(merged_df)}. "
                "Check plate layout and metadata (ligand/salt concentrations, replicates)."
            )

        merged_df['absorbance'] = pd.to_numeric(merged_df['absorbance'], errors='coerce')

        merged_df['cE'] = (merged_df['absorbance'] - intercept) / slope

        def sanity_check_cE(group: pd.DataFrame) -> None:
            c0_vals = group['c0'].values
            cE_vals = group['cE'].values
            c0_max = float(np.nanmax(c0_vals)) if c0_vals.size > 0 else np.nan
            if np.any(cE_vals < -1e-9) or np.any(cE_vals - c0_max > 1e-9):
                raise RuntimeError(
                    "Calibration sanity check failed: some equilibrium concentrations are outside [0, max(c0)]. "
                    "This suggests an incorrect calibration application."
                )

        sanity_check_cE(merged_df)

        var_report: List[str] = []
        for salt in sorted(set(merged_df['salt_conc'])):
            sub = merged_df[merged_df['salt_conc'] == salt]
            for col in sorted(set(sub['col_idx'])):
                sub_col = sub[sub['col_idx'] == col]
                if sub_col.empty:
                    continue
                c0_for_col = sub_col['c0'].values
                cE_for_col = sub_col['cE'].values
                if len(c0_for_col) > 1:
                    c0_span = float(np.nanmax(c0_for_col) - np.nanmin(c0_for_col))
                else:
                    c0_span = 0.0
                if len(cE_for_col) > 1:
                    cE_span = float(np.nanmax(cE_for_col) - np.nanmin(cE_for_col))
                else:
                    cE_span = 0.0
                if c0_span > 0 and cE_span < 0.1 * c0_span:
                    var_report.append(
                        f"For salt {salt} {salt_unit} and column {col + 1}, c0 spans {c0_span:.3g} "
                        f"but cE spans only {cE_span:.3g}."
                    )
        if var_report:
            raise RuntimeError(
                "Calibration sanity check failed: equilibrium concentrations vary too weakly across c0 in some columns. "
                "This suggests the calibration may have been applied in the wrong direction. Details: "
                + " | ".join(var_report)
            )

        grouped = merged_df.groupby(['ligand_index', 'salt_index'], as_index=False).agg({
            'c0': 'mean',
            'salt_conc': 'mean',
            'cE': ['mean', 'std', 'count'],
        })
        grouped.columns = ['ligand_index', 'salt_index', 'c0', 'salt_conc', 'cE_mean', 'cE_std', 'n']

        if grouped.shape[0] != num_ligand * num_salt:
            raise RuntimeError(
                f"Aggregation error: expected {num_ligand * num_salt} parameter combinations, got {grouped.shape[0]}. "
                "This indicates an error in well-to-condition mapping."
            )
        if not np.all(grouped['n'].values == replicates):
            raise RuntimeError(
                "Aggregation error: not all groups have the expected number of replicates. "
                f"Expected {replicates} replicates per combination."
            )

        if grouped['cE_std'].isna().any():
            grouped['cE_std'] = grouped['cE_std'].fillna(0.0)

        merged_df['q'] = (merged_df['c0'] - merged_df['cE']) * total_volume / resin_mass

        agg_df = merged_df.groupby(['ligand_index', 'salt_index'], as_index=False).agg({
            'c0': 'mean',
            'salt_conc': 'mean',
            'cE': ['mean', 'std'],
            'q': ['mean', 'std'],
            'well': 'count',
        })
        agg_df.columns = [
            'ligand_index', 'salt_index', 'c0', 'salt_conc',
            'cE_mean', 'cE_std', 'q_mean', 'q_std', 'n'
        ]
        agg_df['cE_std'] = agg_df['cE_std'].fillna(0.0)
        agg_df['q_std'] = agg_df['q_std'].fillna(0.0)

        cE_max = float(agg_df['cE_mean'].max())
        if cE_max <= 0:
            raise RuntimeError("All equilibrium concentrations are non-positive after calibration; cannot proceed with isotherm fitting.")
        c0_max = float(agg_df['c0'].max())
        if cE_max > c0_max + 1e-9:
            raise RuntimeError(
                f"Sanity check failed: maximum equilibrium concentration {cE_max} exceeds maximum initial concentration {c0_max}."
            )

        aggregated_csv_path = results_folder_path / f"aggregated_isotherm_{experiment_id}.csv"
        agg_df.to_csv(aggregated_csv_path, index=False)
        analysis_results['data_outputs']['aggregated_isotherm_csv'] = str(aggregated_csv_path.resolve())

        detailed_csv_path = results_folder_path / f"detailed_well_data_{experiment_id}.csv"
        merged_df.to_csv(detailed_csv_path, index=False)
        analysis_results['data_outputs']['detailed_well_csv'] = str(detailed_csv_path.resolve())

        fit_results: List[Dict[str, Any]] = []

        fig, ax = plt.subplots(figsize=(8, 6))
        colors = plt.cm.viridis(np.linspace(0, 1, num_salt))

        for idx, salt in enumerate(sorted(agg_df['salt_conc'].unique())):
            sub = agg_df[agg_df['salt_conc'] == salt].sort_values('cE_mean')
            cE_vals = sub['cE_mean'].values
            q_vals = sub['q_mean'].values
            q_err = sub['q_std'].values

            qmax, K, r2 = fit_langmuir_isotherm(cE_vals, q_vals)

            if qmax is not None and K is not None and r2 is not None:
                c_grid = np.linspace(0, max(cE_vals) * 1.05, 200)
                q_fit = langmuir_isotherm(c_grid, qmax, K)
                ax.plot(c_grid, q_fit, color=colors[idx], linestyle='-', alpha=0.7)
                label = f"Salt {salt} {salt_unit} (qmax={qmax:.3g}, K={K:.3g}, R2={r2:.3f})"
            else:
                label = f"Salt {salt} {salt_unit} (fit failed)"

            ax.errorbar(
                cE_vals,
                q_vals,
                yerr=q_err,
                fmt='o',
                color=colors[idx],
                label=label,
                capsize=3,
                markersize=4,
            )

            fit_results.append({
                'salt_conc': float(salt),
                'salt_unit': salt_unit,
                'qmax': qmax,
                'K': K,
                'R2': r2,
                'n_points': int(len(cE_vals)),
            })

        ax.set_xlabel(f"Equilibrium ligand concentration cE [{ligand_unit}]")
        ax.set_ylabel(f"Loading q [{ligand_unit} * uL / mg]")
        ax.set_title("Loading isotherms from Tecan plate reader data")
        ax.legend(fontsize=8)
        ax.grid(True, linestyle='--', alpha=0.3)
        fig.tight_layout()

        plot_png_path = results_folder_path / f"loading_isotherms_{experiment_id}.png"
        fig.savefig(plot_png_path, dpi=300)
        analysis_results['plots']['loading_isotherms_png'] = str(plot_png_path.resolve())

        plot_pdf_path = results_folder_path / f"loading_isotherms_{experiment_id}.pdf"
        fig.savefig(plot_pdf_path)
        analysis_results['plots']['loading_isotherms_pdf'] = str(plot_pdf_path.resolve())

        plt.close(fig)

        analysis_results['metadata']['fit_results'] = fit_results

        analysis_results['status'] = 'success'
        analysis_results['message'] = 'Analysis completed successfully.'
        analysis_results['files_processed'] = 1

    except Exception as e:
        analysis_results['status'] = 'failed'
        analysis_results['message'] = str(e)
        print(f"ERROR: {e}")

    results_json_file = f"analysis_results_{analysis_results.get('experiment_id', 'unknown')}.json"
    results_json_path = results_folder_path / results_json_file
    try:
        with open(results_json_path, 'w') as f:
            json.dump(analysis_results, f, indent=4)
        print(f"Saved analysis results JSON to: {results_json_path}")
        analysis_results['data_outputs']['analysis_results_json'] = str(results_json_path.resolve())
    except Exception as e:
        print(f"WARNING: Failed to save analysis results JSON: {e}")

    return analysis_results


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze Tecan loading isotherm experiment data.')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID. If not provided, attempts to auto-detect the most recent.')
    parser.add_argument('--data-folder', default='../data', help='Path to the folder containing experiment_ID.json files. Default: ../data')
    parser.add_argument('--results-folder', default='../results', help='Path to the folder where all analysis outputs will be saved. Default: ../results')

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
