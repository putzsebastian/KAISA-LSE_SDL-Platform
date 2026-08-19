#!/usr/bin/env python3
"""
Master Control Script - Coordinates Multiple Devices
Can be called externally with experiment ID and IP parameters.
"""

import sys
import argparse
import json
from pathlib import Path
import os
import asyncio
import time
import string

# Direct network control devices
from Devices.OpentronsV2 import Opentrons
from Devices.UniversalRobot_V2 import UniversalRobot
from Devices.URMovement_V2 import Grab, Place

# HTTP wrapper devices (now using API filenames)
from Devices.TecanSpark_API import TecanSpark
from Devices.AktaPure_HTTP import AktaPure

# Initialize experiment_id as module-level variable for logging
experiment_id = None


def get_extra_field(exp_data, field_name, default=None):
    """Extract value from eLabFTW extra_fields structure.

    eLabFTW stores experiment parameters in a nested structure:
    exp_data['metadata_decoded']['extra_fields'][field_name]['value']

    Args:
        exp_data: The loaded experiment JSON dictionary
        field_name: Name of the field (e.g., 'Number of Samples')
        default: Default value if field not found

    Returns:
        The field value as a string, or default if not found
    """
    try:
        # Try metadata_decoded first (pre-parsed structure)
        if 'metadata_decoded' in exp_data:
            extra_fields = exp_data['metadata_decoded'].get('extra_fields', {})
        # Fall back to parsing metadata string if needed
        elif 'metadata' in exp_data:
            metadata = json.loads(exp_data['metadata'])
            extra_fields = metadata.get('extra_fields', {})
        else:
            return default

        field = extra_fields.get(field_name, {})
        return field.get('value', default)
    except Exception:
        return default


def log_execution_step(exp_id, step_name, device_name, phase_name, status, message="", duration_estimate=None):
    """Log execution step progress to Flask API for real-time tracking"""
    print(f" [{status.upper()}] {step_name}: {message}")

    # Also send to Flask API for database tracking and UI updates
    try:
        import requests
        api_url = f"http://<APP_SERVER>:5001/api/experiments/{exp_id}/execution/log"
        payload = {
            "step_name": step_name,
            "device_name": device_name,
            "phase_name": phase_name,
            "status": status,
            "message": message,
            "duration_estimate": duration_estimate
        }
        requests.post(api_url, json=payload, timeout=2)
    except Exception:
        # Don't fail the experiment if logging fails
        pass


def wait(duration, message=""):
    """Wait for specified duration with optional message"""
    if message:
        print(f" Waiting {duration}s: {message}")
        log_execution_step(experiment_id, f"Wait_{message}", "System", "Utility", "running", f"Waiting {duration}s: {message}", duration)
    else:
        print(f" Waiting {duration}s")
        log_execution_step(experiment_id, "Wait", "System", "Utility", "running", f"Waiting {duration}s", duration)
    time.sleep(duration)
    log_execution_step(experiment_id, "Wait_Complete", "System", "Utility", "completed", f"Wait completed")


def pause_for_user(message, step_name="Manual Intervention"):
    """Pause execution for manual intervention - Web-based pause with database tracking"""
    import requests
    import time as _time

    print(f"PAUSED: {message}")
    log_execution_step(experiment_id, "Pause", "System", "Utility", "paused", message)

    # Create pause record via API
    try:
        lab_id = "168"

        pause_response = requests.post(
            f"http://<APP_SERVER>:5001/api/experiments/{experiment_id}/pause",
            json={
                "message": message,
                "lab_id": lab_id,
                "step_name": step_name
            },
            timeout=5
        )

        if pause_response.status_code == 200:
            pause_data = pause_response.json()
            print(f" Pause registered in system. Waiting for user to resume via UI...")

            # Poll for resume (check every 2 seconds)
            while True:
                status_response = requests.get(
                    f"http://<APP_SERVER>:5001/api/experiments/{experiment_id}/pause/status",
                    timeout=5
                )

                if status_response.status_code == 200:
                    status_data = status_response.json()

                    if not status_data.get("is_paused", True):
                        print(f" User resumed execution via UI")
                        log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution via UI")
                        break

                _time.sleep(2)
        else:
            print(f" Could not register pause in system, using terminal input as fallback")
            input("Press Enter to continue...")
            log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution (fallback)")

    except Exception as e:
        print(f" Pause API unavailable ({str(e)}), using terminal input as fallback")
        input("Press Enter to continue...")
        log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution (fallback)")


