from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated incubation on heater shaker with Cytiva 96 filter plate'
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
        return cast(float(default))
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with simulation-friendly worst-case defaults
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, 120.0, float)
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, 60.0, float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 2000.0, float)

    # Load modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load custom labware on heater shaker (with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware: tip rack in slot 7 (not used in this template but loaded per deck layout)
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Ensure latch is closed before any shaking or pipetting
    hs_mod.close_labware_latch()

    # Step 1: conditionally set temperature if incubation_temp_c >= 37
    if incubation_temp_c >= 37.0:
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment(f'Skipping temperature control because setpoint {incubation_temp_c} C is below 37 C.')

    # Step 2: shake for incubation_time_min at shaker_speed_rpm
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    # Step 3: stop heating and shaking and open labware latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
