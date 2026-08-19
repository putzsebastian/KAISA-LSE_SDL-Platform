#!/usr/bin/env python3
"""
Lab 167 Master Control Script - Particle Processing SDL
Workflow for Template 278 coordinating UR5, Opentrons OT-2, Tecan Spark, and VacuuPump
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


def get_extra_field(exp_data, field_name, default=None):
    """Extract value from eLabFTW extra_fields structure.

    eLabFTW stores experiment parameters in a nested structure:
    exp_data['metadata_decoded']['extra_fields'][field_name]['value']

    Args:
        exp_data: The loaded experiment JSON dictionary
        field_name: Name of the field (e.g., 'Equilibration Cycles')
        default: Default value if field not found

    Returns:
        The field value as a string, or default if not found
    """
    try:
        if 'metadata_decoded' in exp_data:
            extra_fields = exp_data['metadata_decoded'].get('extra_fields', {})
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
    try:
        import requests
        api_url = f"http://<APP_SERVER>:5001/api/experiments/{exp_id}/execution/log"
        payload = {
            "step_name": step_name,
            "device_name": device_name,
            "phase_name": phase_name,
            "status": status,
            "message": message,
            "duration_estimate": duration_estimate,
        }
        requests.post(api_url, json=payload, timeout=2)
    except Exception:
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

    try:
        lab_id = "167"
        pause_response = requests.post(
            f"http://<APP_SERVER>:5001/api/experiments/{experiment_id}/pause",
            json={"message": message, "lab_id": lab_id, "step_name": step_name},
            timeout=5,
        )
        if pause_response.status_code == 200:
            print(" Pause registered in system. Waiting for user to resume via UI...")
            while True:
                status_response = requests.get(
                    f"http://<APP_SERVER>:5001/api/experiments/{experiment_id}/pause/status",
                    timeout=5,
                )
                if status_response.status_code == 200:
                    status_data = status_response.json()
                    if not status_data.get("is_paused", True):
                        print(" User resumed execution via UI")
                        log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution via UI")
                        break
                _time.sleep(2)
        else:
            print(" Could not register pause in system, using terminal input as fallback")
            input("Press Enter to continue...")
            log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution (fallback)")
    except Exception:
        print(" Pause API unavailable, using terminal input as fallback")
        input("Press Enter to continue...")
        log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution (fallback)")


# Timers utility
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
        print(" Timer '{timer_name}' was not started")
        return None


async def run_experiment(exp_id=None, template_id=None,
                         ur5_ip="<UR5_IP>",
                         opentrons_ip="<OPENTRONS_IP>",
                         vacuupump_ip="<VACUUMP_IP>"):
    """Execute Lab 167 automated experiment workflow for Template 278."""
    global experiment_id
    experiment_id = exp_id

    # Setup paths
    current_folder = Path(__file__).parent
    tecan_alias = "<TECAN_ALIAS>"  # Fixed Tecan alias for Lab 167
    protocols_folder = current_folder / "protocols"
    data_folder = current_folder / "data"
    results_folder = current_folder / "results"
    for d in (protocols_folder, data_folder, results_folder):
        d.mkdir(exist_ok=True)

    # Define protocol paths using template_id (explicit filenames per user spec)
    opentrons_protocol_1 = protocols_folder / f"Template_{template_id}_OT_Protocol_1.py"
    opentrons_protocol_2 = protocols_folder / f"Template_{template_id}_OT_Protocol_2.py"
    opentrons_protocol_3 = protocols_folder / f"Template_{template_id}_OT_Protocol_3.py"
    opentrons_protocol_4 = protocols_folder / f"Template_{template_id}_OT_Protocol_4.py"
    opentrons_protocol_5 = protocols_folder / f"Template_{template_id}_OT_Protocol_5.py"
    tecan_protocol_1 = protocols_folder / f"Template_{template_id}_TECAN_Protocol_1.xml"

    experiment_results_path = results_folder / f"Experiment_{experiment_id}_Results.json"

    # Validate protocol files exist (only those that the workflow uses)
    log_execution_step(experiment_id, "Protocol_Validation", "System", "Setup", "running", "Validating protocol files exist")
    for path in [
        opentrons_protocol_1,
        opentrons_protocol_2,
        opentrons_protocol_3,
        opentrons_protocol_4,
        opentrons_protocol_5,
        tecan_protocol_1,
    ]:
        if not path.exists():
            raise FileNotFoundError(f"Required protocol not found: {path}")
    log_execution_step(experiment_id, "Protocol_Validation", "System", "Setup", "completed", "Protocol files validated")

    results = {
        "experiment_id": experiment_id,
        "template_id": template_id,
        "lab_id": "167",
        "lab_name": "Particle Processing SDL",
        "status": "running",
        "steps_completed": [],
        "errors": [],
        "file_paths": {
            "opentrons_protocol_1": str(opentrons_protocol_1),
            "opentrons_protocol_2": str(opentrons_protocol_2),
            "opentrons_protocol_3": str(opentrons_protocol_3),
            "opentrons_protocol_4": str(opentrons_protocol_4),
            "opentrons_protocol_5": str(opentrons_protocol_5),
            "tecan_protocol_1": str(tecan_protocol_1),
            "experiment_results": str(experiment_results_path),
        },
    }

    # Pre-declare for cleanup - only init the ones the workflow uses
    ur5 = tecan = ot2 = vacuupump = None

    try:
        # Initialize Lab 167 devices actually used in this workflow
        print("Initializing Lab 167 devices...")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "running", "Initializing Lab 167 devices")

        ur5 = UniversalRobot(ur5_ip, "UR5")
        ot2 = Opentrons(opentrons_ip)
        tecan = TecanSpark(tecan_alias)
        vacuupump = VacuuSelect(vacuupump_ip)

        results["steps_completed"].append("devices_initialized")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "completed", "Lab 167 devices initialized")

        # ---------- Read eLabFTW fields for loop control ----------
        exp_json_path = current_folder / f"experiment_{experiment_id}.json"
        if not exp_json_path.exists():
            raise FileNotFoundError(f"Experiment JSON not found: {exp_json_path}")
        with open(exp_json_path, "r") as f:
            exp_data = json.load(f)

        equil_cycles_str = get_extra_field(exp_data, "Equilibration Cycles", 0)
        equil_cycles = int(equil_cycles_str) if equil_cycles_str is not None else 0
        log_execution_step(
            experiment_id,
            "Loop_Setup",
            "System",
            "Utility",
            "running",
            f"Equilibration loop will run for {equil_cycles} cycles",
        )

        # --------------- WORKFLOW STEPS ---------------
        # Step 1: Opentrons OT-2 - Run Protocol w/ Custom Labware (Protocol 1)
        log_execution_step(experiment_id, "OT2_Protocol_1_Upload", "Opentrons OT-2", "Execution", "running", "Uploading OT-2 protocol 1 with custom labware")
        labware_path = protocols_folder / "cytiva_96_filterwellplate_1ml.json"
        if not labware_path.exists():
            raise FileNotFoundError(f"Labware file not found: {labware_path}")
        prot_id = ot2.Upload_Protocol_Labware(
            str(opentrons_protocol_1),
            str(labware_path),
            info_server=False,
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        results["steps_completed"].append({"step": 1, "run_id": run_id})
        log_execution_step(experiment_id, "OT2_Protocol_1_Run", "Opentrons OT-2", "Execution", "completed", "Completed OT-2 protocol 1")

        # Step 2-3: UR5 Grab/Place 96DeepWP Storage2 -> Pump_Pos1
        Grab(ur5, "Storage2", "96DeepWP")
        Place(ur5, "Pump_Pos1", "96DeepWP", rehome=False)

        # Step 4: UR5 Place Vacuum Manifold
        Place_Vacuum_Manifold(ur5, rehome=False)

        # Step 5-6: UR5 move Filterplate OT2_Pos1 -> Pump_Pos2
        Grab(ur5, "OT2_Pos1", "Filterplate")
        Place(ur5, "Pump_Pos2", "Filterplate", rehome=False)

        # Step 7: VacuuPump Pump at speed 25 for 30s
        vacuupump.Run_Pump_Speed(25, 30)

        # Step 8: Wait 300s Venting...
        wait(300, "Venting...")

        # Step 9-10: UR5 move Filterplate Pump_Pos2 -> OT2_Pos1 (rehome=True)
        Grab(ur5, "Pump_Pos2", "Filterplate")
        Place(ur5, "OT2_Pos1", "Filterplate", rehome=True)

        # Loop start (loop_1) over Equilibration Cycles
        for i in range(equil_cycles):
            log_execution_step(
                experiment_id,
                f"Loop_Iteration_{i+1}",
                "System",
                "Utility",
                "running",
                f"Equilibration cycle {i+1} of {equil_cycles}",
            )

            # Step 12 inside loop: OT-2 Protocol 2 with custom labware
            labware_path = protocols_folder / "cytiva_96_filterwellplate_1ml.json"
            if not labware_path.exists():
                raise FileNotFoundError(f"Labware file not found: {labware_path}")
            prot_id = ot2.Upload_Protocol_Labware(
                str(opentrons_protocol_2),
                str(labware_path),
                info_server=False,
            )
            run_id = ot2.Run_Protocol(prot_id, info_server=False)

            # Step 13-14: UR5 move Filterplate OT2_Pos1 -> Pump_Pos2
            Grab(ur5, "OT2_Pos1", "Filterplate")
            Place(ur5, "Pump_Pos2", "Filterplate", rehome=False)

            # Step 15: VacuuPump Pump at speed 25 for 30s
            vacuupump.Run_Pump_Speed(25, 30)

            # Step 16: Wait 300s Venting
            wait(300, "Venting")

            # Step 17-18: UR5 move Filterplate Pump_Pos2 -> OT2_Pos1 (rehome=True)
            Grab(ur5, "Pump_Pos2", "Filterplate")
            Place(ur5, "OT2_Pos1", "Filterplate", rehome=True)

        # Step 19: UR5 Remove Vacuum Manifold
        Remove_Vacuum_Manifold(ur5, rehome=False)

        # Step 20-21: UR5 move 96DeepWP Pump_Pos1 -> Storage2 (rehome=True)
        Grab(ur5, "Pump_Pos1", "96DeepWP")
        Place(ur5, "Storage2", "96DeepWP", rehome=True)

        # Step 22: OT-2 Protocol 3 with custom labware
        labware_path = protocols_folder / "cytiva_96_filterwellplate_1ml.json"
        if not labware_path.exists():
            raise FileNotFoundError(f"Labware file not found: {labware_path}")
        prot_id = ot2.Upload_Protocol_Labware(
            str(opentrons_protocol_3),
            str(labware_path),
            info_server=False,
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)

        # Step 23: Pause - Cover Filterplate with Foil, etc.
        pause_for_user(
            "Cover Filterplate with Foil. Remove all other labware. Fresh 300 uL tips in Slot 7. Empty Trash.",
            step_name="Cover_Filterplate_with_Foil",
        )

        # Step 24: OT-2 Protocol 4 with custom labware
        labware_path = protocols_folder / "cytiva_96_filterwellplate_1ml.json"
        if not labware_path.exists():
            raise FileNotFoundError(f"Labware file not found: {labware_path}")
        prot_id = ot2.Upload_Protocol_Labware(
            str(opentrons_protocol_4),
            str(labware_path),
            info_server=False,
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)

        # Step 25: Pause - Remove Foil from Filterplate
        pause_for_user("Remove Foil from Filterplate", step_name="Remove_Foil_from_Filterplate")

        # Step 26-27: UR5 move 96DeepWP Storage1 -> Pump_Pos1
        Grab(ur5, "Storage1", "96DeepWP")
        Place(ur5, "Pump_Pos1", "96DeepWP", rehome=False)

        # Step 28: UR5 Place Vacuum Manifold
        Place_Vacuum_Manifold(ur5, rehome=False)

        # Step 29-30: UR5 move Filterplate OT2_Pos1 -> Pump_Pos2
        Grab(ur5, "OT2_Pos1", "Filterplate")
        Place(ur5, "Pump_Pos2", "Filterplate", rehome=False)

        # Step 31: VacuuPump Pump at speed 25 for 30s
        vacuupump.Run_Pump_Speed(25, 30)

        # Step 32: Wait 300s Venting...
        wait(300, "Venting...")

        # Step 33-34: UR5 move Filterplate Pump_Pos2 -> Storage3
        Grab(ur5, "Pump_Pos2", "Filterplate")
        Place(ur5, "Storage3", "Filterplate", rehome=False)

        # Step 35: UR5 Remove Vacuum Manifold
        Remove_Vacuum_Manifold(ur5, rehome=False)

        # Step 36-37: UR5 move 96DeepWP Pump_Pos1 -> OT2_Pos5
        Grab(ur5, "Pump_Pos1", "96DeepWP")
        Place(ur5, "OT2_Pos5", "96DeepWP", rehome=False)

        # Step 38-39: UR5 move WP1 Storage4 -> OT2_Pos1 (rehome=True)
        Grab(ur5, "Storage4", "WP1")
        Place(ur5, "OT2_Pos1", "WP1", rehome=True)

        # Step 40: OT-2 Protocol 5 (no custom labware)
        prot_id = ot2.Upload_Protocol(str(opentrons_protocol_5), info_server=False)
        run_id = ot2.Run_Protocol(prot_id, info_server=False)

        # Step 41-42: UR5 move 96DeepWP OT2_Pos5 -> Storage1
        Grab(ur5, "OT2_Pos5", "96DeepWP")
        Place(ur5, "Storage1", "96DeepWP", rehome=False)

        # Step 43: UR5 Grab WP1 from OT2_Pos1
        Grab(ur5, "OT2_Pos1", "WP1")

        # Step 44: Tecan Spark Open
        await tecan.open_device()

        # Step 45: UR5 Place WP1 at Tecan
        Place(ur5, "Tecan", "WP1", rehome=False)

        # Step 46: Tecan Spark Close
        await tecan.close_device()

        # Step 47: Tecan Spark Run Measurement (single XML protocol, linked to experiment_id)
        await tecan.load_and_run_xml(str(tecan_protocol_1), experiment_id=experiment_id)

        # Step 48: Tecan Spark Open
        await tecan.open_device()

        # Step 49: UR5 Grab WP1 from Tecan
        Grab(ur5, "Tecan", "WP1")

        # Step 50: Tecan Spark Close
        await tecan.close_device()

        # Step 51: UR5 Place WP1 at Storage4
        Place(ur5, "Storage4", "WP1", rehome=False)

        results["status"] = "completed"
        log_execution_step(
            experiment_id,
            "Experiment_Completion",
            "System",
            "Completion",
            "completed",
            f"Lab 167 experiment {experiment_id} completed successfully!",
        )

    except Exception as e:
        error_msg = f"Lab 167 Error: {str(e)}"
        log_execution_step(experiment_id, "Experiment_Error", "System", "Error", "failed", error_msg)
        results["errors"].append(error_msg)
        results["status"] = "failed"
        raise

    finally:
        # Device cleanup - runs on every path.
        print("Disconnecting Lab 167 devices...")
        log_execution_step(experiment_id, "Device_Cleanup", "System", "Completion", "running", "Disconnecting devices")
        try:
            if ur5 is not None:
                ur5.Disconnect_Robot()
                print("UR5 disconnected.")
            if tecan is not None:
                tecan.disconnect()
                print("Tecan Spark disconnected.")
            if vacuupump is not None:
                vacuupump.disconnect()
                print("VacuuPump disconnected.")
            # Opentrons OT-2 disconnects automatically
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
    """Command line interface for Lab 167 Template 278 workflow"""
    parser = argparse.ArgumentParser(description="Execute automated Lab 167 experiment workflow (Template 278)")
    parser.add_argument("experiment_id", nargs="?", help="Experiment ID")
    parser.add_argument("template_id", nargs="?", help="Template ID for protocol files")
    parser.add_argument("--ur5-ip", default="<UR5_IP>", help="UR5 robot IP address")
    parser.add_argument("--opentrons-ip", default="<OPENTRONS_IP>", help="Opentrons OT-2 IP address")
    parser.add_argument("--vacuupump-ip", default="<VACUUMP_IP>", help="VacuuPump IP address")
    args = parser.parse_args()

    if not args.experiment_id or not args.template_id:
        print("ERROR: experiment_id and template_id are required.")
        parser.print_help()
        return 1

    try:
        asyncio.run(
            run_experiment(
                exp_id=args.experiment_id,
                template_id=args.template_id,
                ur5_ip=args.ur5_ip,
                opentrons_ip=args.opentrons_ip,
                vacuupump_ip=args.vacuupump_ip,
            )
        )
        print("Lab 167 experiment execution completed!")
        return 0
    except Exception as e:
        print(f"ERROR: Lab 167 experiment execution failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
