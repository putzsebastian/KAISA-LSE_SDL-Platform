from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for incubation conditions on Heater Shaker with Cytiva 96 filter plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder literals (replaced by the wizard before real runs)
PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string is still an unreplaced [[TOKEN]]."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a numeric placeholder into a scalar, with a simulation fallback.

    During simulation the placeholders are still literal strings like
    '[[INCUBATION_TIME]]'. In that case, return the provided default so that
    the protocol can be simulated end-to-end.

    After substitution on the real robot, the placeholders become concrete
    numeric strings (e.g. '45'), which will then be cast to the requested
    type.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with conservative, worst-case simulation defaults
    # These defaults are ONLY used during simulation when the placeholders
    # have not yet been substituted.
    incubation_time_min = parse_scalar(
        PLACEHOLDER_INCUBATION_TIME,
        default=60.0,
        cast=float,
    )
    incubation_temp_c = parse_scalar(
        PLACEHOLDER_INCUBATION_TEMPERATURE,
        default=50.0,
        cast=float,
    )
    shaker_speed_rpm = parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default=2000.0,
        cast=float,
    )

    # Deck layout
    # Slot 1: Heater Shaker Module Gen1 with Cytiva 96 filter plate (no adapter)
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load custom Cytiva 96 filter well plate directly on the module.
    # For simulation we may not have this custom definition available, so
    # we fall back to a standard 96-well plate with identical well layout.
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Any error other than a missing custom definition must surface.
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; '
            'using a standard 96-well plate as a SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes (loaded per user specification; not used in current steps
    # but included so the template can be extended with pipetting later.)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300],
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300],
    )

    # Step 1: Close labware latch and conditionally set temperature
    hs_mod.close_labware_latch()

    if incubation_temp_c >= 37.0:
        protocol.comment(
            f'Setting Heater Shaker temperature to {incubation_temp_c} C for incubation.'
        )
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment(
            'Incubation temperature below 37C; skipping Heater Shaker temperature set.'
        )

    # Step 2: Shake the Heater Shaker Module for the specified time at
    # the specified speed.
    protocol.comment(
        f'Starting shaking at {shaker_speed_rpm} rpm for {incubation_time_min} minutes.'
    )
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    # Step 3: In the end stop heating and shaking and open labware latch.
    protocol.comment('Stopping shaking and heater, then opening labware latch.')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
