from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt/Ligand Loading on Filter Plate',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand titrations on a Cytiva 96 filter plate on a Heater-Shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (must remain literal strings for the templating system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if s still looks like a [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse scalar placeholder value with a simulation fallback.

    Uses the largest reasonable default so simulation exercises worst case.
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


def run(protocol: protocol_api.ProtocolContext):
    # ------------------------------------------------------------------
    # Parse templated parameters (with simulation fallbacks)
    # ------------------------------------------------------------------
    # Use upper-bound style defaults for simulation: 4 salt, 8 ligands,
    # 3 replicates, 200 uL total per well.
    num_salt = parse_int(PLACEHOLDER_NUM_SALT, 4)
    num_lig = parse_int(PLACEHOLDER_NUM_LIG, 8)
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # uL per well total

    vol_per_step = total_volume / 2.0

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------
    # Heater-Shaker Module Gen1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ------------------------------------------------------------------
    # Labware
    # ------------------------------------------------------------------
    # Filter Plate on Heater-Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using NEST 96 flat plate as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks (all 300 uL)
    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    # Slot 3: Reservoir 4 (salt buffers, 2x concentration)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    # Additional reservoirs present but unused in this script
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    # Mixing plate with ligands
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ------------------------------------------------------------------
    # Pipettes
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Heater-Shaker latch state
    # ------------------------------------------------------------------
    # Ensure latch is closed before any pipetting on module labware
    hs_mod.close_labware_latch()

    # ------------------------------------------------------------------
    # Sanity comments
    # ------------------------------------------------------------------
    max_replicate_blocks = 12 // max(1, num_salt)
    if replicates > max_replicate_blocks:
        protocol.comment(
            f'WARNING: requested replicates ({replicates}) may exceed plate '
            f'capacity for {num_salt} salt conditions on a 12-column plate.'
        )

    protocol.comment(
        f'Configured with {num_salt} salt concentrations, {num_lig} ligand '
        f'concentrations, {replicates} replicates, {vol_per_step} uL per '
        f'step per well.'
    )

    # ------------------------------------------------------------------
    # STEP 1: Transfer buffers from Reservoir 4 to filter plate
    # ------------------------------------------------------------------
    # Each salt buffer (well in reservoir_4) -> [[REPLICATES]] columns
    # of filter plate, vol [[TOTAL_VOLUME]]/2 uL per well, 8-channel.

    filter_columns = filter_plate.columns()  # list of 12 column lists
    salt_wells = reservoir_4.wells()        # assume first num_salt wells hold buffers

    bottom_offset_mm = 7.0  # 7 mm above bottom in filter plate

    for i in range(num_salt):
        if i >= len(salt_wells):
            protocol.comment(
                f'Skipping salt index {i}: no corresponding well in Reservoir 4.'
            )
            continue

        src = salt_wells[i]
        start_col = i * replicates
        end_col = start_col + replicates

        if start_col >= 12:
            protocol.comment(
                f'Skipping salt index {i}: start_col {start_col} beyond plate columns.'
            )
            continue

        dest_cols = filter_columns[start_col:min(end_col, 12)]
        if not dest_cols:
            continue

        # One tip pickup per salt condition; tips are returned for reuse.
        p300_multi.pick_up_tip()

        # For multi-channel, use A-row well of each target column with bottom offset.
        dest_wells = [col[0].bottom(bottom_offset_mm) for col in dest_cols]

        p300_multi.transfer(
            vol_per_step,
            src,
            dest_wells,
            new_tip='never'
        )

        # Return tips to rack for reuse in Step 2
        p300_multi.return_tip()

    # ------------------------------------------------------------------
    # STEP 2: Transfer ligands from mixing plate to filter plate
    # ------------------------------------------------------------------
    # Each column in mixing plate -> same [[REPLICATES]] columns on filter plate
    # as the matching salt buffer in Step 1, vol [[TOTAL_VOLUME]]/2 uL per well.

    ligand_columns = mixing_plate.columns()  # 12 columns, A-H

    for i in range(num_salt):
        if i >= len(ligand_columns):
            protocol.comment(
                f'Skipping ligand column for salt index {i}: '
                f'no such column in mixing plate.'
            )
            continue

        src_col = ligand_columns[i]
        src = src_col[0]  # A-row well for multi-channel reference

        start_col = i * replicates
        end_col = start_col + replicates

        if start_col >= 12:
            protocol.comment(
                f'Skipping ligand transfer for salt index {i}: '
                f'start_col {start_col} beyond plate columns.'
            )
            continue

        dest_cols = filter_columns[start_col:min(end_col, 12)]
        if not dest_cols:
            continue

        p300_multi.pick_up_tip()

        dest_wells = [col[0].bottom(bottom_offset_mm) for col in dest_cols]

        p300_multi.transfer(
            vol_per_step,
            src,
            dest_wells,
            new_tip='never'
        )

        # Return tips again after ligand transfer
        p300_multi.return_tip()

    protocol.comment('Templated salt/ligand loading protocol complete.')
