from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Titration Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand titrations'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder literals (wizard will substitute values)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIGAND = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if string still contains a [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse scalar placeholder to float (then cast), with simulation fallback."""
    s = str(value).strip()
    if _unreplaced(s):
        # simulation-only worst-case default
        return cast(default)
    return cast(float(s))


def parse_int(value, default):
    """Parse scalar placeholder to int, with simulation fallback."""
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse list placeholder ('a;b;c') to list, with simulation fallback."""
    s = str(value).strip()
    if _unreplaced(s):
        return [cast(x) for x in default]
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # -------------------------------------------------------------------------
    # Parse placeholders with conservative/worst-case defaults for simulation
    # -------------------------------------------------------------------------
    # Use a reasonably high default replicate count
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    # TOTAL_VOLUME in uL per well; split 50:50 between buffer and ligand
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)
    # Example default salt and ligand lists (ensure 96-well capacity)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 100, 200, 500])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [1, 2, 3, 4, 5, 6, 7, 8])

    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_ligand = parse_int(PLACEHOLDER_NUM_LIGAND, len(ligand_concs))

    # Basic sanity checks vs 96-well plate geometry
    if num_salt > 12:
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS cannot exceed 12 (plate columns).')
    if replicates * num_salt > 12:
        raise ValueError('REPLICATES * NUMBER_OF_SALT_CONCENTRATIONS cannot exceed 12 columns.')

    buffer_vol_per_well = total_volume / 2.0
    ligand_vol_per_well = total_volume / 2.0

    # -------------------------------------------------------------------------
    # Modules
    # -------------------------------------------------------------------------
    # Heater-Shaker Gen1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # -------------------------------------------------------------------------
    # Labware
    # -------------------------------------------------------------------------
    # Filter plate directly on Heater-Shaker (no adapter). Use custom labware
    # name as specified, with simulation fallback.
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Any error other than missing definition should surface
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using NEST 96 flat as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # NEST 12-well reservoirs
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (unused here)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (unused here)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (unused here)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (unused here)

    # NEST 96 deep-well plate (Mixing Plate) in slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------------------------------------------------------
    # Pipettes
    # -------------------------------------------------------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # Ensure latch is closed before any pipetting on Heater-Shaker
    hs_mod.close_labware_latch()

    # -------------------------------------------------------------------------
    # Helper to compute destination column groups per salt concentration
    # For salt index i (0-based), destination columns are:
    #   start = i * REPLICATES  through  end = start + REPLICATES - 1
    # Example: num_salt=4, replicates=3 -> salt 0 -> cols 0-2, salt 1 -> 3-5, etc.
    # -------------------------------------------------------------------------
    def get_dest_columns_for_salt_index(i: int):
        start = i * replicates
        end = start + replicates
        return filter_plate.columns()[start:end]

    # -------------------------------------------------------------------------
    # STEP 1: Transfer buffers from Reservoir 4 to Filter Plate
    # -------------------------------------------------------------------------
    protocol.comment('Step 1: Transfer buffer from Reservoir 4 to filter plate.')

    # Reservoir 4 holds the buffers; wells 0..(num_salt-1) each contain a 2x buffer
    buffer_wells = reservoir_4.wells()[:num_salt]

    # Use one dedicated tip column from slot 4 per salt concentration and
    # return the tip after use so it can be re-used in Step 2.
    for salt_index, buffer_well in enumerate(buffer_wells):
        # Destination columns for this salt concentration
        dest_cols = get_dest_columns_for_salt_index(salt_index)

        # For multi-channel, address each column via its A-well (index 0) and
        # apply a 7 mm bottom offset in the filter plate.
        dest_wells = [col[0].bottom(7.0) for col in dest_cols]

        # Map salt_index -> tip column in slot 4 (A1, A2, ...)
        tip_column_index = salt_index
        if tip_column_index >= 12:
            raise RuntimeError('Not enough tip columns in slot 4 to assign per salt concentration.')
        tip_well = tiprack_4.columns()[tip_column_index][0]

        # Pick specific tip, perform transfers with new_tip='never', then return
        p300_multi.pick_up_tip(tip_well)
        p300_multi.transfer(
            buffer_vol_per_well,
            buffer_well,
            dest_wells,
            new_tip='never'
        )
        # Return tip to original position for re-use in Step 2
        p300_multi.return_tip()

    # -------------------------------------------------------------------------
    # STEP 2: Transfer ligands from Mixing Plate to Filter Plate
    # -------------------------------------------------------------------------
    protocol.comment('Step 2: Transfer ligands from mixing plate to filter plate.')

    # For each salt concentration, a corresponding column in the mixing plate
    # contains 2x ligand dilutions for all ligand concentrations (rows A-H).
    for salt_index in range(num_salt):
        # Source column on the mixing plate for this salt concentration
        src_col = mixing_plate.columns()[salt_index]
        # For multi-channel, address via A-row (index 0)
        src_well = src_col[0]

        # Destination columns on filter plate for this salt
        dest_cols = get_dest_columns_for_salt_index(salt_index)
        dest_wells = [col[0].bottom(7.0) for col in dest_cols]

        # Reuse the same tip column from slot 4 as in Step 1
        tip_column_index = salt_index
        tip_well = tiprack_4.columns()[tip_column_index][0]

        p300_multi.pick_up_tip(tip_well)
        p300_multi.transfer(
            ligand_vol_per_well,
            src_well,
            dest_wells,
            new_tip='never'
        )
        p300_multi.return_tip()

    protocol.comment('Protocol complete.')
