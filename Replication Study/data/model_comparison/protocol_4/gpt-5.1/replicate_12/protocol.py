from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated protocol controlling heater-shaker incubation with placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a numeric placeholder.

    During simulation, placeholders are unreplaced tokens like '[[INCUBATION_TIME]]'.
    In that case, fall back to a conservative worst-case default so the script
    can be simulated. On the real robot, the wizard will substitute numeric
    strings and this will return the cast value instead.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # Labware on Heater-Shaker: custom Cytiva plate with simulation fallback only
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Any error other than missing custom definition must surface
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; ' 
            'using a standard 96-well plate as a SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tiprack
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument(
        'p300_single_gen2', mount='right', tip_racks=[tiprack_300]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2', mount='left', tip_racks=[tiprack_300]
    )

    # Parse placeholders with worst-case defaults for simulation
    incubation_time_min = parse_scalar(
        PLACEHOLDER_INCUBATION_TIME,
        default=60.0,
        cast=float,
    )
    incubation_temp_c = parse_scalar(
        PLACEHOLDER_INCUBATION_TEMPERATURE,
        default=95.0,
        cast=float,
    )
    shaker_speed_rpm = parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default=2000.0,
        cast=float,
    )

    # Step 1: Close labware latch and set temperature if >= 37 °C
    hs_mod.close_labware_latch()

    if incubation_temp_c >= 37.0:
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment(
            f'Incubation temperature {incubation_temp_c} °C < 37 °C; '
            'skipping heater activation step.'
        )

    # Step 2: Shake for incubation time at specified speed
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking and open labware latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
