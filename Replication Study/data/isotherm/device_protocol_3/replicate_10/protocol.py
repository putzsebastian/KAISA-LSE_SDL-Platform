from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Matrix Templated Assay',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt/ligand matrix setup in Cytiva filter plate on Heater-Shaker'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder like [[TOKEN]] has not yet been replaced."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder (e.g. volume) with a simulation fallback.

    Values are always routed through float() so that both integer and float
    substitutions work. The fallback should be the *worst case* allowed.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    """Parse a list placeholder like "0;100;200" into a list of numbers.

    The default should represent a realistic *upper bound* case for simulation.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # -----------------------------
    # Parse templated parameters
    # -----------------------------
    # Use upper-bound style defaults for simulation only
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)

    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 500, 800, 1000, 1500, 2000],
        cast=float,
    )
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        default=[0, 10, 20, 40, 80, 160, 320, 640],
        cast=float,
    )

    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_lig = parse_int(PLACEHOLDER_NUM_LIG, len(ligand_concs))

    # Bound by 96-well geometry (12 columns x 8 rows)
    num_salt = min(num_salt, 12)
    num_lig = min(num_lig, 8)

    # Ensure that salt blocks * replicates do not exceed 12 columns
    if replicates <= 0:
        replicates = 1
    replicates = min(replicates, 12 // max(1, num_salt))

    # Each step uses TOTAL_VOLUME / 2 per well
    per_well_volume = total_volume / 2.0

    # -----------------------------
    # Modules
    # -----------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # -----------------------------
    # Labware
    # -----------------------------
    # Slot 1: Cytiva 96 filter plate on Heater Shaker (custom labware with fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using NEST 96 flat plate as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (all NEST 12-well, 15 mL)
    # Only Reservoir 4 (slot 3) is used in this protocol per the user spec.
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # buffers with different salt
    protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (not used here)
    protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (not used here)
    protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (not used here)
    protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (not used here)

    # Mixing plate in slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -----------------------------
    # Pipettes
    # -----------------------------
    p300m = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3],
    )

    # Right pipette is not used in this protocol, but is present per configuration
    protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3],
    )

    # Close Heater-Shaker latch before any pipetting on the module
    hs_mod.close_labware_latch()

    # -----------------------------
    # Helper: column mapping on filter plate
    # -----------------------------
    # For each salt concentration i, use a contiguous block of `replicates` columns
    # Example: num_salt=4, replicates=3 -> blocks: [0-2], [3-5], [6-8], [9-11]
    filter_columns = filter_plate.columns()  # list of 12 column lists

    def get_filter_columns_for_salt_index(i: int):
        start = i * replicates
        end = start + replicates
        return filter_columns[start:end]

    # -----------------------------
    # Step 1: Transfer buffers from Reservoir 4 to filter plate
    # -----------------------------
    protocol.comment('Step 1: Transfer buffers from Reservoir 4 to filter plate')

    # Reservoir 4: wells 0..(num_salt-1) hold 2x buffers for each salt concentration
    reservoir4_wells = reservoir_4.wells()

    for salt_index in range(num_salt):
        src_well = reservoir4_wells[salt_index]
        target_columns = get_filter_columns_for_salt_index(salt_index)

        if not target_columns:
            continue

        # For multichannel, target the A-row well of each destination column and use a
        # 7 mm offset from the bottom in the filter plate.
        dest_positions = [col[0].bottom(z=7.0) for col in target_columns]

        # Pick up a tip, transfer to all replicate columns for this salt, then return
        # the tip so that physical tips can be reused across protocol steps.
        p300m.pick_up_tip()
        p300m.transfer(
            per_well_volume,
            src_well,
            dest_positions,
            new_tip='never',
        )
        p300m.return_tip()

    # -----------------------------
    # Step 2: Transfer ligands from Mixing Plate to filter plate
    # -----------------------------
    protocol.comment('Step 2: Transfer ligands from Mixing Plate to filter plate')

    # For each salt concentration there is one column on the mixing plate.
    mixing_columns = mixing_plate.columns()

    for salt_index in range(num_salt):
        src_column = mixing_columns[salt_index]
        target_columns = get_filter_columns_for_salt_index(salt_index)

        if not target_columns:
            continue

        # For multichannel, source is row A of the mixing plate column (represents
        # the whole column for an 8-channel pipette), destination is row A of each
        # replicate column in the filter plate with 7 mm bottom offset.
        src_location = src_column[0]
        dest_positions = [col[0].bottom(z=7.0) for col in target_columns]

        # In the real run, you may coordinate tip reuse with specific tip positions
        # returned in Step 1. For simplicity and robustness, here we pick up a
        # fresh tip for each column block and discard it afterwards.
        p300m.pick_up_tip()
        p300m.transfer(
            per_well_volume,
            src_location,
            dest_positions,
            new_tip='never',
        )
        p300m.drop_tip()

    protocol.comment('Salt-ligand matrix setup complete.')
