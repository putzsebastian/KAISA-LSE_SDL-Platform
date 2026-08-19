from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated incubation on Heater Shaker with placeholders for time, temperature, and speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with simulation-safe fallbacks (worst-case style)
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, 60.0, float)
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, 60.0, float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 2000.0, float)

    # Convert to appropriate Python types
    incubation_time_min = float(incubation_time_min)
    incubation_temp_c = float(incubation_temp_c)
    shaker_speed_rpm = float(shaker_speed_rpm)

    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware on Heater-Shaker: custom filter plate, with simulation fallback
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tiprack per deck layout
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Step 1: Close latch and conditionally set temperature if incubation_temp_c >= 37
    hs_mod.close_labware_latch()

    if incubation_temp_c >= 37.0:
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment('Incubation temperature below 37C; skipping heater activation.')

    # Step 2: Shake for incubation_time_min minutes at shaker_speed_rpm
    if shaker_speed_rpm > 0 and incubation_time_min > 0:
        hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
        protocol.delay(minutes=incubation_time_min)
    else:
        protocol.comment('Shaker speed or incubation time is zero or negative; skipping shaking step.')

    # Step 3: Stop heating and shaking and open labware latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
