from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol that closes heater-shaker latch, optionally sets temperature, shakes for a set time, then stops and opens latch. Uses placeholders for templating.'
}
requirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}

# Placeholders (declared as literal strings so an external templating engine can replace them)
PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'          # minutes
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # degrees C
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]' # rpm


def _unreplaced(s: str) -> bool:
    """Return True if s is an unreplaced placeholder like [[TOKEN]].
    Build the bracket strings rather than write them literally so the templating engine
    can find and replace them later.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder into a typed value for simulation.
    If the token is unreplaced, return the provided default (simulation fallback).
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # 1) Modules
    # Load Heater-Shaker Module Gen1 into slot 1
    hs_mod = protocol.load_module(module_name='heaterShakerModuleV1', location=1)

    # 2) Labware
    # Load custom labware onto the heater-shaker module; provide a simulation fallback
    try:
        plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # If the definition isn't available in the simulation, fall back to a standard 96-well plate
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition cytiva_96_filterwellplate_1ml not available; using a 96-well simulation fallback.')
        plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Load tiprack in slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # 3) Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Parse placeholders with conservative simulation defaults
    # Defaults chosen so the "temperature >= 37" branch executes during simulation
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=10, cast=float)
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=37, cast=float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=1000, cast=int)

    protocol.comment(f'Parsed incubation_time_min={incubation_time_min} min, incubation_temp_c={incubation_temp_c} C, shaker_speed_rpm={shaker_speed_rpm} rpm')

    # Step 1: Close labware latch and set temperature if >= 37 C
    hs_mod.close_labware_latch()
    if incubation_temp_c >= 37:
        protocol.comment(f'Setting heater-shaker temperature to {incubation_temp_c} C')
        # Non-blocking set so we can start shaking while heating ramps
        try:
            hs_mod.set_target_temperature(incubation_temp_c)
        except AttributeError:
            # Fallback if naming differs between API versions
            hs_mod.set_target_temperature(incubation_temp_c)
    else:
        protocol.comment('Incubation temperature below 37 C; skipping temperature setpoint')

    # Step 2: Shake for the incubation time at the specified speed
    protocol.comment(f'Setting shaker speed to {shaker_speed_rpm} rpm and waiting for setpoint')
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)

    # Delay for the incubation duration (convert minutes to seconds)
    protocol.delay(seconds=incubation_time_min * 60)

    # Step 3: Stop heating and shaking and open latch
    protocol.comment('Deactivating shaker and heater')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()

    hs_mod.open_labware_latch()
    protocol.comment('Incubation complete; labware latch opened')
