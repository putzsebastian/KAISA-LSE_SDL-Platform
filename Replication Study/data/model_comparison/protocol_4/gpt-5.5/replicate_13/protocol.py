from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Heater-Shaker Incubation',
    'author': 'Lab 167',
    'description': 'Templated incubation on a custom 96-well filter plate mounted directly on a Heater-Shaker Module Gen1.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(value):
    s = str(value).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # modules
    heater_shaker = protocol.load_module('heaterShakerModuleV1', 1)

    # labware
    try:
        filter_plate = heater_shaker.load_labware(
            'cytiva_96_filterwellplate_1ml',
            label='Cytiva 96 Filter Well Plate 1 mL'
        )
    except Exception as exc:
        if 'not found' not in str(exc).lower():
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; using a standard '
            '96-well plate as a SIMULATION fallback only.'
        )
        filter_plate = heater_shaker.load_labware(
            'nest_96_wellplate_2ml_deep',
            label='SIMULATION fallback 96-well plate'
        )

    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # pipettes
    p300_single = protocol.load_instrument(
        'p300_single_gen2', mount='right', tip_racks=[tiprack_300]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2', mount='left', tip_racks=[tiprack_300]
    )

    # parameters; defaults are for simulation before placeholder substitution
    incubation_time_minutes = parse_scalar(PLACEHOLDER_INCUBATION_TIME, 60.0, float)
    incubation_temperature_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, 95.0, float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 3000, int)

    # commands
    heater_shaker.close_labware_latch()

    if incubation_temperature_c >= 37:
        heater_shaker.set_target_temperature(incubation_temperature_c)

    heater_shaker.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_minutes)

    heater_shaker.deactivate_shaker()
    heater_shaker.deactivate_heater()
    heater_shaker.open_labware_latch()
