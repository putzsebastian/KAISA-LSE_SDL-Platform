from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Concentration Column Transfer Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for replicates and salt concentrations to transfer from a deep-well plate on a Heater-Shaker to a 360 uL plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (left as literal strings for the template system to replace)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder token has not yet been replaced.

    Uses constructed brackets so that no literal '[[' or ']]' appear in code,
    which keeps the templating system safe.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar numeric placeholder.

    If the token is still unreplaced, return the provided default (simulation only).
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder into a list of numbers.

    If the token is still unreplaced, return the provided default list (simulation only).
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ----------------------------------------------------------------------
    # 1) Derive num_transfers from placeholders
    # ----------------------------------------------------------------------
    # [[REPLICATES]]: scalar (e.g. 3)
    # [[SALT_CONCENTRATIONS]]: semicolon-separated list (e.g. "0;100;200")
    # For simulation we use worst-case-style defaults.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=float))
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float,
    )

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    # ----------------------------------------------------------------------
    # 2) Deck Layout
    # ----------------------------------------------------------------------
    # Slot 1: NEST 96 Deep-Well Plate 2 mL mounted directly on Heater-Shaker Module V1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    deepwell = hs_mod.load_labware('nest_96_wellplate_2ml_deep')

    # Slot 5: 360 uL 96-well plate (Corning 96 Well Plate 360 uL Flat)
    dest_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    # Right: P300 Single-Channel GEN2 (loaded but unused by this specific step)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )

    # Left: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    # Ensure the Heater-Shaker latch is closed before pipetting on its labware
    hs_mod.close_labware_latch()

    # Limit num_transfers to the available number of columns (12 in a 96-well plate)
    max_columns = 12
    if num_transfers > max_columns:
        protocol.comment(
            f"Computed num_transfers={num_transfers} exceeds available columns={max_columns}. "
            f"Capping to {max_columns} for this run."
        )
        num_transfers = max_columns

    # ----------------------------------------------------------------------
    # 3) Column-wise transfers with 8-channel pipette
    # ----------------------------------------------------------------------
    # For each i in num_transfers, transfer 200 uL from column i of the deep-well
    # plate to column i of the 360 uL plate.
    transfer_volume = 200  # uL per well in the column

    # columns() returns a list of 12 columns (each column is a list of 8 wells A–H)
    source_columns = deepwell.columns()[:num_transfers]
    dest_columns = dest_plate.columns()[:num_transfers]

    # Each column object (a list of 8 wells) is one multi-channel target.
    # Use one new tip per column transfer.
    p300_multi.transfer(
        transfer_volume,
        source_columns,
        dest_columns,
        new_tip='always'
    )
