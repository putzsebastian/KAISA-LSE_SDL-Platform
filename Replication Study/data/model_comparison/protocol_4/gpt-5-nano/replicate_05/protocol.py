from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol with placeholders for incubation temperature, time and shaker speed',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol):
    # 1) Load Heater-Shaker module on slot 1 and ensure latch is closed
    hs_mod = protocol.load_module("heaterShakerModuleV1", location=1)
    hs_mod.close_labware_latch()

    # 2) Load labware onto the Heater-Shaker using a Cytiva 96-well plate, with a simulation fallback
    try:
        incubation_labware = hs_mod.load_labware("cytiva_96_filterwellplate_1ml")
    except Exception as exc:
        if 'not found' not in str(exc) and 'Not Found' not in str(exc) and 'Labware' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; using a standard 96-well plate for simulation.')
        incubation_labware = hs_mod.load_labware("nest_96_wellplate_200ul_flat")

    # 3) Load the Opentrons 96 Tip Rack 300 uL in slot 7
    tiprack = protocol.load_labware("opentrons_96_tiprack_300ul", 7)

    # 4) Load P300 pipettes on the specified mounts (right: 1-channel; left: 8-channel)
    p300s = protocol.load_instrument("p300_single_gen2", mount="right", tip_racks=[tiprack])
    p300m = protocol.load_instrument("p300_multi_gen2", mount="left", tip_racks=[tiprack])

    # Placeholders for user templating
    INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    INCUBATION_TIME = '[[INCUBATION_TIME]]'
    SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'

    def _to_float(val):
        try:
            return float(val)
        except Exception:
            return None

    incubation_temp_val = _to_float(INCUBATION_TEMPERATURE)
    incubation_time_min = _to_float(INCUBATION_TIME)
    shaker_rpm = _to_float(SHAKER_SPEED_INCUBATION)

    # Step 1: Close latch already done. If incubation temperature is >= 37, set target temp
    if incubation_temp_val is not None and incubation_temp_val >= 37:
        hs_mod.set_target_temperature(incubation_temp_val)
        # Optional: wait for temperature to be reached if desired
        # hs_mod.wait_for_temperature()

    # Step 2: Start shaking for the given duration at the given speed
    if shaker_rpm is not None:
        hs_mod.set_and_wait_for_shake_speed(int(shaker_rpm))
        if incubation_time_min is not None:
            protocol.delay(incubation_time_min * 60)

    # Step 3: Stop heating and shaking, then open latch
    try:
        hs_mod.deactivate_shaker()
    except Exception:
        pass
    try:
        if incubation_temp_val is not None:
            # Return to ambient temperature after incubation
            hs_mod.set_target_temperature(25)
    except Exception:
        pass

    hs_mod.open_labware_latch()
