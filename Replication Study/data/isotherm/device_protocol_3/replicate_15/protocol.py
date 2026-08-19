from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Matrix Templated Protocol',
    'author': 'User',
    'description': 'Templated protocol for combining salt buffers and ligands in a filter plate using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder literals (wizard will substitute real values)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if string still looks like a [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder with a worst-case simulation default.

    Cast goes via float so that integer-like strings still parse.
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
    """Parse a semicolon-separated list placeholder.

    Example: "0;100;200;500" -> [0.0, 100.0, 200.0, 500.0]
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with worst-case simulation defaults
    # Use relatively large defaults so simulation exercises upper bounds.
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 100, 200, 500])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [1, 2, 3, 4, 5, 6, 7, 8])

    # If explicit numbers of concentrations are provided, use them;
    # otherwise derive from the lists.
    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_lig = parse_int(PLACEHOLDER_NUM_LIG, len(ligand_concs))

    # Volume per component (buffer + ligand = TOTAL_VOLUME)
    component_vol = total_volume / 2.0

    # -----------------------------
    # Modules
    # -----------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # -----------------------------
    # Labware
    # -----------------------------
    # Filter Plate on Heater Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml', label='Filter Plate')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat', label='Filter Plate Fallback')

    # Tipracks for P300 multi (slots 4, 7, 10)
    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (slots as specified; only Reservoir 4 used in this script)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3, label='Reservoir 4 (Salt Buffers)')
    protocol.load_labware('nest_12_reservoir_15ml', 6, label='Reservoir 3')
    protocol.load_labware('nest_12_reservoir_15ml', 8, label='Reservoir 2')
    protocol.load_labware('nest_12_reservoir_15ml', 9, label='Reservoir 1')
    protocol.load_labware('nest_12_reservoir_15ml', 5, label='Reservoir 0')

    # Mixing plate in slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11, label='Mixing Plate')

    # -----------------------------
    # Pipettes
    # -----------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    # Right-mount single-channel is loaded for completeness (not used in this protocol)
    protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    # Ensure Heater-Shaker latch is closed before pipetting
    hs_mod.close_labware_latch()

    # -----------------------------
    # Helpers
    # -----------------------------
    def get_filter_columns_for_salt_index(idx: int):
        """Return the list of filter plate columns assigned to a given salt index.

        Columns are assigned in contiguous blocks of size `replicates`.
        Example with 4 salt concentrations and 3 replicates:
        - salt_index 0 -> columns 0,1,2
        - salt_index 1 -> columns 3,4,5
        - salt_index 2 -> columns 6,7,8
        - salt_index 3 -> columns 9,10,11
        """
        start_col = idx * replicates
        end_col = start_col + replicates
        return filter_plate.columns()[start_col:end_col]

    # -----------------------------
    # STEP 1: Transfer salt buffers from Reservoir 4 to filter plate
    # -----------------------------
    protocol.comment('Step 1: Transfer salt buffers from Reservoir 4 to filter plate')

    # For each salt concentration, transfer buffer into its replicate block of columns
    for salt_index in range(num_salt):
        if salt_index >= len(salt_concs):
            break

        # Reservoir 4 wells are arranged with ascending salt concentration in ascending well index
        source_well = reservoir4.wells()[salt_index]
        dest_columns = get_filter_columns_for_salt_index(salt_index)

        # For multi-channel, name each destination column by its A-row well
        dest_wells = [col[0] for col in dest_columns]

        # Use one specific tip per salt index so it can be reused in Step 2
        tip_position = tiprack_300_1.columns()[salt_index][0]
        p300_multi.pick_up_tip(tip_position)

        for dest in dest_wells:
            # Aspirate from buffer reservoir (no special height required here)
            p300_multi.aspirate(component_vol, source_well)
            # Dispense into filter plate 7 mm above bottom
            p300_multi.dispense(component_vol, dest.bottom(7))
            p300_multi.blow_out(dest.top())

        # Return tip for reuse in Step 2
        p300_multi.return_tip()

    # -----------------------------
    # STEP 2: Transfer ligands from mixing plate to filter plate
    # -----------------------------
    protocol.comment('Step 2: Transfer ligands from mixing plate to filter plate')

    # For each salt concentration (i.e. each column in the mixing plate)
    for salt_index in range(num_salt):
        if salt_index >= len(salt_concs):
            break

        source_column = mixing_plate.columns()[salt_index]
        dest_columns = get_filter_columns_for_salt_index(salt_index)

        # Reuse the same tip position as in Step 1 for this salt index
        tip_position = tiprack_300_1.columns()[salt_index][0]
        p300_multi.pick_up_tip(tip_position)

        # For each replicate column on the filter plate
        for dest_col in dest_columns:
            # For each ligand concentration row up to num_lig
            for row_idx in range(num_lig):
                if row_idx >= len(source_column):
                    break
                src = source_column[row_idx]
                dst = dest_col[row_idx]
                p300_multi.aspirate(component_vol, src)
                p300_multi.dispense(component_vol, dst.bottom(7))
                p300_multi.blow_out(dst.top())

        p300_multi.return_tip()

    protocol.comment('Protocol complete.')
