from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated incubation on Heater Shaker with conditional temperature control.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with simulation-safe defaults (worst-case style)
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, 60.0)  # minutes
    incubation_temperature_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, 60.0)  # °C
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 2000.0)  # rpm

    # 1. Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # 2. Labware
    # Custom labware on Heater-Shaker in slot 1. Use fallback for simulation only.
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', '7')

    # 3. Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_300])

    # 4. Protocol Steps
    # Step 1: Close labware latch and conditionally set temperature
    hs_mod.close_labware_latch()

    if incubation_temperature_c >= 37.0:
        hs_mod.set_and_wait_for_temperature(incubation_temperature_c)
    else:
        protocol.comment('Incubation temperature below 37 °C; skipping temperature set step.')

    # Step 2: Shake for incubation time at specified speed
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking and open labware latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()

    hs_mod.open_labware_latch()

    protocol.comment('Incubation sequence complete.')
