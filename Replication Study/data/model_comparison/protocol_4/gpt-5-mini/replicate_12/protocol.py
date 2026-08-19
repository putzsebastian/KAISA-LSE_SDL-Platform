from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol controlling a Heater-Shaker with placeholders for temperature, time and speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


# Placeholders (declared literally so an external templating wizard can substitute them)
INCUBATION_TIME = '[[INCUBATION_TIME]]'
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    """Detect an unreplaced [[TOKEN]] in simulation.

    Build the brackets by repetition so the literal "[[" never appears in the file
    other than in the placeholder declarations above.
    """
    return str(s).startswith('[' * 2) and str(s).endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder for simulation; return default if unreplaced.

    cast is the callable used to convert the substituted string to a numeric type.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s)) if cast is float else cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # Load Heater-Shaker module into slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load the custom labware onto the Heater-Shaker. Use a simulation fallback if the
    # custom definition is not available.
    try:
        plate_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        plate_on_hs = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip rack in slot 7
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Parse placeholders into numeric values for simulation. Choose conservative fallbacks.
    incubation_minutes = parse_scalar(INCUBATION_TIME, default=10, cast=float)
    # temperature as float
    target_temp_c = parse_scalar(INCUBATION_TEMPERATURE, default=37, cast=float)
    # shake speed as int; use int(...) on the substitution when present
    s = str(SHAKER_SPEED_INCUBATION).strip()
    if _unreplaced(s):
        shake_rpm = 500
    else:
        # cast to int safely
        shake_rpm = int(float(s))

    protocol.comment(f'Parsed incubation_minutes={incubation_minutes}, '
                     f'target_temp_c={target_temp_c}, shake_rpm={shake_rpm}')

    # Step 1: Close latch and set temperature if target >= 37 °C
    hs_mod.close_labware_latch()
    protocol.comment('Heater-Shaker labware latch closed')

    if target_temp_c >= 37:
        protocol.comment(f'Setting heater to {target_temp_c} °C and waiting to reach target')
        hs_mod.set_and_wait_for_temperature(target_temp_c)
    else:
        protocol.comment('Target temperature is below 37 °C; skipping heating step')

    # Step 2: Shake the module for the requested time at the requested speed
    protocol.comment(f'Setting shake speed to {shake_rpm} rpm and incubating for {incubation_minutes} minutes')
    hs_mod.set_and_wait_for_shake_speed(shake_rpm)
    protocol.delay(minutes=incubation_minutes)

    # Step 3: Stop heating and shaking, then open the labware latch
    protocol.comment('Deactivating shaker and heater')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()

    hs_mod.open_labware_latch()
    protocol.comment('Heater-Shaker labware latch opened')
