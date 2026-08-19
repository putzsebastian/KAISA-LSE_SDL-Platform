from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Mixing Templated',
    'author': 'User',
    'description': 'Templated protocol for distributing salt buffers and ligands into a filter plate on a heater-shaker using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIGAND = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder or return default for simulation.

    The placeholder will be replaced as a string before running on the robot.
    During simulation the literal token remains (e.g. '[[TOTAL_VOLUME]]'),
    which we detect and replace by a worst-case default.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder or return default.

    Example real value: '0;100;200;500'.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with simulation defaults (upper bounds where possible)
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # uL per well TOTAL

    # Worst-case: up to 4 salt concentrations and 8 ligand concentrations
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 100, 200, 500])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [1, 2, 3, 4, 5, 6, 7, 8])

    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_ligand = parse_int(PLACEHOLDER_NUM_LIGAND, len(ligand_concs))

    # Safety: cap by physical plate columns (12)
    num_salt = min(num_salt, 12)

    # Ensure column usage fits in 96-well plate (12 columns A1-H12)
    if replicates * num_salt > 12:
        raise RuntimeError('replicates * number_of_salt_concentrations exceeds 12 plate columns')

    half_volume = total_volume / 2.0

    # ----------------- Modules -----------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ----------------- Labware -----------------
    # Slot 1: Filter Plate on Heater Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware not found; using a standard plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tipracks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    # Slot 3: Reservoir 4 – buffers with different salt concentrations
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    # The following reservoirs are not used in this protocol but are loaded per layout
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    # Slot 11: Mixing Plate – NEST 96 Deep-Well Plate 2 mL
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ----------------- Pipettes -----------------
    # Right: P300 Single GEN2 (present but unused in this specific protocol)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        'right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # Left: P300 8-Channel GEN2 (used for all transfers)
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        'left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # ----------------- Heater-Shaker Prep -----------------
    hs_mod.close_labware_latch()

    # ----------------- Helper Functions -----------------
    def get_filterplate_columns_for_salt(salt_index: int):
        """Return the list of filter-plate columns used for a given salt index.

        Each salt concentration is assigned `replicates` consecutive columns
        starting at index `salt_index * replicates`.
        """
        start_col = salt_index * replicates
        end_col = start_col + replicates
        return filter_plate.columns()[start_col:end_col]

    # ----------------- STEP 1: Buffers to Filter Plate -----------------
    protocol.comment('Step 1: Transfer buffers from Reservoir 4 to filter plate')

    # Each salt concentration is in one well of Reservoir 4 (slot 3), ascending by well index
    for salt_index in range(num_salt):
        src_well = reservoir_4.wells()[salt_index]
        dest_cols = get_filterplate_columns_for_salt(salt_index)

        # Use one multichannel tip column per salt concentration, reused across its replicate columns
        p300_multi.pick_up_tip()
        for col in dest_cols:
            # col is a list of 8 wells (A..H) for that column. For multichannel,
            # addressing col[0] (row A) targets the entire column.
            dest_location = col[0].bottom(7.0)  # 7 mm above bottom in filter plate
            p300_multi.transfer(
                half_volume,
                src_well.bottom(1.0),  # 1 mm above bottom in reservoir
                dest_location,
                new_tip='never',
                blow_out=True,
                blowout_location='source well'
            )
        # Return tips to be reused in Step 2
        p300_multi.return_tip()

    # ----------------- STEP 2: Ligands to Filter Plate -----------------
    protocol.comment('Step 2: Transfer ligands from mixing plate to filter plate')

    # Each column in mixing plate corresponds to one salt concentration (same index)
    for salt_index in range(num_salt):
        src_col = mixing_plate.columns()[salt_index]
        dest_cols = get_filterplate_columns_for_salt(salt_index)

        # Reuse a tip column for all replicates of this salt index
        p300_multi.pick_up_tip()
        for col in dest_cols:
            dest_location = col[0].bottom(7.0)  # 7 mm above bottom in filter plate
            # Use A-row of mixing plate column as the multichannel source reference
            src_well = src_col[0]
            p300_multi.transfer(
                half_volume,
                src_well.bottom(1.0),
                dest_location,
                new_tip='never',
                blow_out=True,
                blowout_location='source well'
            )
        # Return tips after ligand transfer
        p300_multi.return_tip()

    protocol.comment('Protocol complete.')