def countdown_for_user(duration, message, step_name="Countdown"):
    """Show a countdown timer in the execution UI and wait for the specified duration.
    Auto-resumes after the countdown — no user interaction required."""
    import requests

    print(f"COUNTDOWN: {duration}s - {message}")
    log_execution_step(experiment_id, step_name, "System", "Utility", "running",
                       f"Countdown {duration}s: {message}", duration)

    # Create countdown record via API (UI shows countdown modal)
    try:
        countdown_response = requests.post(
            f"http://<APP_SERVER>:5001/api/experiments/{experiment_id}/countdown",
            json={
                "message": f"[COUNTDOWN:{duration}] {message}",
                "duration": duration,
                "step_name": step_name,
                "lab_id": "168"
            },
            timeout=5
        )
        if countdown_response.status_code == 200:
            print(f" Countdown registered in UI. Waiting {duration}s...")
        else:
            print(f" Could not register countdown in UI, sleeping locally")
    except Exception as e:
        print(f" Countdown API unavailable ({str(e)}), sleeping locally")

    # Sleep for the duration (the actual wait)
    time.sleep(duration)

    # End the countdown (dismiss the UI modal)
    try:
        requests.post(
            f"http://<APP_SERVER>:5001/api/experiments/{experiment_id}/countdown/end",
            json={},
            timeout=5
        )
    except Exception:
        pass

    print(f"COUNTDOWN COMPLETE: {message}")
    log_execution_step(experiment_id, f"{step_name}_Complete", "System", "Utility", "completed",
                       f"Countdown finished: {message}")


# Timers

timers = {}


def start_timer(timer_name):
    """Start a named timer"""
    timers[timer_name] = time.time()
    print(f" Timer '{timer_name}' started")
    log_execution_step(experiment_id, f"Timer_Start_{timer_name}", "System", "Utility", "running", f"Timer '{timer_name}' started")


def stop_timer(timer_name):
    """Stop a named timer and log duration"""
    if timer_name in timers:
        duration = time.time() - timers[timer_name]
        print(f" Timer '{timer_name}' stopped: {duration:.2f}s")
        log_execution_step(experiment_id, f"Timer_Stop_{timer_name}", "System", "Utility", "completed", f"Timer '{timer_name}' duration: {duration:.2f}s")
        del timers[timer_name]
        return duration
    else:
        print(f" Timer '{timer_name}' was not started")
        return None


# 96-well helper functions

def wells_in_column(col: int, x: int):
    """Return the first x wells of a column, e.g. col=1, x=3 -> ['A1','B1','C1']."""
    if not (1 <= col <= 12):
        raise ValueError("96-well plate has columns 1-12.")
    if not (1 <= x <= 8):
        raise ValueError("96-well plate has max 8 rows (A-H).")
    rows = list(string.ascii_uppercase[:8])
    return [f"{rows[i]}{col}" for i in range(x)]


def wells_in_row(row: int, x: int):
    """Return the first x wells of a row, e.g. row=1, x=3 -> ['A1','A2','A3']."""
    if not (1 <= row <= 8):
        raise ValueError("96-well plate has rows 1-8 corresponding to A-H.")
    if not (1 <= x <= 12):
        raise ValueError("96-well plate has max 12 columns (1-12).")
    r = string.ascii_uppercase[row - 1]
    return [f"{r}{col}" for col in range(1, x + 1)]


def resolve_num_columns(exp_data, params=None, default=1, candidate_field_names=None) -> int:
    """Resolve number of columns to process. Reads (in order):
    params['num_columns'] -> params['column_count_field'] ->
    heuristic fallback over candidate_field_names -> default. Clamped to [1, 12]."""
    def _to_int(val, fallback):
        if val in (None, ""):
            return fallback
        try:
            return int(val)
        except Exception:
            digits = "".join(ch for ch in str(val) if ch.isdigit())
            return int(digits) if digits else fallback

    if params:
        v = params.get("num_columns")
        if v not in (None, ""):
            return max(1, min(12, _to_int(v, default)))
        field_name = params.get("column_count_field")
        if field_name:
            return max(1, min(12, _to_int(get_extra_field(exp_data, str(field_name), default), default)))

    if candidate_field_names is None:
        candidate_field_names = [
            "Number of Columns",
            "Columns",
            "Column Count",
            "Num Columns",
            "num_columns",
            "n_columns",
            "columns_to_measure",
        ]
    for name in candidate_field_names:
        v = get_extra_field(exp_data, name, None)
        if v not in (None, ""):
            return max(1, min(12, _to_int(v, default)))
    return max(1, min(12, _to_int(default, 1)))


