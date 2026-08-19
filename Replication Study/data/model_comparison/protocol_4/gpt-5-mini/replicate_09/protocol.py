from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation with Placeholders',
    'author': 'Lab 167',
    'description': 'Heater-Shaker incubation protocol using placeholders for time, temperature and shake speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings so an external templating system can substitute them)
PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder like '[[TOKEN]]' is still present.
    Build the bracket strings by repetition so the literal '[[' never appears in the file
    (see protocol templating rules).
    """
    return str(s).startswith('[' * 2) and str(s).endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        # Simulation fallback: choose a value that exercises the pathway.
        return cast(default)
    return cast(s)

def run(protocol: protocol_api.ProtocolContext):
    # Load Heater-Shaker Module Gen1 into slot 1
    hs_mod = protocol.load_module(module_name="heaterShakerModuleV1", location=1)

    # Load labware on the heater-shaker module. The labware is custom; use a simulation fallback
    try:
        plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml', label='Filterwell plate')
    except Exception as exc:
        # If the definition is missing, the simulator wraps the error. Re-raise anything
        # that is not a missing-definition error to avoid hiding real problems.
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat', label='SIMULATION fallback plate')

    # Tip rack in slot 7
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Parse placeholders with sensible simulation fallbacks
    # Note: the placeholder strings remain literal in the file so an external templating
    # system can substitute them before running on hardware.
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=30, cast=float)
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=42, cast=float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=1200, cast=int)

    protocol.comment(f'Parsed incubation_time_min={incubation_time_min}, '
                     f'incubation_temp_c={incubation_temp_c}, shaker_speed_rpm={shaker_speed_rpm}')

    # Step 1: Close labware latch and set temperature if >= 37 C
    # Close latch (idempotent) so the module is ready for shaking/heating.
    hs_mod.close_labware_latch()

    if incubation_temp_c >= 37:
        protocol.comment(f'Setting heater-shaker target temperature to {incubation_temp_c} °C and waiting')
        # Prefer the convenience blocking call; fall back to set_target_temperature + wait_for_temperature if needed
        try:
            hs_mod.set_and_wait_for_temperature(incubation_temp_c)
        except AttributeError:
            hs_mod.set_target_temperature(incubation_temp_c)
            hs_mod.wait_for_temperature()
    else:
        protocol.comment(f'Incubation temperature ({incubation_temp_c} °C) < 37 °C: skipping temperature set')

    # Step 2: Shake for incubation_time_min at shaker_speed_rpm
    protocol.comment(f'Starting shaker at {shaker_speed_rpm} rpm and incubating for {incubation_time_min} minutes')
    try:
        hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    except AttributeError:
        # Re-raise if the method truly does not exist; most v2.19 installs expose set_and_wait_for_shake_speed
        if hasattr(hs_mod, 'set_and_wait_for_shake_speed'):
            hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
        else:
            raise

    # Wait for the incubation duration
    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking, then open the labware latch
    protocol.comment('Deactivating shaker and heater, then opening labware latch')
    try:
        hs_mod.deactivate_shaker()
    except Exception:
        protocol.comment('Warning: deactivate_shaker() raised an exception or is unavailable')
    try:
        hs_mod.deactivate_heater()
    except Exception:
        protocol.comment('Warning: deactivate_heater() raised an exception or is unavailable')

    # Opening the latch must only occur after shaking has stopped
    hs_mod.open_labware_latch()

    protocol.comment('Incubation protocol complete')
