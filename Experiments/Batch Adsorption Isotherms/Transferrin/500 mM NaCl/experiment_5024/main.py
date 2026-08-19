#!/usr/bin/env python3
"""
Lab 167 Master Control Script - Particle Processing SDL
Coordinates UR5, Opentrons OT-2, Tecan Spark, and VacuuPump
Can be called externally with experiment ID and IP parameters.
"""

import sys
import argparse
import json
from pathlib import Path
import os
import asyncio
import time  # Required for utility functions

# Lab 167 Direct network control devices
from Devices_167.OpentronsV2 import Opentrons
from Devices_167.UniversalRobot_V2 import UniversalRobot
from Devices_167.URMovement_V2 import Grab, Place, Remove_Vacuum_Manifold, Place_Vacuum_Manifold

# Lab 167 HTTP wrapper devices (now using API filenames)
from Devices_167.TecanSpark_API import TecanSpark
from Devices_167.VacuuSelect import VacuuSelect

# Initialize experiment_id as module-level variable for logging
experiment_id = None


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
    lab_id = "168"  # Default lab, can be extracted from experiment context if needed

    pause_created = False

    try:
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
            pause_created = True
            print(f" Pause registered in system. Waiting for user to resume via UI...")
        elif pause_response.status_code == 400:
            # Pause already exists - this is OK, just wait for resume
            response_data = pause_response.json()
            if "already has an active pause" in response_data.get("error", ""):
                print(f" Pause already exists for this experiment. Waiting for user to resume via UI...")
                pause_created = True  # Treat as success since pause exists
            else:
                print(f" Pause API error: {response_data.get('error', 'Unknown error')}")
        else:
            print(f" Pause API returned status {pause_response.status_code}")

    except Exception as e:
        print(f" Pause API error: {str(e)}")

    if not pause_created:
        error_msg = "Failed to create or find pause record. Cannot continue without user interface."
        print(f" ERROR: {error_msg}")
        log_execution_step(experiment_id, "Pause_Error", "System", "Utility", "failed", error_msg)
        raise RuntimeError(error_msg)

    # Poll for resume (check every 2 seconds)
    print(f" Polling for resume every 2 seconds...")
    poll_count = 0
    while True:
        try:
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
                else:
                    poll_count += 1
                    if poll_count % 30 == 0:  # Print status every 60 seconds
                        print(f" Still waiting for resume... ({poll_count * 2} seconds elapsed)")
            else:
                print(f" Pause status check returned {status_response.status_code}, retrying...")

        except Exception as e:
            print(f" Error checking pause status: {str(e)}, retrying...")

        time.sleep(2)  # Poll every 2 seconds

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


