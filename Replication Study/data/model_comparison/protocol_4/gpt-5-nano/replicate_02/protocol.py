from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Placeholder Template',
    'author': 'Lab 167',
    'description': 'Template protocol with placeholders for incubation time, temperature, and shaker speed',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol):
    # Heater Shaker on slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware on heater shaker
    source_labware = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')

    # Destination labware on deck slot 9
    destination_labware = protocol.load_labware('opentrons_tough_96_wellplate_200ul_pcr_full_skirt', 9)

    # Tip rack on deck slot 7
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Placeholders (templated values)
    PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
    PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'

    def _unreplaced(s):
        return isinstance(s, str) and s.startswith('[[') and s.endswith(']]')

    def _parse_float(value, default):
        s = str(value).strip()
        if _unreplaced(s):
            return default
        try:
            return float(s)
        except Exception:
            return default

    # Convert placeholders to numbers, using safe defaults for simulation
    incubation_time = _parse_float(PLACEHOLDER_INCUBATION_TIME, 60.0)  # minutes
    incubation_temp = _parse_float(PLACEHOLDER_INCUBATION_TEMPERATURE, 25.0)  # Celsius
    shaker_speed = _parse_float(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 1200.0)  # RPM

    # Step 1: Close latch and conditionally set temperature
    hs_mod.close_labware_latch()
    if incubation_temp >= 37:
        try:
            hs_mod.set_and_wait_for_temperature(incubation_temp)
        except Exception:
            hs_mod.set_target_temperature(incubation_temp)
            hs_mod.wait_for_temperature()

    # Step 2: Shake for incubation_time at shaker_speed
    try:
        hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    except Exception:
        try:
            hs_mod.set_target_shake_speed(shaker_speed)
            hs_mod.wait_for_shake()
        except Exception:
            pass

    protocol.delay(int(incubation_time * 60))

    # Step 3: Stop heating and shaking and open latch
    try:
        hs_mod.set_target_temperature(0)
        hs_mod.wait_for_temperature()
    except Exception:
        pass

    try:
        hs_mod.deactivate_shaker()
    except Exception:
        pass

    hs_mod.open_labware_latch()

    # Transfer: 1 uL from first tube of source to every well in destination
    src_well = source_labware.wells()[0]
    dest_wells = destination_labware.wells()
    transfer_vol = 1.0  # microliters

    p300s.transfer(transfer_vol, src_well, dest_wells, new_tip='once')
