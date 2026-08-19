from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol that closes the heater-shaker latch, optionally sets temperature, shakes for an incubation time, then stops and opens the latch. Uses placeholders for templating.'
}
requirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}


def run(protocol: protocol_api.ProtocolContext):
    # Placeholders (must be literal for the external templating system)
    PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
    PLACEHOLDER_SHAKER_SPEED = '[[SHAKER_SPEED_INCUBATION]]'

    # Helpers to detect unreplaced tokens in simulation
    def _unreplaced(s: str) -> bool:
        return s.startswith('[' * 2) and s.endswith(']' * 2)

    def parse_scalar(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return cast(default)
        return cast(s)

    # Fallback defaults for simulation (choose conservative values to exercise behavior)
    DEFAULT_TEMPERATURE = 37.0        # degrees C (fallback)
    DEFAULT_INCUBATION_TIME = 60.0    # minutes (fallback)
    DEFAULT_SHAKER_SPEED = 1200       # RPM (fallback)

    # Parse placeholders (during real runs these will be substituted by the caller)
    incubation_temp = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, DEFAULT_TEMPERATURE, cast=float)
    incubation_time = parse_scalar(PLACEHOLDER_INCUBATION_TIME, DEFAULT_INCUBATION_TIME, cast=float)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED, DEFAULT_SHAKER_SPEED, cast=float)

    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load labware onto the heater-shaker module. Use a simulation fallback if the custom
    # definition is not available in the simulator environment.
    try:
        hs_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a 96-well fallback for SIMULATION only.')
        hs_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware (tip rack)
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_300])

    # Protocol steps
    protocol.comment('Step 1: Close labware latch of heater-shaker module')
    hs_mod.close_labware_latch()

    # Conditionally set temperature if >= 37 degC
    if incubation_temp >= 37.0:
        protocol.comment(f'Setting heater target temperature to {incubation_temp} C and waiting to reach target...')
        # Blocking set-and-wait for temperature
        hs_mod.set_and_wait_for_temperature(incubation_temp)
    else:
        protocol.comment('Incubation temperature is below 37 C; skipping heating step as requested.')

    # Step 2: Start shaking at specified speed and wait for the incubation time
    protocol.comment(f'Starting shaker at {shaker_speed} RPM and incubating for {incubation_time} minutes...')
    # set and wait for shake speed (blocking until target rpm reached)
    hs_mod.set_and_wait_for_shake_speed(int(shaker_speed))

    # Wait for the incubation duration (protocol.delay accepts minutes keyword)
    protocol.delay(minutes=incubation_time)

    # Step 3: Stop shaking and heating, then open the latch
    protocol.comment('Stopping shaker and heater...')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()

    protocol.comment('Opening labware latch of heater-shaker module')
    hs_mod.open_labware_latch()

    protocol.comment('Protocol complete.')
