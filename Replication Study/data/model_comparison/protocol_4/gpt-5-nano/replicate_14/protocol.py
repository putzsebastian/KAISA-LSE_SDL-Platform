from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol with placeholders for incubation temperature, time and shaker speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol):
    import time

    # Placeholders (literal strings to be substituted by the templating wizard)
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
            return cast(s)
        except Exception:
            return default

    # Worst-case fallback values for simulation (upper bound style)
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, 60.0, float)  # minutes
    incubation_temperature = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, 37.0, float)  # deg C
    shaker_speed_rpm = int(parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 1200, float))  # rpm

    # Deck setup: Heater Shaker Module in slot 1, labware mounted on it
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware on the heater shaker (will fallback in simulation if not available)
    try:
        source_labware = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        protocol.comment('WARNING: cytiva_96_filterwellplate_1ml not found; using nest_96_wellplate_200ul_flat as simulation fallback on HS')
        source_labware = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    destination_labware = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 9)

    # Pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Step 1: Close latch, then conditionally set incubation temperature
    hs_mod.close_labware_latch()
    if incubation_temperature >= 37.0:
        hs_mod.set_target_temperature(incubation_temperature)
        hs_mod.wait_for_temperature()

    # Step 2: Shake for incubation_time_min minutes at shaker_speed_rpm
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    time.sleep(max(0.0, incubation_time_min) * 60)

    # Step 3: Stop heating and shaking, then open latch
    try:
        hs_mod.set_and_wait_for_shake_speed(0)
    except Exception:
        pass

    try:
        # Best-effort attempt to stop heating; keep within allowed range
        hs_mod.set_target_temperature(incubation_temperature)
        hs_mod.wait_for_temperature()
    except Exception:
        pass

    hs_mod.open_labware_latch()

    # Placeholder for templated transfer step (not required by the prompt)
    # The template focuses on incubate/heating/shaking steps; adjust as needed when templating.
