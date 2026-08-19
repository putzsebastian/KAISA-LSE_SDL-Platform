from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'User',
    'description': 'Incubation protocol using Heater-Shaker Module with placeholders for time, temperature and speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (must remain literal in the file for the external templating system)
INCUBATION_TIME = '[[INCUBATION_TIME]]'
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    """Detect an unreplaced placeholder. Build the brackets by repetition to avoid literal '[[' in checks."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_float_placeholder(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return float(default)
    return float(s)


def parse_int_placeholder(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load custom labware directly onto the heater-shaker module. Use a SIMULATION fallback if definition missing.
    try:
        plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition cytiva_96_filterwellplate_1ml not available; '
                         'using nest_96_wellplate_200ul_flat as a SIMULATION fallback only.')
        plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware off the module
    tiprack300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack300])

    # Parse placeholders with sensible simulation fallbacks
    incubation_minutes = parse_float_placeholder(INCUBATION_TIME, 30.0)  # minutes
    incubation_temp = parse_float_placeholder(INCUBATION_TEMPERATURE, 37.0)  # °C
    shake_rpm = parse_int_placeholder(SHAKER_SPEED_INCUBATION, 1000)  # rpm

    protocol.comment(f'Incubation time (min): {incubation_minutes}')
    protocol.comment(f'Incubation temperature (°C): {incubation_temp}')
    protocol.comment(f'Shaker speed (rpm): {shake_rpm}')

    # Step 1: Close labware latch and set temperature if >= 37
    protocol.comment('Step 1: Ensure latch closed and (conditionally) set temperature')
    hs_mod.close_labware_latch()

    if incubation_temp >= 37.0:
        # Attempt to set target temperature if the API exposes a method. If not available, warn and continue.
        if hasattr(hs_mod, 'set_target_temperature'):
            hs_mod.set_target_temperature(incubation_temp)
            protocol.comment(f'set_target_temperature({incubation_temp}) called')
        elif hasattr(hs_mod, 'set_temperature'):
            hs_mod.set_temperature(incubation_temp)
            protocol.comment(f'set_temperature({incubation_temp}) called')
        elif hasattr(hs_mod, 'set_and_wait_for_temperature'):
            hs_mod.set_and_wait_for_temperature(incubation_temp)
            protocol.comment(f'set_and_wait_for_temperature({incubation_temp}) called')
        else:
            protocol.comment('WARNING: Heater-Shaker target temperature method not available in this API; '
                             'skipping temperature set (simulation only).')
    else:
        protocol.comment('Incubation temperature below 37°C; skipping heat step as requested.')

    # Step 2: Shake for incubation time at specified speed
    protocol.comment('Step 2: Start shaking')
    hs_mod.close_labware_latch()  # safe to call again
    hs_mod.set_and_wait_for_shake_speed(shake_rpm)

    # Delay for incubation time (convert minutes to seconds)
    protocol.delay(seconds=incubation_minutes * 60)

    # Step 3: Stop heating and shaking, open latch
    protocol.comment('Step 3: Stop heating and shaking, then open latch')
    # Stop shaking
    hs_mod.deactivate_shaker()
    protocol.comment('Shaker deactivated')

    # Stop heating if available
    if hasattr(hs_mod, 'deactivate_heater'):
        hs_mod.deactivate_heater()
        protocol.comment('Heater deactivated')
    else:
        protocol.comment('WARNING: Heater-Shaker deactivate_heater method not available; cannot explicitly stop heating')

    # Open latch to allow removal of labware
    hs_mod.open_labware_latch()
    protocol.comment('Labware latch opened')
