from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol that runs an incubation on the Heater-Shaker using placeholders'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


# Placeholders (literal strings so an external templating system can substitute them)
INCUBATION_TIME = '[[INCUBATION_TIME]]'
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    """Detect an unreplaced [[TOKEN]] without embedding literal brackets in the source.

    Builds the bracket characters by repetition so the file never contains the literal string
    "[[" which the external templating system will search for.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder. During simulation, return a safe default if the
    placeholder has not yet been replaced.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # Load Heater-Shaker module in slot 1
    hs_mod = protocol.load_module("heaterShakerModuleV1", 1)

    # Load custom labware directly on the Heater-Shaker. Use a simulation fallback if missing.
    try:
        plate_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # Surface unexpected errors but fall back only for a missing definition
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '\
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        plate_on_hs = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip rack in slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Parse placeholders (simulation-safe defaults chosen to exercise both branches)
    incubation_temp = parse_scalar(INCUBATION_TEMPERATURE, 37.0, cast=float)
    incubation_time_min = parse_scalar(INCUBATION_TIME, 30.0, cast=float)
    shaker_speed_rpm = parse_scalar(SHAKER_SPEED_INCUBATION, 1000, cast=int)

    protocol.comment(f'Parsed incubation temperature: {incubation_temp} °C')
    protocol.comment(f'Parsed incubation time: {incubation_time_min} minutes')
    protocol.comment(f'Parsed shaker speed: {shaker_speed_rpm} RPM')

    # Step 1: close the labware latch and set temperature conditionally
    hs_mod.close_labware_latch()

    if incubation_temp >= 37:
        protocol.comment(f'Setting heater-shaker temperature to {incubation_temp} °C and waiting to reach setpoint')
        # Blocking call: set temperature and wait until reached
        hs_mod.set_and_wait_for_temperature(incubation_temp)
    else:
        protocol.comment('Incubation temperature below 37 °C; skipping heating step')

    # Step 2: start shaking at desired RPM and incubate for the requested time
    protocol.comment(f'Starting shaking at {shaker_speed_rpm} RPM')
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)

    protocol.comment(f'Incubating for {incubation_time_min} minute(s)')
    protocol.delay(minutes=incubation_time_min)

    # Step 3: stop heating and shaking, then open the labware latch
    protocol.comment('Stopping shaking and heating')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()

    # It is safe to open latch after shaker is stopped
    hs_mod.open_labware_latch()
    protocol.comment('Heater-Shaker incubation complete')
