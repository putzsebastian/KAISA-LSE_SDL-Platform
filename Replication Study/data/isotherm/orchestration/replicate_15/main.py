#!/usr/bin/env python3
"""
Lab 167 Master Control Script - Particle Processing SDL
Coordinates UR5, Opentrons OT-2, Tecan Spark, VacuuPump, DLS Sampler, Zetasizer, Heater-Shaker
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
from Devices_167.DLSInjectionSetup_HTTP import DLS_Injection_Setup
from Devices_167.Zetasizer_HTTP import ZetasizerNano
from Devices_167.HeaterShaker_HTTP import HeaterShakerHTTP

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
                _time.sleep(2)
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
                         vacuupump_ip="<VACUUPUMP_IP>"):
    """Execute Lab 167 automated experiment workflow."""
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

    # Define protocol paths using template_id (explicit filenames from mapping)
    opentrons_protocol_1 = protocols_folder / f"Template_{template_id}_OT_Protocol_1.py"
    opentrons_protocol_2 = protocols_folder / f"Template_{template_id}_OT_Protocol_2.py"
    opentrons_protocol_3 = protocols_folder / f"Template_{template_id}_OT_Protocol_3.py"
    opentrons_protocol_4 = protocols_folder / f"Template_{template_id}_OT_Protocol_4.py"
    opentrons_protocol_5 = protocols_folder / f"Template_{template_id}_OT_Protocol_5.py"
    tecan_protocol_1 = protocols_folder / f"Template_{template_id}_TECAN_Protocol_1.xml"

    experiment_results_path = results_folder / f"Experiment_{experiment_id}_Results.json"

    # Validate protocol files exist (only those that the workflow uses)
    log_execution_step(experiment_id, "Protocol_Validation", "System", "Setup", "running", "Validating protocol files exist")
    required_protocols = [
        opentrons_protocol_1,
        opentrons_protocol_2,
        opentrons_protocol_3,
        opentrons_protocol_4,
        opentrons_protocol_5,
        tecan_protocol_1,
    ]
    for proto in required_protocols:
        if not proto.exists():
            raise FileNotFoundError(f"Protocol file not found: {proto}")
    log_execution_step(experiment_id, "Protocol_Validation", "System", "Setup", "completed", "Protocol files validated")

    results = {
        "experiment_id": experiment_id,
        "template_id":   template_id,
        "lab_id":        "167",
        "lab_name":      "Particle Processing SDL",
        "status":        "running",
        "steps_completed": [],
        "errors": [],
        "file_paths": {
            "opentrons_protocol_1": str(opentrons_protocol_1),
            "opentrons_protocol_2": str(opentrons_protocol_2),
            "opentrons_protocol_3": str(opentrons_protocol_3),
            "opentrons_protocol_4": str(opentrons_protocol_4),
            "opentrons_protocol_5": str(opentrons_protocol_5),
            "tecan_protocol_1":     str(tecan_protocol_1),
            "experiment_results":   str(experiment_results_path),
        },
    }

    # Pre-declare for cleanup - only init the ones the workflow uses
    ur5 = tecan = ot2 = vacuupump = injector = zs = hs = None

    try:
        # Initialize Lab 167 devices actually used in this workflow
        print("Initializing Lab 167 devices...")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "running", "Initializing Lab 167 devices")
        ur5 = UniversalRobot(ur5_ip, "UR5")
        ot2 = Opentrons(opentrons_ip)
        vacuupump = VacuuSelect(vacuupump_ip)
        tecan = TecanSpark(tecan_alias)
        results["steps_completed"].append("devices_initialized")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "completed", "Lab 167 devices initialized")

        # ---------- Reading eLabFTW fields ----------
        exp_json_path = current_folder / f'experiment_{experiment_id}.json'
        with open(exp_json_path, 'r') as f:
            exp_data = json.load(f)

        equil_cycles = int(get_extra_field(exp_data, 'Equilibration Cycles', 1))
        pump_speed = int(get_extra_field(exp_data, 'Pump Speed', 25))
        pump_duration = int(get_extra_field(exp_data, 'Pump Duration', 30))
        venting_time = int(get_extra_field(exp_data, 'Venting time', 300))

        # ---------- WORKFLOW STEPS ----------
        # Step 1: OT-2 run protocol with custom labware (protocol 1)
        log_execution_step(experiment_id, "OT2_Protocol_1_Upload", "Opentrons OT-2", "Execution", "running", "Uploading OT-2 protocol 1 with custom labware")
        labware_path_1 = protocols_folder / "cytiva_96_filterwellplate_1ml.json"
        if not labware_path_1.exists():
            raise FileNotFoundError(f"Labware file not found: {labware_path_1}")
        prot_id = ot2.Upload_Protocol_Labware(
            str(opentrons_protocol_1),
            str(labware_path_1),
            info_server=False,
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        log_execution_step(experiment_id, "OT2_Protocol_1_Run", "Opentrons OT-2", "Execution", "completed", f"OT-2 protocol 1 run started, run_id={run_id}")

        # Step 2: UR5 Grab 96DeepWP from Storage2
        Grab(ur5, 'Storage2', '96DeepWP')
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Storage2", "UR5", "Execution", "completed", "Grabbed 96DeepWP from Storage2")

        # Step 3: UR5 Place 96DeepWP to Pump_Pos1
        Place(ur5, 'Pump_Pos1', '96DeepWP', rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Pump_Pos1", "UR5", "Execution", "completed", "Placed 96DeepWP on Pump_Pos1")

        # Step 4: UR5 Place Vacuum Manifold
        Place_Vacuum_Manifold(ur5, rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Vacuum_Manifold", "UR5", "Execution", "completed", "Placed Vacuum Manifold")

        # Step 5: UR5 Grab Filterplate from OT2_Pos1
        Grab(ur5, 'OT2_Pos1', 'Filterplate')
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1", "UR5", "Execution", "completed", "Grabbed Filterplate from OT2_Pos1")

        # Step 6: UR5 Place Filterplate to Pump_Pos2
        Place(ur5, 'Pump_Pos2', 'Filterplate', rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2", "UR5", "Execution", "completed", "Placed Filterplate on Pump_Pos2")

        # Step 7: VacuuPump run by speed and duration
        vacuupump.Run_Pump_Speed(pump_speed, pump_duration)
        log_execution_step(experiment_id, "VacuuPump_Run", "VacuuPump", "Execution", "completed", f"Ran VacuuPump at {pump_speed}% for {pump_duration}s")

        # Step 8: Utility Wait for venting
        wait(venting_time, "Venting...")

        # Step 9: UR5 Grab Filterplate from Pump_Pos2
        Grab(ur5, 'Pump_Pos2', 'Filterplate')
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2", "UR5", "Execution", "completed", "Grabbed Filterplate from Pump_Pos2")

        # Step 10: UR5 Place Filterplate to OT2_Pos1 with rehome=True
        Place(ur5, 'OT2_Pos1', 'Filterplate', rehome=True)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_OT2_Pos1", "UR5", "Execution", "completed", "Placed Filterplate on OT2_Pos1 with rehome")

        # Loop over Equilibration Cycles (loop_1)
        log_execution_step(experiment_id, "Loop_Setup", "System", "Utility", "running", f"Starting equilibration loop with {equil_cycles} iterations")
        for i in range(equil_cycles):
            log_execution_step(experiment_id, f"Loop_Iteration_{i+1}", "System", "Utility", "running", f"Processing equilibration cycle {i+1} of {equil_cycles}")

            # Step 12: OT-2 Run Protocol with custom labware (protocol 2)
            labware_path_2 = protocols_folder / "cytiva_96_filterwellplate_1ml.json"
            if not labware_path_2.exists():
                raise FileNotFoundError(f"Labware file not found: {labware_path_2}")
            prot_id_2 = ot2.Upload_Protocol_Labware(
                str(opentrons_protocol_2),
                str(labware_path_2),
                info_server=False,
            )
            run_id_2 = ot2.Run_Protocol(prot_id_2, info_server=False)
            log_execution_step(experiment_id, "OT2_Protocol_2_Run", "Opentrons OT-2", "Execution", "completed", f"OT-2 protocol 2 run started for cycle {i+1}, run_id={run_id_2}")

            # Step 13: UR5 Grab Filterplate from OT2_Pos1
            Grab(ur5, 'OT2_Pos1', 'Filterplate')
            log_execution_step(experiment_id, f"UR5_Grab_Filterplate_OT2_Pos1_Cycle_{i+1}", "UR5", "Execution", "completed", f"Grabbed Filterplate from OT2_Pos1 for cycle {i+1}")

            # Step 14: UR5 Place Filterplate to Pump_Pos2
            Place(ur5, 'Pump_Pos2', 'Filterplate', rehome=False)
            log_execution_step(experiment_id, f"UR5_Place_Filterplate_Pump_Pos2_Cycle_{i+1}", "UR5", "Execution", "completed", f"Placed Filterplate on Pump_Pos2 for cycle {i+1}")

            # Step 15: VacuuPump run
            vacuupump.Run_Pump_Speed(pump_speed, pump_duration)
            log_execution_step(experiment_id, f"VacuuPump_Run_Cycle_{i+1}", "VacuuPump", "Execution", "completed", f"Ran VacuuPump at {pump_speed}% for {pump_duration}s in cycle {i+1}")

            # Step 16: Utility Wait for venting (message 'Venting')
            wait(venting_time, "Venting")

            # Step 17: UR5 Grab Filterplate from Pump_Pos2
            Grab(ur5, 'Pump_Pos2', 'Filterplate')
            log_execution_step(experiment_id, f"UR5_Grab_Filterplate_Pump_Pos2_Cycle_{i+1}", "UR5", "Execution", "completed", f"Grabbed Filterplate from Pump_Pos2 for cycle {i+1}")

            # Step 18: UR5 Place Filterplate to OT2_Pos1 with rehome=True
            Place(ur5, 'OT2_Pos1', 'Filterplate', rehome=True)
            log_execution_step(experiment_id, f"UR5_Place_Filterplate_OT2_Pos1_Cycle_{i+1}", "UR5", "Execution", "completed", f"Placed Filterplate on OT2_Pos1 with rehome for cycle {i+1}")

        log_execution_step(experiment_id, "Loop_Complete", "System", "Utility", "completed", f"Completed all {equil_cycles} equilibration cycles")

        # Step 19: UR5 Remove Vacuum Manifold
        Remove_Vacuum_Manifold(ur5, rehome=False)
        log_execution_step(experiment_id, "UR5_Remove_Vacuum_Manifold", "UR5", "Execution", "completed", "Removed Vacuum Manifold")

        # Step 20: UR5 Grab 96DeepWP from Pump_Pos1
        Grab(ur5, 'Pump_Pos1', '96DeepWP')
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Pump_Pos1", "UR5", "Execution", "completed", "Grabbed 96DeepWP from Pump_Pos1")

        # Step 21: UR5 Place 96DeepWP back to Storage2 with rehome=True
        Place(ur5, 'Storage2', '96DeepWP', rehome=True)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Storage2", "UR5", "Execution", "completed", "Placed 96DeepWP back to Storage2 with rehome")

        # Step 22: OT-2 Run Protocol with custom labware (protocol 3)
        labware_path_3 = protocols_folder / "cytiva_96_filterwellplate_1ml.json"
        if not labware_path_3.exists():
            raise FileNotFoundError(f"Labware file not found: {labware_path_3}")
        prot_id_3 = ot2.Upload_Protocol_Labware(
            str(opentrons_protocol_3),
            str(labware_path_3),
            info_server=False,
        )
        run_id_3 = ot2.Run_Protocol(prot_id_3, info_server=False)
        log_execution_step(experiment_id, "OT2_Protocol_3_Run", "Opentrons OT-2", "Execution", "completed", f"OT-2 protocol 3 run started, run_id={run_id_3}")

        # Step 23: Utility Pause - Cover Filterplate with Foil...
        pause_for_user("Cover Filterplate with Foil. Remove all other labware. Fresh 300 uL tips in Slot 7. Empty Trash.")

        # Step 24: OT-2 Run Protocol with custom labware (protocol 4)
        labware_path_4 = protocols_folder / "cytiva_96_filterwellplate_1ml.json"
        if not labware_path_4.exists():
            raise FileNotFoundError(f"Labware file not found: {labware_path_4}")
        prot_id_4 = ot2.Upload_Protocol_Labware(
            str(opentrons_protocol_4),
            str(labware_path_4),
            info_server=False,
        )
        run_id_4 = ot2.Run_Protocol(prot_id_4, info_server=False)
        log_execution_step(experiment_id, "OT2_Protocol_4_Run", "Opentrons OT-2", "Execution", "completed", f"OT-2 protocol 4 run started, run_id={run_id_4}")

        # Step 25: Utility Pause - Remove Foil from Filterplate
        pause_for_user("Remove Foil from Filterplate")

        # Step 26: UR5 Grab 96DeepWP from Storage1
        Grab(ur5, 'Storage1', '96DeepWP')
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Storage1", "UR5", "Execution", "completed", "Grabbed 96DeepWP from Storage1")

        # Step 27: UR5 Place 96DeepWP to Pump_Pos1
        Place(ur5, 'Pump_Pos1', '96DeepWP', rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Pump_Pos1_2", "UR5", "Execution", "completed", "Placed 96DeepWP on Pump_Pos1 (second phase)")

        # Step 28: UR5 Place Vacuum Manifold
        Place_Vacuum_Manifold(ur5, rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Vacuum_Manifold_2", "UR5", "Execution", "completed", "Placed Vacuum Manifold (second phase)")

        # Step 29: UR5 Grab Filterplate from OT2_Pos1
        Grab(ur5, 'OT2_Pos1', 'Filterplate')
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1_2", "UR5", "Execution", "completed", "Grabbed Filterplate from OT2_Pos1 (second phase)")

        # Step 30: UR5 Place Filterplate to Pump_Pos2
        Place(ur5, 'Pump_Pos2', 'Filterplate', rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2_2", "UR5", "Execution", "completed", "Placed Filterplate on Pump_Pos2 (second phase)")

        # Step 31: VacuuPump run
        vacuupump.Run_Pump_Speed(pump_speed, pump_duration)
        log_execution_step(experiment_id, "VacuuPump_Run_2", "VacuuPump", "Execution", "completed", f"Ran VacuuPump at {pump_speed}% for {pump_duration}s (second phase)")

        # Step 32: Utility Wait for venting
        wait(venting_time, "Venting...")

        # Step 33: UR5 Grab Filterplate from Pump_Pos2
        Grab(ur5, 'Pump_Pos2', 'Filterplate')
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2_2", "UR5", "Execution", "completed", "Grabbed Filterplate from Pump_Pos2 (second phase)")

        # Step 34: UR5 Place Filterplate to Storage3
        Place(ur5, 'Storage3', 'Filterplate', rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_Storage3", "UR5", "Execution", "completed", "Placed Filterplate in Storage3")

        # Step 35: UR5 Remove Vacuum Manifold
        Remove_Vacuum_Manifold(ur5, rehome=False)
        log_execution_step(experiment_id, "UR5_Remove_Vacuum_Manifold_2", "UR5", "Execution", "completed", "Removed Vacuum Manifold (second phase)")

        # Step 36: UR5 Grab 96DeepWP from Pump_Pos1
        Grab(ur5, 'Pump_Pos1', '96DeepWP')
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Pump_Pos1_2b", "UR5", "Execution", "completed", "Grabbed 96DeepWP from Pump_Pos1 (second phase)")

        # Step 37: UR5 Place 96DeepWP to OT2_Pos5
        Place(ur5, 'OT2_Pos5', '96DeepWP', rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_OT2_Pos5", "UR5", "Execution", "completed", "Placed 96DeepWP on OT2_Pos5")

        # Step 38: UR5 Grab WP1 from Storage4
        Grab(ur5, 'Storage4', 'WP1')
        log_execution_step(experiment_id, "UR5_Grab_WP1_Storage4", "UR5", "Execution", "completed", "Grabbed WP1 from Storage4")

        # Step 39: UR5 Place WP1 to OT2_Pos1 with rehome=True
        Place(ur5, 'OT2_Pos1', 'WP1', rehome=True)
        log_execution_step(experiment_id, "UR5_Place_WP1_OT2_Pos1", "UR5", "Execution", "completed", "Placed WP1 on OT2_Pos1 with rehome")

        # Step 40: OT-2 Run Protocol without custom labware (protocol 5)
        prot_id_5 = ot2.Upload_Protocol(str(opentrons_protocol_5), info_server=False)
        run_id_5 = ot2.Run_Protocol(prot_id_5, info_server=False)
        log_execution_step(experiment_id, "OT2_Protocol_5_Run", "Opentrons OT-2", "Execution", "completed", f"OT-2 protocol 5 run started, run_id={run_id_5}")

        # Step 41: UR5 Grab 96DeepWP from OT2_Pos5
        Grab(ur5, 'OT2_Pos5', '96DeepWP')
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_OT2_Pos5", "UR5", "Execution", "completed", "Grabbed 96DeepWP from OT2_Pos5")

        # Step 42: UR5 Place 96DeepWP to Storage1
        Place(ur5, 'Storage1', '96DeepWP', rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Storage1_Final", "UR5", "Execution", "completed", "Placed 96DeepWP back to Storage1")

        # Step 43: UR5 Grab WP1 from OT2_Pos1
        Grab(ur5, 'OT2_Pos1', 'WP1')
        log_execution_step(experiment_id, "UR5_Grab_WP1_OT2_Pos1_Final", "UR5", "Execution", "completed", "Grabbed WP1 from OT2_Pos1 for Tecan measurement")

        # Step 44: Tecan Spark Open
        await tecan.open_device()
        log_execution_step(experiment_id, "Tecan_Open_1", "Tecan Spark", "Execution", "completed", "Opened Tecan device")

        # Step 45: UR5 Place WP1 to Tecan
        Place(ur5, 'Tecan', 'WP1', rehome=False)
        log_execution_step(experiment_id, "UR5_Place_WP1_Tecan", "UR5", "Execution", "completed", "Placed WP1 on Tecan")

        # Step 46: Tecan Spark Close
        await tecan.close_device()
        log_execution_step(experiment_id, "Tecan_Close_1", "Tecan Spark", "Execution", "completed", "Closed Tecan device")

        # Step 47: Tecan Spark Run Measurement using XML protocol 1
        await tecan.load_and_run_xml(str(tecan_protocol_1), experiment_id=experiment_id)
        log_execution_step(experiment_id, "Tecan_Run_Measurement", "Tecan Spark", "Execution", "completed", "Tecan measurement run completed")

        # Step 48: Tecan Spark Open
        await tecan.open_device()
        log_execution_step(experiment_id, "Tecan_Open_2", "Tecan Spark", "Execution", "completed", "Opened Tecan device (post-measurement)")

        # Step 49: UR5 Grab WP1 from Tecan
        Grab(ur5, 'Tecan', 'WP1')
        log_execution_step(experiment_id, "UR5_Grab_WP1_Tecan", "UR5", "Execution", "completed", "Grabbed WP1 from Tecan")

        # Step 50: Tecan Spark Close
        await tecan.close_device()
        log_execution_step(experiment_id, "Tecan_Close_2", "Tecan Spark", "Execution", "completed", "Closed Tecan device (post-measurement)")

        # Step 51: UR5 Place WP1 back to Storage4
        Place(ur5, 'Storage4', 'WP1', rehome=False)
        log_execution_step(experiment_id, "UR5_Place_WP1_Storage4_Final", "UR5", "Execution", "completed", "Placed WP1 back to Storage4")

        results["status"] = "completed"
        log_execution_step(experiment_id, "Experiment_Completion", "System", "Completion", "completed",
                           f"Lab 167 experiment {experiment_id} completed successfully!")

    except Exception as e:
        error_msg = f"Lab 167 Error in step {len(results['steps_completed']) + 1}: {str(e)}"
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
    """Command line interface for Lab 167"""
    parser = argparse.ArgumentParser(description='Execute automated Lab 167 experiment workflow')
    parser.add_argument('experiment_id', nargs='?', help='Experiment ID')
    parser.add_argument('template_id', nargs='?', help='Template ID for protocol files')
    parser.add_argument('--ur5-ip', default="<UR5_IP>", help='UR5 robot IP address')
    parser.add_argument('--opentrons-ip', default="<OPENTRONS_IP>", help='Opentrons OT-2 IP address')
    parser.add_argument('--vacuupump-ip', default="<VACUUPUMP_IP>", help='VacuuPump IP address')
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
