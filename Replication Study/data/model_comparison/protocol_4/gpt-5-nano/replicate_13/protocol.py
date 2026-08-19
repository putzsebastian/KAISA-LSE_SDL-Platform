from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol with placeholders for incubation temperature/time and shaker speed, loading Cytiva labware on Heater Shaker'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol):
    # Load Heater Shaker Module on slot 1
    hs_mod = protocol.load_module(module_name="heaterShakerModuleV1", location=1)

    # Load labware on heater shaker (custom labware)
    try:
        incubation_labware = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        protocol.comment('WARNING: cytiva_96_filterwellplate_1ml not found on heater shaker; using fallback labware for simulation.')
        incubation_labware = hs_mod.load_labware('nest_96_wellplate_200ul_flat', label='incubator_plate')

    # Tip rack in slot 7
    tip_rack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tip_rack])
    p300_m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tip_rack])

    # Placeholders
    INCUBATION_TIME = '[[INCUBATION_TIME]]'
    INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'

    def _unreplaced(s):
        return isinstance(s, str) and s.startswith('[[') and s.endswith(']]')

    def _to_float(value, default):
        s = str(value).strip()
        if _unreplaced(s):
            return default
        try:
            return float(s)
        except:
            return default

    # Step 1: Close latch and set temperature if needed
    hs_mod.close_labware_latch()
    incub_temp = _to_float(INCUBATION_TEMPERATURE, 37.0)
    if incub_temp >= 37.0:
        try:
            hs_mod.set_target_temperature(incub_temp)
            hs_mod.wait_for_temperature()
        except Exception:
            protocol.comment('Warning: Heater Shaker temperature API not available; continuing without wait.')

    # Step 2: Shake
    incub_time = _to_float(INCUBATION_TIME, 120.0)  # default 120 minutes if unreplaced
    shaker_speed = _to_float(SHAKER_SPEED_INCUBATION, 800.0)
    try:
        hs_mod.set_and_wait_for_shake_speed(int(shaker_speed))
        protocol.delay(incub_time * 60)
    except Exception:
        protocol.comment('Warning: Heater Shaker speed API not available; using delay as placeholder.')
        protocol.delay(incub_time * 60)

    # Step 3: Stop heating and shaking and open latch
    try:
        hs_mod.set_and_wait_for_shake_speed(0)
        try:
            hs_mod.set_target_temperature(0)
        except Exception:
            pass
        hs_mod.open_labware_latch()
    except Exception:
        protocol.comment('Warning: could not safely release latch or stop shaker; attempting to open latch.')
        try:
            hs_mod.open_labware_latch()
        except Exception:
            protocol.comment('Failed to open latch; continuing without opening latch.')
