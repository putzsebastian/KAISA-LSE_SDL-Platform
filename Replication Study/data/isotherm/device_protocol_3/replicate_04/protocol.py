from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt/Ligand Gradient on Filter Plate',
    'author': 'User',
    'description': 'Templated protocol using placeholders to dispense salt buffers and ligands into a filter plate on a heater-shaker.'
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
    """Parse scalar placeholder with a simulation fallback.

    Value is interpreted as a float; `cast` can be used to coerce to another type.
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
    """Parse list placeholder like '0;100;200', with a simulation fallback list."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with simulation fallbacks (use upper-bound style defaults)
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # uL per well total
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 100, 200, 500])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [1, 2, 3, 4, 5, 6, 7, 8])
    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_lig = parse_int(PLACEHOLDER_NUM_LIG, len(ligand_concs))

    # Use only required number from lists
    salt_concs = salt_concs[:num_salt]
    ligand_concs = ligand_concs[:num_lig]

    volume_per_step = total_volume / 2.0  # uL per well for buffer or ligand

    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware
    # Filter plate on heater-shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Additional reservoirs present on deck (not actively used in this script but loaded as specified)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    # Heater-shaker latch handling: close latch before pipetting
    hs_mod.close_labware_latch()

    # Compute column groups for each salt / ligand condition
    # For each salt concentration, use `replicates` consecutive columns on the filter plate.
    filter_columns = filter_plate.columns()  # list of 12 column lists (each 8 wells)

    # Safety: ensure we do not exceed available columns
    total_required_columns = num_salt * replicates
    if total_required_columns > len(filter_columns):
        raise RuntimeError(
            'Total required columns (%d) exceed filter plate capacity (%d).' % (
                total_required_columns,
                len(filter_columns),
            )
        )

    # Map salt index to the corresponding group of filter plate columns
    salt_to_filter_col_groups = []  # list of lists of column lists
    col_start = 0
    for _ in range(num_salt):
        group = filter_columns[col_start:col_start + replicates]
        salt_to_filter_col_groups.append(group)
        col_start += replicates

    # STEP 1: Transfer buffers from Reservoir 4 to filter plate using p300 multi
    protocol.comment('Step 1: Distributing buffer from Reservoir 4 to filter plate.')

    for salt_index in range(num_salt):
        # Reservoir 4: use wells in order for each salt concentration (2x buffer)
        source_well = reservoir4.wells()[salt_index]
        target_columns_group = salt_to_filter_col_groups[salt_index]

        # Use one tip per source well (buffer condition), reused across replicate columns
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        for col in target_columns_group:
            # Multi-channel must use the A-row well; apply 7 mm bottom offset in filter plate
            dest = col[0].bottom(7.0)
            p300_multi.transfer(
                volume_per_step,
                source_well,
                dest,
                new_tip='never',
                blow_out=True,
                blowout_location='destination well'
            )
        p300_multi.return_tip()

    # STEP 2: Transfer ligands from Mixing Plate (slot 11) to filter plate
    protocol.comment('Step 2: Distributing ligands from mixing plate to filter plate.')

    mixing_columns = mixing_plate.columns()

    if num_salt > len(mixing_columns):
        raise RuntimeError(
            'Number of salt/ligand conditions (%d) exceeds mixing plate columns (%d).' % (
                num_salt,
                len(mixing_columns),
            )
        )

    for salt_index in range(num_salt):
        source_column = mixing_columns[salt_index]
        target_columns_group = salt_to_filter_col_groups[salt_index]

        # Use one tip per source column, reused across replicate columns
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        for dest_col in target_columns_group:
            # Transfer from top well (row A) of source column to top well of destination column
            src = source_column[0]
            dest = dest_col[0].bottom(7.0)
            p300_multi.transfer(
                volume_per_step,
                src,
                dest,
                new_tip='never',
                blow_out=True,
                blowout_location='destination well'
            )
        p300_multi.return_tip()
