from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol to control a Heater-Shaker module using placeholders for incubation time, temperature and shaker speed.'
}
requirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}


def run(protocol: protocol_api.ProtocolContext):
    # Placeholders (must remain literal for external substitution)
    PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
    PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    PLACEHOLDER_SHAKER_SPEED = '[[SHAKER_SPEED_INCUBATION]]'

    # Helpers to detect unreplaced tokens and parse values for simulation
    def _unreplaced(s):
        s = str(s).strip()
        return s.startswith('[' * 2) and s.endswith(']' * 2)

    def parse_scalar(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            # Simulation fallback
            return cast(default)
        return cast(s)

    # Simulation-safe defaults (worst-case values to exercise the run)
    INCUBATION_TIME_DEFAULT = 60        # minutes
    INCUBATION_TEMPERATURE_DEFAULT = 37 # degC (>=37 to exercise heating branch)
    SHAKER_SPEED_DEFAULT = 1200         # rpm

    # Parse placeholders (during real run these will be replaced by concrete values)
    incubation_time = parse_scalar(PLACEHOLDER_INCUBATION_TIME, INCUBATION_TIME_DEFAULT, cast=float)
    incubation_temp = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, INCUBATION_TEMPERATURE_DEFAULT, cast=float)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED, SHAKER_SPEED_DEFAULT, cast=int)

    # MODULE: Heater-Shaker Gen1 in slot 1
    hs_mod = protocol.load_module(module_name='heaterShakerModuleV1', location=1)

    # Load labware directly on the Heater-Shaker. Use a simulation fallback for the custom definition.
    try:
        plate_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        plate_on_hs = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # TIP RACK in slot 7
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # PIPETTES
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])

    # Step 1: Close labware latch and set temperature if required
    protocol.comment('Step 1: Prepare Heater-Shaker for incubation (latch close and optional heating).')
    hs_mod.close_labware_latch()

    if incubation_temp >= 37:
        protocol.comment(f'Incubation temperature {incubation_temp} °C >= 37 °C: setting target temperature and waiting to reach it.')
        # Blocking set-and-wait to reach the requested temperature
        hs_mod.set_and_wait_for_temperature(incubation_temp)
    else:
        protocol.comment(f'Incubation temperature {incubation_temp} °C < 37 °C: skipping heating step.')

    # Step 2: Shake for incubation time at requested speed
    protocol.comment(f'Step 2: Start shaking at {shaker_speed} rpm and incubate for {incubation_time} minutes.')
    # Blocking set-and-wait to reach the requested shake speed
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)

    # Wait for the requested incubation period
    protocol.delay(minutes=incubation_time)

    # Step 3: Stop heating and shaking and open labware latch
    protocol.comment('Step 3: Stop heating and shaking, then open the labware latch.')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
