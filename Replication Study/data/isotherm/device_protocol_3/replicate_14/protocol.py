from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Matrix on Filter Plate (Templated)',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt/ligand matrix on Cytiva 96 filter plate with heater-shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder literals (these will be replaced by the template system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_NUM_SALT_CONC = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIGAND_CONC = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder token like [[TOKEN]] has not been substituted yet."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder, falling back to a worst-case default for simulation."""
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
    """Parse a semicolon-separated list placeholder, with default for simulation."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # -------------------------------------------------------------------------
    # Parse templated parameters (using conservative worst-case defaults)
    # -------------------------------------------------------------------------
    # Use up to 4 replicates so that 4 salt concentrations can span all 12 columns
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # uL per well total

    # Example default gradients for simulation only
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 100.0, 200.0, 500.0])
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0.0, 10.0, 20.0, 40.0, 80.0, 160.0, 320.0, 640.0]
    )

    num_salt = parse_int(PLACEHOLDER_NUM_SALT_CONC, len(salt_concs))
    num_lig = parse_int(PLACEHOLDER_NUM_LIGAND_CONC, len(ligand_concs))

    # Limit to physical layout of 96‑well plate
    num_salt = min(num_salt, 12)
    num_lig = min(num_lig, 8)

    buffer_volume_per_well = total_volume / 2.0
    ligand_volume_per_well = total_volume / 2.0

    # -------------------------------------------------------------------------
    # Modules
    # -------------------------------------------------------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # -------------------------------------------------------------------------
    # Labware
    # -------------------------------------------------------------------------
    # Slot 1: Filter plate on Heater Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tipracks: Slots 4, 7, 10
    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs: Slots 3, 6, 8, 9, 5 (only Reservoir 4 in slot 3 used here)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (unused)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (unused)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (unused)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (unused)

    # Mixing plate: Slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------------------------------------------------------
    # Pipettes
    # -------------------------------------------------------------------------
    p300s = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    p300m = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    # Ensure latch is closed before pipetting on Heater Shaker
    hs_mod.close_labware_latch()

    # -------------------------------------------------------------------------
    # Step 1: Transfer buffers from Reservoir 4 to filter plate
    # -------------------------------------------------------------------------
    protocol.comment('Step 1: Transfer buffers from Reservoir 4 to filter plate')

    filter_columns = filter_plate.columns()  # 12 columns, each a list of 8 wells

    for salt_index in range(num_salt):
        # Source buffer well in Reservoir 4: one well per salt concentration
        buffer_source = reservoir4.wells()[salt_index]

        # Destination columns for this salt concentration:
        # a block of `replicates` consecutive columns per salt
        start_col = salt_index * replicates
        end_col = start_col + replicates
        dest_cols = filter_columns[start_col:end_col]

        if not dest_cols:
            protocol.comment(
                f'No destination columns available for salt index {salt_index}, skipping.'
            )
            continue

        for idx, col in enumerate(dest_cols):
            # Use a single multi‑channel transfer per destination column
            if not p300m.has_tip:
                p300m.pick_up_tip()

            src = buffer_source
            # Target the column via its A‑well with a 7 mm bottom offset
            dst = col[0].bottom(7.0)

            p300m.transfer(
                buffer_volume_per_well,
                src,
                dst,
                new_tip='never',
                blow_out=True,
                blowout_location='destination well'
            )

            # Return the tip to a defined rack position (reusable in next step)
            # Here we cycle over the first tiprack's row A positions
            tip_pos = tiprack_300_1.columns()[idx][0]
            p300m.drop_tip(tip_pos)

    # -------------------------------------------------------------------------
    # Step 2: Transfer ligands from mixing plate to filter plate
    # -------------------------------------------------------------------------
    protocol.comment('Step 2: Transfer ligands from mixing plate to filter plate')

    mixing_columns = mixing_plate.columns()

    for col_index in range(num_salt):
        if col_index >= len(mixing_columns):
            protocol.comment(
                f'Mixing plate has fewer columns than num_salt at index {col_index}, '
                'stopping ligand transfers.'
            )
            break

        lig_source_col = mixing_columns[col_index]

        start_col = col_index * replicates
        end_col = start_col + replicates
        dest_cols = filter_columns[start_col:end_col]

        if not dest_cols:
            protocol.comment(
                f'No destination columns for ligand column {col_index}, skipping.'
            )
            continue

        for ridx, dest_col in enumerate(dest_cols):
            if not p300m.has_tip:
                p300m.pick_up_tip()

            # Row A source defines the full column for multi‑channel
            src = lig_source_col[0]
            dst = dest_col[0].bottom(7.0)

            p300m.transfer(
                ligand_volume_per_well,
                src,
                dst,
                new_tip='never',
                blow_out=True,
                blowout_location='destination well'
            )

            # Return tip to rack (reusable pattern, mirroring Step 1)
            tip_pos = tiprack_300_1.columns()[ridx][0]
            p300m.drop_tip(tip_pos)

    protocol.comment('Protocol complete.')
