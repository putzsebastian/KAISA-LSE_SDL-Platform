from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol for incubation on Heater-Shaker with placeholders',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings for template substitution)
INCUBATION_TIME = '[[INCUBATION_TIME]]'            # minutes
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # degrees C
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'  # rpm


def _unreplaced(s: str) -> bool:
    """Detect unreplaced placeholder tokens during simulation.
    Build the brackets so the literal '[[' does not appear in the file elsewhere.
    """
    return str(s).startswith('[' * 2) and str(s).endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder or return a simulation fallback.

    The _unreplaced() check uses built-up brackets so the literal token does appear in
    the source file and is therefore substitutable by the wizard.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware
    # Load the custom filter plate on the heater-shaker module; use a simulation fallback if not available
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Parse placeholders for simulation (and for real run after substitution these will parse to numeric values)
    # Defaults chosen to exercise heating and shaking behavior during simulation.
    incub_time_min = parse_scalar(INCUBATION_TIME, 60, cast=float)
    incub_temp_c = parse_scalar(INCUBATION_TEMPERATURE, 37, cast=float)
    shaker_rpm = parse_scalar(SHAKER_SPEED_INCUBATION, 1200, cast=int)

    # Step 1: Close labware latch and (conditionally) set temperature
    protocol.comment('Step 1: Close heater-shaker latch and optionally set temperature')
    hs_mod.close_labware_latch()

    if incub_temp_c >= 37:
        protocol.comment(f'Setting heater target temperature to {incub_temp_c} °C')
        # Prefer a blocking set-and-wait so the temperature is reached before proceeding
        try:
            hs_mod.set_and_wait_for_temperature(incub_temp_c)
        except Exception:
            # Fallback to non-blocking API if needed
            hs_mod.set_target_temperature(incub_temp_c)
            hs_mod.wait_for_temperature()
    else:
        protocol.comment(f'Incubation temperature {incub_temp_c} °C is below threshold 37 °C — skipping heating')

    # Step 2: Shake for the requested time at the requested speed
    protocol.comment(f'Step 2: Start shaking at {shaker_rpm} rpm for {incub_time_min} minutes')
    hs_mod.close_labware_latch()  # idempotent: ensure latch closed before shaking

    # Start shaking (blocking until speed reached)
    hs_mod.set_and_wait_for_shake_speed(shaker_rpm)

    # Hold the shake for the requested incubation time (convert minutes to seconds)
    protocol.delay(seconds=incub_time_min * 60)

    # Step 3: Stop heating and shaking, then open latch
    protocol.comment('Step 3: Stop heating and shaking, then open latch')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()

    protocol.comment('Protocol complete.')
