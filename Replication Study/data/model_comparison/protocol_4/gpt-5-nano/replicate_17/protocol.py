from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation with Placeholders',
    'author': 'Lab 167',
    'description': 'Incubation protocol on Heater-Shaker with placeholders for temperature, time and shaker speed; deck layout matches user requirements.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # 1. Heater-Shaker module in Slot 1 (as per deck layout)
    hs = protocol.load_module("heaterShakerModuleV1", location=1)

    # Close latch before any operation
    hs.close_labware_latch()

    # Load custom labware directly on the Heater Shaker (Slot 1)
    try:
        incubation_labware = hs.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; using a standard plate fallback for simulation.')
        incubation_labware = hs.load_labware('nest_96_wellplate_200ul_flat')

    # Slot 7: Tip rack for 300 uL tips
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single_right = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi_left = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Placeholders (templatable values)
    INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    INCUBATION_TIME = '[[INCUBATION_TIME]]'
    SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'

    def _unreplaced(s):
        return isinstance(s, str) and s.startswith('[[') and s.endswith(']]')

    def parse_float(value, default):
        s = str(value).strip()
        if _unreplaced(s):
            return default
        try:
            return float(s)
        except:
            return default

    # Convert placeholders with safe defaults for simulation
    inc_temp = parse_float(INCUBATION_TEMPERATURE, 37.0)
    inc_time_min = parse_float(INCUBATION_TIME, 5.0)
    shaker_rpm = int(parse_float(SHAKER_SPEED_INCUBATION, 600.0))

    # Step 1: Close latch already done. Heating if >= 37°C
    if inc_temp >= 37.0:
        hs.set_and_wait_for_temperature(inc_temp)

    # Step 2: Shake for inc_time_min minutes at shaker_rpm rpm
    hs.set_and_wait_for_shake_speed(shaker_rpm)
    protocol.delay(minutes=inc_time_min)

    # Step 3: Stop heating/shaking and open the latch
    hs.deactivate_shaker()
    hs.deactivate_heater()
    hs.open_labware_latch()
