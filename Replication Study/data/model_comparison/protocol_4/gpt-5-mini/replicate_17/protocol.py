from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol using placeholders for incubation temperature, time and shaker speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Placeholders (literal strings for wizard substitution)
    INCUBATION_TIME = '[[INCUBATION_TIME]]'
    INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'

    # Helpers to detect unreplaced tokens and parse values for simulation
    def _unreplaced(s: str) -> bool:
        return s.startswith('[' * 2) and s.endswith(']' * 2)

    def parse_scalar(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return default
        return cast(s)

    # Simulation fallbacks (chosen to exercise heating and shaking paths)
    FALLBACK_INCUBATION_TIME = 30       # minutes
    FALLBACK_INCUBATION_TEMPERATURE = 42.0  # degrees C (>=37 to trigger heating step in simulation)
    FALLBACK_SHAKER_SPEED = 1000        # RPM

    incubation_time = parse_scalar(INCUBATION_TIME, FALLBACK_INCUBATION_TIME, cast=float)
    incubation_temp = parse_scalar(INCUBATION_TEMPERATURE, FALLBACK_INCUBATION_TEMPERATURE, cast=float)
    shaker_speed = parse_scalar(SHAKER_SPEED_INCUBATION, FALLBACK_SHAKER_SPEED, cast=int)

    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load labware onto heater-shaker module (custom definition may be unavailable in simulator)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware on deck
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Step 1: close latch and set temperature if >= 37
    hs_mod.close_labware_latch()
    if incubation_temp >= 37:
        protocol.comment(f'Incubation temperature {incubation_temp} >= 37: setting heater and waiting to reach target')
        try:
            # blocking call: set and wait for temperature
            hs_mod.set_and_wait_for_temperature(incubation_temp)
        except Exception:
            # fallback to non-blocking + wait
            hs_mod.set_target_temperature(incubation_temp)
            hs_mod.wait_for_temperature()
    else:
        protocol.comment(f'Incubation temperature {incubation_temp} < 37: skipping heating step')

    # Step 2: set shaker speed and incubate for specified time
    protocol.comment(f'Setting shaker speed to {shaker_speed} RPM and incubating for {incubation_time} minutes')
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=incubation_time)

    # Step 3: stop heating and shaking, then open latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()