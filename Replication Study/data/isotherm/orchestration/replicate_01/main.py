#!/usr/bin/env python3
"""
Lab 167 Master Control Script - Particle Processing SDL
Workflow for Template 278: coordinated OT-2, UR5, VacuuPump, and Tecan Spark operations
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
from Devices_167.DLSInjectionSetup_HTTP import DLS_Injection_Setup
from Devices_167.Zetasizer_HTTP import ZetasizerNano
from Devices_167.HeaterShaker_HTTP import HeaterShakerHTTP

# Initialize experiment_id as module-level variable for logging
experiment_id = None


def get_extra_field(exp_data, field_name, default=None):
    """Extract value from eLabFTW extra_fields structure."""
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
            print(f" Pause registered in system. Waiting for user to resume via UI...")
            while True:
                status_response = requests.get(
                    f"http://<APP_SERVER>:5001/api/experiments/{experiment_id}/pause/status",
                    timeout=5,
                )
                if status_response.status_code == 200:
                    status_data = status_response.json()
                    if not status_data.get("is_paused", True):
                        print(f" User resumed execution via UI")
                        log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution via UI")
                        break
                time.sleep(2)
        else:
            print(f" Could not register pause in system, using terminal input as fallback")
            input("Press Enter to continue...")
            log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution (fallback)")
    except Exception as e:
        print(f" Pause API unavailable ({str(e)}), using terminal input as fallback")
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
        print(f" Timer '{timer_name}' was not started")
        return None


async def run_experiment(exp_id=None, template_id=None,
                         ur5_ip="<UR5_IP>",
                         opentrons_ip="<OPENTRONS_IP>",
                         vacuupump_ip="<VACUUMPUMP_IP>"):
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

    # Define protocol paths using template_id (278 for this workflow)
    # Mapping from user description
    ot2_protocol_1_path = protocols_folder / f"Template_{template_id}_OT_Protocol_1.py"  # step 1
    ot2_protocol_2_path = protocols_folder / f"Template_{template_id}_OT_Protocol_2.py"  # step 12 (inside loop)
    ot2_protocol_3_path = protocols_folder / f"Template_{template_id}_OT_Protocol_3.py"  # step 23
    ot2_protocol_4_path = protocols_folder / f"Template_{template_id}_OT_Protocol_4.py"  # step 25
    ot2_protocol_5_path = protocols_folder / f"Template_{template_id}_OT_Protocol_5.py"  # step 41
    tecan_protocol_1_path = protocols_folder / f"Template_{template_id}_TECAN_Protocol_1.xml"  # step 48

    experiment_results_path = results_folder / f"Experiment_{experiment_id}_Results.json"

    # Validate protocol files exist (only those that the workflow uses)
    log_execution_step(experiment_id, "Protocol_Validation", "System", "Setup", "running", "Validating protocol files exist")
    required_protocols = [
        ot2_protocol_1_path,
        ot2_protocol_2_path,
        ot2_protocol_3_path,
        ot2_protocol_4_path,
        ot2_protocol_5_path,
        tecan_protocol_1_path,
    ]
    for path in required_protocols:
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
            "ot2_protocol_1": str(ot2_protocol_1_path),
            "ot2_protocol_2": str(ot2_protocol_2_path),
            "ot2_protocol_3": str(ot2_protocol_3_path),
            "ot2_protocol_4": str(ot2_protocol_4_path),
            "ot2_protocol_5": str(ot2_protocol_5_path),
            "tecan_protocol_1": str(tecan_protocol_1_path),
            "experiment_results": str(experiment_results_path),
        },
    }

    # Pre-declare for cleanup - only init the ones the workflow uses
    ur5 = tecan = ot2 = vacuupump = injector = zs = hs = None

    try:
        # Initialize devices actually used in this workflow: UR5, OT-2, VacuuPump, Tecan
        print("Initializing Lab 167 devices...")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "running", "Initializing Lab 167 devices")
        ur5 = UniversalRobot(ur5_ip, "UR5")
        ot2 = Opentrons(opentrons_ip)
        vacuupump = VacuuSelect(vacuupump_ip)
        tecan = TecanSpark(tecan_alias)
        results["steps_completed"].append("devices_initialized")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "completed", "Lab 167 devices initialized")

        # Read eLabFTW fields (Equilibration Cycles for loop)
        current_folder = Path(__file__).parent
        exp_json_path = current_folder / f"experiment_{experiment_id}.json"
        if not exp_json_path.exists():
            raise FileNotFoundError(f"Experiment JSON not found: {exp_json_path}")
        with open(exp_json_path, 'r') as f:
            exp_data = json.load(f)

        equil_cycles = int(get_extra_field(exp_data, 'Equilibration Cycles', 0))
        log_execution_step(experiment_id, "Loop_Setup", "System", "Utility", "running", f"Equilibration Cycles: {equil_cycles}")

        # ------------------------------------------------------------
        # WORKFLOW IMPLEMENTATION
        # ------------------------------------------------------------

        # Step 1: Opentrons OT-2 - Run Protocol w/ Custom Labware (Template_278_OT_Protocol_1.py)
        log_execution_step(experiment_id, "OT2_Protocol_1", "Opentrons OT-2", "OT2", "running", "Running OT-2 protocol 1 with custom labware")
        labware_path = protocols_folder / "cytiva_96_filterwellplate_1ml.json"
        if not labware_path.exists():
            raise FileNotFoundError(f"Labware file not found: {labware_path}")
        prot_id = ot2.Upload_Protocol_Labware(
            str(ot2_protocol_1_path),
            str(labware_path),
            info_server=False,
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        results["steps_completed"].append("ot2_protocol_1_completed")
        log_execution_step(experiment_id, "OT2_Protocol_1", "Opentrons OT-2", "OT2", "completed", f"OT-2 protocol 1 completed (run_id={run_id})")

        # Step 2: UR5 Grab 96DeepWP from Storage2
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Storage2", "UR5", "UR5", "running", "Grabbing 96DeepWP from Storage2")
        Grab(ur5, 'Storage2', '96DeepWP')
        results["steps_completed"].append("ur5_grab_96DeepWP_storage2")

        # Step 3: UR5 Place 96DeepWP at Pump_Pos1 (rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Pump_Pos1", "UR5", "UR5", "running", "Placing 96DeepWP at Pump_Pos1")
        Place(ur5, 'Pump_Pos1', '96DeepWP', rehome=False)
        results["steps_completed"].append("ur5_place_96DeepWP_pump_pos1")

        # Step 4: UR5 Place_Vacuum_Manifold (rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Vacuum_Manifold", "UR5", "UR5", "running", "Placing Vacuum Manifold")
        Place_Vacuum_Manifold(ur5, rehome=False)
        results["steps_completed"].append("ur5_place_vacuum_manifold")

        # Step 5: UR5 Grab Filterplate from OT2_Pos1
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1", "UR5", "UR5", "running", "Grabbing Filterplate from OT2_Pos1")
        Grab(ur5, 'OT2_Pos1', 'Filterplate')
        results["steps_completed"].append("ur5_grab_filterplate_ot2_pos1")

        # Step 6: UR5 Place Filterplate at Pump_Pos2 (rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2", "UR5", "UR5", "running", "Placing Filterplate at Pump_Pos2")
        Place(ur5, 'Pump_Pos2', 'Filterplate', rehome=False)
        results["steps_completed"].append("ur5_place_filterplate_pump_pos2_initial")

        # Step 7: VacuuPump Pump speed 25 duration 30
        log_execution_step(experiment_id, "VacuuPump_Run_Initial", "VacuuPump", "Pump", "running", "Running pump at 25% for 30s")
        vacuupump.Run_Pump_Speed(25, 30)
        results["steps_completed"].append("vacuupump_run_initial")

        # Step 8: Utility Wait 300s, message "Venting..."
        wait(300, "Venting...")
        results["steps_completed"].append("wait_venting_initial")

        # Step 9: UR5 Grab Filterplate from Pump_Pos2
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2_AfterInitial", "UR5", "UR5", "running", "Grabbing Filterplate from Pump_Pos2")
        Grab(ur5, 'Pump_Pos2', 'Filterplate')
        results["steps_completed"].append("ur5_grab_filterplate_pump_pos2_after_initial")

        # Step 10: UR5 Place Filterplate at OT2_Pos1 (rehome=True)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_OT2_Pos1_AfterInitial", "UR5", "UR5", "running", "Placing Filterplate at OT2_Pos1 and rehoming")
        Place(ur5, 'OT2_Pos1', 'Filterplate', rehome=True)
        results["steps_completed"].append("ur5_place_filterplate_ot2_pos1_after_initial")

        # ------------------------------------------------------------
        # Loop start (loop_1) - Equilibration Cycles from eLabFTW
        # Steps 12-18 are inside this loop
        # ------------------------------------------------------------
        log_execution_step(experiment_id, "Loop_Equilibration_Start", "System", "Utility", "running", f"Starting equilibration loop with {equil_cycles} cycles")

        for i in range(equil_cycles):
            log_execution_step(experiment_id, f"Loop_Iteration_{i+1}", "System", "Utility", "running", f"Equilibration cycle {i+1} of {equil_cycles}")

            # Step 12: Opentrons OT-2 - Run Protocol w/ Custom Labware (Template_278_OT_Protocol_2.py)
            log_execution_step(experiment_id, f"OT2_Protocol_2_Cycle_{i+1}", "Opentrons OT-2", "OT2", "running", f"Running OT-2 protocol 2 with custom labware, cycle {i+1}")
            prot_id = ot2.Upload_Protocol_Labware(
                str(ot2_protocol_2_path),
                str(labware_path),
                info_server=False,
            )
            run_id = ot2.Run_Protocol(prot_id, info_server=False)
            results["steps_completed"].append(f"ot2_protocol_2_cycle_{i+1}_completed")

            # Step 13: UR5 Grab Filterplate from OT2_Pos1
            log_execution_step(experiment_id, f"UR5_Grab_Filterplate_OT2_Pos1_Cycle_{i+1}", "UR5", "UR5", "running", f"Grabbing Filterplate from OT2_Pos1, cycle {i+1}")
            Grab(ur5, 'OT2_Pos1', 'Filterplate')

            # Step 14: UR5 Place Filterplate at Pump_Pos2 (rehome=False)
            log_execution_step(experiment_id, f"UR5_Place_Filterplate_Pump_Pos2_Cycle_{i+1}", "UR5", "UR5", "running", f"Placing Filterplate at Pump_Pos2, cycle {i+1}")
            Place(ur5, 'Pump_Pos2', 'Filterplate', rehome=False)

            # Step 15: VacuuPump Pump speed 25 duration 30
            log_execution_step(experiment_id, f"VacuuPump_Run_Cycle_{i+1}", "VacuuPump", "Pump", "running", f"Running pump at 25% for 30s, cycle {i+1}")
            vacuupump.Run_Pump_Speed(25, 30)

            # Step 16: Utility Wait 300s, message "Venting"
            wait(300, "Venting")

            # Step 17: UR5 Grab Filterplate from Pump_Pos2
            log_execution_step(experiment_id, f"UR5_Grab_Filterplate_Pump_Pos2_Cycle_{i+1}", "UR5", "UR5", "running", f"Grabbing Filterplate from Pump_Pos2, cycle {i+1}")
            Grab(ur5, 'Pump_Pos2', 'Filterplate')

            # Step 18: UR5 Place Filterplate at OT2_Pos1 (rehome=True)
            log_execution_step(experiment_id, f"UR5_Place_Filterplate_OT2_Pos1_Cycle_{i+1}", "UR5", "UR5", "running", f"Placing Filterplate at OT2_Pos1 and rehoming, cycle {i+1}")
            Place(ur5, 'OT2_Pos1', 'Filterplate', rehome=True)

        log_execution_step(experiment_id, "Loop_Equilibration_Complete", "System", "Utility", "completed", f"Completed {equil_cycles} equilibration cycles")

        # Step 19: UR5 Remove_Vacuum_Manifold (rehome=False)
        log_execution_step(experiment_id, "UR5_Remove_Vacuum_Manifold", "UR5", "UR5", "running", "Removing Vacuum Manifold")
        Remove_Vacuum_Manifold(ur5, rehome=False)
        results["steps_completed"].append("ur5_remove_vacuum_manifold_first")

        # Step 20: UR5 Grab 96DeepWP from Pump_Pos1
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Pump_Pos1_First", "UR5", "UR5", "running", "Grabbing 96DeepWP from Pump_Pos1")
        Grab(ur5, 'Pump_Pos1', '96DeepWP')

        # Step 21: UR5 Place 96DeepWP at Storage2 (rehome=True)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Storage2_First", "UR5", "UR5", "running", "Placing 96DeepWP at Storage2 and rehoming")
        Place(ur5, 'Storage2', '96DeepWP', rehome=True)

        # Step 22: Opentrons OT-2 - Run Protocol w/ Custom Labware (Template_278_OT_Protocol_3.py)
        log_execution_step(experiment_id, "OT2_Protocol_3", "Opentrons OT-2", "OT2", "running", "Running OT-2 protocol 3 with custom labware")
        prot_id = ot2.Upload_Protocol_Labware(
            str(ot2_protocol_3_path),
            str(labware_path),
            info_server=False,
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        results["steps_completed"].append("ot2_protocol_3_completed")

        # Step 23: Utility Pause (Cover Filterplate with Foil...)
        pause_for_user("Cover Filterplate with Foil. Remove all other labware. Fresh 300 uL tips in Slot 7. Empty Trash.",
                       step_name="Cover_Filterplate_with_Foil")
        results["steps_completed"].append("pause_cover_filterplate_with_foil")

        # Step 24: Opentrons OT-2 - Run Protocol w/ Custom Labware (Template_278_OT_Protocol_4.py)
        log_execution_step(experiment_id, "OT2_Protocol_4", "Opentrons OT-2", "OT2", "running", "Running OT-2 protocol 4 with custom labware")
        prot_id = ot2.Upload_Protocol_Labware(
            str(ot2_protocol_4_path),
            str(labware_path),
            info_server=False,
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        results["steps_completed"].append("ot2_protocol_4_completed")

        # Step 25: Utility Pause (Remove Foil from Filterplate)
        pause_for_user("Remove Foil from Filterplate", step_name="Remove_Foil_from_Filterplate")
        results["steps_completed"].append("pause_remove_foil")

        # Step 26: UR5 Grab 96DeepWP from Storage1
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Storage1", "UR5", "UR5", "running", "Grabbing 96DeepWP from Storage1")
        Grab(ur5, 'Storage1', '96DeepWP')

        # Step 27: UR5 Place 96DeepWP at Pump_Pos1 (rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Pump_Pos1_Second", "UR5", "UR5", "running", "Placing 96DeepWP at Pump_Pos1")
        Place(ur5, 'Pump_Pos1', '96DeepWP', rehome=False)

        # Step 28: UR5 Place_Vacuum_Manifold (rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Vacuum_Manifold_Second", "UR5", "UR5", "running", "Placing Vacuum Manifold (second time)")
        Place_Vacuum_Manifold(ur5, rehome=False)

        # Step 29: UR5 Grab Filterplate from OT2_Pos1
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1_Second", "UR5", "UR5", "running", "Grabbing Filterplate from OT2_Pos1 (second block)")
        Grab(ur5, 'OT2_Pos1', 'Filterplate')

        # Step 30: UR5 Place Filterplate at Pump_Pos2 (rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2_Second", "UR5", "UR5", "running", "Placing Filterplate at Pump_Pos2 (second block)")
        Place(ur5, 'Pump_Pos2', 'Filterplate', rehome=False)

        # Step 31: VacuuPump Pump speed 25 duration 30
        log_execution_step(experiment_id, "VacuuPump_Run_Second", "VacuuPump", "Pump", "running", "Running pump at 25% for 30s (second block)")
        vacuupump.Run_Pump_Speed(25, 30)

        # Step 32: Utility Wait 300s, message "Venting..."
        wait(300, "Venting...")

        # Step 33: UR5 Grab Filterplate from Pump_Pos2
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2_SecondBlock", "UR5", "UR5", "running", "Grabbing Filterplate from Pump_Pos2 (second block)")
        Grab(ur5, 'Pump_Pos2', 'Filterplate')

        # Step 34: UR5 Place Filterplate at Storage3 (rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_Storage3", "UR5", "UR5", "running", "Placing Filterplate at Storage3")
        Place(ur5, 'Storage3', 'Filterplate', rehome=False)

        # Step 35: UR5 Remove_Vacuum_Manifold (rehome=False)
        log_execution_step(experiment_id, "UR5_Remove_Vacuum_Manifold_Second", "UR5", "UR5", "running", "Removing Vacuum Manifold (second time)")
        Remove_Vacuum_Manifold(ur5, rehome=False)

        # Step 36: UR5 Grab 96DeepWP from Pump_Pos1
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Pump_Pos1_SecondBlock", "UR5", "UR5", "running", "Grabbing 96DeepWP from Pump_Pos1 (second block)")
        Grab(ur5, 'Pump_Pos1', '96DeepWP')

        # Step 37: UR5 Place 96DeepWP at OT2_Pos5 (rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_OT2_Pos5", "UR5", "UR5", "running", "Placing 96DeepWP at OT2_Pos5")
        Place(ur5, 'OT2_Pos5', '96DeepWP', rehome=False)

        # Step 38: UR5 Grab WP1 from Storage4
        log_execution_step(experiment_id, "UR5_Grab_WP1_Storage4", "UR5", "UR5", "running", "Grabbing WP1 from Storage4")
        Grab(ur5, 'Storage4', 'WP1')

        # Step 39: UR5 Place WP1 at OT2_Pos1 (rehome=True)
        log_execution_step(experiment_id, "UR5_Place_WP1_OT2_Pos1", "UR5", "UR5", "running", "Placing WP1 at OT2_Pos1 and rehoming")
        Place(ur5, 'OT2_Pos1', 'WP1', rehome=True)

        # Step 40: Opentrons OT-2 - Run Protocol (no custom labware, Template_278_OT_Protocol_5.py)
        log_execution_step(experiment_id, "OT2_Protocol_5", "Opentrons OT-2", "OT2", "running", "Running OT-2 protocol 5 (no custom labware)")
        prot_id = ot2.Upload_Protocol(str(ot2_protocol_5_path), info_server=False)
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        results["steps_completed"].append("ot2_protocol_5_completed")

        # Step 41: UR5 Grab 96DeepWP from OT2_Pos5
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_OT2_Pos5", "UR5", "UR5", "running", "Grabbing 96DeepWP from OT2_Pos5")
        Grab(ur5, 'OT2_Pos5', '96DeepWP')

        # Step 42: UR5 Place 96DeepWP at Storage1 (rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Storage1_Final", "UR5", "UR5", "running", "Placing 96DeepWP at Storage1 (final)")
        Place(ur5, 'Storage1', '96DeepWP', rehome=False)

        # Step 43: UR5 Grab WP1 from OT2_Pos1
        log_execution_step(experiment_id, "UR5_Grab_WP1_OT2_Pos1", "UR5", "UR5", "running", "Grabbing WP1 from OT2_Pos1")
        Grab(ur5, 'OT2_Pos1', 'WP1')

        # Step 44: Tecan Spark Open
        log_execution_step(experiment_id, "Tecan_Open_1", "Tecan Spark", "Tecan", "running", "Opening Tecan device")
        await tecan.open_device()

        # Step 45: UR5 Place WP1 at Tecan (rehome=False)
        log_execution_step(experiment_id, "UR5_Place_WP1_Tecan", "UR5", "UR5", "running", "Placing WP1 at Tecan")
        Place(ur5, 'Tecan', 'WP1', rehome=False)

        # Step 46: Tecan Spark Close
        log_execution_step(experiment_id, "Tecan_Close_1", "Tecan Spark", "Tecan", "running", "Closing Tecan device")
        await tecan.close_device()

        # Step 47: Tecan Spark Run Measurement (Template_278_TECAN_Protocol_1.xml)
        log_execution_step(experiment_id, "Tecan_Run_Measurement_1", "Tecan Spark", "Tecan", "running", "Running Tecan measurement 1")
        await tecan.load_and_run_xml(str(tecan_protocol_1_path), experiment_id=experiment_id)
        results["steps_completed"].append("tecan_measurement_1_completed")

        # Step 48: Tecan Spark Open
        log_execution_step(experiment_id, "Tecan_Open_2", "Tecan Spark", "Tecan", "running", "Opening Tecan device (post-measurement)")
        await tecan.open_device()

        # Step 49: UR5 Grab WP1 from Tecan
        log_execution_step(experiment_id, "UR5_Grab_WP1_Tecan", "UR5", "UR5", "running", "Grabbing WP1 from Tecan")
        Grab(ur5, 'Tecan', 'WP1')

        # Step 50: Tecan Spark Close
        log_execution_step(experiment_id, "Tecan_Close_2", "Tecan Spark", "Tecan", "running", "Closing Tecan device after retrieval")
        await tecan.close_device()

        # Step 51: UR5 Place WP1 at Storage4 (rehome=False)
        log_execution_step(experiment_id, "UR5_Place_WP1_Storage4_Final", "UR5", "UR5", "running", "Placing WP1 at Storage4 (final)")
        Place(ur5, 'Storage4', 'WP1', rehome=False)

        # Mark experiment completed
        results["status"] = "completed"
        log_execution_step(experiment_id, "Experiment_Completion", "System", "Completion", "completed",
                           f"Lab 167 experiment {experiment_id} completed successfully!")

    except Exception as e:
        error_msg = f"Lab 167 Error: {str(e)}"
        log_execution_step(experiment_id, "Experiment_Error", "System", "Error", "failed", error_msg)
        results["errors"].append(error_msg)
        results["status"] = "failed"
        raise

    finally:
        # Device cleanup
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
            if injector is not None:
                injector.disconnect()
                print("DLS Injector disconnected.")
            if zs is not None:
                zs.disconnect()
                print("Zetasizer disconnected.")
            if hs is not None:
                hs.disconnect()
                print("Heater-Shaker disconnected.")
            # Opentrons OT-2 disconnects automatically
        except Exception as cleanup_e:
            print(f"WARNING: Error during cleanup: {cleanup_e}")
            log_execution_step(experiment_id, "Device_Cleanup", "System", "Completion", "warning",
                               f"Error during cleanup: {cleanup_e}")

        # Save execution results
        try:
            with open(experiment_results_path, 'w') as f:
                json.dump(results, f, indent=2)
            print(f"Results saved to: {experiment_results_path}")
            log_execution_step(experiment_id, "Save_Results", "System", "Completion", "completed",
                               f"Results saved to: {experiment_results_path}")
        except Exception as save_e:
            print(f"ERROR: Error saving results: {save_e}")
            log_execution_step(experiment_id, "Save_Results", "System", "Completion", "failed",
                               f"Error saving results: {save_e}")

    return results


def main():
    """Command line interface for Lab 167 Template 278 workflow"""
    parser = argparse.ArgumentParser(description='Execute automated Lab 167 experiment workflow (Template 278)')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID')
    parser.add_argument('template_id', nargs='?', help='Template ID for protocol files')
    parser.add_argument('--ur5-ip', default="<UR5_IP>", help='UR5 robot IP address')
    parser.add_argument('--opentrons-ip', default="<OPENTRONS_IP>", help='Opentrons OT-2 IP address')
    parser.add_argument('--vacuupump-ip', default="<VACUUMPUMP_IP>", help='VacuuPump IP address')
    args = parser.parse_args()

    if not args.experiment_id or not args.template_id:
        print("ERROR: experiment_id and template_id are required.")
        parser.print_help()
        return 1

    try:
        asyncio.run(run_experiment(
            exp_id=args.experiment_id,
            template_id=args.template_id,
            ur5_ip=args.ur5_ip,
            opentrons_ip=args.opentrons_ip,
            vacuupump_ip=args.vacuupump_ip,
        ))
        print(f"Lab 167 experiment execution completed!")
        return 0
    except Exception as e:
        print(f"ERROR: Lab 167 experiment execution failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
