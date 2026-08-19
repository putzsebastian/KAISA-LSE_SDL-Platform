from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol to run temperature-controlled shaking on the Heater-Shaker Module V1 using placeholders.'
}
requirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}

# Placeholders (must remain literal for the external templating system)
INCUBATION_TIME = '[[INCUBATION_TIME]]'             # minutes
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # degrees C
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'  # rpm


def _unreplaced(s: str) -> bool:
    """Detect an unreplaced token during simulation without writing '[[' as a literal."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Modules
    hs = protocol.load_module('heaterShakerModuleV1', 1)

    # Load labware onto the Heater-Shaker module. This is a custom definition; use a
    # simulation fallback if the custom definition is not available.
    try:
        filter_plate = hs.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs.load_labware('nest_96_wellplate_200ul_flat')

    # Labware (tip rack)
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])

    # Parse placeholders into numeric values for simulation; choose conservative defaults
    # so the simulation exercises temperature and shaking paths.
    incubation_time_min = parse_scalar(INCUBATION_TIME, default=10, cast=float)  # minutes
    incubation_temp_c = parse_scalar(INCUBATION_TEMPERATURE, default=37, cast=float)  # degC
    shaker_speed_rpm = parse_scalar(SHAKER_SPEED_INCUBATION, default=500, cast=int)  # rpm

    protocol.comment(f'Parsed incubation_time_min={incubation_time_min} min, incubation_temp_c={incubation_temp_c} C, shaker_speed_rpm={shaker_speed_rpm} rpm')

    # Step 1: Close labware latch and set temperature if >= 37 C
    protocol.comment('Step 1: Close labware latch on Heater-Shaker module')
    hs.close_labware_latch()

    if incubation_temp_c >= 37:
        protocol.comment(f'Setting Heater-Shaker target temperature to {incubation_temp_c} C and waiting to reach it')
        hs.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment(f'Incubation temperature {incubation_temp_c} C is below 37 C; skipping temperature set')

    # Step 2: Shake the Heater-Shaker for the requested time at the requested speed
    protocol.comment(f'Step 2: Set shake speed to {shaker_speed_rpm} rpm and incubate for {incubation_time_min} minutes')
    hs.set_and_wait_for_shake_speed(int(shaker_speed_rpm))

    # Wait for the incubation time (convert minutes to seconds)
    protocol.delay(seconds=incubation_time_min * 60)

    # Step 3: Stop heating and shaking and open the latch
    protocol.comment('Step 3: Deactivate shaker and heater, then open labware latch')
    hs.deactivate_shaker()
    hs.deactivate_heater()
    hs.open_labware_latch()

    protocol.comment('Protocol complete')
