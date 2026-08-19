from opentrons import protocol_api

metadata = {
    'protocolName': '[protocol name by user]',
    'author': '[user name]',
    'description': "[what is the protocol about]"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

def run(protocol: protocol_api.ProtocolContext):
    # Placeholders (templated as strings; replace with concrete values via your templating system)
    PLACEHOLDER_INCUBATION_TIME = '120'            # minutes
    PLACEHOLDER_INCUBATION_TEMPERATURE = '25'  # deg C
    PLACEHOLDER_SHAKER_SPEED_INCUBATION = '1300'  # rpm

    # Helper: parse placeholders to numeric values with safe defaults if not replaced
    def parse_placeholder(value, default, cast):
        try:
            s = str(value).strip()
            # If it still looks like a template token (e.g., [[TOKEN]]), use the default
            if s.startswith('[' * 2) and s.endswith(']' * 2):
                return default
            return cast(s)
        except Exception:
            return default

    # Safe defaults are only used if placeholders are not replaced prior to running
    incubation_time_min = parse_placeholder(PLACEHOLDER_INCUBATION_TIME, 0.1, float)  # minutes
    incubation_temp_c = parse_placeholder(PLACEHOLDER_INCUBATION_TEMPERATURE, 25, float)  # deg C
    shaker_speed_rpm = parse_placeholder(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 500, int)  # rpm

    # Modules (Slot 1): Heater-Shaker Module V1
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # Custom labware expected: 'cytiva_96_filterwellplate_1ml'
    # If not available on the system, fall back to a standard deep-well plate for simulation
    try:
        hs_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        hs_plate = hs_mod.load_labware('nest_96_wellplate_2ml_deep')

    # Labware (Slot 7): Tiprack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', '7')

    # Pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Step 1: Close labware latch and conditionally set temperature
    protocol.comment('Step 1: Close Heater-Shaker labware latch and set temperature if 25 >= 37 C.')
    hs_mod.close_labware_latch()
    if incubation_temp_c >= 37:
        hs_mod.set_target_temperature(incubation_temp_c)
        protocol.comment(f"Heater-Shaker target temperature set to {incubation_temp_c} C.")
    else:
        protocol.comment('Temperature step skipped because 25 < 37 C or not provided.')

    # Step 2: Shake for 2 minutes at 1300 rpm
    protocol.comment('Step 2: Shaking at 1300 rpm for 2 minutes.')
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking, then open labware latch
    protocol.comment('Step 3: Stop shaking and heating, then open Heater-Shaker labware latch.')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()