from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Heater Shaker Incubation',
    'author': 'User',
    'description': 'Heater shaker incubation with placeholders for time, temperature, and speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s):
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # --- Parameters from placeholders ---
    # Use large defaults for simulation only; real values are substituted into the placeholders.
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, 60.0)  # minutes (worst-case large default)
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, 50.0)  # degC
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 1000.0)  # rpm (worst-case)

    # --- Modules ---
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # --- Labware (custom filter plate directly on module) ---
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # --- Labware: tip rack in slot 7 ---
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # --- Pipettes ---
    # Right mount: P300 Single-Channel GEN2
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    # Left mount: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # NOTE: No pipetting steps are performed in this protocol; pipettes are loaded
    # to match the requested deck configuration only.

    # STEP 1: Close labware latch and conditionally set temperature
    protocol.comment('Step 1: Preparing Heater-Shaker for incubation.')
    hs_mod.close_labware_latch()

    # Set temperature only if placeholder incubation temperature is >= 37 °C
    if incubation_temp_c >= 37.0:
        protocol.comment(f'Setting Heater-Shaker temperature to {incubation_temp_c} C (placeholder-based).')
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment(
            f'Skipping temperature set because placeholder incubation temperature '
            f'({incubation_temp_c} C) is below 37 C.'
        )

    # STEP 2: Shake the Heater-Shaker for the incubation time at the placeholder speed
    protocol.comment('Step 2: Starting shaking for incubation.')
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.comment(
        f'Incubating at {shaker_speed_rpm} rpm for {incubation_time_min} minutes (placeholders).'
    )
    protocol.delay(minutes=incubation_time_min)

    # STEP 3: Stop heating and shaking and open labware latch
    protocol.comment('Step 3: Stopping shaking and heating, opening latch.')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()

    protocol.comment('Templated Heater-Shaker incubation complete.')
