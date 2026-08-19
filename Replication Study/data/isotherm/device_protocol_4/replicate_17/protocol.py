from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Heater Shaker Incubation',
    'author': 'User',
    'description': 'Heater Shaker incubation with placeholders for time, temperature, and shaking speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings, replaced by the template system)
PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string still contains an unreplaced [[TOKEN]]."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder.

    During TEMPLATE SUBSTITUTION on the real robot, the [[TOKEN]] will be
    replaced with a concrete value. In simulation (where tokens are still
    present), fall back to a conservative worst-case default so the
    protocol can be validated end-to-end.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with worst-case simulation defaults
    # Use large defaults so simulation exercises maximum duration/speed.
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME,
                                       default=60.0,
                                       cast=float)
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE,
                                     default=95.0,
                                     cast=float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION,
                                    default=3000.0,
                                    cast=float)

    # Load Heater-Shaker module in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load custom labware directly on the Heater-Shaker (no adapter).
    # Use a 96-well plate as a SIMULATION-ONLY fallback if the custom
    # definition is not available to the simulator.
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; '
            'using a standard plate as a SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Load tiprack in slot 7 (per deck layout)
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Load pipettes (not used in the current steps, but declared per spec)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    # STEP 1: Close labware latch and conditionally set temperature
    hs_mod.close_labware_latch()

    # Only set temperature if incubation_temp_c >= 37 °C
    if incubation_temp_c >= 37.0:
        protocol.comment(
            f'Setting Heater-Shaker temperature to {incubation_temp_c} °C '
            'for incubation.'
        )
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment(
            f'Skipping heater activation because incubation temperature '
            f'({incubation_temp_c} °C) is below 37 °C.'
        )

    # STEP 2: Shake for incubation_time_min at shaker_speed_rpm
    protocol.comment(
        f'Starting shaking at {shaker_speed_rpm} RPM for '
        f'{incubation_time_min} minutes.'
    )
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    # STEP 3: Stop heating and shaking and open labware latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()

    protocol.comment('Incubation on Heater-Shaker completed.')
