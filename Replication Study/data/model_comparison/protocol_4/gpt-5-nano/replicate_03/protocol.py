from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol with placeholders for incubation temperature, time, and shaker speed'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # 1) Load Heater-Shaker Gen1 module on slot 1
    hs = protocol.load_module(module_name="heaterShakerModuleV1", location=1)

    # 2) Load cytiva labware directly onto module; provide fallback for simulation if not found
    try:
        labware_on_hs = hs.load_labware("cytiva_96_filterwellplate_1ml")
    except Exception:
        protocol.comment('WARNING: cytiva labware not found; using standard 96-well plate as SIM fallback only.')
        labware_on_hs = hs.load_labware("nest_96_wellplate_200ul_flat")

    # 3) Load tip rack on slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # 4) Load pipettes
    p300_s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Placeholders (strings to be substituted by templating wizard)
    PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
    PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'

    def _unreplaced(s):
        return isinstance(s, str) and s.startswith('[[') and s.endswith(']]')

    def parse_float(token, default):
        s = str(token).strip()
        if _unreplaced(s):
            return float(default)
        try:
            return float(s)
        except Exception:
            return float(default)

    inc_temp = parse_float(PLACEHOLDER_INCUBATION_TEMPERATURE, 37.0)

    # Step 1: Close latch and optionally heat
    hs.close_labware_latch()
    if inc_temp >= 37.0:
        hs.set_target_temperature(inc_temp)
        hs.wait_for_temperature()

    inc_time = parse_float(PLACEHOLDER_INCUBATION_TIME, 60.0)
    shaker_speed = parse_float(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 1200.0)

    # Step 2: Shake for inc_time minutes at shaker_speed rpm
    hs.set_and_wait_for_shake_speed(int(shaker_speed))
    protocol.delay(minutes=inc_time)

    # Step 3: Stop heating and shaking, open latch
    hs.deactivate_heater()
    hs.deactivate_shaker()
    hs.open_labware_latch()
