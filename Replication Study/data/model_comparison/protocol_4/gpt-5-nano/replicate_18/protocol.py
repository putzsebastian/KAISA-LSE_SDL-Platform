from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Incubation on Heater Shaker with placeholders: [[INCUBATION_TIME]], [[INCUBATION_TEMPERATURE]], [[SHAKER_SPEED_INCUBATION]]',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Placeholders (templatable values)
    INCUBATION_TIME = '[[INCUBATION_TIME]]'
    INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'

    def _unreplaced(value):
        return isinstance(value, str) and value.startswith('[[') and value.endswith(']]')

    def _parse_float(token, default):
        s = str(token).strip()
        if _unreplaced(s):
            return float(default)
        try:
            return float(s)
        except Exception:
            return float(default)

    def _parse_int(token, default):
        s = str(token).strip()
        if _unreplaced(s):
            return int(default)
        try:
            return int(s)
        except Exception:
            return int(default)

    # Fallback defaults (used when placeholders are not substituted in simulation)
    incubation_time = _parse_float(INCUBATION_TIME, 60.0)
    incubation_temp = _parse_float(INCUBATION_TEMPERATURE, 37.0)
    shaker_rpm = _parse_int(SHAKER_SPEED_INCUBATION, 1200)

    # Deck setup: Heater Shaker Module on slot 1
    hs = protocol.load_module(module_name="heaterShakerModuleV1", location=1)

    # Labware on Heater Shaker directly (no adapter)
    try:
        sample_plate = hs.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        protocol.comment('WARNING: cytiva_96_filterwellplate_1ml not found on Heater-Shaker; using a 96-well plate as fallback.')
        sample_plate = hs.load_labware('nest_96_wellplate_200ul_flat')

    # Slot 7: Tip rack for pipettes (300 µL)
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipette configuration (both mounted, though this protocol only uses heater-shaker steps)
    p300_s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300_m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Step 1: Close latch and set temperature if required
    hs.close_labware_latch()
    if incubation_temp >= 37:
        hs.set_and_wait_for_temperature(incubation_temp)

    # Step 2: Shake for the specified time at the given speed
    hs.set_and_wait_for_shake_speed(int(shaker_rpm))
    protocol.delay(minutes=int(incubation_time))

    # Step 3: Stop heating/shaking and open latch
    hs.deactivate_shaker()
    hs.deactivate_heater()
    hs.open_labware_latch()

    # End of protocol (no transfer steps defined in this template)
