from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation - templated',
    'author': '[user name]',
    'description': 'Incubation protocol on Heater Shaker with placeholders for temperature, time, and shaker speed'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    import time

    # Placeholders declared as strings for templating; the wizard will substitute real values
    INCUBATION_TIME = '[[INCUBATION_TIME]]'           # minutes
    INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # Celsius
    SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'  # rpm

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

    # 1) Load Heater Shaker module onto the deck slot 1
    hs_mod = protocol.load_module(module_name="heaterShakerModuleV1", location=1)

    # Load Cytiva 96 filter plate directly onto the Heater Shaker; fallback to a standard 96-well plate for simulation
    try:
        cytiva_labware = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        protocol.comment('WARNING: cytiva_96_filterwellplate_1ml not available; using a standard 96-well plate as fallback for simulation.')
        cytiva_labware = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Slot 7 tip rack on the deck for the p300
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes: Right mount - P300 Single Channel Gen2; Left mount - P300 Multi-Channel Gen2
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Optional: Destination labware on deck (not used for the incubation-only template)
    destination = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 9)

    # 1) Close labware latch on the Heater Shaker and conditionally set temperature
    hs_mod.close_labware_latch()
    inc_temp = parse_scalar(INCUBATION_TEMPERATURE, 37.0, float)
    if inc_temp >= 37.0:
        hs_mod.set_and_wait_for_temperature(inc_temp)

    # 2) Start shaking for INCUBATION_TIME minutes at SHAKER_SPEED_INCUBATION rpm
    inc_time = parse_scalar(INCUBATION_TIME, 60.0, float)
    shaker_speed = parse_scalar(SHAKER_SPEED_INCUBATION, 1000.0, float)
    hs_mod.set_and_wait_for_shake_speed(int(shaker_speed))

    # Incubate for the specified duration
    if inc_time > 0:
        time.sleep(inc_time * 60)

    # 3) Stop heating and shaking and open labware latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
