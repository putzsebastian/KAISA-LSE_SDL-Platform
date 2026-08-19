from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Heater Shaker Incubation',
    'author': 'Lab 167',
    'description': 'Templated Heater Shaker incubation using custom filter plate labware.'
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
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # labware
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_2ml_deep')
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # parameters
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, 1.0, float)
    incubation_temperature_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, 37.0, float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 500, int)

    # commands
    if incubation_temperature_c >= 37:
        hs_mod.close_labware_latch()
        hs_mod.set_target_temperature(incubation_temperature_c)
    else:
        protocol.comment('Skipping Heater-Shaker temperature set because incubation temperature is below 37 C.')
        hs_mod.close_labware_latch()

    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