async def run_experiment(exp_id=None, template_id=None, ur5_ip="<UR5_IP>", opentrons_ip="<OPENTRONS_IP>", vacuupump_ip="<VACUUPUMP_IP>"):
    """
    Execute Lab 167 automated experiment workflow
    
    Args:
        experiment_id: Unique experiment identifier
        template_id: Template ID for protocol files
        ur5_ip: UR5 robot IP address (default: <UR5_IP>)
        opentrons_ip: Opentrons OT-2 IP address (default: <OPENTRONS_IP>)
        vacuupump_ip: VacuuPump IP address (default: <VACUUPUMP_IP>)
    """
    # Make experiment_id available globally for logging utilities
    global experiment_id
    experiment_id = exp_id
    
    # Setup paths
    current_folder = Path(__file__).parent
    tecan_alias = "<TECAN_ALIAS>"  # Fixed Tecan alias for Lab 167
    protocols_folder = current_folder / "protocols"
    data_folder = current_folder / "data"
    results_folder = current_folder / "results"
    
    # Create folders if they don't exist
    protocols_folder.mkdir(exist_ok=True)
    data_folder.mkdir(exist_ok=True)
    results_folder.mkdir(exist_ok=True)
    
    # Define protocol paths using template_id
    opentrons_protocol_path_1 = protocols_folder / f"Template_{template_id}_OT_Protocol_1.py"
    opentrons_protocol_path_2 = protocols_folder / f"Template_{template_id}_OT_Protocol_2.py"
    opentrons_protocol_path_3 = protocols_folder / f"Template_{template_id}_OT_Protocol_3.py"
    opentrons_protocol_path_4 = protocols_folder / f"Template_{template_id}_OT_Protocol_4.py"
    opentrons_protocol_path_5 = protocols_folder / f"Template_{template_id}_OT_Protocol_5.py"
    tecan_protocol_path = protocols_folder / f"Template_{template_id}_TECAN_Protocol_1.xml"
    experiment_results_path = results_folder / f"Experiment_{experiment_id}_Results.json"
    
    # Validate protocol files exist
    log_execution_step(experiment_id, "Protocol_Validation", "System", "Setup", "running", "Validating protocol files exist")

    # Check all Opentrons protocols
    opentrons_protocols = [
        opentrons_protocol_path_1,
        opentrons_protocol_path_2,
        opentrons_protocol_path_3,
        opentrons_protocol_path_4,
        opentrons_protocol_path_5
    ]

    for protocol_path in opentrons_protocols:
        if not protocol_path.exists():
            raise FileNotFoundError(f"Opentrons protocol not found: {protocol_path}")

    # Check Tecan protocol
    if not tecan_protocol_path.exists():
        raise FileNotFoundError(f"Tecan protocol not found: {tecan_protocol_path}")

    log_execution_step(experiment_id, "Protocol_Validation", "System", "Setup", "completed", "Protocol files validated")
    print(f"Protocol files validated")
    print(f"   Opentrons OT-2 Protocol 1: {opentrons_protocol_path_1}")
    print(f"   Opentrons OT-2 Protocol 2: {opentrons_protocol_path_2}")
    print(f"   Opentrons OT-2 Protocol 3: {opentrons_protocol_path_3}")
    print(f"   Opentrons OT-2 Protocol 4: {opentrons_protocol_path_4}")
    print(f"   Opentrons OT-2 Protocol 5: {opentrons_protocol_path_5}")
    print(f"   Tecan: {tecan_protocol_path}")

    # Initialize execution results
    results = {
        "experiment_id": experiment_id,
        "template_id": template_id,
        "lab_id": "167",
        "lab_name": "Particle Processing SDL",
        "status": "running",
        "steps_completed": [],
        "errors": [],
        "file_paths": {
            "opentrons_protocol_1": str(opentrons_protocol_path_1),
            "opentrons_protocol_2": str(opentrons_protocol_path_2),
            "opentrons_protocol_3": str(opentrons_protocol_path_3),
            "opentrons_protocol_4": str(opentrons_protocol_path_4),
            "opentrons_protocol_5": str(opentrons_protocol_path_5),
            "tecan_protocol": str(tecan_protocol_path),
            "experiment_results": str(experiment_results_path)
        }
    }

    # Initialize device objects outside try-block to ensure they are defined for cleanup
    ur5 = None
    tecan = None
    ot2 = None
    vacuupump = None

    try:
        # Initialize Lab 167 devices
        print("Initializing Lab 167 devices...")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "running", "Initializing Lab 167 devices")
        ur5 = UniversalRobot(ur5_ip, "UR5")
        tecan = TecanSpark(tecan_alias)
        ot2 = Opentrons(opentrons_ip)
        vacuupump = VacuuSelect(vacuupump_ip)
        results["steps_completed"].append("devices_initialized")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "completed", "Lab 167 devices initialized")
        print("Lab 167 devices initialized.")

        # Pre-resolve custom labware path
        labware_file = "cytiva_96_filterwellplate_1ml.json"
        labware_path = Path("Devices_167") / "Labware" / labware_file
        if not labware_path.exists():
            raise FileNotFoundError(f"Labware file not found: {labware_path}")

        # Step 1: Opentrons OT-2 - Run Protocol w/ Custom Labware
        print("Step 1: Running Opentrons OT-2 protocol with custom labware...")
        log_execution_step(experiment_id, "Opentrons_Run_Protocol_Custom_1", "Opentrons OT-2", "Execution", "running", "Uploading and running protocol with custom labware")
        prot_id = ot2.Upload_Protocol_Labware(
            str(opentrons_protocol_path_1),
            str(labware_path),
            info_server=False
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        results["steps_completed"].append("opentrons_protocol_custom_run_1")
        log_execution_step(experiment_id, "Opentrons_Run_Protocol_Custom_1", "Opentrons OT-2", "Execution", "completed", f"Run ID: {run_id}")

        # Steps 2-10: UR5, VacuuPump, Utility
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Storage2", "UR5", "Execution", "running", "Grabbing 96DeepWP from Storage2")
        Grab(ur5, 'Storage2', '96DeepWP')
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Storage2", "UR5", "Execution", "completed", "Grabbed 96DeepWP from Storage2")

        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Pump_Pos1", "UR5", "Execution", "running", "Placing 96DeepWP at Pump_Pos1")
        Place(ur5, 'Pump_Pos1', '96DeepWP', rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Pump_Pos1", "UR5", "Execution", "completed", "Placed 96DeepWP at Pump_Pos1")

        log_execution_step(experiment_id, "UR5_Place_Vacuum_Manifold_1", "UR5", "Execution", "running", "Placing Vacuum Manifold")
        Place_Vacuum_Manifold(ur5, rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Vacuum_Manifold_1", "UR5", "Execution", "completed", "Vacuum Manifold placed")

        log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1_1", "UR5", "Execution", "running", "Grabbing Filterplate from OT2_Pos1")
        Grab(ur5, 'OT2_Pos1', 'Filterplate')
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1_1", "UR5", "Execution", "completed", "Filterplate grabbed from OT2_Pos1")

        log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2_1", "UR5", "Execution", "running", "Placing Filterplate at Pump_Pos2")
        Place(ur5, 'Pump_Pos2', 'Filterplate', rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2_1", "UR5", "Execution", "completed", "Filterplate placed at Pump_Pos2")

        log_execution_step(experiment_id, "VacuuPump_Run_Speed_25_30s_1", "VacuuPump", "Execution", "running", "Running pump at 25% for 30s")
        vacuupump.Run_Pump_Speed(25, 30)
        log_execution_step(experiment_id, "VacuuPump_Run_Speed_25_30s_1", "VacuuPump", "Execution", "completed", "Pump cycle completed")

        wait(300, "Venting...")

        log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2_1", "UR5", "Execution", "running", "Grabbing Filterplate from Pump_Pos2")
        Grab(ur5, 'Pump_Pos2', 'Filterplate')
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2_1", "UR5", "Execution", "completed", "Filterplate grabbed from Pump_Pos2")

        log_execution_step(experiment_id, "UR5_Place_Filterplate_OT2_Pos1_1", "UR5", "Execution", "running", "Placing Filterplate back to OT2_Pos1 (rehome True)")
        Place(ur5, 'OT2_Pos1', 'Filterplate', rehome=True)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_OT2_Pos1_1", "UR5", "Execution", "completed", "Filterplate placed at OT2_Pos1")

        # Loop: Equilibration Cycles from eLabFTW field
        exp_json_path = data_folder / f'experiment_{experiment_id}.json'
        try:
            with open(exp_json_path, 'r') as f:
                exp_data = json.load(f)
            equil_cycles = int(exp_data.get('Equilibration Cycles', 3))  # Default to 3 if not found
        except Exception as e:
            equil_cycles = 3
            print("WARNING: Could not read Equilibration Cycles from experiment metadata. Defaulting to 3.")
            log_execution_step(experiment_id, "Loop_Setup", "System", "Utility", "warning", "Metadata not found; defaulting 'Equilibration Cycles' to 3")

        print(f"Looping {equil_cycles} times based on eLabFTW field 'Equilibration Cycles'")
        log_execution_step(experiment_id, "Loop_Setup", "System", "Utility", "running", f"Starting loop with {equil_cycles} iterations from field 'Equilibration Cycles'")

        for i in range(equil_cycles):
            log_execution_step(experiment_id, f"Loop_Iteration_{i+1}", "System", "Utility", "running", f"Processing cycle {i+1} of {equil_cycles}")

            # Opentrons run with custom labware
            log_execution_step(experiment_id, f"Opentrons_Run_Protocol_Custom_Loop_{i+1}", "Opentrons OT-2", "Execution", "running", "Uploading and running protocol with custom labware (loop)")
            prot_id = ot2.Upload_Protocol_Labware(
                str(opentrons_protocol_path_2),
                str(labware_path),
                info_server=False
            )
            run_id = ot2.Run_Protocol(prot_id, info_server=False)
            log_execution_step(experiment_id, f"Opentrons_Run_Protocol_Custom_Loop_{i+1}", "Opentrons OT-2", "Execution", "completed", f"Run ID: {run_id}")

            # UR5 and pump steps inside loop
            Grab(ur5, 'OT2_Pos1', 'Filterplate')
            Place(ur5, 'Pump_Pos2', 'Filterplate', rehome=False)
            vacuupump.Run_Pump_Speed(25, 30)
            wait(300, "Venting")
            Grab(ur5, 'Pump_Pos2', 'Filterplate')
            Place(ur5, 'OT2_Pos1', 'Filterplate', rehome=True)

        log_execution_step(experiment_id, "Loop_Complete", "System", "Utility", "completed", f"Completed all {equil_cycles} loop iterations")

        # Post-loop sequence
        log_execution_step(experiment_id, "UR5_Remove_Vacuum_Manifold_1", "UR5", "Execution", "running", "Removing Vacuum Manifold")
        Remove_Vacuum_Manifold(ur5, rehome=False)
        log_execution_step(experiment_id, "UR5_Remove_Vacuum_Manifold_1", "UR5", "Execution", "completed", "Vacuum Manifold removed")

        Grab(ur5, 'Pump_Pos1', '96DeepWP')
        Place(ur5, 'Storage2', '96DeepWP', rehome=True)

        # Opentrons run with custom labware
        log_execution_step(experiment_id, "Opentrons_Run_Protocol_Custom_2", "Opentrons OT-2", "Execution", "running", "Uploading and running protocol with custom labware")
        prot_id = ot2.Upload_Protocol_Labware(
            str(opentrons_protocol_path_3),
            str(labware_path),
            info_server=False
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        log_execution_step(experiment_id, "Opentrons_Run_Protocol_Custom_2", "Opentrons OT-2", "Execution", "completed", f"Run ID: {run_id}")

        # Pause for user
        pause_for_user("Cover Filterplate with Foil. Remove all other labware. Fresh 300 uL tips in Slot 7. Empty Trash.")

        # Opentrons run with custom labware again
        log_execution_step(experiment_id, "Opentrons_Run_Protocol_Custom_3", "Opentrons OT-2", "Execution", "running", "Uploading and running protocol with custom labware")
        prot_id = ot2.Upload_Protocol_Labware(
            str(opentrons_protocol_path_4),
            str(labware_path),
            info_server=False
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        log_execution_step(experiment_id, "Opentrons_Run_Protocol_Custom_3", "Opentrons OT-2", "Execution", "completed", f"Run ID: {run_id}")

        # Pause for user
        pause_for_user("Remove Foil from Filterplate")

        # Second vacuum sequence
        Grab(ur5, 'Storage1', '96DeepWP')
        Place(ur5, 'Pump_Pos1', '96DeepWP', rehome=False)
        Place_Vacuum_Manifold(ur5, rehome=False)
        Grab(ur5, 'OT2_Pos1', 'Filterplate')
        Place(ur5, 'Pump_Pos2', 'Filterplate', rehome=False)
        vacuupump.Run_Pump_Speed(25, 30)
        wait(300, "Venting...")
        Grab(ur5, 'Pump_Pos2', 'Filterplate')
        Place(ur5, 'Storage3', 'Filterplate', rehome=False)
        Remove_Vacuum_Manifold(ur5, rehome=False)

        # Move deep well plate to OT-2 position 1 and prepare WP1 on OT-2
        Grab(ur5, 'Pump_Pos1', '96DeepWP')
        Place(ur5, 'OT2_Pos1', '96DeepWP', rehome=False)
        Grab(ur5, 'Storage4', 'WP1')
        Place(ur5, 'OT2_Pos5', 'WP1', rehome=True)

        # Standard Opentrons run (no custom labware)
        log_execution_step(experiment_id, "Opentrons_Run_Protocol_Standard_1", "Opentrons OT-2", "Execution", "running", "Uploading and running standard protocol")
        prot_id = ot2.Upload_Protocol(str(opentrons_protocol_path_5), info_server=False)
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        log_execution_step(experiment_id, "Opentrons_Run_Protocol_Standard_1", "Opentrons OT-2", "Execution", "completed", f"Run ID: {run_id}")

        # Return deep well plate to storage
        Grab(ur5, 'OT2_Pos1', '96DeepWP')
        Place(ur5, 'Storage1', '96DeepWP', rehome=False)

        # Tecan measurement sequence
        Grab(ur5, 'OT2_Pos5', 'WP1')
        log_execution_step(experiment_id, "Tecan_Open_1", "Tecan Spark", "Execution", "running", "Opening Tecan Spark")
        await tecan.open_device()
        log_execution_step(experiment_id, "Tecan_Open_1", "Tecan Spark", "Execution", "completed", "Tecan Spark opened")

        Place(ur5, 'Tecan', 'WP1', rehome=False)

        log_execution_step(experiment_id, "Tecan_Close_1", "Tecan Spark", "Execution", "running", "Closing Tecan Spark")
        await tecan.close_device()
        log_execution_step(experiment_id, "Tecan_Close_1", "Tecan Spark", "Execution", "completed", "Tecan Spark closed")

        log_execution_step(experiment_id, "Tecan_Run_Measurement", "Tecan Spark", "Execution", "running", "Loading and running Tecan XML protocol with experiment linking")
        await tecan.load_and_run_xml(str(tecan_protocol_path), experiment_id=experiment_id)
        log_execution_step(experiment_id, "Tecan_Run_Measurement", "Tecan Spark", "Execution", "completed", "Tecan measurement completed")

        log_execution_step(experiment_id, "Tecan_Open_2", "Tecan Spark", "Execution", "running", "Opening Tecan Spark to retrieve plate")
        await tecan.open_device()
        log_execution_step(experiment_id, "Tecan_Open_2", "Tecan Spark", "Execution", "completed", "Tecan Spark opened")

        Grab(ur5, 'Tecan', 'WP1')

        log_execution_step(experiment_id, "Tecan_Close_2", "Tecan Spark", "Execution", "running", "Closing Tecan Spark after retrieval")
        await tecan.close_device()
        log_execution_step(experiment_id, "Tecan_Close_2", "Tecan Spark", "Execution", "completed", "Tecan Spark closed")

        Place(ur5, 'Storage4', 'WP1', rehome=True)

        # Update final status
        results["status"] = "completed"
        log_execution_step(experiment_id, "Experiment_Completion", "System", "Completion", "completed", f"Lab 167 experiment {experiment_id} completed successfully!")
        print(f"Lab 167 experiment {experiment_id} completed successfully!")

    except Exception as e:
        error_msg = f"Lab 167 Error in step {len(results['steps_completed']) + 1}: {str(e)}"
        print(f" {error_msg}")
        log_execution_step(experiment_id, "Experiment_Error", "System", "Error", "failed", error_msg)
        results["errors"].append(error_msg)
        results["status"] = "failed"

        # Lab 167 device cleanup
        print("Attempting Lab 167 device cleanup...")
        log_execution_step(experiment_id, "Device_Cleanup", "System", "Error Handling", "running", "Attempting Lab 167 device cleanup after error")
        try:
            if ur5 is not None:
                ur5.Disconnect_Robot()
                print("UR5 disconnected.")
            if tecan is not None:
                await tecan.disconnect()
                print("Tecan Spark disconnected.")
            if vacuupump is not None:
                vacuupump.disconnect()
                print("VacuuPump disconnected.")
            # Opentrons OT-2 disconnects automatically
        except Exception as cleanup_e:
            print(f"WARNING: Error during cleanup: {cleanup_e}")
            log_execution_step(experiment_id, "Device_Cleanup", "System", "Error Handling", "warning", f"Error during cleanup: {cleanup_e}")

        # Re-raise the exception after cleanup so the main function knows about the failure
        raise

    finally:
        # Attempt normal device disconnection as well
        try:
            if ur5 is not None:
                ur5.Disconnect_Robot()
            if tecan is not None:
                await tecan.disconnect()
            if vacuupump is not None:
                vacuupump.disconnect()
        except Exception:
            pass

        # Save execution results to data folder regardless of success or failure
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
    """Command line interface for Lab 167"""
    parser = argparse.ArgumentParser(description='Execute automated Lab 167 experiment workflow')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID')
    parser.add_argument('template_id', nargs='?', help='Template ID for protocol files')
    parser.add_argument('--ur5-ip', default="<UR5_IP>", help='UR5 robot IP address')
    parser.add_argument('--opentrons-ip', default="<OPENTRONS_IP>", help='Opentrons OT-2 IP address')
    parser.add_argument('--vacuupump-ip', default="<VACUUPUMP_IP>", help='VacuuPump IP address')

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
        # Run the async experiment function
        asyncio.run(run_experiment(
            exp_id=args.experiment_id,
            template_id=args.template_id,
            ur5_ip=args.ur5_ip,
            opentrons_ip=args.opentrons_ip,
            vacuupump_ip=args.vacuupump_ip
        ))
        print(f"Lab 167 experiment execution completed!")
        return 0
    except Exception as e:
        print(f"ERROR: Lab 167 experiment execution failed: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())