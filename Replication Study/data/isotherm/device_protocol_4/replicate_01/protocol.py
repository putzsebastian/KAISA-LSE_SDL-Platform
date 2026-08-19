from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated incubation on Heater-Shaker with placeholders for time, temperature, and speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value: str, default: float, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Load Heater-Shaker Module Gen1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load custom labware directly on the Heater-Shaker (no adapter)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Load tiprack in slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Load pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Parse placeholder values with simulation fallbacks (worst-case reasonable defaults)
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=60.0, cast=float)
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=50.0, cast=float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0, cast=float)

    # Step 1: Close labware latch and set temperature if INCUBATION_TEMPERATURE >= 37
    hs_mod.close_labware_latch()

    if incubation_temp_c >= 37.0:
        # Start heating and wait until target is reached
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment(f'Incubation temperature {incubation_temp_c} C < 37 C: skipping heating step.')

    # Step 2: Shake the Heater-Shaker for INCUBATION_TIME minutes at SHAKER_SPEED_INCUBATION rpm
    if shaker_speed_rpm > 0:
        hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
        if incubation_time_min > 0:
            protocol.delay(minutes=incubation_time_min)
    else:
        protocol.comment('Shaker speed is 0 or negative; skipping shaking step.')

    # Step 3: In the end stop heating and shaking and open labware latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
