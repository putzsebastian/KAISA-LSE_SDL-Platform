#!/usr/bin/env python3
"""
CADET Database Commit Script (PostgreSQL)
Generated for Process ID: 62
Simulation: 3 Comp - Two Step Elution - MPM
Result Type: chromatogram_single

This script commits simulation results to the SDL experiment database
(PostgreSQL experiment_executions table).
"""

import os
import json
import sys
from pathlib import Path
from datetime import datetime

import pg8000.dbapi

# Configuration
PROCESS_ID = 62
PROCESS_NAME = "3 Comp - Two Step Elution - MPM"
RESULT_TYPE = "chromatogram_single"
CONFIGURED_METRICS = {"retention_time": {"enabled": True}, "peak_concentration": {"enabled": True}, "peak_width_half": {"enabled": True}, "recovery": {"enabled": True}}

# Database connection config (from environment)
DB_CONFIG = {
    "host": os.environ.get("EXPERIMENT_DB_HOST", "experiment-db"),
    "port": int(os.environ.get("EXPERIMENT_DB_INTERNAL_PORT", "5432")),
    "database": os.environ.get("EXPERIMENT_DB_NAME", "sdl_experiments"),
    "user": os.environ.get("EXPERIMENT_DB_USER", "sdl_admin"),
    "password": os.environ.get("EXPERIMENT_DB_PASSWORD", "sdl_secure_2025"),
}


def get_db_connection():
    """Create database connection using pg8000."""
    return pg8000.dbapi.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        database=DB_CONFIG["database"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        timeout=30
    )


def load_simulation_results(experiment_folder):
    """Load simulation results from experiment folder."""
    results_file = Path(experiment_folder) / 'results' / 'simulation_results.json'

    if not results_file.exists():
        # Try alternative patterns
        for f in Path(experiment_folder).rglob('*results*.json'):
            results_file = f
            break

    if results_file.exists():
        with open(results_file, 'r') as f:
            return json.load(f)

    return None


def load_experiment_metadata(experiment_folder):
    """Load experiment metadata."""
    for f in Path(experiment_folder).glob('experiment_*.json'):
        with open(f, 'r') as file:
            return json.load(file)
    return None


def extract_metrics(simulation_results):
    """Extract configured metrics from results."""
    metrics = {}

    if not simulation_results:
        return metrics

    result_data = simulation_results.get('results', {})

    for metric_name in CONFIGURED_METRICS:
        metric_key = metric_name.lower().replace(' ', '_')

        if metric_key in result_data:
            metrics[metric_name] = result_data[metric_key]
        elif metric_name in result_data:
            metrics[metric_name] = result_data[metric_name]
        elif metric_key in simulation_results:
            metrics[metric_name] = simulation_results[metric_key]
        else:
            metrics[metric_name] = None

    return metrics


def build_results_jsonb(metrics, experiment_folder):
    """Build results JSONB for experiment_executions table."""
    results = {
        "analysis_status": "completed",
        "result_type": RESULT_TYPE,
        "simulation_name": PROCESS_NAME,
        "metrics": metrics,
        "data_files": {},
        "plots": {},
    }

    # List result files
    exp_path = Path(experiment_folder)

    # Find data files (JSON, CSV)
    for pattern in ['results/*.json', 'results/*.csv', '*.json']:
        for f in exp_path.glob(pattern):
            if f.is_file() and f.name != 'activity_log.json':
                results["data_files"][f.stem] = f.name

    # Find plot files
    for f in exp_path.glob('*.png'):
        if f.is_file():
            results["plots"][f.stem] = f.name
    for f in exp_path.glob('results/*.png'):
        if f.is_file():
            results["plots"][f.stem] = f.name

    return results


def build_metadata_jsonb(experiment_folder):
    """Build metadata JSONB from extracted parameters."""
    metadata = {}

    # Load extracted_parameters.json (CADET input params)
    params_file = Path(experiment_folder) / 'extracted_parameters.json'
    if params_file.exists():
        with open(params_file, 'r') as f:
            params = json.load(f)
        metadata = params

    return metadata


def commit_to_database(experiment_id, experiment_folder, metrics, elab_metadata):
    """
    Commit results to PostgreSQL experiment_executions table.

    Uses UPSERT (ON CONFLICT) to insert or update by elab_id.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Build JSONB payloads
        results_jsonb = build_results_jsonb(metrics, experiment_folder)
        metadata_jsonb = build_metadata_jsonb(experiment_folder)

        # Get experiment title from elab metadata
        elab_title = None
        if elab_metadata:
            elab_title = elab_metadata.get('title', PROCESS_NAME)

        sql = """
            INSERT INTO experiment_executions (
                elab_id,
                elab_title,
                process_id,
                process_name,
                status,
                completed_at,
                metadata,
                results
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (elab_id)
            DO UPDATE SET
                elab_title = EXCLUDED.elab_title,
                process_id = EXCLUDED.process_id,
                process_name = EXCLUDED.process_name,
                status = EXCLUDED.status,
                completed_at = EXCLUDED.completed_at,
                metadata = EXCLUDED.metadata,
                results = EXCLUDED.results,
                updated_at = NOW()
            RETURNING id;
        """

        cur.execute(sql, (
            int(experiment_id),
            elab_title or PROCESS_NAME,
            PROCESS_ID,
            PROCESS_NAME,
            'completed',
            datetime.now(),
            json.dumps(metadata_jsonb),
            json.dumps(results_jsonb),
        ))

        row = cur.fetchone()
        row_id = row[0] if row else None
        conn.commit()
        cur.close()
        conn.close()

        print(f"SUCCESS: Committed to experiment_executions (id={row_id}) for elab_id={experiment_id}")
        return True

    except Exception as e:
        print(f"ERROR: Failed to commit to PostgreSQL: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python commit_to_db.py <experiment_id> [experiment_folder]")
        sys.exit(1)

    experiment_id = sys.argv[1]
    experiment_folder = sys.argv[2] if len(sys.argv) > 2 else f'experiments/experiment_{experiment_id}'

    print(f"Committing CADET simulation results for experiment {experiment_id}")
    print(f"Process ID: {PROCESS_ID}")
    print(f"Result Type: {RESULT_TYPE}")
    print(f"Database: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")

    # Load data
    simulation_results = load_simulation_results(experiment_folder)
    if not simulation_results:
        print("ERROR: No simulation results found")
        sys.exit(1)

    metadata = load_experiment_metadata(experiment_folder)

    # Prefer pre-computed metrics from report generation (metrics.json)
    metrics_file = Path(experiment_folder) / 'metrics.json'
    if metrics_file.exists():
        with open(metrics_file, 'r') as f:
            metrics = json.load(f)
        print(f"Loaded metrics from metrics.json: {list(metrics.keys())}")
    else:
        metrics = extract_metrics(simulation_results)
        print(f"Extracted metrics from results: {list(metrics.keys())}")

    # Commit to database
    success = commit_to_database(experiment_id, experiment_folder, metrics, metadata)

    if success:
        print("\nDatabase commit completed successfully!")
        sys.exit(0)
    else:
        print("\nDatabase commit failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
