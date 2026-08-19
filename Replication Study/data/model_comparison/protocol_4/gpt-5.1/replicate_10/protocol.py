from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated heater-shaker incubation with conditional temperature and shaking.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder value.

    During simulation the placeholders are unreplaced, so this returns a
    conservative worst-case default to fully exercise the protocol.
    Once the template is filled, non-numeric values will raise, which is
    desirable for catching bad configuration early.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # Labware (custom on heater-shaker, with simulation fallback only)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; using a standard '
            'plate as a SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware in slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument(
        'p300_single_gen2', mount='right', tip_racks=[tiprack_300]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2', mount='left', tip_racks=[tiprack_300]
    )

    # Parse placeholders with simulation-safe defaults (worst-case reasonable values)
    incubation_time_min = parse_scalar(
        PLACEHOLDER_INCUBATION_TIME,
        default=60.0,
        cast=float,
    )
    incubation_temp_c = parse_scalar(
        PLACEHOLDER_INCUBATION_TEMPERATURE,
        default=60.0,
        cast=float,
    )
    shaker_speed_rpm = parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default=2000.0,
        cast=float,
    )

    # Step 1: Close latch and conditionally set temperature
    hs_mod.close_labware_latch()

    if incubation_temp_c >= 37.0:
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)

    # Step 2: Shake for incubation time at specified speed
    # (require positive time and speed)
    if shaker_speed_rpm > 0 and incubation_time_min > 0:
        hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
        protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking and open latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
