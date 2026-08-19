from opentrons import protocol_api

metadata = {
    'protocolName': '[protocol name by user]',
    'author': '[user name]',
    'description': "[what is the protocol about]"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s):
    return isinstance(s, str) and s.startswith('[[') and s.endswith(']]')


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return default
    try:
        return cast(float(s))
    except Exception:
        return default


def run(protocol):
    # 1) Load Heater Shaker Module (Slot 1)
    hs_mod = protocol.load_module(module_name="heaterShakerModuleV1", location=1)

    # 2) Load source labware on heater shaker (slot 1)
    try:
        source_labware = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
        labware_loaded = 'cytiva'
    except Exception:
        protocol.comment('Custom labware cytiva_96_filterwellplate_1ml not found on Heater Shaker; using a fallback labware.')
        source_labware = hs_mod.load_labware('nest_96_wellplate_200ul_flat')
        labware_loaded = 'nest_fallback'

    # 3) Destination labware (slot 9)
    destination_plate = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 9)

    # 4) Tip rack (slot 7)
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # 5) Pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # 6) Placeholders with worst-case defaults for simulation
    incubation_time = parse_scalar(PLACEHOLDER_INCUBATION_TIME, 60.0)
    incubation_temperature = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, 40.0)
    shaker_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 1500.0)

    # 7) Step 1 - Close latch and conditional heating
    hs_mod.close_labware_latch()
    if incubation_temperature >= 37:
        hs_mod.set_target_temperature(incubation_temperature)
        hs_mod.wait_for_temperature()
    else:
        protocol.comment('Incubation temperature below 37C; skipping heating step')

    # 8) Step 2 - Shake for incubation_time minutes at shaker_rpm
    hs_mod.set_and_wait_for_shake_speed(shaker_rpm)
    protocol.delay(minutes=incubation_time)

    # 9) Step 3 - Stop heating and shaking and open latch
    hs_mod.deactivate_shaker()
    # Return heater to minimum allowed temperature (37 C) before opening latch
    hs_mod.set_target_temperature(37.0)
    hs_mod.open_labware_latch()

    # 10) Step 4 - Transfer 1 uL from first tube to all destination wells using a single tip
    # Use the first well from the source labware (A1) as the reagent reservoir
    src_well = source_labware.wells()[0]
    dest_wells = destination_plate.wells()

    p300s.transfer(1.0, src_well, dest_wells, new_tip='once')
