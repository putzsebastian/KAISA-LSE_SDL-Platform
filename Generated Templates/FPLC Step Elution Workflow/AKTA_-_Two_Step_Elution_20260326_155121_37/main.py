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
import time  # Required for utility functions

# Direct network control devices
from Devices.OpentronsV2 import Opentrons
from Devices.UniversalRobot_V2 import UniversalRobot
from Devices.URMovement_V2 import Grab, Place

# HTTP wrapper devices (now using API filenames)
from Devices.TecanSpark_API import TecanSpark
from Devices.ESIInjectionSetup_API import ESI_Injection_Setup
from Devices.X500R_API_V2 import SciexX500R
from Devices.AktaPure_HTTP import AktaPure

# Initialize experiment_id as module-level variable for logging
experiment_id = None


def get_extra_field(exp_data, field_name, default=None):
    """
    Extract value from eLabFTW extra_fields structure.
    
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
    except Exception as e:
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
    import time

    print(f" PAUSED: {message}")
    log_execution_step(experiment_id, "Pause", "System", "Utility", "paused", message)

    # Create pause record via API
    try:
        # Determine lab_id from experiment_id or default
        lab_id = "168"  # Default lab, can be extracted from experiment context if needed

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
                        # Pause has been resumed
                        print(f" User resumed execution via UI")
                        log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution via UI")
                        break

                time.sleep(2)  # Poll every 2 seconds
        else:
            # Fallback to terminal input if API fails
            print(f" Could not register pause in system, using terminal input as fallback")
            input("Press Enter to continue...")
            log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution (fallback)")

    except Exception as e:
        # Fallback to terminal input if API is unavailable
        print(f" Pause API unavailable ({str(e)}), using terminal input as fallback")
        input("Press Enter to continue...")
        log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution (fallback)")


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


async def run_experiment(exp_id=None, template_id=None, ur5_ip="<UR5_IP>", ur10_ip="<UR10_IP>", opentrons_ip="<OPENTRONS_IP>"):
    """
    Main experiment execution function

    Args:
        exp_id (str): Experiment ID (assigned to global experiment_id)
        template_id (str): Template ID for protocol file naming
        ur5_ip (str): UR5 robot IP address
        ur10_ip (str): UR10 robot IP address
        opentrons_ip (str): Opentrons Flex IP address

    Returns:
        dict: Execution results with file paths and status
    """
    # Assign parameter to global variable for utility functions
    global experiment_id
    experiment_id = exp_id

    if not experiment_id:
        raise ValueError("Experiment ID is required")
    if not template_id:
        raise ValueError("Template ID is required")

    # Fixed configuration values
    current_folder = Path(__file__).parent
    tecan_alias = "<TECAN_ALIAS>"
    protocols_folder = current_folder / "protocols"
    data_folder = current_folder / "data"
    results_folder = current_folder / "results"

    # Create folders if they don't exist
    protocols_folder.mkdir(exist_ok=True)
    data_folder.mkdir(exist_ok=True)
    results_folder.mkdir(exist_ok=True)

    print(f"Starting experiment execution for ID: {experiment_id}, Template ID: {template_id}")
    log_execution_step(experiment_id, "Experiment_Start", "System", "Setup", "running", f"Starting experiment {experiment_id}")

    # Construct dynamic paths for protocol files
    akta_protocol_path = protocols_folder / f"Template_{template_id}_AKTA_Protocol_1.py"

    # Construct dynamic path for results file
    experiment_results_path = results_folder / f"Experiment_{experiment_id}_Results.json"

    # Initialize execution results
    results = {
        "experiment_id": experiment_id,
        "template_id": template_id,
        "lab_id": "168",
        "status": "running",
        "steps_completed": [],
        "errors": [],
        "file_paths": {
            "akta_protocol": str(akta_protocol_path),
            "experiment_results": str(experiment_results_path)
        }
    }

    # Initialize device objects outside try-block
    akta = None

    try:
        # Initialize devices
        print("Initializing devices...")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "running", "Initializing devices")
        akta = AktaPure()
        results["steps_completed"].append("devices_initialized")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "completed", "Devices initialized")
        print("Devices initialized.")

        # Step 1: Utility - Pause
        print("Step 1: Pausing for user intervention to manually fill sample loop...")
        pause_for_user("Manually fill sample loop")
        results["steps_completed"].append("pause_manual_fill_sample_loop")
        print("Step 1: Pause completed.")

        # Step 2: AKTA Pure - Run Protocol
        print("Step 2: Running AKTA Pure protocol...")
        # Validate protocol exists
        if not akta_protocol_path.exists():
            raise FileNotFoundError(f"ÄKTA protocol not found: {akta_protocol_path}")

        log_execution_step(experiment_id, "AKTA_Run_Protocol", "ÄKTA Pure", "Execution", "running",
                           "Starting ÄKTA chromatography run")
        akta_run_result = akta.run_protocol(str(akta_protocol_path), experiment_id=experiment_id)

        if akta_run_result.get('success'):
            results["steps_completed"].append("akta_protocol_executed")
            log_execution_step(experiment_id, "AKTA_Run_Protocol", "ÄKTA Pure", "Execution", "completed",
                               "ÄKTA chromatography run completed")
            print("ÄKTA chromatography run completed.")
            # Save results if available
            if akta_run_result.get('results_data'):
                akta_results_file_path = results_folder / f"akta_results_{experiment_id}.json"
                with open(akta_results_file_path, 'w') as f:
                    json.dump(akta_run_result['results_data'], f, indent=2)
                results["file_paths"]["akta_results"] = str(akta_results_file_path)
                print(f"ÄKTA results saved to {akta_results_file_path}")
                log_execution_step(experiment_id, "AKTA_Save_Results", "ÄKTA Pure", "Data", "completed", f"ÄKTA results saved to {akta_results_file_path}")

        else:
            error_msg = akta_run_result.get('error', 'Unknown error during ÄKTA run')
            log_execution_step(experiment_id, "AKTA_Run_Protocol", "ÄKTA Pure", "Execution", "failed",
                               f"ÄKTA run failed: {error_msg}")
            raise RuntimeError(f"ÄKTA chromatography failed: {error_msg}")
        print("Step 2: AKTA Pure protocol completed.")

        # Update final status
        results["status"] = "completed"
        log_execution_step(experiment_id, "Experiment_Completion", "System", "Completion", "completed", f"Experiment {experiment_id} completed successfully!")
        print(f"Experiment {experiment_id} completed successfully!")

    except Exception as e:
        error_msg = f"Error in experiment execution: {str(e)}"
        print(f" {error_msg}")
        log_execution_step(experiment_id, "Experiment_Error", "System", "Error", "failed", error_msg)
        results["errors"].append(error_msg)
        results["status"] = "failed"

        # Cleanup devices
        print("Attempting device cleanup...")
        log_execution_step(experiment_id, "Device_Cleanup", "System", "Error Handling", "running", "Attempting device cleanup after error")
        try:
            if akta is not None:
                akta.disconnect()
                print("ÄKTA Pure disconnected.")
        except Exception as cleanup_e:
            print(f"WARNING: Error during cleanup: {cleanup_e}")
            log_execution_step(experiment_id, "Device_Cleanup", "System", "Error Handling", "warning", f"Error during cleanup: {cleanup_e}")

        # Re-raise the exception after cleanup so the main function knows about the failure
        raise

    finally:
        # Save execution results to results folder regardless of success or failure
        try:
            with open(experiment_results_path, 'w') as f:
                json.dump(results, f, indent=2)
            print(f"Results saved to: {experiment_results_path}")
            log_execution_step(experiment_id, "Save_Results", "System", "Completion", "completed", f"Results saved to: {experiment_results_path}")
        except Exception as save_e:
            print(f"ERROR: Error saving results: {save_e}")
            log_execution_step(experiment_id, "Save_Results", "System", "Completion", "failed", f"Error saving results: {save_e}")

    return results


def main():
    """
    Command line interface
    """
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
