from opentrons import protocol_api
import time

metadata = {
    'protocolName': 'Heater Shaker Incubation Template (with placeholders)',
    'author': 'Lab 167',
    'description': 'Incubation protocol templated with placeholders for temperature, time and shaker speed',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Deck layout
    # Slot 1: Cytiva Cytiva 96 Filter Well Plate 1 mL mounted directly on Heater Shaker Gen1
    hs = protocol.load_module(module_name="heaterShakerModuleV1", location=1)

    try:
        labware_on_hs = hs.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # Simulation fallback: use a standard plate if custom labware not available
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        labware_on_hs = hs.load_labware('nest_96_wellplate_200ul_flat')

    # Slot 7: Tip rack 300 uL for the pipettes
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Placeholders (literal strings for templating)
    INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    INCUBATION_TIME = '[[INCUBATION_TIME]]'
    SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'

    def _unreplaced(s):
        return isinstance(s, str) and s.startswith('[[') and s.endswith(']]')

    def parse_scalar(value, default=None, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return default
        try:
            return cast(s)
        except Exception:
            return default

    incubation_temperature = parse_scalar(INCUBATION_TEMPERATURE, default=None, cast=float)
    incubation_time = parse_scalar(INCUBATION_TIME, default=None, cast=float)
    shaker_speed = parse_scalar(SHAKER_SPEED_INCUBATION, default=None, cast=float)

    # Step 1: Close latch and set temperature if >= 37
    if incubation_temperature is not None and incubation_temperature >= 37.0:
        # Ensure latch is closed before heating
        try:
            hs.close_labware_latch()
        except Exception:
            pass
        hs.set_and_wait_for_temperature(incubation_temperature)

    # Step 2: Shake for incubation_time minutes at shaker_speed rpm
    if incubation_time is not None and shaker_speed is not None:
        # Ensure latch is closed before shaking
        try:
            hs.close_labware_latch()
        except Exception:
            pass
        hs.set_and_wait_for_shake_speed(shaker_speed)
        # incubation_time in minutes -> seconds
        time.sleep(incubation_time * 60.0)

    # Step 3: Stop heating and shaking, then open latch
    try:
        hs.deactivate_shaker()
    except Exception:
        pass

    try:
        hs.deactivate_heater()
    except Exception:
        pass

    # Open latch to remove labware
    hs.open_labware_latch()
