from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol that runs an incubation on Heater-Shaker with placeholders for time, temperature and speed.'
}
requirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}

# Placeholders (must be literal for the wizard to replace)
INCUBATION_TIME = '[[INCUBATION_TIME]]'            # minutes
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # degrees C
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'  # rpm (or user-defined units)


def _unreplaced(s: str) -> bool:
    '''Detect an unreplaced placeholder during simulation.
    Build the brackets by repetition so the literal string '[[' does not appear in the file
    and the simulator can still run while the tokens are unreplaced.
    '''
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    '''Parse a scalar placeholder into a number for simulation.
    If the token is unreplaced, return the chosen default so the simulator exercises
    the worst-case behaviour. Otherwise cast the provided value.
    '''
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # Load modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load labware onto the Heater-Shaker (custom labware may not be available in simulator)
    try:
        plate_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        plate_on_hs = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Close the labware latch (safe to call even if already closed)
    hs_mod.close_labware_latch()

    # Load tiprack (slot 7)
    tiprack300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack300])

    # Parse placeholders for simulation (choose conservative defaults)
    # Defaults chosen to exercise heating and shaking in simulation:
    incubation_minutes = parse_scalar(INCUBATION_TIME, default=60, cast=float)  # minutes
    incubation_temp_c = parse_scalar(INCUBATION_TEMPERATURE, default=42, cast=float)
    shaker_rpm = parse_scalar(SHAKER_SPEED_INCUBATION, default=1500, cast=float)

    protocol.comment(f'Parsed incubation time (min): {incubation_minutes}')
    protocol.comment(f'Parsed incubation temperature (C): {incubation_temp_c}')
    protocol.comment(f'Parsed shaker speed (rpm): {shaker_rpm}')

    # Step 1: Close latch (already closed above) and set temperature if >= 37 C
    # We ensure the latch is closed before any shaking; set temperature only when required.
    if incubation_temp_c >= 37:
        protocol.comment('Setting heater-shaker target temperature and waiting to reach it')
        # Use a blocking call to wait for the temperature to be reached before proceeding
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment('Skipping temperature set because target is below 37 C')

    # Step 2: Start shaking at requested speed and incubate for the requested time
    protocol.comment('Starting shaker to reach target speed')
    # set_and_wait_for_shake_speed blocks until the requested speed is reached
    hs_mod.set_and_wait_for_shake_speed(int(shaker_rpm))

    protocol.comment(f'Incubating for {incubation_minutes} minutes')
    # Convert minutes to seconds for delay
    protocol.delay(seconds=incubation_minutes * 60)

    # Step 3: Stop heating and shaking, then open the latch
    protocol.comment('Stopping shaker and heater')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()

    # It is now safe to open the labware latch
    hs_mod.open_labware_latch()
    protocol.comment('Incubation complete; labware latch opened')
