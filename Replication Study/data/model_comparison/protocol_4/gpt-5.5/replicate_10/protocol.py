from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Heater Shaker Incubation',
    'author': 'Lab 167',
    'description': 'Templated incubation protocol for a custom Cytiva 96 filter well plate on Heater-Shaker Module Gen1.'
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
    heater_shaker = protocol.load_module('heaterShakerModuleV1', '1')

    # labware
    try:
        filter_plate = heater_shaker.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = heater_shaker.load_labware('nest_96_wellplate_200ul_flat')

    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', '7')

    # pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # parameters
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=60.0, cast=float)
    incubation_temperature_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=37.0, cast=float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0, cast=float)

    # commands
    heater_shaker.close_labware_latch()

    if incubation_temperature_c >= 37.0:
        heater_shaker.set_target_temperature(incubation_temperature_c)
    else:
        protocol.comment('Incubation temperature is below 37 C; skipping Heater-Shaker temperature set step.')

    heater_shaker.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    heater_shaker.deactivate_shaker()
    heater_shaker.deactivate_heater()
    heater_shaker.open_labware_latch()