def resolve_rows_per_column(exp_data, params=None, default=8, candidate_field_names=None) -> int:
    """Resolve number of rows per column (wells per column processed, A-H -> 1..8).
    Same precedence as resolve_num_columns. Clamped to [1, 8]."""
    def _to_int(val, fallback):
        if val in (None, ""):
            return fallback
        try:
            return int(val)
        except Exception:
            digits = "".join(ch for ch in str(val) if ch.isdigit())
            return int(digits) if digits else fallback

    if params:
        v = params.get("rows_per_column")
        if v not in (None, ""):
            return max(1, min(8, _to_int(v, default)))
        field_name = params.get("row_count_field")
        if field_name:
            return max(1, min(8, _to_int(get_extra_field(exp_data, str(field_name), default), default)))

    if candidate_field_names is None:
        candidate_field_names = [
            "Rows Per Column",
            "Samples Per Column",
            "Wells Per Column",
            "Number of Rows",
            "Row Count",
            "rows_per_column",
            "wells_per_column",
        ]
    for name in candidate_field_names:
        v = get_extra_field(exp_data, name, None)
        if v not in (None, ""):
            return max(1, min(8, _to_int(v, default)))
    return max(1, min(8, _to_int(default, 8)))


async def run_experiment(exp_id=None, template_id=None,
                         ur5_ip="<UR5_IP>", ur10_ip="<UR10_IP>",
                         opentrons_ip="<OPENTRONS_IP>"):
    """Main experiment execution function."""
    global experiment_id
    experiment_id = exp_id
    if not experiment_id:
        raise ValueError("Experiment ID is required")
    if not template_id:
        raise ValueError("Template ID is required")

    # MS sampler dispatch for this workflow: no MS acquisition
    sampler = None
    granularity = None

    # Folders
    current_folder = Path(__file__).parent
    tecan_alias = "<TECAN_ALIAS>"
    protocols_folder = current_folder / "protocols"
    data_folder = current_folder / "data"
    results_folder = current_folder / "results"
    for d in (protocols_folder, data_folder, results_folder):
        d.mkdir(exist_ok=True)

    # Protocol paths (dynamic, based on template_id)
    opentrons_protocol_path = protocols_folder / f"Template_{template_id}_OT_Protocol_1.py"
    tecan_protocol_path = protocols_folder / f"Template_{template_id}_TECAN_Protocol_1.xml"
    akta_protocol_path = protocols_folder / f"Template_{template_id}_AKTA_Protocol_1.py"
    batch_files_folder = protocols_folder / "MS_batch_files"
    experiment_results_path = results_folder / f"Experiment_{experiment_id}_Results.json"

    # Results scaffold
    results = {
        "experiment_id": experiment_id,
        "template_id": template_id,
        "lab_id": "168",
        "status": "running",
        "steps_completed": [],
        "errors": [],
        "file_paths": {
            "opentrons_protocol": str(opentrons_protocol_path),
            "tecan_protocol": str(tecan_protocol_path),
            "akta_protocol": str(akta_protocol_path),
            "experiment_results": str(experiment_results_path),
        },
    }

    # Device placeholders for cleanup
    ur5e = None
    ur10e = None
    tecan = None
    flex = None
    akta = None

    try:
        # Initialize devices actually needed in this workflow
        print("Initializing devices...")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "running", "Initializing devices")

        ur5e = UniversalRobot(ur5_ip, "UR5")
        ur10e = UniversalRobot(ur10_ip, "UR10")
        tecan = TecanSpark(tecan_alias)
        flex = Opentrons(opentrons_ip)
        akta = AktaPure()

        results["steps_completed"].append("devices_initialized")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "completed", "Devices initialized")
        print("Devices initialized.")

        # -------- Workflow steps --------

        # Step 1: Utility Pause - "Manually fill sample loop"
        pause_for_user("Manually fill sample loop", step_name="Manual Intervention")
        results["steps_completed"].append("pause_manual_fill_sample_loop")

        # Step 2: ÄKTA Pure - Run Protocol
        # Validate ÄKTA protocol exists
        if not akta_protocol_path.exists():
            raise FileNotFoundError(f"ÄKTA protocol not found: {akta_protocol_path}")

        log_execution_step(
            experiment_id,
            "AKTA_Run_Protocol",
            "ÄKTA Pure",
            "Execution",
            "running",
            "Starting ÄKTA chromatography run",
        )
        print(f"Running ÄKTA protocol: {akta_protocol_path}")

        akta_result = akta.run_protocol(str(akta_protocol_path), experiment_id=experiment_id)

        if akta_result.get("success"):
            log_execution_step(
                experiment_id,
                "AKTA_Run_Protocol",
                "ÄKTA Pure",
                "Execution",
                "completed",
                "ÄKTA chromatography run completed",
            )
            results["steps_completed"].append("akta_protocol_completed")

            if akta_result.get("results_data"):
                akta_results_path = results_folder / f"akta_results_{experiment_id}.json"
                with open(akta_results_path, "w") as f:
                    json.dump(akta_result["results_data"], f, indent=2)
                print(f"ÄKTA results saved to {akta_results_path}")
                results.setdefault("file_paths", {})["akta_results"] = str(akta_results_path)
        else:
            error_msg = akta_result.get("error", "Unknown error")
            log_execution_step(
                experiment_id,
                "AKTA_Run_Protocol",
                "ÄKTA Pure",
                "Execution",
                "failed",
                f"ÄKTA run failed: {error_msg}",
            )
            raise RuntimeError(f"ÄKTA chromatography failed: {error_msg}")

        # Mark experiment as completed
        results["status"] = "completed"
        log_execution_step(
            experiment_id,
            "Experiment_Completion",
            "System",
            "Completion",
            "completed",
            f"Experiment {experiment_id} completed successfully!",
        )
        print(f"Experiment {experiment_id} completed successfully!")

    except Exception as e:
        error_msg = f"Error in step {len(results['steps_completed']) + 1}: {str(e)}"
        print(f" {error_msg}")
        log_execution_step(experiment_id, "Experiment_Error", "System", "Error", "failed", error_msg)
        results["errors"].append(error_msg)
        results["status"] = "failed"
        # Re-raise to signal failure to caller
        raise

    finally:
        # Device cleanup
        print("Disconnecting devices...")
        log_execution_step(experiment_id, "Device_Cleanup", "System", "Completion", "running", "Disconnecting devices")
        try:
            if ur5e is not None:
                ur5e.Disconnect_Robot()
                print("UR5 disconnected.")
            if ur10e is not None:
                ur10e.Disconnect_Robot()
                print("UR10 disconnected.")
            if tecan is not None:
                tecan.disconnect()
                print("Tecan Spark disconnected.")
            if akta is not None:
                akta.disconnect()
                print("ÄKTA Pure disconnected.")
            # Opentrons disconnects automatically
        except Exception as cleanup_e:
            print(f"WARNING: Error during cleanup: {cleanup_e}")
            log_execution_step(
                experiment_id,
                "Device_Cleanup",
                "System",
                "Completion",
                "warning",
                f"Error during cleanup: {cleanup_e}",
            )

        # Save execution results
        try:
            with open(experiment_results_path, "w") as f:
                json.dump(results, f, indent=2)
            print(f"Results saved to: {experiment_results_path}")
            log_execution_step(
                experiment_id,
                "Save_Results",
                "System",
                "Completion",
                "completed",
                f"Results saved to: {experiment_results_path}",
            )
        except Exception as save_e:
            print(f"ERROR: Error saving results: {save_e}")
            log_execution_step(
                experiment_id,
                "Save_Results",
                "System",
                "Completion",
                "failed",
                f"Error saving results: {save_e}",
            )

    return results


def main():
    """Command line interface"""
    parser = argparse.ArgumentParser(description='Execute automated experiment workflow')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID')
    parser.add_argument('template_id', nargs='?', help='Template ID for protocol files')
    parser.add_argument('--ur5-ip', default="<UR5_IP>", help='UR5 robot IP address')
    parser.add_argument('--ur10-ip', default="<UR10_IP>", help='UR10 robot IP address')
    parser.add_argument('--opentrons-ip', default="<OPENTRONS_IP>", help='Opentrons Flex IP address')

    args = parser.parse_args()

    if not args.experiment_id:
        print("ERROR: Experiment ID is required.")
        parser.print_help()
        return 1
    if not args.template_id:
        print("ERROR: Template ID is required.")
        parser.print_help()
        return 1

    try:
        asyncio.run(run_experiment(
            exp_id=args.experiment_id,
            template_id=args.template_id,
            ur5_ip=args.ur5_ip,
            ur10_ip=args.ur10_ip,
            opentrons_ip=args.opentrons_ip
        ))
        print(f"Experiment execution completed!")
        return 0
    except Exception as e:
        print(f"ERROR: Experiment execution failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
