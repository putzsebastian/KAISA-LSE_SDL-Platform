from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol that closes heater-shaker latch, conditionally sets temperature, shakes for an incubation period, then stops and opens the latch. Uses placeholders for incubation time, temperature and shaker speed.'
}
requirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}


def _unreplaced(s: str) -> bool:
    """Detect whether a substitution token is still present.
    Build brackets by repetition so the literal '[[...]]' does not appear in the file
    outside the placeholder declarations.
    """
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder into a numeric type for simulation.

    During simulation the placeholders will still be the literal tokens; this helper
    returns a sensible default in that case so the script can be validated. Once the
    external templating system replaces the tokens with real values the helper casts
    them to the requested type and returns the real value.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


# Placeholders (must appear verbatim for the external templating tool)
PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def run(protocol: protocol_api.ProtocolContext):
    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware on Heater-Shaker (custom labware, wrap with a simulation fallback)
    try:
        plate_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # If the error is not a missing definition, re-raise it - that indicates a real stacking/slot problem
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        plate_on_hs = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip rack
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Parse placeholders (provides sensible defaults for simulation)
    # Defaults chosen to exercise the simulation: 30 min, 42 °C, 500 rpm
    incubation_minutes = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=30, cast=float)
    incubation_temp = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=42, cast=float)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=500, cast=int)

    # Step 1: Close labware latch and conditionally set temperature if >= 37
    protocol.comment('Closing heater-shaker labware latch.')
    hs_mod.close_labware_latch()

    if incubation_temp >= 37:
        protocol.comment(f'Setting heater-shaker temperature to {incubation_temp} °C and waiting to reach target.')
        # Blocking call: set target and wait until reached
        hs_mod.set_and_wait_for_temperature(incubation_temp)
    else:
        protocol.comment(f'Incubation temperature {incubation_temp} °C is below 37 °C - skipping heat setpoint.')

    # Step 2: Shake for the incubation period at specified speed
    protocol.comment(f'Starting shaking at {shaker_speed} rpm and incubating for {incubation_minutes} minutes.')
    # Ensure latch is closed before shaking (close_labware_latch is idempotent)
    hs_mod.close_labware_latch()
    # Blocking call: set shake speed and wait until reached
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)

    # Incubation delay (convert minutes to seconds)
    protocol.delay(incubation_minutes * 60)

    # Step 3: Stop heating and shaking and open the labware latch
    protocol.comment('Stopping shaker and heater, then opening the labware latch.')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()

    protocol.comment('Protocol complete.')
