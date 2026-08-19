from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Concentration Transfers Template',
    'author': 'User',
    'description': 'Templated protocol transferring from deep-well plate on heater shaker to 360 uL plate using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder.

    If the placeholder is unreplaced (still like [[TOKEN]]), return the
    provided default (cast to the given type). Otherwise, parse as float
    first so that both integer and float strings work, then cast.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    If unreplaced, return a copy of the default list. Otherwise, split the
    string on ';' and cast each non-empty item.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ------------------------------------------------------------------
    # 1. Resolve placeholder parameters (with simulation fallbacks)
    # ------------------------------------------------------------------
    # Use worst-case style defaults so simulation stresses tip and column usage.
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700]
    )
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=float))

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    # A 96-well plate has 12 columns. Truncate in simulation if more are requested.
    if num_transfers > 12:
        protocol.comment(
            'WARNING: num_transfers (%d) exceeds available columns (12). '
            'Truncating to 12 for this run.' % num_transfers
        )
        num_transfers = 12

    # ------------------------------------------------------------------
    # 2. Modules and labware
    # ------------------------------------------------------------------
    # Heater Shaker Module GEN1 in slot 1, deep-well plate mounted directly.
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    deep_well_plate = hs_mod.load_labware('nest_96_wellplate_2ml_deep')

    # 360 uL 96-well plate in slot 5
    plate_360 = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Tip rack in slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # ------------------------------------------------------------------
    # 3. Pipettes
    # ------------------------------------------------------------------
    # Right: P300 single-channel GEN2 (not used in this simple transfer but
    # loaded as specified)
    protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )

    # Left: P300 8-channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    # Ensure latch closed before any pipetting on the Heater-Shaker labware
    hs_mod.close_labware_latch()

    # ------------------------------------------------------------------
    # 4. Compute columns and perform transfers
    # ------------------------------------------------------------------
    # Use columns 1..num_transfers on both plates.
    deep_cols = deep_well_plate.columns()[:num_transfers]
    dest_cols = plate_360.columns()[:num_transfers]

    transfer_volume = 200.0  # uL

    # Each element of deep_cols/dest_cols is a list of 8 wells (one column).
    # Passing the column lists directly to transfer() is correct for
    # multi-channel: one command per column, 200 uL per well in that column.
    p300_multi.transfer(
        transfer_volume,
        deep_cols,
        dest_cols,
        new_tip='always'
    )
