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
    except Exception as e:
        print(f" Pause API unavailable ({str(e)}), using terminal input as fallback")
        input("Press Enter to continue...")
        log_execution_step(experiment_id, "Pause_Resume", "System", "Utility", "running", "User resumed execution (fallback)")


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
        print(" Timer '{timer_name}' was not started")
        return None


async def run_experiment(exp_id=None, template_id=None,
                         ur5_ip="<UR5_IP>",
                         opentrons_ip="<OPENTRONS_IP>",
                         vacuupump_ip="<VACUUPUMP_IP>",
                         tecan_alias="<TECAN_ALIAS>"):
    """Execute Lab 167 automated experiment workflow."""
    global experiment_id
    experiment_id = exp_id

    # Setup paths
    current_folder = Path(__file__).parent
    protocols_folder = current_folder / "protocols"
    data_folder = current_folder / "data"
    results_folder = current_folder / "results"
    for d in (protocols_folder, data_folder, results_folder):
        d.mkdir(exist_ok=True)

    # Define protocol paths using template_id (278 in this workflow)
    # Opentrons protocols
    ot2_protocol_1_path = protocols_folder / f"Template_{template_id}_OT_Protocol_1.py"
    ot2_protocol_2_path = protocols_folder / f"Template_{template_id}_OT_Protocol_2.py"
    ot2_protocol_3_path = protocols_folder / f"Template_{template_id}_OT_Protocol_3.py"
    ot2_protocol_4_path = protocols_folder / f"Template_{template_id}_OT_Protocol_4.py"
    ot2_protocol_5_path = protocols_folder / f"Template_{template_id}_OT_Protocol_5.py"

    # Tecan protocol
    tecan_protocol_1_path = protocols_folder / f"Template_{template_id}_TECAN_Protocol_1.xml"

    experiment_results_path = results_folder / f"Experiment_{experiment_id}_Results.json"

    # Validate protocol files exist (only those that the workflow uses)
    log_execution_step(experiment_id, "Protocol_Validation", "System", "Setup", "running", "Validating protocol files exist")
    required_protocols = [
        ("Opentrons_1", ot2_protocol_1_path),
        ("Opentrons_2", ot2_protocol_2_path),
        ("Opentrons_3", ot2_protocol_3_path),
        ("Opentrons_4", ot2_protocol_4_path),
        ("Opentrons_5", ot2_protocol_5_path),
        ("Tecan_1", tecan_protocol_1_path),
    ]
    for name, path in required_protocols:
        if not path.exists():
            raise FileNotFoundError(f"Required protocol not found ({name}): {path}")
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
        # Initialize Lab 167 devices actually used in this workflow
        print("Initializing Lab 167 devices...")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "running", "Initializing Lab 167 devices")
        ur5 = UniversalRobot(ur5_ip, "UR5")
        ot2 = Opentrons(opentrons_ip)
        vacuupump = VacuuSelect(vacuupump_ip)
        tecan = TecanSpark(tecan_alias)
        # DLS, Zetasizer, Heater-Shaker are not used in this workflow
        results["steps_completed"].append("devices_initialized")
        log_execution_step(experiment_id, "Device_Initialization", "System", "Setup", "completed", "Lab 167 devices initialized")

        # ---------- Reading eLabFTW fields ----------
        exp_json_path = current_folder / f"experiment_{experiment_id}.json"
        if exp_json_path.exists():
            with open(exp_json_path, "r") as f:
                exp_data = json.load(f)
        else:
            exp_data = {}

        equil_cycles = int(get_extra_field(exp_data, "Equilibration Cycles", 1))
        log_execution_step(experiment_id, "Parameters_Loaded", "System", "Setup", "running", f"Equilibration Cycles: {equil_cycles}")

        # ---------- WORKFLOW STEPS ----------
        # 1. Opentrons OT-2 - Run Protocol 1 with Custom Labware
        log_execution_step(experiment_id, "OT2_Protocol_1", "Opentrons OT-2", "Execution", "running", "Running OT-2 protocol 1 with custom labware")
        labware_file_1 = protocols_folder / "cytiva_96_filterwellplate_1ml.json"
        if not labware_file_1.exists():
            raise FileNotFoundError(f"Labware file not found: {labware_file_1}")
        prot_id = ot2.Upload_Protocol_Labware(
            str(ot2_protocol_1_path),
            str(labware_file_1),
            info_server=False,
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        results["steps_completed"].append("ot2_protocol_1_completed")
        log_execution_step(experiment_id, "OT2_Protocol_1", "Opentrons OT-2", "Execution", "completed", f"OT-2 protocol 1 run id: {run_id}")

        # 2-3. UR5 - Move 96DeepWP from Storage2 to Pump_Pos1
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Storage2", "UR5", "Execution", "running", "Grabbing 96DeepWP from Storage2")
        Grab(ur5, "Storage2", "96DeepWP")
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Storage2", "UR5", "Execution", "completed", "96DeepWP grabbed from Storage2")

        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Pump_Pos1", "UR5", "Execution", "running", "Placing 96DeepWP on Pump_Pos1")
        Place(ur5, "Pump_Pos1", "96DeepWP", rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Pump_Pos1", "UR5", "Execution", "completed", "96DeepWP placed on Pump_Pos1")

        # 4. UR5 - Place Vacuum Manifold
        log_execution_step(experiment_id, "UR5_Place_Vacuum_Manifold", "UR5", "Execution", "running", "Placing Vacuum Manifold")
        Place_Vacuum_Manifold(ur5, rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Vacuum_Manifold", "UR5", "Execution", "completed", "Vacuum Manifold placed")

        # 5-6. UR5 - Move Filterplate from OT2_Pos1 to Pump_Pos2
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1", "UR5", "Execution", "running", "Grabbing Filterplate from OT2_Pos1")
        Grab(ur5, "OT2_Pos1", "Filterplate")
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1", "UR5", "Execution", "completed", "Filterplate grabbed from OT2_Pos1")

        log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2", "UR5", "Execution", "running", "Placing Filterplate on Pump_Pos2")
        Place(ur5, "Pump_Pos2", "Filterplate", rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2", "UR5", "Execution", "completed", "Filterplate placed on Pump_Pos2")

        # 7. VacuuPump - Pump (speed 25, duration 30s)
        log_execution_step(experiment_id, "VacuuPump_Initial_Pump", "VacuuPump", "Execution", "running", "Running VacuuPump at 25% for 30s")
        vacuupump.Run_Pump_Speed(25, 30)
        log_execution_step(experiment_id, "VacuuPump_Initial_Pump", "VacuuPump", "Execution", "completed", "VacuuPump initial run completed")

        # 8. Utility - Wait 300s (Venting...)
        wait(300, "Venting...")

        # 9-10. UR5 - Move Filterplate back to OT2_Pos1 (with rehome True)
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2_1", "UR5", "Execution", "running", "Grabbing Filterplate from Pump_Pos2")
        Grab(ur5, "Pump_Pos2", "Filterplate")
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2_1", "UR5", "Execution", "completed", "Filterplate grabbed from Pump_Pos2")

        log_execution_step(experiment_id, "UR5_Place_Filterplate_OT2_Pos1_1", "UR5", "Execution", "running", "Placing Filterplate on OT2_Pos1 with rehome")
        Place(ur5, "OT2_Pos1", "Filterplate", rehome=True)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_OT2_Pos1_1", "UR5", "Execution", "completed", "Filterplate placed on OT2_Pos1")

        # Loop over Equilibration Cycles
        log_execution_step(experiment_id, "Loop_Setup", "System", "Utility", "running", f"Starting equilibration loop with {equil_cycles} iterations")
        for i in range(equil_cycles):
            log_execution_step(experiment_id, f"Loop_Iteration_{i+1}", "System", "Utility", "running", f"Equilibration cycle {i+1} of {equil_cycles}")

            # 12. Opentrons OT-2 - Run Protocol 2 with Custom Labware
            log_execution_step(experiment_id, "OT2_Protocol_2", "Opentrons OT-2", "Execution", "running", "Running OT-2 protocol 2 with custom labware")
            if not labware_file_1.exists():
                raise FileNotFoundError(f"Labware file not found: {labware_file_1}")
            prot_id = ot2.Upload_Protocol_Labware(
                str(ot2_protocol_2_path),
                str(labware_file_1),
                info_server=False,
            )
            run_id = ot2.Run_Protocol(prot_id, info_server=False)
            log_execution_step(experiment_id, "OT2_Protocol_2", "Opentrons OT-2", "Execution", "completed", f"OT-2 protocol 2 run id: {run_id}")

            # 13-14. UR5 - Move Filterplate OT2_Pos1 -> Pump_Pos2
            log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1_Loop", "UR5", "Execution", "running", "Grabbing Filterplate from OT2_Pos1 (loop)")
            Grab(ur5, "OT2_Pos1", "Filterplate")
            log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1_Loop", "UR5", "Execution", "completed", "Filterplate grabbed from OT2_Pos1 (loop)")

            log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2_Loop", "UR5", "Execution", "running", "Placing Filterplate on Pump_Pos2 (loop)")
            Place(ur5, "Pump_Pos2", "Filterplate", rehome=False)
            log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2_Loop", "UR5", "Execution", "completed", "Filterplate placed on Pump_Pos2 (loop)")

            # 15. VacuuPump - Pump (speed 25, duration 30s)
            log_execution_step(experiment_id, "VacuuPump_Loop_Pump", "VacuuPump", "Execution", "running", "Running VacuuPump at 25% for 30s (loop)")
            vacuupump.Run_Pump_Speed(25, 30)
            log_execution_step(experiment_id, "VacuuPump_Loop_Pump", "VacuuPump", "Execution", "completed", "VacuuPump loop run completed")

            # 16. Utility - Wait 300s (Venting)
            wait(300, "Venting")

            # 17-18. UR5 - Move Filterplate Pump_Pos2 -> OT2_Pos1 (rehome True)
            log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2_Loop_Back", "UR5", "Execution", "running", "Grabbing Filterplate from Pump_Pos2 (loop back)")
            Grab(ur5, "Pump_Pos2", "Filterplate")
            log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2_Loop_Back", "UR5", "Execution", "completed", "Filterplate grabbed from Pump_Pos2 (loop back)")

            log_execution_step(experiment_id, "UR5_Place_Filterplate_OT2_Pos1_Loop_Back", "UR5", "Execution", "running", "Placing Filterplate on OT2_Pos1 with rehome (loop back)")
            Place(ur5, "OT2_Pos1", "Filterplate", rehome=True)
            log_execution_step(experiment_id, "UR5_Place_Filterplate_OT2_Pos1_Loop_Back", "UR5", "Execution", "completed", "Filterplate placed on OT2_Pos1 (loop back)")

        log_execution_step(experiment_id, "Loop_Complete", "System", "Utility", "completed", f"Completed all {equil_cycles} equilibration cycles")

        # 19. UR5 - Remove Vacuum Manifold
        log_execution_step(experiment_id, "UR5_Remove_Vacuum_Manifold", "UR5", "Execution", "running", "Removing Vacuum Manifold")
        Remove_Vacuum_Manifold(ur5, rehome=False)
        log_execution_step(experiment_id, "UR5_Remove_Vacuum_Manifold", "UR5", "Execution", "completed", "Vacuum Manifold removed")

        # 20-21. UR5 - Move 96DeepWP Pump_Pos1 -> Storage2 (rehome True)
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Pump_Pos1_1", "UR5", "Execution", "running", "Grabbing 96DeepWP from Pump_Pos1")
        Grab(ur5, "Pump_Pos1", "96DeepWP")
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Pump_Pos1_1", "UR5", "Execution", "completed", "96DeepWP grabbed from Pump_Pos1")

        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Storage2_1", "UR5", "Execution", "running", "Placing 96DeepWP back to Storage2 with rehome")
        Place(ur5, "Storage2", "96DeepWP", rehome=True)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Storage2_1", "UR5", "Execution", "completed", "96DeepWP placed in Storage2")

        # 22. Opentrons OT-2 - Run Protocol 3 with Custom Labware
        log_execution_step(experiment_id, "OT2_Protocol_3", "Opentrons OT-2", "Execution", "running", "Running OT-2 protocol 3 with custom labware")
        if not labware_file_1.exists():
            raise FileNotFoundError(f"Labware file not found: {labware_file_1}")
        prot_id = ot2.Upload_Protocol_Labware(
            str(ot2_protocol_3_path),
            str(labware_file_1),
            info_server=False,
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        log_execution_step(experiment_id, "OT2_Protocol_3", "Opentrons OT-2", "Execution", "completed", f"OT-2 protocol 3 run id: {run_id}")

        # 23. Utility - Pause (Cover Filterplate with Foil...)
        pause_for_user("Cover Filterplate with Foil. Remove all other labware. Fresh 300 uL tips in Slot 7. Empty Trash.", step_name="Cover_Filterplate_With_Foil")

        # 24. Opentrons OT-2 - Run Protocol 4 with Custom Labware
        log_execution_step(experiment_id, "OT2_Protocol_4", "Opentrons OT-2", "Execution", "running", "Running OT-2 protocol 4 with custom labware")
        if not labware_file_1.exists():
            raise FileNotFoundError(f"Labware file not found: {labware_file_1}")
        prot_id = ot2.Upload_Protocol_Labware(
            str(ot2_protocol_4_path),
            str(labware_file_1),
            info_server=False,
        )
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        log_execution_step(experiment_id, "OT2_Protocol_4", "Opentrons OT-2", "Execution", "completed", f"OT-2 protocol 4 run id: {run_id}")

        # 25. Utility - Pause (Remove Foil from Filterplate)
        pause_for_user("Remove Foil from Filterplate", step_name="Remove_Foil_From_Filterplate")

        # 26-27. UR5 - Move 96DeepWP Storage1 -> Pump_Pos1
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Storage1", "UR5", "Execution", "running", "Grabbing 96DeepWP from Storage1")
        Grab(ur5, "Storage1", "96DeepWP")
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Storage1", "UR5", "Execution", "completed", "96DeepWP grabbed from Storage1")

        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Pump_Pos1_2", "UR5", "Execution", "running", "Placing 96DeepWP on Pump_Pos1 (second stage)")
        Place(ur5, "Pump_Pos1", "96DeepWP", rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Pump_Pos1_2", "UR5", "Execution", "completed", "96DeepWP placed on Pump_Pos1 (second stage)")

        # 28. UR5 - Place Vacuum Manifold
        log_execution_step(experiment_id, "UR5_Place_Vacuum_Manifold_2", "UR5", "Execution", "running", "Placing Vacuum Manifold (second stage)")
        Place_Vacuum_Manifold(ur5, rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Vacuum_Manifold_2", "UR5", "Execution", "completed", "Vacuum Manifold placed (second stage)")

        # 29-30. UR5 - Move Filterplate OT2_Pos1 -> Pump_Pos2
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1_2", "UR5", "Execution", "running", "Grabbing Filterplate from OT2_Pos1 (second stage)")
        Grab(ur5, "OT2_Pos1", "Filterplate")
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_OT2_Pos1_2", "UR5", "Execution", "completed", "Filterplate grabbed from OT2_Pos1 (second stage)")

        log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2_2", "UR5", "Execution", "running", "Placing Filterplate on Pump_Pos2 (second stage)")
        Place(ur5, "Pump_Pos2", "Filterplate", rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_Pump_Pos2_2", "UR5", "Execution", "completed", "Filterplate placed on Pump_Pos2 (second stage)")

        # 31. VacuuPump - Pump (speed 25, duration 30s)
        log_execution_step(experiment_id, "VacuuPump_Second_Pump", "VacuuPump", "Execution", "running", "Running VacuuPump at 25% for 30s (second stage)")
        vacuupump.Run_Pump_Speed(25, 30)
        log_execution_step(experiment_id, "VacuuPump_Second_Pump", "VacuuPump", "Execution", "completed", "VacuuPump second run completed")

        # 32. Utility - Wait 300s (Venting...)
        wait(300, "Venting...")

        # 33-34. UR5 - Move Filterplate Pump_Pos2 -> Storage3
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2_2", "UR5", "Execution", "running", "Grabbing Filterplate from Pump_Pos2 (second stage)")
        Grab(ur5, "Pump_Pos2", "Filterplate")
        log_execution_step(experiment_id, "UR5_Grab_Filterplate_Pump_Pos2_2", "UR5", "Execution", "completed", "Filterplate grabbed from Pump_Pos2 (second stage)")

        log_execution_step(experiment_id, "UR5_Place_Filterplate_Storage3", "UR5", "Execution", "running", "Placing Filterplate in Storage3")
        Place(ur5, "Storage3", "Filterplate", rehome=False)
        log_execution_step(experiment_id, "UR5_Place_Filterplate_Storage3", "UR5", "Execution", "completed", "Filterplate placed in Storage3")

        # 35. UR5 - Remove Vacuum Manifold
        log_execution_step(experiment_id, "UR5_Remove_Vacuum_Manifold_2", "UR5", "Execution", "running", "Removing Vacuum Manifold (second stage)")
        Remove_Vacuum_Manifold(ur5, rehome=False)
        log_execution_step(experiment_id, "UR5_Remove_Vacuum_Manifold_2", "UR5", "Execution", "completed", "Vacuum Manifold removed (second stage)")

        # 36-37. UR5 - Move 96DeepWP Pump_Pos1 -> OT2_Pos5
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Pump_Pos1_2_back", "UR5", "Execution", "running", "Grabbing 96DeepWP from Pump_Pos1 (to OT2_Pos5)")
        Grab(ur5, "Pump_Pos1", "96DeepWP")
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_Pump_Pos1_2_back", "UR5", "Execution", "completed", "96DeepWP grabbed from Pump_Pos1 (to OT2_Pos5)")

        log_execution_step(experiment_id, "UR5_Place_96DeepWP_OT2_Pos5", "UR5", "Execution", "running", "Placing 96DeepWP on OT2_Pos5")
        Place(ur5, "OT2_Pos5", "96DeepWP", rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_OT2_Pos5", "UR5", "Execution", "completed", "96DeepWP placed on OT2_Pos5")

        # 38-39. UR5 - Move WP1 Storage4 -> OT2_Pos1 (rehome True)
        log_execution_step(experiment_id, "UR5_Grab_WP1_Storage4", "UR5", "Execution", "running", "Grabbing WP1 from Storage4")
        Grab(ur5, "Storage4", "WP1")
        log_execution_step(experiment_id, "UR5_Grab_WP1_Storage4", "UR5", "Execution", "completed", "WP1 grabbed from Storage4")

        log_execution_step(experiment_id, "UR5_Place_WP1_OT2_Pos1", "UR5", "Execution", "running", "Placing WP1 on OT2_Pos1 with rehome")
        Place(ur5, "OT2_Pos1", "WP1", rehome=True)
        log_execution_step(experiment_id, "UR5_Place_WP1_OT2_Pos1", "UR5", "Execution", "completed", "WP1 placed on OT2_Pos1")

        # 40. Opentrons OT-2 - Run Protocol 5 (no custom labware method)
        log_execution_step(experiment_id, "OT2_Protocol_5", "Opentrons OT-2", "Execution", "running", "Running OT-2 protocol 5")
        prot_id = ot2.Upload_Protocol(str(ot2_protocol_5_path), info_server=False)
        run_id = ot2.Run_Protocol(prot_id, info_server=False)
        log_execution_step(experiment_id, "OT2_Protocol_5", "Opentrons OT-2", "Execution", "completed", f"OT-2 protocol 5 run id: {run_id}")

        # 41-42. UR5 - Move 96DeepWP OT2_Pos5 -> Storage1
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_OT2_Pos5", "UR5", "Execution", "running", "Grabbing 96DeepWP from OT2_Pos5")
        Grab(ur5, "OT2_Pos5", "96DeepWP")
        log_execution_step(experiment_id, "UR5_Grab_96DeepWP_OT2_Pos5", "UR5", "Execution", "completed", "96DeepWP grabbed from OT2_Pos5")

        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Storage1_Final", "UR5", "Execution", "running", "Placing 96DeepWP back to Storage1")
        Place(ur5, "Storage1", "96DeepWP", rehome=False)
        log_execution_step(experiment_id, "UR5_Place_96DeepWP_Storage1_Final", "UR5", "Execution", "completed", "96DeepWP placed in Storage1")

        # 43. UR5 - Grab WP1 from OT2_Pos1
        log_execution_step(experiment_id, "UR5_Grab_WP1_OT2_Pos1_For_Tecan", "UR5", "Execution", "running", "Grabbing WP1 from OT2_Pos1 for Tecan")
        Grab(ur5, "OT2_Pos1", "WP1")
        log_execution_step(experiment_id, "UR5_Grab_WP1_OT2_Pos1_For_Tecan", "UR5", "Execution", "completed", "WP1 grabbed from OT2_Pos1 for Tecan")

        # 44. Tecan Spark - Open
        log_execution_step(experiment_id, "Tecan_Open_1", "Tecan Spark", "Execution", "running", "Opening Tecan device")
        await tecan.open_device()
        log_execution_step(experiment_id, "Tecan_Open_1", "Tecan Spark", "Execution", "completed", "Tecan device opened")

        # 45. UR5 - Place WP1 into Tecan
        log_execution_step(experiment_id, "UR5_Place_WP1_Tecan", "UR5", "Execution", "running", "Placing WP1 into Tecan")
        Place(ur5, "Tecan", "WP1", rehome=False)
        log_execution_step(experiment_id, "UR5_Place_WP1_Tecan", "UR5", "Execution", "completed", "WP1 placed into Tecan")

        # 46. Tecan Spark - Close
        log_execution_step(experiment_id, "Tecan_Close_1", "Tecan Spark", "Execution", "running", "Closing Tecan device")
        await tecan.close_device()
        log_execution_step(experiment_id, "Tecan_Close_1", "Tecan Spark", "Execution", "completed", "Tecan device closed")

        # 47. Tecan Spark - Run Measurement
        log_execution_step(experiment_id, "Tecan_Run_Measurement", "Tecan Spark", "Execution", "running", "Running Tecan measurement")
        await tecan.load_and_run_xml(str(tecan_protocol_1_path), experiment_id=experiment_id)
        log_execution_step(experiment_id, "Tecan_Run_Measurement", "Tecan Spark", "Execution", "completed", "Tecan measurement completed")

        # 48. Tecan Spark - Open
        log_execution_step(experiment_id, "Tecan_Open_2", "Tecan Spark", "Execution", "running", "Opening Tecan device (post-measurement)")
        await tecan.open_device()
        log_execution_step(experiment_id, "Tecan_Open_2", "Tecan Spark", "Execution", "completed", "Tecan device opened (post-measurement)")

        # 49. UR5 - Grab WP1 from Tecan
        log_execution_step(experiment_id, "UR5_Grab_WP1_Tecan_Back", "UR5", "Execution", "running", "Grabbing WP1 from Tecan")
        Grab(ur5, "Tecan", "WP1")
        log_execution_step(experiment_id, "UR5_Grab_WP1_Tecan_Back", "UR5", "Execution", "completed", "WP1 grabbed from Tecan")

        # 50. Tecan Spark - Close
        log_execution_step(experiment_id, "Tecan_Close_2", "Tecan Spark", "Execution", "running", "Closing Tecan device (post-measurement)")
        await tecan.close_device()
        log_execution_step(experiment_id, "Tecan_Close_2", "Tecan Spark", "Execution", "completed", "Tecan device closed (post-measurement)")

        # 51. UR5 - Place WP1 back to Storage4
        log_execution_step(experiment_id, "UR5_Place_WP1_Storage4_Final", "UR5", "Execution", "running", "Placing WP1 back to Storage4")
        Place(ur5, "Storage4", "WP1", rehome=False)
        log_execution_step(experiment_id, "UR5_Place_WP1_Storage4_Final", "UR5", "Execution", "completed", "WP1 placed back to Storage4")

        results["status"] = "completed"
        log_execution_step(experiment_id, "Experiment_Completion", "System", "Completion", "completed", f"Lab 167 experiment {experiment_id} completed successfully!")

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
            log_execution_step(experiment_id, "Device_Cleanup", "System", "Completion", "warning", f"Error during cleanup: {cleanup_e}")

        # Save execution results
        try:
            with open(experiment_results_path, "w") as f:
                json.dump(results, f, indent=2)
            print(f"Results saved to: {experiment_results_path}")
            log_execution_step(experiment_id, "Save_Results", "System", "Completion", "completed", f"Results saved to: {experiment_results_path}")
        except Exception as save_e:
            print(f"ERROR: Error saving results: {save_e}")
            log_execution_step(experiment_id, "Save_Results", "System", "Completion", "failed", f"Error saving results: {save_e}")

    return results


def main():
    """Command line interface for Lab 167"""
    parser = argparse.ArgumentParser(description="Execute automated Lab 167 experiment workflow")
    parser.add_argument("experiment_id", nargs="?", help="Experiment ID")
    parser.add_argument("template_id", nargs="?", help="Template ID for protocol files")
    parser.add_argument("--ur5-ip", default="<UR5_IP>", help="UR5 robot IP address")
    parser.add_argument("--opentrons-ip", default="<OPENTRONS_IP>", help="Opentrons OT-2 IP address")
    parser.add_argument("--vacuupump-ip", default="<VACUUPUMP_IP>", help="VacuuPump IP address")
    parser.add_argument("--tecan-alias", default="<TECAN_ALIAS>", help="Tecan Spark device alias")
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
                tecan_alias=args.tecan_alias,
            )
        )
        print("Lab 167 experiment execution completed!")
        return 0
    except Exception as e:
        print(f"ERROR: Lab 167 experiment execution failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
