from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Incubate on Heater-Shaker with placeholders for temperature, time, and shaker speed'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # 1. Load Heater-Shaker module in slot 1
    hs_mod = protocol.load_module(module_name="heaterShakerModuleV1", location=1)

    # 2. Load Cytiva plate onto heater shaker, with latch handling
    hs_mod.open_labware_latch()
    try:
        cytiva_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        protocol.comment('WARNING: cytiva_96_filterwellplate_1ml not available; using standard 96-well plate for simulation.')
        cytiva_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')
    hs_mod.close_labware_latch()

    # 3. Load destination plate and tip rack
    destination_plate = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 9)
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # 4. Load pipettes
    p300_s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300_m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # 5. Placeholders
    INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    INCUBATION_TIME = '[[INCUBATION_TIME]]'
    SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'

    def _unreplaced(s):
        return isinstance(s, str) and s.startswith('[[') and s.endswith(']]')
    def _to_float(token, default):
        s = str(token)
        if _unreplaced(s):
            return default
        try:
            return float(s)
        except ValueError:
            return default

    incubation_temp = _to_float(INCUBATION_TEMPERATURE, 37.0)
    incubation_time_min = _to_float(INCUBATION_TIME, 60.0)
    shaker_rpm = _to_float(SHAKER_SPEED_INCUBATION, 1200.0)

    # Step 1: Temperature control if temperature >= 37
    if incubation_temp >= 37:
        hs_mod.set_and_wait_for_temperature(incubation_temp)

    # Step 2: Shake for the incubation duration at specified speed
    hs_mod.set_and_wait_for_shake_speed(shaker_rpm)
    protocol.delay(seconds=incubation_time_min * 60)

    # Step 3: Stop heating and shaking and open latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
