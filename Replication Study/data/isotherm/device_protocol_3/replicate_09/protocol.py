from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Matrix on Filter Plate (Templated)',
    'author': 'User',
    'description': 'Templated protocol to combine salt buffers and ligands on a filter plate using a heater-shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_N_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_N_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        # Simulation fallback: use provided default (worst-case reasonable value)
        return cast(float(default))
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        # Simulation fallback: use provided default list
        return [cast(x) for x in default]
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with simulation fallbacks (worst-case within intended bounds)
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)
    n_salt = parse_int(PLACEHOLDER_N_SALT, 4)
    n_lig = parse_int(PLACEHOLDER_N_LIG, 8)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 100, 200, 500])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [1, 2, 3, 4, 5, 6, 7, 8])

    # Basic validation (will still simulate with defaults)
    if n_salt != len(salt_concs):
        protocol.comment(
            f"WARNING: NUMBER_OF_SALT_CONCENTRATIONS ({n_salt}) does not match length of "
            f"SALT_CONCENTRATIONS list ({len(salt_concs)}). Using list length instead."
        )
        n_salt = len(salt_concs)

    if n_lig != len(ligand_concs):
        protocol.comment(
            f"WARNING: NUMBER_OF_LIGAND_CONCENTRATIONS ({n_lig}) does not match length of "
            f"LIGAND_CONCENTRATIONS list ({len(ligand_concs)}). Using list length instead."
        )
        n_lig = len(ligand_concs)

    # Each step adds half of TOTAL_VOLUME per well
    vol_per_step = total_volume / 2.0

    # =========================
    # Modules
    # =========================
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # =========================
    # Labware
    # =========================
    # Filter plate on Heater-Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Real error (e.g. bad stacking) should surface
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
            'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (0–4) laid out as requested, even if some are unused here
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # holds salt buffers
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # =========================
    # Pipettes
    # =========================
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    # Close latch before any pipetting on Heater-Shaker labware
    hs_mod.close_labware_latch()

    # Convenience references
    filter_columns = filter_plate.columns()   # list of 12 lists (columns)
    mixing_columns = mixing_plate.columns()

    # Sanity checks
    total_required_columns = n_salt * replicates
    if total_required_columns > 12:
        protocol.comment(
            f"WARNING: total required columns ({total_required_columns}) exceed 96-well "
            f"plate capacity (12 columns). Some intended conditions may not be placed."
        )

    if n_salt > 12:
        raise RuntimeError(
            'More than 12 salt concentrations are not supported with a single 96-well plate.'
        )

    # =========================
    # STEP 1: Buffers from Reservoir 4 to Filter Plate
    # =========================
    protocol.comment('Step 1: Transferring buffers from Reservoir 4 to Filter Plate')

    # For each salt concentration, there is one well in reservoir_4 (single row)
    for salt_index in range(n_salt):
        src_well = reservoir_4.wells()[salt_index]

        # Determine destination column indices for this salt: replicates per concentration
        start_col = salt_index * replicates
        end_col = start_col + replicates
        if start_col >= len(filter_columns):
            break

        dest_cols = filter_columns[start_col:min(end_col, len(filter_columns))]

        # For multi-channel, use the A-row (index 0) and a 7 mm bottom offset in the filter plate
        dest_locs = [col[0].bottom(z=7) for col in dest_cols]

        # Use and then return a single tip for this entire salt group
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        p300_multi.transfer(
            vol_per_step,
            src_well,
            dest_locs,
            new_tip='never'
        )

        # Return the tip for reuse in step 2
        p300_multi.return_tip()

    # =========================
    # STEP 2: Ligands from Mixing Plate to Filter Plate
    # =========================
    protocol.comment('Step 2: Transferring ligands from Mixing Plate to Filter Plate')

    for salt_index in range(n_salt):
        if salt_index >= len(mixing_columns):
            break

        # Each salt concentration corresponds to one column in the mixing plate
        src_col = mixing_columns[salt_index]
        src_well = src_col[0]  # A-row well used with multi-channel

        start_col = salt_index * replicates
        end_col = start_col + replicates
        if start_col >= len(filter_columns):
            break

        dest_cols = filter_columns[start_col:min(end_col, len(filter_columns))]
        dest_locs = [col[0].bottom(z=7) for col in dest_cols]

        # Reuse the returned tip
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        p300_multi.transfer(
            vol_per_step,
            src_well,
            dest_locs,
            new_tip='never'
        )

        # Return tip again for potential further reuse
        p300_multi.return_tip()
