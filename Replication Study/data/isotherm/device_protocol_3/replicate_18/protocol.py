from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt-Ligand Mixing on Filter Plate',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand titrations on a filter plate with heater-shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (these will be replaced by the templating system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_N_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_N_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string still looks like an unreplaced [[TOKEN]]."""
    s_clean = str(s).strip()
    return s_clean.startswith('[' * 2) and s_clean.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder to a number, using a simulation fallback if unreplaced.

    The fallback should represent the *worst-case* value you expect, so that
    simulation stresses volume/tip usage appropriately.
    """
    text = str(value).strip()
    if _unreplaced(text):
        return cast(default)
    return cast(float(text))


def parse_int(value, default):
    text = str(value).strip()
    if _unreplaced(text):
        return int(default)
    return int(float(text))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    Example raw value: '0;100;200;500'
    """
    text = str(value).strip()
    if _unreplaced(text):
        return list(default)
    return [cast(v) for v in text.split(';') if v.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # -------------------------------------------------------------------------
    # Resolve placeholders with simulation fallbacks (choose upper-bound style
    # defaults so simulation is conservative on tips/volumes)
    # -------------------------------------------------------------------------
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume_ul = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)

    # Example: 4 salt concentrations, 8 ligand concentrations for simulation
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0, 100, 200, 500],
        float,
    )
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0, 1, 3, 10, 30, 100, 300, 1000],
        float,
    )

    n_salt = parse_int(PLACEHOLDER_N_SALT, len(salt_concs))
    n_lig = parse_int(PLACEHOLDER_N_LIG, len(ligand_concs))

    # Use the minimum of declared and actual list lengths (safety for mismatches)
    n_salt = min(n_salt, len(salt_concs))
    n_lig = min(n_lig, len(ligand_concs))

    # Derived per-step volume
    half_volume = total_volume_ul / 2.0

    protocol.comment(f'Replicates (columns per salt/ligand): {replicates}')
    protocol.comment(f'Total volume per well: {total_volume_ul} uL')
    protocol.comment(f'Buffer (salt) concentrations: {salt_concs[:n_salt]}')
    protocol.comment(f'Ligand concentrations: {ligand_concs[:n_lig]}')

    # -------------------------------------------------------------------------
    # Modules
    # -------------------------------------------------------------------------
    # Heater Shaker Module GEN1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    hs_mod.close_labware_latch()

    # -------------------------------------------------------------------------
    # Labware
    # -------------------------------------------------------------------------
    # Tip racks
    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Filter plate (custom labware) on Heater Shaker in slot 1, no adapter
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # On the real robot the custom definition will exist.
        # For SIMULATION ONLY, fall back to a standard 96-well plate.
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom filter plate definition not available; '
            'using a NEST 96 well plate as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Reservoirs (named per user, though only Reservoir 4 is used here)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0

    # Mixing plate (NEST 96 Deep-Well 2 mL) in slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------------------------------------------------------
    # Pipettes
    # -------------------------------------------------------------------------
    # Right: P300 Single-Channel Gen2 (not used in these steps but loaded per spec)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3],
    )

    # Left: P300 8-Channel Gen2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3],
    )

    # Use multi-channel for all column-wise operations
    p300_multi.flow_rate.aspirate = 94
    p300_multi.flow_rate.dispense = 94

    # -------------------------------------------------------------------------
    # Helper: destination column pattern per salt index
    # -------------------------------------------------------------------------
    # Columns are indexed 0–11 in code (1–12 in human terms).
    # Each salt concentration uses `replicates` adjacent columns.
    # Example (replicates = 3):
    #   salt 0 -> columns [0, 1, 2]
    #   salt 1 -> columns [3, 4, 5]
    #   salt 2 -> columns [6, 7, 8]
    #   salt 3 -> columns [9, 10, 11]

    def get_replicate_column_indices(salt_index: int):
        start = salt_index * replicates
        return list(range(start, start + replicates))

    # Ensure we do not exceed plate width
    if n_salt * replicates > 12:
        raise RuntimeError(
            'Number of salt concentrations times replicates exceeds 12 columns of the filter plate.'
        )

    # -------------------------------------------------------------------------
    # STEP 1: Transfer buffers from Reservoir 4 (slot 3) to filter plate on HS
    # -------------------------------------------------------------------------
    hs_mod.close_labware_latch()
    protocol.comment(
        'Step 1: Transferring buffer (salt) solutions from Reservoir 4 to filter plate.'
    )

    # Reservoir 4: one well per 2x salt concentration, in ascending order across wells
    src_buffer_wells = reservoir_4.wells()  # A1..A12

    for salt_idx in range(n_salt):
        src_well = src_buffer_wells[salt_idx]
        dest_col_indices = get_replicate_column_indices(salt_idx)
        dest_columns = [filter_plate.columns()[i] for i in dest_col_indices]

        protocol.comment(
            f'  Salt index {salt_idx} -> reservoir well {src_well.display_name}, '
            f'filter plate columns {dest_col_indices}'
        )

        # For each destination column: transfer [[TOTAL_VOLUME]]/2 µL per well
        # using the 8-channel pipette with a 7 mm bottom offset in the filter plate.
        # Tips are picked, used, and then returned to the rack to be reused in step 2.
        for col_group in dest_columns:
            # col_group: list of 8 wells (A–H) that form one column
            p300_multi.pick_up_tip()
            p300_multi.transfer(
                half_volume,
                src_well,
                [w.bottom(7.0) for w in col_group],
                new_tip='never',
                blow_out=True,
                blowout_location='source well',
            )
            # Return tip for reuse
            p300_multi.return_tip()

    # -------------------------------------------------------------------------
    # STEP 2: Transfer ligands from mixing plate (slot 11) to filter plate on HS
    # -------------------------------------------------------------------------
    hs_mod.close_labware_latch()
    protocol.comment(
        'Step 2: Transferring ligand solutions from mixing plate to filter plate.'
    )

    if n_salt > 12:
        raise RuntimeError('Number of salt concentrations exceeds number of columns (12).')

    # For each salt concentration, one column in the mixing plate is used.
    # That column is replicated into `replicates` columns of the filter plate, as
    # in step 1, again using [[TOTAL_VOLUME]]/2 µL per well and a 7 mm offset.
    for salt_idx in range(n_salt):
        src_col = mixing_plate.columns()[salt_idx]  # list of 8 wells (rows A–H)
        dest_col_indices = get_replicate_column_indices(salt_idx)
        dest_columns = [filter_plate.columns()[i] for i in dest_col_indices]

        protocol.comment(
            f'  Salt/ligand column index {salt_idx} -> mixing plate column {salt_idx}, '
            f'filter plate columns {dest_col_indices}'
        )

        for col_group in dest_columns:
            p300_multi.pick_up_tip()
            p300_multi.transfer(
                half_volume,
                [w for w in src_col],
                [w.bottom(7.0) for w in col_group],
                new_tip='never',
                blow_out=True,
                blowout_location='source well',
            )
            # Return tip after transfer, as requested
            p300_multi.return_tip()

    protocol.comment('Protocol complete.')
