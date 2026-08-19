from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated protocol with placeholders for incubation on Heater Shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (must appear as plain string literals for the template system)
PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if the given string still looks like an unreplaced [[TOKEN]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder value with a simulation-time default.

    - If the value is still a [[TOKEN]], return the provided worst-case default.
    - Otherwise cast via float() first, then to the requested type.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders using worst-case defaults for simulation
    # (these are only used when the [[TOKENS]] have not yet been substituted)
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=120.0, cast=float)
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=60.0, cast=float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0, cast=float)

    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware on module (custom labware with simulation fallback only)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware: tiprack
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes (configured but not used in this simple incubation template)
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Step 1: Close labware latch and set temperature if incubation_temp >= 37 °C
    hs_mod.close_labware_latch()
    protocol.comment('Labware latch closed.')

    if incubation_temp_c >= 37.0:
        hs_mod.set_target_temperature(incubation_temp_c)
        protocol.comment(f'Setting Heater Shaker temperature to {incubation_temp_c} °C.')
    else:
        protocol.comment(f'Incubation temperature {incubation_temp_c} °C is below 37 °C; skipping temperature set.')

    # Step 2: Shake for incubation_time at shaker_speed
    # Ensure latch is closed before shaking (already closed above, but safe to call again)
    hs_mod.close_labware_latch()
    protocol.comment('Confirming labware latch is closed before shaking.')

    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.comment(f'Shaking at {shaker_speed_rpm} rpm for {incubation_time_min} minutes.')
    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking and open latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    protocol.comment('Heater and shaker deactivated.')

    hs_mod.open_labware_latch()
    protocol.comment('Labware latch opened; incubation complete.')
