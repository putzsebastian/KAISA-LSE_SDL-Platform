from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt/Ligand Transfer to Filter Plate',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand gradients into a Cytiva 96 filter plate on a Heater-Shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
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
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ------------ Parse placeholders (simulation fallbacks use worst-case values) ------------
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume_ul = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 100.0, 200.0, 500.0])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [1, 2, 3, 4, 5, 6, 7, 8])

    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_lig = parse_int(PLACEHOLDER_NUM_LIG, len(ligand_concs))

    # Safety: limit by what fits in a 96-well plate (12 columns, 8 rows)
    if num_salt > 12:
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS exceeds 12 columns.')
    if num_lig > 8:
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS exceeds 8 rows of a 96-well plate.')

    # Use only the first num_salt / num_lig entries
    salt_concs = salt_concs[:num_salt]
    ligand_concs = ligand_concs[:num_lig]

    per_component_volume_ul = total_volume_ul / 2.0

    # ------------ Modules ------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ------------ Labware ------------
    # Filter plate on Heater-Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (only Reservoir 4 is used for this protocol logic, but load all as specified)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    # Mixing plate in slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ------------ Pipettes ------------
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left',
                                          tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3])
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right',
                                           tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3])

    # Close HS latch before any pipetting
    hs_mod.close_labware_latch()

    # ------------ Helper: generate column groups for replicates ------------
    # For each salt / ligand concentration index i, use REPLICATES consecutive columns
    # Example: replicates=3 -> salt 0 -> cols 0,1,2; salt 1 -> cols 3,4,5; etc.

    def get_replicate_column_indices(salt_index: int, replicates_per_condition: int):
        start = salt_index * replicates_per_condition
        end = start + replicates_per_condition
        return list(range(start, end))

    # Basic safety: ensure we do not exceed 12 columns
    max_needed_columns = num_salt * replicates
    if max_needed_columns > 12:
        raise ValueError('num_salt * REPLICATES requires %d columns, but plate has only 12.' % max_needed_columns)

    # ------------ Step 1: Transfer buffers from Reservoir 4 to Filter Plate ------------

    # Reservoir 4 wells A1, A2, ... hold buffers at 2x salt concentrations
    # Use multi-channel p300, tips from slot 4/7/10. Offset 7 mm above bottom in filter plate.

    # For each salt concentration index, transfer to its group of replicate columns
    for salt_index in range(num_salt):
        source_well = reservoir_4.wells()[salt_index]
        replicate_col_indices = get_replicate_column_indices(salt_index, replicates)

        # Multi-channel: each transfer is column-wise (A-H). Use columns()[i][0] for A* column start.
        source_loc = source_well.bottom(1.0)  # slight offset above bottom in reservoir

        # Pick up one tip set per salt condition, reuse across replicate columns
        p300_multi.pick_up_tip()

        for col_idx in replicate_col_indices:
            dest_column = filter_plate.columns()[col_idx]
            dest_well_top = dest_column[0].bottom(7.0)  # offset 7 mm above bottom in filter plate

            # Use transfer with new_tip='never' since we manage tips manually
            p300_multi.transfer(
                per_component_volume_ul,
                source_loc,
                dest_well_top,
                new_tip='never',
                blow_out=True,
                blowout_location='destination well'
            )

        # Return tips to rack for reuse in step 2
        p300_multi.return_tip()

    # ------------ Step 2: Transfer ligands from Mixing Plate to Filter Plate ------------

    # Each mixing plate column corresponds to one salt concentration.
    # For each column i in mixing plate, transfer from that column into the same replicate
    # columns used in step 1.

    for salt_index in range(num_salt):
        source_column = mixing_plate.columns()[salt_index]
        replicate_col_indices = get_replicate_column_indices(salt_index, replicates)

        # Pick up tip set; in the real run, this will likely be the same physical tips
        # returned in step 1, depending on run configuration.
        p300_multi.pick_up_tip()

        for col_idx in replicate_col_indices:
            dest_column = filter_plate.columns()[col_idx]
            # Offset 7 mm above bottom of filter plate
            dest_well_top = dest_column[0].bottom(7.0)

            # Transfer per-component volume from the mixing plate column to each replicate column.
            p300_multi.transfer(
                per_component_volume_ul,
                source_column[0],
                dest_well_top,
                new_tip='never',
                blow_out=True,
                blowout_location='destination well'
            )

        p300_multi.return_tip()

    protocol.comment('Templated salt/ligand transfer protocol completed.')
