from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt/Ligand Matrix on Filter Plate',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand titrations on a filter plate mounted on a heater-shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (remain as literals for the template system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_N_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_N_LIGAND = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a value is still a [[PLACEHOLDER]] (for simulation only)."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder; fall back to default during simulation.

    Once the template engine has substituted real values, they must parse cleanly.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder; simulation uses default list."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # -------------------------------------------------------------------------
    # Parse placeholders with simulation fallbacks (use upper-range examples)
    # -------------------------------------------------------------------------
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume_ul = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)

    # Example: 4 salt concentrations, 8 ligand concentrations for simulation
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCS, [0, 100, 200, 500])
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCS,
        [1, 2, 4, 8, 16, 32, 64, 128]
    )

    num_salt = parse_int(PLACEHOLDER_N_SALT, len(salt_concs))
    num_ligand = parse_int(PLACEHOLDER_N_LIGAND, len(ligand_concs))

    # Sanity: enforce lengths to avoid quiet mismatches
    if num_salt != len(salt_concs):
        protocol.comment(
            'WARNING: NUMBER_OF_SALT_CONCENTRATIONS does not match '
            'SALT_CONCENTRATIONS length; using parsed list length.'
        )
        num_salt = len(salt_concs)
    if num_ligand != len(ligand_concs):
        protocol.comment(
            'WARNING: NUMBER_OF_LIGAND_CONCENTRATIONS does not match '
            'LIGAND_CONCENTRATIONS length; using parsed list length.'
        )
        num_ligand = len(ligand_concs)

    # Plate has 12 columns → replicates * num_salt must fit
    if replicates * num_salt > 12:
        raise RuntimeError(
            'Total number of target columns (REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS) '
            'exceeds 12 columns of a 96-well plate.'
        )

    # Each step uses half of the total volume per well
    per_well_half_vol = total_volume_ul / 2.0

    # -------------------------------------------------------------------------
    # Modules
    # -------------------------------------------------------------------------
    # Slot 1: Heater Shaker Module Gen1 with filter plate mounted directly on it
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # -------------------------------------------------------------------------
    # Labware
    # -------------------------------------------------------------------------
    # Tip racks (300 µL) in slots 4, 7, 10
    tiprack_primary = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_extra1 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_extra2 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (NEST 12-well 15 mL)
    # Slot 3: Reservoir 4 - contains the salt buffers (2x concentration)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)

    # Other reservoirs present but unused in this template (loaded to match layout)
    protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3
    protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0

    # Slot 11: NEST 96 Deep-Well Plate 2 mL (Mixing Plate)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Slot 1 on the Heater-Shaker: Filter Plate mounted directly, with simulation fallback
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # Simulator does not know the custom labware; use a 96-well plate fallback
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition for cytiva_96_filterwellplate_1ml '
            'not found; using NEST 96 flat plate as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # -------------------------------------------------------------------------
    # Pipettes
    # -------------------------------------------------------------------------
    # Left: P300 8-channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_primary, tiprack_extra1, tiprack_extra2]
    )

    # Right: P300 single-channel GEN2 (loaded but unused in this protocol)
    protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_primary, tiprack_extra1, tiprack_extra2]
    )

    # Ensure Heater-Shaker latch is closed before any pipetting on the module
    hs_mod.close_labware_latch()

    # -------------------------------------------------------------------------
    # STEP 1: Transfer buffers from Reservoir 4 to Filter Plate
    # -------------------------------------------------------------------------
    protocol.comment('Step 1: Transfer buffer from Reservoir 4 to filter plate')

    # Reservoir 4 wells: each well i contains 2x buffer for salt_concs[i]
    buffer_wells = reservoir4.wells()  # index 0..11

    # For salt index i, target filter-plate columns are
    #   [i*REPLICATES, ..., i*REPLICATES + REPLICATES-1]
    # Use multichannel: address each destination column by its A-row well and an offset

    for salt_index in range(num_salt):
        src_buffer = buffer_wells[salt_index]
        start_col = salt_index * replicates
        end_col = start_col + replicates

        # Columns on filter plate corresponding to this salt concentration
        dest_columns = filter_plate.columns()[start_col:end_col]

        # For 8-channel, use the A-row well of each column with 7 mm Z-offset
        dest_wells = [col[0].bottom(7.0) for col in dest_columns]

        # Use one tip column per salt group; return tips after use so rack can be reused
        p300_multi.pick_up_tip()
        p300_multi.transfer(
            per_well_half_vol,
            src_buffer,
            dest_wells,
            new_tip='never',
            blow_out=True,
            blowout_location='destination well'
        )
        # Return the current tips to their original positions in the rack
        p300_multi.return_tip()

    # -------------------------------------------------------------------------
    # STEP 2: Transfer ligands from Mixing Plate to Filter Plate
    # -------------------------------------------------------------------------
    protocol.comment('Step 2: Transfer ligands from mixing plate to filter plate')

    # For each salt concentration, one column of the mixing plate is used
    ligand_columns = mixing_plate.columns()[:num_salt]

    for salt_index, src_col in enumerate(ligand_columns):
        # Multichannel position: A-row of the given column (other rows share the same ligand series)
        src_well = src_col[0]

        start_col = salt_index * replicates
        end_col = start_col + replicates
        dest_columns = filter_plate.columns()[start_col:end_col]
        dest_wells = [col[0].bottom(7.0) for col in dest_columns]

        p300_multi.pick_up_tip()
        p300_multi.transfer(
            per_well_half_vol,
            src_well,
            dest_wells,
            new_tip='never',
            blow_out=True,
            blowout_location='destination well'
        )
        p300_multi.return_tip()

    protocol.comment('Protocol complete.')
