from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Heater shaker incubation with placeholders for time, temperature, and speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder to a float, with a simulation fallback.

    The wizard will replace the placeholder string with a concrete value.
    During simulation, the placeholder remains unreplaced, so we fall back
    to a worst-case value to exercise tip and time usage.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Parameters parsed from placeholders with simulation fallbacks (worst-case values)
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, 60.0)  # minutes
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, 60.0)  # °C
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 1000.0)  # rpm

    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # Labware on heater shaker - custom labware with simulation fallback only
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; using a standard plate as a '
            'SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware: tips
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', '7')

    # Pipettes (loaded but not used in this incubation-only template)
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Step 1: Close latch and conditionally set temperature
    hs_mod.close_labware_latch()

    if incubation_temp_c >= 37.0:
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment(
            f'Skipping heater activation: incubation temperature {incubation_temp_c} °C < 37 °C.'
        )

    # Step 2: Shake for incubation time at specified speed
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking, open latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()

    protocol.comment('Incubation step complete.')
