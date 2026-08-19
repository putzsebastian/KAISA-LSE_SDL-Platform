from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol that closes the heater-shaker latch, optionally sets temperature, shakes for a placeholder time at a placeholder speed, then stops and opens the latch.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (must remain literal for external templating)
INCUBATION_TIME = '[[INCUBATION_TIME]]'
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    """Detect whether a placeholder token is still unreplaced in the file.
    Build the bracket markers by repetition so the string literal '[[' never appears
    in the source code (see guidelines for templating tokens).
    """
    s_str = str(s).strip()
    return s_str.startswith('[' * 2) and s_str.endswith(']' * 2)


def parse_float(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return float(default)
    return float(s)


def parse_int(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load labware onto the heater-shaker module. The labware is a custom definition
    # and may not be present in the simulation environment; provide a simulation fallback.
    try:
        plate_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # If the exception is not about a missing definition, re-raise so real errors surface.
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition "cytiva_96_filterwellplate_1ml" '
                         'not available; using a 96-well standard plate as a SIMULATION fallback.')
        plate_on_hs = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware (tiprack)
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Parse placeholders to usable values for the simulation. Real run will have tokens
    # substituted by the external system before upload to the robot.
    # Choose conservative/big defaults so the simulation exercises heating/shaking behavior.
    incubation_temp = parse_float(INCUBATION_TEMPERATURE, default=42.0)
    incubation_time_min = parse_float(INCUBATION_TIME, default=60.0)
    shaker_speed_rpm = parse_int(SHAKER_SPEED_INCUBATION, default=1200)

    protocol.comment('Heater-Shaker module and labware loaded.')

    # Step 1: Close latch and conditionally set temperature
    protocol.comment('Closing heater-shaker labware latch.')
    hs_mod.close_labware_latch()

    if incubation_temp >= 37.0:
        protocol.comment(f'Setting heater-shaker target temperature to {incubation_temp} °C and waiting to reach target.')
        # Set target temperature and wait until reached
        hs_mod.set_target_temperature(incubation_temp)
        hs_mod.wait_for_temperature()
    else:
        protocol.comment('Incubation temperature below 37 °C; skipping heating step as requested.')

    # Step 2: Start shaking and incubate for the requested time
    protocol.comment(f'Starting shaking at {shaker_speed_rpm} rpm for {incubation_time_min} minutes.')
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)

    # Delay for incubation time (convert minutes to seconds)
    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking, then open latch
    protocol.comment('Stopping shaker and heater, then opening labware latch.')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()

    protocol.comment('Protocol complete.')
