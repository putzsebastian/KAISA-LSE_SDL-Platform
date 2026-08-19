from opentrons import protocol_api

metadata = {
    'protocolName': '[protocol name by user]',
    'author': '[user name]',
    'description': "[what is the protocol about]"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def _unreplaced(s):
    return isinstance(s, str) and s.startswith('[[') and s.endswith(']]')


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


# Placeholders for templating
INCUBATION_TIME = '[[INCUBATION_TIME]]'
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def run(protocol):
    # Load Heater Shaker module on slot 1 (Gen1)
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware on heater shaker (slot 1) with custom cytiva plate or fallback
    try:
        labplate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        protocol.comment('WARNING: cytiva_96_filterwellplate_1ml not available; using standard 96-well plate fallback for simulation.')
        labplate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware on the deck tip rack and slots
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes (load for templating; not used for any transfers in this template)
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left')

    # Template values (will be replaced by wizard); use defaults for simulation
    incubation_time_min = parse_scalar(INCUBATION_TIME, 60.0)
    incubation_temp_c = parse_scalar(INCUBATION_TEMPERATURE, 37.0)
    shaker_rpm = parse_scalar(SHAKER_SPEED_INCUBATION, 1200.0)

    # Step 1: Close latch and set temp if >=37
    hs_mod.close_labware_latch()
    if incubation_temp_c >= 37:
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)

    # Step 2: Shake for incubation_time at rpm
    hs_mod.set_and_wait_for_shake_speed(shaker_rpm)
    protocol.delay(seconds=incubation_time_min * 60)

    # Step 3: Stop heating and shaking, open latch
    try:
        hs_mod.set_target_temperature(incubation_temp_c)
        hs_mod.wait_for_temperature()
    except Exception:
        protocol.comment('Could not adjust heater temperature to stop heating; proceeding to stop shaker.')
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
