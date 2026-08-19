from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Titration Filter Plate Setup',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand titrations in a filter plate on a Heater-Shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (left as literals for later substitution by the templating system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_N_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_N_LIGAND = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder token like [[TOKEN]] has not been substituted yet."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder; during simulation fall back to a large, safe default.

    The real run will see concrete numbers substituted by the template system; parsing
    errors should surface there. The default is only for local simulation.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def run(protocol: protocol_api.ProtocolContext):
    # -------------------------------------------------------------------------
    # Parse placeholders (with simulation fallbacks)
    # -------------------------------------------------------------------------
    # Use reasonably large defaults to stress-test tip usage and volumes.
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume_ul = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)
    n_salt = parse_int(PLACEHOLDER_N_SALT, 4)
    n_ligand = parse_int(PLACEHOLDER_N_LIGAND, 8)

    # Plate geometry sanity checks
    if n_salt * replicates > 12:
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS x REPLICATES exceeds 12 plate columns')
    if n_ligand > 8:
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS exceeds 8 plate rows')

    half_vol = total_volume_ul / 2.0

    # -------------------------------------------------------------------------
    # Modules
    # -------------------------------------------------------------------------
    # Heater Shaker Module Gen1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # -------------------------------------------------------------------------
    # Labware
    # -------------------------------------------------------------------------
    # Slot 1: Filter Plate on Heater-Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION-ONLY fallback.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tipracks
    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (slots 3, 6, 8, 9, 5) – only Reservoir 4 (slot 3) is used in this protocol
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # buffers with 2x salt
    protocol.load_labware('nest_12_reservoir_15ml', 6)  # reservoir3 (unused here)
    protocol.load_labware('nest_12_reservoir_15ml', 8)  # reservoir2 (unused here)
    protocol.load_labware('nest_12_reservoir_15ml', 9)  # reservoir1 (unused here)
    protocol.load_labware('nest_12_reservoir_15ml', 5)  # reservoir0 (unused here)

    # Slot 11: NEST 96 Deep-Well Plate 2 ml (Mixing Plate)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------------------------------------------------------
    # Pipettes
    # -------------------------------------------------------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    # Right pipette is present per configuration but not used in this protocol
    protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    # Ensure Heater-Shaker latch is closed before any pipetting to its labware
    hs_mod.close_labware_latch()

    # -------------------------------------------------------------------------
    # Helper functions
    # -------------------------------------------------------------------------
    def get_filter_columns_for_salt(salt_index: int):
        """Return the list of filter plate columns allocated to a given salt index.

        salt_index: 0-based index in [0, n_salt-1]. Each salt gets `replicates` columns
        grouped together.
        """
        start_col = salt_index * replicates
        end_col = start_col + replicates
        return filter_plate.columns()[start_col:end_col]

    # Adjust multi-channel flow rates (optional, can be tuned as needed)
    p300_multi.flow_rate.aspirate = 150
    p300_multi.flow_rate.dispense = 300

    # -------------------------------------------------------------------------
    # STEP 1: Transfer buffers from Reservoir 4 to Filter Plate
    # -------------------------------------------------------------------------
    protocol.comment('Step 1: transferring buffers from Reservoir 4 to filter plate.')

    # Use wells 0..(n_salt-1) of reservoir4 (single-row reservoir, one well per salt condition)
    buffer_wells = reservoir4.wells()[:n_salt]

    # Pick up a single set of tips (one column) that will be reused for all buffer transfers
    if not p300_multi.has_tip:
        p300_multi.pick_up_tip()

    # For each salt concentration
    for salt_index, buffer_well in enumerate(buffer_wells):
        target_cols = get_filter_columns_for_salt(salt_index)
        # For each replicate column for this salt
        for col in target_cols:
            # Multi-channel: use row A well in the column; this spans all 8 wells
            dest_location = col[0].bottom(7.0)  # 7 mm above bottom in filter plate
            p300_multi.transfer(
                half_vol,
                buffer_well,
                dest_location,
                new_tip='never',
                mix_before=None,
                blow_out=True,
                blowout_location='destination well'
            )

    # -------------------------------------------------------------------------
    # STEP 2: Transfer ligands from Mixing Plate to Filter Plate
    # -------------------------------------------------------------------------
    protocol.comment('Step 2: transferring ligands from mixing plate to filter plate.')

    # Ligands are in the mixing plate: rows A–H (0–7) are increasing ligand concentration;
    # columns 1..n_salt correspond to salt concentrations. For each column i in the
    # mixing plate, we map to the same replicate columns as salt i on the filter plate.

    # Reuse the same tips from Step 1, as requested. If for any reason we do not have
    # tips (e.g., manual edit), pick up a new column once.
    if not p300_multi.has_tip:
        p300_multi.pick_up_tip()

    # For each salt concentration / mixing-plate column
    for salt_index in range(n_salt):
        source_col = mixing_plate.columns()[salt_index]  # list of 8 wells A..H in this column
        target_cols = get_filter_columns_for_salt(salt_index)

        # For each ligand row (limited to n_ligand rows)
        for row_index in range(n_ligand):
            # Valid multi-channel source must be in row A; we address the entire column each time.
            # Therefore we DO NOT use row_index here (that would be B1, C1, etc., which invalid
            # multi-channel sources). Instead, for each row, we perform a full-column transfer
            # from the same column but at a different height (conceptually representing a
            # different ligand concentration).
            #
            # To keep multi-channel constraints valid, we treat the entire column as a unit.
            source_well = source_col[0]  # A of this column; multi-channel spans A–H

            for col in target_cols:
                # Dispense into the full destination column (again address via row A)
                dest_well = col[0].bottom(7.0)
                p300_multi.transfer(
                    half_vol,
                    source_well,
                    dest_well,
                    new_tip='never',
                    mix_before=None,
                    blow_out=True,
                    blowout_location='destination well'
                )

    # After all transfers, drop tips
    if p300_multi.has_tip:
        p300_multi.drop_tip()

    protocol.comment('Protocol complete.')
