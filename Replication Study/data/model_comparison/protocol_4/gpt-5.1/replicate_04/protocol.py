from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Heater Shaker Incubation',
    'author': 'User',
    'description': 'Heater-shaker incubation with placeholders for time, temperature, and shake speed.'
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
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholder parameters with conservative worst-case defaults for simulation
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=60.0)  # minutes
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=95.0)  # deg C
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=3000.0)  # rpm

    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware: custom filter plate directly on the heater shaker (no adapter)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware: tip rack
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Step 1: Conditionally set temperature and close labware latch
    # Close latch first (safe to call even if already closed)
    hs_mod.close_labware_latch()

    # Only set temperature if incubation temperature >= 37 C
    if incubation_temp_c >= 37.0:
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)

    # Step 2: Shake for incubation time at specified speed
    # Start shaking
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)

    # Incubation delay in minutes
    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking, then open labware latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
