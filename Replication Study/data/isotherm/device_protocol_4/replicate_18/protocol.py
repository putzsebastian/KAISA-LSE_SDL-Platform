from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Heater Shaker Incubation',
    'author': 'User',
    'description': 'Templated protocol using placeholders for incubation conditions'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (kept as literal strings for later substitution)
PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if the given string still looks like an unreplaced [[TOKEN]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder into a numeric value.

    During real runs, the [[TOKENS]] will be replaced with concrete values that
    must parse successfully. During simulation, when the literal tokens are
    still present, fall back to a conservative *maximum* default so that
    tip/volume/time demands are validated under worst-case conditions.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # --- Modules ---
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # --- Labware on Heater-Shaker (custom labware with simulation fallback only) ---
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; '
            'using a standard 96-well plate as a SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # --- Tip rack ---
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # --- Pipettes ---
    p300_single = protocol.load_instrument(
        'p300_single_gen2', mount='right', tip_racks=[tiprack_300]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2', mount='left', tip_racks=[tiprack_300]
    )

    # --- Parse placeholders with worst-case defaults for simulation ---
    incubation_time_min = parse_scalar(
        PLACEHOLDER_INCUBATION_TIME,
        default=120.0,   # worst-case long incubation for validation
        cast=float,
    )
    incubation_temp_c = parse_scalar(
        PLACEHOLDER_INCUBATION_TEMPERATURE,
        default=95.0,    # high temperature to ensure heater usage is exercised
        cast=float,
    )
    shaker_speed_rpm = parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default=2000.0,  # high rpm to exercise shaker
        cast=float,
    )

    # --- Step 1: Close latch and conditionally set temperature if >= 37 °C ---
    hs_mod.close_labware_latch()

    if incubation_temp_c >= 37.0:
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)

    # --- Step 2: Shake for incubation time at specified speed ---
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    # --- Step 3: Stop heating and shaking and open latch ---
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
