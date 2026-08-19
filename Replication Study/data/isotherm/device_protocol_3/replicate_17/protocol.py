from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Matrix on Filter Plate',
    'author': 'User',
    'description': 'Templated protocol to combine salt buffers and ligands on a filter plate using placeholders.'
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
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return [cast(v) for v in default]
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Parse parameters with simulation-friendly defaults (worst-case within expected ranges)
    num_replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # uL per well total
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 100, 200, 500])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS,
                              [0, 1, 2, 3, 4, 5, 6, 7])

    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_lig = parse_int(PLACEHOLDER_NUM_LIG, len(ligand_concs))

    # Safety: truncate to available
    num_salt = min(num_salt, 12)
    num_lig = min(num_lig, 8)

    # Derived volumes
    half_volume = total_volume / 2.0

    protocol.comment(f"Replicates: {num_replicates}")
    protocol.comment(f"Total volume per well: {total_volume} uL (buffer + ligand)")
    protocol.comment(f"Number of salt concentrations: {num_salt}")
    protocol.comment(f"Number of ligand concentrations: {num_lig}")

    # Modules and labware
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Custom filter plate on heater-shaker with simulation fallback
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION ONLY fallback.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    hs_mod.close_labware_latch()

    tiprack_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (decked but not used yet except Reservoir 4 for buffers)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2', 'left', tip_racks=[tiprack_1, tiprack_2, tiprack_3]
    )
    p300_single = protocol.load_instrument(
        'p300_single_gen2', 'right', tip_racks=[tiprack_1, tiprack_2, tiprack_3]
    )

    # Step 1: Transfer buffers from Reservoir 4 (slot 3) to filter plate
    protocol.comment('Step 1: Transferring buffer from Reservoir 4 to filter plate')

    buff_source_wells = reservoir_4.wells()  # A1..A12 as a list

    for i in range(num_salt):
        src = buff_source_wells[i]
        start_col = i * num_replicates
        end_col = start_col + num_replicates  # exclusive
        dest_cols = filter_plate.columns()[start_col:end_col]

        if not dest_cols:
            continue

        # Use multi-channel, one tip per source buffer, reused across its replicate columns
        if not p300_multi.has_tip:
            if i < 12:
                p300_multi.pick_up_tip(tiprack_1.columns()[i][0])
            else:
                p300_multi.pick_up_tip()

        src_loc = src.bottom(1.0)
        for col in dest_cols:
            dest = col[0]
            dest_loc = dest.bottom(7.0)
            p300_multi.transfer(
                half_volume,
                src_loc,
                dest_loc,
                new_tip='never',
                blow_out=False,
                mix_after=(0, 0)
            )

        # Return tip to its original location for reuse in step 2
        p300_multi.return_tip()

    # Step 2: Transfer ligands from mixing plate (slot 11) to filter plate
    protocol.comment('Step 2: Transferring ligands from mixing plate to filter plate')

    # For each ligand column (0..num_salt-1), map to the corresponding group of replicate columns
    for i in range(num_salt):
        if i >= len(mixing_plate.columns()):
            break

        src_col = mixing_plate.columns()[i]  # list of wells A..H in column i
        src = src_col[0]  # A row, multi-channel source

        start_col = i * num_replicates
        end_col = start_col + num_replicates
        dest_cols = filter_plate.columns()[start_col:end_col]

        if not dest_cols:
            continue

        # Reuse the same tips used for the corresponding buffer transfer (parked in same column)
        if not p300_multi.has_tip:
            if i < 12:
                p300_multi.pick_up_tip(tiprack_1.columns()[i][0])
            else:
                p300_multi.pick_up_tip()

        src_loc = src.bottom(1.0)
        for col in dest_cols:
            dest = col[0]
            dest_loc = dest.bottom(7.0)
            p300_multi.transfer(
                half_volume,
                src_loc,
                dest_loc,
                new_tip='never',
                blow_out=False,
                mix_after=(0, 0)
            )

        p300_multi.return_tip()

    protocol.comment('Protocol complete.')
