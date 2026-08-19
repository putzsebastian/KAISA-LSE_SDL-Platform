from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated incubation on Heater Shaker with conditional temperature control and shaking.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder.

    During real runs, the wizard will substitute the placeholder strings
    with numeric values as text. During simulation (when the placeholders
    are still unreplaced), this returns a conservative worst-case default
    so the protocol can be validated.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # --- Modules ---
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # --- Labware on Heater Shaker (custom labware with simulation fallback) ---
    # Custom labware: cytiva_96_filterwellplate_1ml mounted directly on the module
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Any error other than "definition not found" should surface
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; '
            'using a standard plate as a SIMULATION fallback only.'
        )
        # Fallback to a 96-well standard plate for simulation only
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # --- Labware: Tip rack in slot 7 (per layout, even if unused) ---
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # --- Pipettes ---
    # Right: P300 Single GEN2
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        'right',
        tip_racks=[tiprack_300]
    )

    # Left: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        'left',
        tip_racks=[tiprack_300]
    )

    # --- Parse placeholders with conservative worst-case defaults for simulation ---
    # Use upper-bound defaults so the simulation exercises maximum load.
    incubation_time_min = parse_scalar(
        PLACEHOLDER_INCUBATION_TIME,
        default=120.0,  # minutes
        cast=float
    )
    incubation_temp_c = parse_scalar(
        PLACEHOLDER_INCUBATION_TEMPERATURE,
        default=80.0,   # °C
        cast=float
    )
    shaker_speed_rpm = parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default=2500.0,  # rpm
        cast=float
    )

    # --- Step 1: Close labware latch and conditionally set temperature ---
    hs_mod.close_labware_latch()

    if incubation_temp_c >= 37.0:
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment(
            'Skipping heater activation because target temperature '
            + str(incubation_temp_c)
            + ' is < 37'
        )

    # --- Step 2: Shake for incubation time at specified speed ---
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    # --- Step 3: Stop heating and shaking, then open latch ---
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()

    protocol.comment('Incubation and shaking step completed.')
