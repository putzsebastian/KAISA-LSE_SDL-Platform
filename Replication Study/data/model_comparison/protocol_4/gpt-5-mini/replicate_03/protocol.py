from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol to incubate a filter-well plate on the Heater-Shaker module using placeholders.'
}
requirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}


# Placeholder tokens (literal strings for the wizard to replace)
PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    '''Detect whether a placeholder token is still unsubstituted in the script.

    Built brackets by repetition to avoid writing the literal "[[" in the check,
    see protocol authoring guidelines.
    '''
    return str(s).startswith('[' * 2) and str(s).endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    '''Parse a scalar placeholder or use a simulation-friendly default.

    On the robot the placeholder will be replaced and cast() should apply. During
    simulation the unreplaced token is detected and the provided default is used
    so the run exercises a realistic worst-case.
    '''
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # -----------------------
    # Modules
    # -----------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # -----------------------
    # Labware
    # -----------------------
    # Load custom labware directly on the Heater-Shaker module. Provide a simulation
    # fallback if the custom definition is not available in the simulator.
    try:
        plate_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # If the error is not about a missing definition, re-raise it so real errors surface.
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition for cytiva_96_filterwellplate_1ml not found; using a 96-well plate as a SIMULATION fallback only.')
        plate_on_hs = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # -----------------------
    # Pipettes
    # -----------------------
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # -----------------------
    # Parse placeholders (simulation-friendly defaults chosen to exercise the protocol)
    # -----------------------
    # Defaults chosen for simulation: time=60 min, temp=37 C, speed=1200 rpm
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=60, cast=float)
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=37, cast=float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED, default=1200, cast=float)

    protocol.comment(f'Incubation time (min): {incubation_time_min}')
    protocol.comment(f'Incubation temperature (C): {incubation_temp_c}')
    protocol.comment(f'Shaker speed (rpm): {shaker_speed_rpm}')

    # -----------------------
    # Protocol steps
    # -----------------------

    # 1) Close labware latch. If requested temperature is >= 37 C then set temperature.
    protocol.comment('Closing Heater-Shaker labware latch...')
    hs_mod.close_labware_latch()

    # Only set the temperature when the (parsed) temperature is >= 37 C, else skip.
    if incubation_temp_c >= 37:
        protocol.comment(f'Setting Heater-Shaker target temperature to {incubation_temp_c} °C and waiting...')
        # set_and_wait_for_temperature blocks until the module reaches the target temp
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment('Incubation temperature is below 37 °C; skipping heating step.')

    # 2) Start shaking at the requested speed and wait for the incubation duration.
    protocol.comment(f'Starting shaker to reach {shaker_speed_rpm} rpm...')
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)

    protocol.comment(f'Incubating for {incubation_time_min} minutes...')
    protocol.delay(seconds=incubation_time_min * 60)

    # 3) Stop heating and shaking, then open the labware latch.
    protocol.comment('Stopping shaker and heater...')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()

    protocol.comment('Opening Heater-Shaker labware latch...')
    hs_mod.open_labware_latch()

    protocol.comment('Incubation protocol complete.')
