from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt Concentration Transfers',
    'author': 'User',
    'description': 'Transfer 200 uL from deep-well plate on Heater-Shaker to 360 uL plate using placeholders for replicates and salt concentrations.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder literals (these will be replaced by the wizard)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a value is still an unreplaced [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder.

    During simulation, when the placeholder is still literal [[...]],
    fall back to the provided *worst-case* default.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    # Go via float so that values like '3.0' are accepted for integer casts
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    During simulation, when the placeholder is still literal [[...]],
    fall back to the provided *worst-case* default list.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --- Parse templated parameters ---
    # Use large, worst-case defaults for simulation so that tip usage and
    # column indexing are exercised at maximum expected load.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 12, int)
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        # Worst-case example list: 8 different concentrations
        [0, 1, 2, 3, 4, 5, 6, 7],
        float,
    )

    num_concentrations = len(salt_concs)

    # USER STEP 1: num_transfers = number_of_concentrations * replicates
    num_transfers = num_concentrations * replicates

    # A 96-well plate has 12 columns; cap to 12 for safety.
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: num_transfers ({num_transfers}) exceeds 12 columns; "
            "only the first 12 columns will be used on the 96-well plates."
        )
        num_transfers = 12

    # --- Modules ---
    # Heater-Shaker Module GEN1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # --- Labware ---
    # Slot 1: NEST 96 Deep-Well Plate 2 mL mounted directly on Heater-Shaker (no adapter)
    deep_well_plate = hs_mod.load_labware(
        'nest_96_wellplate_2ml_deep',
        label='Deep Well Source Plate'
    )

    # Slot 5: 360 uL 96-well plate
    dest_plate = protocol.load_labware(
        'corning_96_wellplate_360ul_flat',
        5,
        label='360uL Destination Plate'
    )

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware(
        'opentrons_96_tiprack_300ul',
        7
    )

    # --- Pipettes ---
    # Left: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    # Right: P300 Single-Channel GEN2 (loaded but unused here, as specified)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )

    # Ensure Heater-Shaker latch is closed before pipetting on the module
    hs_mod.close_labware_latch()

    # --- Transfers ---
    # USER STEP 2: For each i in num_transfers, transfer 200 uL from column i
    # of the deep-well plate (slot 1 on Heater-Shaker) to column i of the
    # 360 uL 96-well plate in slot 5, using the 8-channel pipette.
    transfer_volume = 200.0  # uL

    # columns() returns a list of column lists; for a multi-channel pipette,
    # we address the column via its row A well (index 0 of each column list).
    source_columns = deep_well_plate.columns()[:num_transfers]
    dest_columns = dest_plate.columns()[:num_transfers]

    p300_multi.transfer(
        transfer_volume,
        [col[0] for col in source_columns],
        [col[0] for col in dest_columns],
        new_tip='always'
    )
