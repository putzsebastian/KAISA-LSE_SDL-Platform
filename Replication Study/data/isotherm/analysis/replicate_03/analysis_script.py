#!/usr/bin/env python3
"""
Analysis Script - Tecan Loading Isotherm Evaluation
Can be called externally with experiment ID as parameter.

This script fetches Tecan Spark absorbance data for a 96-well plate loading
isotherm experiment, converts absorbance to equilibrium ligand concentration
using a linear calibration curve, aggregates replicates for each (ligand, salt)
condition, computes loading q, fits Langmuir isotherms per salt concentration,
and generates plots and CSV/JSON outputs.

It supports both CLI and import usage.
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
DEVICE_CONTROL_SERVER = os.getenv("DEVICE_CONTROL_SERVER", "http://localhost:8000")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "your-secure-api-key-here")


# ------------------------- Tecan helper functions -------------------------

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
        print(f"SUCCESS: Successfully downloaded Tecan data to: {file_path}")
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


# ------------------------- Core analysis helpers -------------------------

WELL_ROWS = list("ABCDEFGH")
WELL_COLS = list(range(1, 13))


def langmuir_isotherm(c, qmax, K):
    """Langmuir isotherm: q(c) = qmax * K * c / (1 + K * c)."""
    return qmax * K * c / (1.0 + K * c)


def r2_score_manual(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Compute coefficient of determination R^2 manually."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    if ss_tot == 0:
        return 1.0 if ss_res == 0 else 0.0
    return 1.0 - ss_res / ss_tot


def parse_semicolon_floats(value: Any, field_name: str) -> List[float]:
    """Parse a semicolon-separated string of numbers into a list of floats.

    Comma decimal separators are converted to point; whitespace is stripped.
    Raises ValueError on invalid tokens.
    """
    if value is None:
        raise ValueError(f"Missing value for field '{field_name}'")
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, str):
        raise ValueError(f"Field '{field_name}' must be a string or number, got {type(value)}")
    tokens = [t.strip() for t in value.split(';') if t.strip() != ""]
    floats: List[float] = []
    for tok in tokens:
        tok_norm = tok.replace(',', '.')
        try:
            floats.append(float(tok_norm))
        except ValueError:
            raise ValueError(f"Cannot convert token '{tok}' in field '{field_name}' to float")
    if not floats:
        raise ValueError(f"Field '{field_name}' contained no numeric tokens")
    return floats


def safe_float(value: Any, field_name: str) -> float:
    """Convert a single metadata value to float with clear error messages."""
    if value is None:
        raise ValueError(f"Missing value for field '{field_name}'")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"Field '{field_name}' must be a string or number, got {type(value)}")
    tok_norm = value.strip().replace(',', '.')
    try:
        return float(tok_norm)
    except ValueError:
        raise ValueError(f"Cannot convert value '{value}' of field '{field_name}' to float")


def read_tecan_absorbance_matrix(tecan_path: Path) -> pd.DataFrame:
    """Read a single Tecan Excel file and return an 8x12 DataFrame of absorbance.

    The Tecan export has no headers; absorbance data start at row 34 (0-based index 33)
    and column B (1-based index 2, i.e. pandas usecols starting at 1).

    Returns
    -------
    df : DataFrame
        Index: row letters A-H
        Columns: 1-12 (integers)
    """
    print(f"INFO: Reading Tecan absorbance data from {tecan_path}")
    # Read 8 rows (A-H) and 12 columns (1-12) of absorbance values
    raw_df = pd.read_excel(
        tecan_path,
        header=None,
        skiprows=33,  # skip first 33 rows so that row 34 is first
        usecols=list(range(0, 13)),  # column A (row label) + 12 data columns
        nrows=8,
        engine="openpyxl",
    )

    if raw_df.shape[1] < 13:
        raise ValueError(f"Unexpected Tecan file format: expected at least 13 columns, got {raw_df.shape[1]}")

    row_labels = raw_df.iloc[:, 0].astype(str).str.strip().tolist()
    expected_rows = WELL_ROWS
    if row_labels != expected_rows:
        raise ValueError(
            f"Unexpected row labels in Tecan file. Expected {expected_rows}, got {row_labels}"
        )

    data = raw_df.iloc[:, 1:13].copy()
    data.index = expected_rows
    data.columns = WELL_COLS
    print("INFO: Successfully read 8x12 absorbance matrix from Tecan file")
    return data


def build_well_condition_mapping(
    num_ligand_conc: int,
    num_salt_conc: int,
    replicates: int,
    ligand_concs: List[float],
    salt_concs: List[float],
) -> Dict[Tuple[str, int], Dict[str, Any]]:
    """Create a mapping from (row_letter, col_index) to condition info.

    Mapping rules from user description:
    - Within a column, ligand concentrations c0 ascend with row index
      (e.g. A..H). Only the first num_ligand_conc rows contain data.
    - Salt concentrations are assigned row-wise in blocks of 'replicates'
      columns, ascending with column index (across entire plate):
      e.g. for 4 salt concentrations and 3 replicates, columns 1-3: salt[0],
      4-6: salt[1], 7-9: salt[2], 10-12: salt[3].
    - All columns share the same set of c0 values.
    """
    if num_ligand_conc > len(ligand_concs):
        raise ValueError(
            f"Number of ligand concentrations ({num_ligand_conc}) exceeds length of LIGAND_CONCENTRATIONS list ({len(ligand_concs)})"
        )
    if num_salt_conc > len(salt_concs):
        raise ValueError(
            f"Number of salt concentrations ({num_salt_conc}) exceeds length of SALT_CONCENTRATIONS list ({len(salt_concs)})"
        )

    expected_cols = num_salt_conc * replicates
    if expected_cols > 12:
        raise ValueError(
            f"Plate layout expects {expected_cols} columns from num_salt_conc * replicates, but a 96-well plate only has 12."
        )

    mapping: Dict[Tuple[str, int], Dict[str, Any]] = {}

    for col in WELL_COLS:
        # Determine salt concentration index from column
        salt_block_index = (col - 1) // replicates
        if salt_block_index >= num_salt_conc:
            # This column is unused according to description
            continue
        salt_index = salt_block_index
        salt_conc = salt_concs[salt_index]

        for row_idx, row_letter in enumerate(WELL_ROWS):
            if row_idx >= num_ligand_conc:
                # Well contains no data to be used
                continue
            c0 = ligand_concs[row_idx]
            mapping[(row_letter, col)] = {
                "row": row_letter,
                "col": col,
                "c0": c0,
                "salt_conc": salt_conc,
                "ligand_index": row_idx,
                "salt_index": salt_index,
            }

    # Quick sanity check: expected number of used wells
    expected_wells = num_ligand_conc * num_salt_conc * replicates
    if len(mapping) != expected_wells:
        raise ValueError(
            f"Well mapping size mismatch: expected {expected_wells} wells, got {len(mapping)}. "
            f"Check num_ligand_conc={num_ligand_conc}, num_salt_conc={num_salt_conc}, replicates={replicates}."
        )

    return mapping


def compute_equilibrium_concentrations(
    absorbance_df: pd.DataFrame,
    well_map: Dict[Tuple[str, int], Dict[str, Any]],
    slope: float,
    intercept: float,
    max_c0: float,
) -> pd.DataFrame:
    """Compute equilibrium concentrations cE for all mapped wells.

    Sanity checks
    -------------
    - slope must be nonzero
    - cE must lie in [0, max_c0]
    - for each column, cE must vary with row index; otherwise calibration is
      likely applied incorrectly.
    """
    if slope == 0:
        raise ValueError("Calibration curve slope is zero; cannot invert calibration.")

    records: List[Dict[str, Any]] = []

    for (row_letter, col), info in well_map.items():
        try:
            absorbance = float(absorbance_df.loc[row_letter, col])
        except KeyError:
            print(f"WARNING: Missing absorbance for well {row_letter}{col}; skipping")
            continue
        except Exception as e:
            print(f"WARNING: Could not read absorbance for well {row_letter}{col}: {e}; skipping")
            continue

        c0 = info["c0"]
        # Invert calibration: Abs = slope * c + intercept -> c = (Abs - intercept) / slope
        cE = (absorbance - intercept) / slope

        if not np.isfinite(cE):
            print(f"WARNING: Non-finite cE for well {row_letter}{col}; skipping")
            continue

        if cE < -1e-9 or cE - max_c0 > 1e-9:
            raise ValueError(
                f"Sanity check failed for well {row_letter}{col}: cE={cE:.6g} outside [0, max_c0={max_c0:.6g}]. "
                "Calibration may be wrong (applied in wrong direction)."
            )

        records.append(
            {
                "row": row_letter,
                "col": col,
                "well": f"{row_letter}{col}",
                "absorbance": absorbance,
                "c0": c0,
                "cE": cE,
                "salt_conc": info["salt_conc"],
                "ligand_index": info["ligand_index"],
                "salt_index": info["salt_index"],
            }
        )

    df = pd.DataFrame.from_records(records)

    # Column-wise sanity check: cE should monotonically increase with ligand_index
    # (not strictly required but should generally follow trend).
    for col in sorted(df["col"].unique()):
        col_df = df[df["col"] == col].sort_values("ligand_index")
        if col_df.empty:
            continue
        cE_values = col_df["cE"].values
        c0_values = col_df["c0"].values
        # Check variation of cE across column: standard deviation should not be tiny
        if np.std(cE_values) < 0.01 * max(1.0, np.mean(cE_values)) and np.std(c0_values) > 0:
            raise ValueError(
                f"Sanity check failed for column {col}: cE varies too weakly across c0. "
                "Calibration may have been applied backwards."
            )

    return df


def aggregate_replicates(
    df: pd.DataFrame,
    num_ligand_conc: int,
    num_salt_conc: int,
    replicates: int,
) -> pd.DataFrame:
    """Aggregate replicates by (c0, salt_conc) and compute mean/std of cE.

    The number of resulting groups must equal num_ligand_conc * num_salt_conc.
    """
    if df.empty:
        raise ValueError("No equilibrium concentration data available for aggregation.")

    grouped = (
        df.groupby(["c0", "salt_conc"], as_index=False)
        .agg(
            cE_mean=("cE", "mean"),
            cE_std=("cE", "std"),
            n_reps=("cE", "count"),
        )
    )

    # Replace NaN std (single replicate) with 0
    grouped["cE_std"] = grouped["cE_std"].fillna(0.0)

    expected_groups = num_ligand_conc * num_salt_conc
    if len(grouped) != expected_groups:
        raise ValueError(
            f"Aggregation mismatch: expected {expected_groups} (ligand, salt) combinations, "
            f"but obtained {len(grouped)}. Check well-to-condition mapping and metadata."
        )

    # Check replicate counts
    missing_reps = grouped[grouped["n_reps"] != replicates]
    if not missing_reps.empty:
        print(
            "WARNING: Not all (c0, salt) combinations have the expected number of replicates. "
            f"Expected {replicates}, observed counts:\n{missing_reps[['c0','salt_conc','n_reps']]}"
        )

    return grouped


def compute_loading_q(
    grouped: pd.DataFrame,
    resin_mass_mg: float,
    total_volume_uL: float,
) -> pd.DataFrame:
    """Compute loading q for each aggregated (c0, salt) condition.

    q = (c0 - cE_mean) * v_total / m_resin

    v_total is in uL, m_resin in mg. Units are not converted.
    """
    if resin_mass_mg <= 0 or total_volume_uL <= 0:
        raise ValueError("Resin mass and total volume must be positive for q calculation.")

    grouped = grouped.copy()
    grouped["q"] = (grouped["c0"] - grouped["cE_mean"]) * total_volume_uL / resin_mass_mg
    return grouped


def fit_langmuir_per_salt(grouped_q: pd.DataFrame) -> pd.DataFrame:
    """Fit Langmuir isotherms per salt concentration.

    Fits q(cE) = qmax * K * cE / (1 + K * cE) for each unique salt_conc.

    Returns a DataFrame with columns:
    - salt_conc
    - qmax
    - K
    - r2
    - n_points
    - success (bool)
    - message
    """
    results: List[Dict[str, Any]] = []

    for salt in sorted(grouped_q["salt_conc"].unique()):
        sub = grouped_q[grouped_q["salt_conc"] == salt].copy()
        sub = sub.sort_values("cE_mean")
        cE = sub["cE_mean"].values.astype(float)
        q = sub["q"].values.astype(float)

        mask = np.isfinite(cE) & np.isfinite(q)
        cE = cE[mask]
        q = q[mask]

        entry: Dict[str, Any] = {
            "salt_conc": salt,
            "qmax": np.nan,
            "K": np.nan,
            "r2": np.nan,
            "n_points": int(mask.sum()),
            "success": False,
            "message": "",
        }

        if len(cE) < 3:
            entry["message"] = "Not enough points for reliable Langmuir fit (need at least 3)."
            results.append(entry)
            continue

        # Initial guesses based on data
        qmax0 = float(np.max(q)) if np.max(q) > 0 else 1.0
        # Rough guess for K: use mid-range cE
        mid_c = float(np.median(cE)) if np.median(cE) > 0 else 1.0
        K0 = 1.0 / max(mid_c, 1e-9)

        try:
            popt, pcov = curve_fit(
                langmuir_isotherm,
                cE,
                q,
                p0=[qmax0, K0],
                bounds=([0.0, 0.0], [np.inf, np.inf]),
                maxfev=10000,
            )
            qmax_fit, K_fit = popt
            q_pred = langmuir_isotherm(cE, qmax_fit, K_fit)
            r2 = r2_score_manual(q, q_pred)

            # Sanity checks on fit parameters
            if not np.isfinite(qmax_fit) or not np.isfinite(K_fit):
                raise RuntimeError("Non-finite fit parameters")

            if qmax_fit < 0 or K_fit < 0:
                raise RuntimeError("Negative fit parameters not physically meaningful")

            # Degeneracy check: ensure q varies meaningfully over range
            if np.std(q_pred) < 0.05 * max(1.0, np.mean(q_pred)) and np.std(q) > 0:
                raise RuntimeError(
                    "Fitted isotherm shows too little variation; data likely in low-signal regime with degenerate fit."
                )

            entry.update({
                "qmax": float(qmax_fit),
                "K": float(K_fit),
                "r2": float(r2),
                "success": True,
                "message": "Fit successful",
            })

        except Exception as e:
            entry["message"] = f"Fit failed: {e}"

        results.append(entry)

    return pd.DataFrame(results)


def plot_isotherms(
    grouped_q: pd.DataFrame,
    fit_results: pd.DataFrame,
    ligand_unit: str,
    salt_unit: str,
    experiment_id: str,
    results_folder: Path,
) -> Dict[str, str]:
    """Generate isotherm plot (PNG and PDF) and return paths.

    All isotherms (per salt_conc) are plotted together, with Langmuir fits.
    """
    fig, ax = plt.subplots(figsize=(8, 6))

    cmap = plt.get_cmap("tab10")

    unique_salts = sorted(grouped_q["salt_conc"].unique())
    color_map = {salt: cmap(i % 10) for i, salt in enumerate(unique_salts)}

    # Plot data points
    for salt in unique_salts:
        sub = grouped_q[grouped_q["salt_conc"] == salt]
        color = color_map[salt]
        label = f"Salt {salt:g} {salt_unit} (data)"
        ax.errorbar(
            sub["cE_mean"],
            sub["q"],
            yerr=sub["cE_std"],
            fmt="o",
            color=color,
            label=label,
            capsize=3,
        )

    # Plot fits
    for _, row in fit_results.iterrows():
        if not row.get("success", False):
            continue
        salt = row["salt_conc"]
        color = color_map.get(salt, "k")
        # Range of cE for this salt
        sub = grouped_q[grouped_q["salt_conc"] == salt]
        cE_min = float(sub["cE_mean"].min())
        cE_max = float(sub["cE_mean"].max())
        cE_range = np.linspace(cE_min, cE_max, 100)
        q_fit = langmuir_isotherm(cE_range, row["qmax"], row["K"])
        label = (
            f"Salt {salt:g} {salt_unit} fit: qmax={row['qmax']:.3g}, "
            f"K={row['K']:.3g}, R2={row['r2']:.3f}"
        )
        ax.plot(cE_range, q_fit, "-", color=color, label=label)

    ax.set_xlabel(f"Equilibrium ligand concentration cE [{ligand_unit}]")
    ax.set_ylabel(f"Loading q [{ligand_unit} * uL / mg]")
    ax.set_title("Loading isotherms from Tecan plate reader")
    ax.grid(True, which="both", linestyle=":", linewidth=0.5)
    ax.legend(fontsize=8)
    fig.tight_layout()

    png_path = results_folder / f"isotherms_{experiment_id}.png"
    pdf_path = results_folder / f"isotherms_{experiment_id}.pdf"
    fig.savefig(png_path, dpi=300)
    fig.savefig(pdf_path)
    plt.close(fig)

    print(f"SUCCESS: Saved isotherm plots to {png_path} and {pdf_path}")
    return {"png": str(png_path.resolve()), "pdf": str(pdf_path.resolve())}


# ------------------------- Main analysis function -------------------------


def analyze_experiment(experiment_id: Optional[str] = None, data_folder: str = '../data', results_folder: str = '../results') -> Dict[str, Any]:
    """Main analysis function for Tecan loading isotherm experiments.

    Args:
        experiment_id (str): Experiment ID for data linking. If None, tries to auto-detect.
        data_folder (str): Path to the folder containing experiment_ID.json files.
        results_folder (str): Path to the folder where all analysis outputs will be saved.

    Returns:
        dict: Analysis results with all key metrics and paths to generated files.
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
    data_folder_path = Path(data_folder)
    if experiment_id is None:
        # Look for experiment_*.json in data folder and pick most recent
        json_files = sorted(
            data_folder_path.glob('experiment_*.json'),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not json_files:
            raise FileNotFoundError("No experiment_*.json files found for auto-detection of experiment_id.")
        latest = json_files[0]
        name = latest.stem  # experiment_XXXX
        if '_' not in name:
            raise ValueError(f"Cannot parse experiment_id from filename {latest.name}")
        experiment_id = name.split('_', 1)[1]
        print(f"INFO: Auto-detected experiment_id={experiment_id} from {latest.name}")
        analysis_results["experiment_id"] = experiment_id

    # Try root-level JSON first, then fallback to data subfolder as per instructions
    exp_json_path_root = Path('..') / f"experiment_{experiment_id}.json"
    if exp_json_path_root.exists():
        data_file_path = exp_json_path_root
    else:
        data_file_path = data_folder_path / f"experiment_{experiment_id}.json"

    print(f"INFO: Loading experiment data from {data_file_path}")
    try:
        with open(data_file_path, 'r') as f:
            experiment_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {data_file_path}: {e}")

    # Extract metadata / extra_fields
    try:
        metadata_fields = experiment_data['metadata_decoded']['extra_fields']
    except KeyError as e:
        raise KeyError(f"Missing expected metadata structure: {e}")

    def get_field(name: str) -> Any:
        if name not in metadata_fields:
            raise KeyError(f"Missing expected metadata field: {name}")
        return metadata_fields[name].get('value')

    # Read numeric parameters from metadata
    num_ligand_conc = int(safe_float(get_field('Number of ligand concentrations'), 'Number of ligand concentrations'))
    num_salt_conc = int(safe_float(get_field('Number of salt concentrations'), 'Number of salt concentrations'))
    replicates = int(safe_float(get_field('Replicates'), 'Replicates'))
    ligand_concs = parse_semicolon_floats(get_field('Ligand concentrations'), 'Ligand concentrations')
    salt_concs = parse_semicolon_floats(get_field('Salt concentrations'), 'Salt concentrations')
    ligand_conc_unit = str(get_field('Ligand concentration unit'))
    salt_conc_unit = str(get_field('Salt concentration unit'))
    resin_mass_mg = safe_float(get_field('Resin Mass'), 'Resin Mass')
    total_volume_uL = safe_float(get_field('Total volume'), 'Total volume')
    calib_slope = safe_float(get_field('Calibration Curve Slope'), 'Calibration Curve Slope')
    calib_intercept = safe_float(get_field('Calibration Curve Intercept'), 'Calibration Curve Intercept')

    analysis_results["metadata"].update(
        {
            "num_ligand_concentrations": num_ligand_conc,
            "num_salt_concentrations": num_salt_conc,
            "replicates": replicates,
            "ligand_concentrations": ligand_concs,
            "salt_concentrations": salt_concs,
            "ligand_concentration_unit": ligand_conc_unit,
            "salt_concentration_unit": salt_conc_unit,
            "resin_mass_mg": resin_mass_mg,
            "total_volume_uL": total_volume_uL,
            "calibration_slope": calib_slope,
            "calibration_intercept": calib_intercept,
        }
    )

    max_c0 = max(ligand_concs[:num_ligand_conc])

    # Fetch Tecan data (single-file pattern)
    print(f"INFO: Fetching Tecan data for experiment {experiment_id} from device control server...")
    try:
        data_info = check_tecan_data_availability(str(experiment_id))
        if not data_info.get("available", False):
            raise FileNotFoundError(
                f"No Tecan data available for experiment {experiment_id}: "
                f"{data_info.get('error', 'Unknown error')}"
            )

        print(
            f"INFO: Tecan data found on server: {data_info.get('total_files', 0)} file(s). "
            "Using first file."
        )
        tecan_data_path_str = fetch_tecan_data_file(str(experiment_id), str(results_folder_path))
        tecan_data_path = Path(tecan_data_path_str)

    except Exception as device_server_error:
        print(
            f"WARNING: Device control server access failed: {device_server_error}. "
            "Falling back to local file search..."
        )
        tecan_raw_path = "C:/Users/Public/Documents/Tecan/SparkControl/Workspaces"

        if not Path(tecan_raw_path).exists():
            analysis_results["message"] = (
                "Tecan data not available - analysis skipped. "
                "No data on server and local Tecan workspace directory is missing."
            )
            analysis_results["status"] = "success"
            analysis_results["metadata"]["data_source"] = "none"
            print(f"INFO: {analysis_results['message']}")
            # Save results JSON before returning
            results_json_file = f"analysis_results_{experiment_id}.json"
            results_json_path = results_folder_path / results_json_file
            with open(results_json_path, 'w') as f:
                json.dump(analysis_results, f, indent=4)
            print(f"SUCCESS: Saved analysis results JSON to: {results_json_path}")
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
            print(f"SUCCESS: Saved analysis results JSON to: {results_json_path}")
            return analysis_results

        tecan_data_path = results_folder_path / f"tecan_data_{experiment_id}.xlsx"
        shutil.copy(most_recent_excel_source, tecan_data_path)
        print(f"INFO: Raw data copied from '{most_recent_excel_source}' to '{tecan_data_path}'")

    analysis_results["metadata"]["data_source"] = "device_or_local"
    analysis_results["data_outputs"]["tecan_raw"] = str(tecan_data_path.resolve())
    analysis_results["files_processed"] += 1

    # Read absorbance matrix
    absorbance_df = read_tecan_absorbance_matrix(tecan_data_path)

    # Build well-condition mapping
    well_map = build_well_condition_mapping(
        num_ligand_conc=num_ligand_conc,
        num_salt_conc=num_salt_conc,
        replicates=replicates,
        ligand_concs=ligand_concs,
        salt_concs=salt_concs,
    )

    # Compute equilibrium concentrations
    eq_df = compute_equilibrium_concentrations(
        absorbance_df=absorbance_df,
        well_map=well_map,
        slope=calib_slope,
        intercept=calib_intercept,
        max_c0=max_c0,
    )

    # Aggregate replicates per (c0, salt)
    grouped = aggregate_replicates(
        eq_df,
        num_ligand_conc=num_ligand_conc,
        num_salt_conc=num_salt_conc,
        replicates=replicates,
    )

    # Compute loading q
    grouped_q = compute_loading_q(grouped, resin_mass_mg=resin_mass_mg, total_volume_uL=total_volume_uL)

    # Fit Langmuir isotherms per salt concentration
    fit_results = fit_langmuir_per_salt(grouped_q)

    # Plot isotherms
    plot_paths = plot_isotherms(
        grouped_q=grouped_q,
        fit_results=fit_results,
        ligand_unit=ligand_conc_unit,
        salt_unit=salt_conc_unit,
        experiment_id=str(experiment_id),
        results_folder=results_folder_path,
    )

    # Save processed data
    absorbance_csv = results_folder_path / f"absorbance_matrix_{experiment_id}.csv"
    eq_csv = results_folder_path / f"equilibrium_concentrations_{experiment_id}.csv"
    grouped_csv = results_folder_path / f"aggregated_cE_{experiment_id}.csv"
    grouped_q_csv = results_folder_path / f"loading_q_{experiment_id}.csv"
    fit_results_csv = results_folder_path / f"langmuir_fit_results_{experiment_id}.csv"

    absorbance_df.to_csv(absorbance_csv)
    eq_df.to_csv(eq_csv, index=False)
    grouped.to_csv(grouped_csv, index=False)
    grouped_q.to_csv(grouped_q_csv, index=False)
    fit_results.to_csv(fit_results_csv, index=False)

    analysis_results["data_outputs"].update(
        {
            "absorbance_matrix_csv": str(absorbance_csv.resolve()),
            "equilibrium_concentrations_csv": str(eq_csv.resolve()),
            "aggregated_cE_csv": str(grouped_csv.resolve()),
            "loading_q_csv": str(grouped_q_csv.resolve()),
            "langmuir_fit_results_csv": str(fit_results_csv.resolve()),
        }
    )

    analysis_results["plots"]["isotherms"] = plot_paths

    analysis_results["status"] = "success"
    analysis_results["message"] = "Analysis completed successfully."

    # Save the analysis results as JSON
    results_json_file = f"analysis_results_{experiment_id}.json"
    results_json_path = results_folder_path / results_json_file
    with open(results_json_path, 'w') as f:
        json.dump(analysis_results, f, indent=4)
    print(f"SUCCESS: Saved analysis results JSON to: {results_json_path}")

    return analysis_results


# ------------------------- CLI interface -------------------------


def main() -> int:
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Analyze Tecan loading isotherm experiment data.')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID. If not provided, attempts to auto-detect the most recent experiment_*.json file.')
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
            print(f"ERROR: Analysis finished with status '{results.get('status')}', message: {results.get('message', 'Unknown error.')}")
            return 1
    except Exception as e:
        print(f"ERROR: An unhandled error occurred during analysis: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
