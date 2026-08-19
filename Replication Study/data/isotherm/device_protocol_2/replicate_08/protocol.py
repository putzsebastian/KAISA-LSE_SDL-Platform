from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration Template',
    'author': 'User',
    'description': 'Templated equilibration protocol using heater-shaker and filter plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (wizard will substitute literal strings)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Detect unreplaced [[TOKEN]] values for simulation.

    During simulation the placeholders are not yet substituted, so they
    appear literally as [[TOKEN]]. The real run will never see them.
    """
    s_str = str(s).strip()
    return s_str.startswith('[' * 2) and s_str.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder to a number, with a worst-case default.

    The default is only used during simulation when the placeholder is
    still unreplaced. On real hardware, a bad value should raise.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_int(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    Example substituted string: "0;100;200;500".
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ------------------------------------------------------------------
    # Parse placeholders with conservative worst-case defaults for SIM
    # ------------------------------------------------------------------
    # Use upper-bound style values so simulation exercises tip usage
    # and timing under demanding conditions. On the real robot the
    # placeholders will be replaced with the user-provided values.
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=8)
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
    )
    equilibration_vol = parse_scalar(PLACEHOLDER_EQUILIBRATION_VOLUME, default=200.0)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0)
    equilibration_time_min = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION,
        default=60.0,
    )

    # Step 1: number of transfers
    num_transfers = replicates * len(salt_concs)

    # The filter plate has 12 columns, so cap at 12 while warning.
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: Computed num_transfers ({num_transfers}) exceeds available 12 columns; "
            "only the first 12 will be used in this run."
        )
        num_transfers = 12

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # ------------------------------------------------------------------
    # Labware
    # ------------------------------------------------------------------
    # Filter plate on heater-shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # Only fall back if the definition is missing; re-raise other issues
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
            'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (slots and logical names per user spec)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (slot 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (slot 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (slot 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (slot 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (slot 5)

    # Mixing plate in slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ------------------------------------------------------------------
    # Pipettes
    # ------------------------------------------------------------------
    # Right: P300 Single GEN2 (not used in this specific workflow, but loaded per spec)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10],
    )

    # Left: P300 8-Channel GEN2 (used for the equilibration transfers)
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10],
    )

    # ------------------------------------------------------------------
    # Step 2 – Transfers from Reservoir 3 to filter plate columns
    # ------------------------------------------------------------------
    # Ensure latch is closed before any pipetting on the Heater-Shaker
    hs_mod.close_labware_latch()

    # Reservoir 3 wells hold the buffers in order; one well per transfer index i
    reservoir3_wells = reservoir_3.wells()  # 12 wells, indexed 0–11

    # Destination columns on the filter plate; multi-channel uses columns
    dest_columns = filter_plate.columns()[:num_transfers]

    # Tip height offset (7 mm above bottom of filter plate)
    dest_offset = 7.0

    for i in range(num_transfers):
        src = reservoir3_wells[i]
        dest_col = dest_columns[i]

        # Use a fresh tip for each transfer and return it to the rack
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        # Transfer [[EQUILIBRATION_VOLUME]] µL from reservoir well i to
        # every well in column i of the filter plate, using a 7 mm
        # bottom offset in the destination column.
        p300_multi.transfer(
            equilibration_vol,
            src,
            [well.bottom(dest_offset) for well in dest_col],
            new_tip='never',
        )
        p300_multi.return_tip()

    # ------------------------------------------------------------------
    # Step 3 – Shaking incubation
    # ------------------------------------------------------------------
    # Set shaker to [[SHAKER_SPEED_INCUBATION]] RPM for
    # [[EQUILIBRATION_CYCLE_DURATION]] minutes, then stop and open latch.
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=equilibration_time_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
