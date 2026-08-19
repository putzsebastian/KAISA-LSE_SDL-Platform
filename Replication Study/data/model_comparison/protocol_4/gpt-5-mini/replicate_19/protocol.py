from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Incubation and shaking on Heater-Shaker Module with placeholders'
}
requirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}

# Placeholder tokens (literal strings for external substitution)
INCUBATION_TIME = '[[INCUBATION_TIME]]'
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    '''Detect whether a placeholder token has not been replaced yet.
    Build the brackets by repetition so the literal string '[[' does not appear in the file
    (see protocol authoring guidelines).
    '''
    return str(s).startswith('[' * 2) and str(s).endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    '''Parse a scalar placeholder into the requested type for simulation.

    If the token is unreplaced (simulation time), return the provided default. Otherwise
    cast the value and return it.
    '''
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # ---- Modules ----
    hs = protocol.load_module(module_name='heaterShakerModuleV1', location=1)

    # ---- Labware ----
    # Load the (custom) Cytiva 96 Filterwell Plate directly onto the heater-shaker module.
    # The simulator does not contain custom labware, so wrap in a fallback as required.
    try:
        plate_on_hs = hs.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # If the error is not a missing definition, re-raise so real problems surface.
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well deep plate as a SIMULATION fallback only.')
        plate_on_hs = hs.load_labware('nest_96_wellplate_2ml_deep')

    # Tip rack in slot 7
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # ---- Pipettes ----
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # ---- Parse placeholders (use simulation-friendly defaults) ----
    # Defaults chosen to exercise heating and shaking in the simulator.
    incubation_time_min = parse_scalar(INCUBATION_TIME, default=30.0, cast=float)
    incubation_temp_c = parse_scalar(INCUBATION_TEMPERATURE, default=42.0, cast=float)
    shaker_speed_rpm = parse_scalar(SHAKER_SPEED_INCUBATION, default=1200, cast=int)

    protocol.comment(f'Parsed incubation_time_min={incubation_time_min} min, '
                     f'incubation_temp_c={incubation_temp_c} °C, '
                     f'shaker_speed_rpm={shaker_speed_rpm} rpm')

    # ---- Step 1: Close labware latch and set temperature conditionally ----
    # The requirement: close the labware latch and set temperature if temperature >= 37.
    # Otherwise skip setting temperature. Note: closing the latch is idempotent.
    if incubation_temp_c >= 37.0:
        protocol.comment('Incubation temperature >= 37 °C: closing labware latch and setting temperature.')
        hs.close_labware_latch()
        # Use blocking set-and-wait so the module reaches the temperature before proceeding.
        hs.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment('Incubation temperature < 37 °C: skipping heating step.')

    # Ensure latch is closed before shaking (close is safe to call even if already closed)
    hs.close_labware_latch()

    # ---- Step 2: Shake for incubation time at requested speed ----
    protocol.comment(f'Starting shaking at {shaker_speed_rpm} rpm for {incubation_time_min} minutes.')
    # Set and wait for the requested shake speed
    hs.set_and_wait_for_shake_speed(shaker_speed_rpm)

    # Hold for the incubation time
    protocol.delay(minutes=incubation_time_min)

    # ---- Step 3: Stop heating and shaking, then open the labware latch ----
    protocol.comment('Deactivating shaker and heater.')
    # Always stop shaking before attempting to open the latch
    hs.deactivate_shaker()
    hs.deactivate_heater()

    # Now it is safe to open the labware latch
    hs.open_labware_latch()
    protocol.comment('Incubation and shaking complete; heater and shaker deactivated.')
