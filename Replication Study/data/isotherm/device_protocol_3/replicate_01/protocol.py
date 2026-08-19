from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Matrix on Filter Plate',
    'author': 'User',
    'description': 'Templated protocol with placeholders for salt and ligand concentrations, replicates, and total volume.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings so the template engine can substitute them)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if s still contains an unreplaced [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a numeric placeholder or fall back to a simulation default.

    Uses WORST-CASE style defaults so simulation stresses volumes/tips.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder or use default list."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --- Resolve placeholder-driven parameters (with simulation fallbacks) ---
    # Defaults chosen to represent a high-demand, but realistic, scenario.
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume_ul = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 100, 200, 500])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS,
                              [1, 2, 5, 10, 20, 50, 100, 200])

    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_lig = parse_int(PLACEHOLDER_NUM_LIG, len(ligand_concs))

    # Keep counts internally consistent
    num_salt = min(num_salt, len(salt_concs))
    num_lig = min(num_lig, len(ligand_concs))

    # ---------------------------------------------------------------------
    # Deck Setup
    # ---------------------------------------------------------------------

    # Slot 1: Heater-Shaker Module GEN1 with filter plate directly on it
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Filter plate on heater-shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Any other error (e.g. bad stacking) must surface
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (Slot naming per user; only reservoir_4 is used functionally here)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (buffers)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    # Slot 11: Mixing plate (NEST 96 Deep-Well Plate 2 mL)
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

    # Ensure latch is closed before any pipetting on HS
    hs_mod.close_labware_latch()

    # ---------------------------------------------------------------------
    # Helper calculations
    # ---------------------------------------------------------------------

    # Volume per reagent (half buffer, half ligand)
    half_total_volume = float(total_volume_ul) / 2.0

    # Reservoir 4: buffers with 2x salt concentrations, ascending in wells 0..(num_salt-1)
    buffer_wells = reservoir_4.wells()

    # Filter plate columns (12 total)
    filter_columns = filter_plate.columns()  # list of 12 column lists

    # Total columns required on filter plate for all salt concentrations
    total_columns_needed = num_salt * replicates
    if total_columns_needed > 12:
        raise RuntimeError(
            'Requested salt conditions x replicates (%d) exceed 12 columns of plate'
            % total_columns_needed
        )

    # Use starting tips from slot 4 and reuse them by returning
    p300_multi.starting_tip = tiprack_300_1.columns()[0][0]

    # ---------------------------------------------------------------------
    # Step 1:
    # Transfer buffers from Reservoir 4 to Filter Plate using 8-channel pipette
    # Each buffer -> [[REPLICATES]] columns, [[TOTAL_VOLUME]]/2 uL per well
    # ---------------------------------------------------------------------

    for salt_index in range(num_salt):
        source_well = buffer_wells[salt_index]
        start_col = salt_index * replicates
        end_col = start_col + replicates
        dest_columns = filter_columns[start_col:end_col]

        # Pick up one 8-channel column of tips, reuse across replicate columns, then return.
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        for dest_col in dest_columns:
            # For multi-channel, address the column via its A-row well (index 0).
            p300_multi.transfer(
                half_total_volume,
                source_well,
                dest_col[0].bottom(7.0),  # 7 mm above bottom in filter plate
                new_tip='never',
                blow_out=False
            )

        p300_multi.return_tip()

    # ---------------------------------------------------------------------
    # Step 2:
    # Transfer ligands from Mixing Plate (slot 11) to Filter Plate using 8-channel
    # Each column in mixing plate -> same [[REPLICATES]] columns group in filter plate
    # Volume [[TOTAL_VOLUME]]/2 uL per well.
    # ---------------------------------------------------------------------

    mixing_columns = mixing_plate.columns()

    for salt_index in range(num_salt):
        source_col = mixing_columns[salt_index]  # column of A-H ligand wells for this salt
        start_col = salt_index * replicates
        end_col = start_col + replicates
        dest_columns = filter_columns[start_col:end_col]

        # Reuse same tip column pattern: pick up, use, then return.
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        for dest_col in dest_columns:
            # Multi-channel transfer: specify the column via its A-row well
            p300_multi.transfer(
                half_total_volume,
                source_col[0],          # 2x ligand concentration in A-row of mixing plate
                dest_col[0].bottom(7.0),
                new_tip='never',
                blow_out=False
            )

        p300_multi.return_tip()

    protocol.comment('Salt-ligand matrix setup complete.')
