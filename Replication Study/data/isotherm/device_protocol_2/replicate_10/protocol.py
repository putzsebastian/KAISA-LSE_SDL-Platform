from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt Equilibration on Heater-Shaker',
    'author': 'User',
    'description': 'Equilibration of filter plate with salt buffers using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (templated values)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if a value is still a [[PLACEHOLDER]] token.

    Uses built brackets so the literal [[ never appears in code, keeping
    downstream substitution safe.
    """
    s_clean = str(s).strip()
    return s_clean.startswith('[' * 2) and s_clean.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder.

    During simulation, if the token is unreplaced, use the provided
    worst-case default. After substitution, a value that cannot be parsed
    must raise.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_int(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder into a Python list."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ---------------------------------------------------------------------
    # Parse templated parameters with simulation-safe defaults
    # ---------------------------------------------------------------------
    # Use a reasonably large default list for salt concentrations
    # (up to 8 entries here; num_transfers is capped by plate columns).
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float,
    )
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=3)
    equil_vol = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_VOLUME,
        default=200.0,
        cast=float,
    )
    shaker_speed = parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default=1000.0,
        cast=float,
    )
    equil_time_min = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION,
        default=60.0,
        cast=float,
    )

    # Derived parameter: total number of (concentration x replicate) conditions
    num_transfers = len(salt_concs) * replicates

    # Physical constraint: filter plate has 12 columns.
    if num_transfers > 12:
        protocol.comment(
            'WARNING: num_transfers exceeds 12; limiting to 12 for available columns.'
        )
        num_transfers = 12

    protocol.comment(
        f"Using {len(salt_concs)} salt concentrations, {replicates} replicates -> "
        f"{num_transfers} columns."
    )

    # ---------------------------------------------------------------------
    # Modules
    # ---------------------------------------------------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ---------------------------------------------------------------------
    # Labware
    # ---------------------------------------------------------------------
    # Filter Plate (custom labware) on Heater-Shaker (no adapter)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Surface real placement/stacking errors
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
            'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tipracks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    # NOTE: Deck layout labels from prompt:
    # Slot 3: Reservoir 4; Slot 6: Reservoir 3 (buffers); Slot 8: Reservoir 2;
    # Slot 9: Reservoir 1; Slot 5: Reservoir 0
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # buffers
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    # Mixing plate in slot 11 (not used in this step but loaded as specified)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ---------------------------------------------------------------------
    # Pipettes
    # ---------------------------------------------------------------------
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10],
    )

    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10],
    )

    # ---------------------------------------------------------------------
    # Step 2: Equilibration transfers
    # ---------------------------------------------------------------------
    # Close latch before pipetting on Heater-Shaker labware
    hs_mod.close_labware_latch()

    # For each i in num_transfers, transfer [[EQUILIBRATION_VOLUME]] uL
    # from well i of Reservoir 3 (slot 6) to each well in column i of
    # the filter plate. Use multi-channel pipette, 7 mm bottom offset,
    # and return tips after each column.

    for i in range(num_transfers):
        # Source in Reservoir 3: well index i (0-based)
        source_well = reservoir_3.wells()[i]

        # Destination: column i of filter plate
        dest_column = filter_plate.columns()[i]

        # Ensure we have a tip; then reuse within this column
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        # Use transfer() to allow automatic chunking if equil_vol > pipette max
        # Apply 7 mm offset from bottom in destination wells
        p300_multi.transfer(
            equil_vol,
            source_well,
            [w.bottom(7.0) for w in dest_column],
            new_tip='never',
        )

        # Return tip to rack after finishing this column
        p300_multi.return_tip()

    # ---------------------------------------------------------------------
    # Step 3: Shaking incubation
    # ---------------------------------------------------------------------
    hs_mod.set_and_wait_for_shake_speed(rpm=shaker_speed)
    protocol.delay(minutes=equil_time_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
