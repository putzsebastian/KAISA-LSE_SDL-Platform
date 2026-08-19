from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Gradient Transfer Template',
    'author': 'User',
    'description': 'Templated salt gradient transfer using placeholders [[REPLICATES]] and [[SALT_CONCENTRATIONS]]'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (these will be replaced by the template system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if string still contains an unreplaced [[TOKEN]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder, with a simulation-time default.

    - `value`: raw placeholder string like '[[REPLICATES]]' or a substituted value.
    - `default`: numeric default used only during simulation when token is unreplaced.
    - `cast`: type constructor for the final value (int/float).
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    # always go through float first so '3.0' parses cleanly for int as well
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder into a list of numbers.

    Example substituted string: '0;100;200;300'
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ---------------------------------------------------------------
    # Parse template placeholders with simulation-time fallbacks
    # ---------------------------------------------------------------
    # Use the largest likely values as defaults so simulation stresses limits.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, 12, cast=float))
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float,
    )

    num_concentrations = len(salt_concs)
    num_transfers = num_concentrations * replicates

    # A 96-well plate has 12 columns; guard but do not fail if higher
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: Computed num_transfers={num_transfers} exceeds 12 columns; "
            f"only the first 12 columns will be used on 96-well plates."
        )
        num_transfers = 12

    # ---------------------------------------------------------------
    # Modules and labware
    # ---------------------------------------------------------------
    # Heater Shaker Module GEN1 in slot 1
    hs_module = protocol.load_module('heaterShakerModuleV1', 1)

    # NEST 96 Deep-Well Plate 2 mL on the Heater Shaker (no adapter on real robot).
    # For simulation we fall back to the supported HS+adapter combo if needed.
    try:
        deep_plate = hs_module.load_labware('nest_96_wellplate_2ml_deep')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: nest_96_wellplate_2ml_deep definition not found; '
            'using opentrons_96_deep_well_adapter_nest_wellplate_2ml_deep '
            'as SIMULATION fallback only.'
        )
        deep_plate = hs_module.load_labware(
            'opentrons_96_deep_well_adapter_nest_wellplate_2ml_deep'
        )

    # 360 uL 96-well destination plate in slot 5
    dest_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Opentrons 96 Tiprack 300 uL in slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # ---------------------------------------------------------------
    # Pipettes
    # ---------------------------------------------------------------
    # Right: P300 Single-Channel GEN2
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300],
    )

    # Left: P300 8-Channel (Multi-Channel) GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300],
    )

    # Ensure HS latch is closed before pipetting on it
    hs_module.close_labware_latch()

    # ---------------------------------------------------------------
    # Column-wise multi-channel transfers (protocol requirement)
    # ---------------------------------------------------------------
    # 1) num_transfers already computed as: len([[SALT_CONCENTRATIONS]]) * [[REPLICATES]]
    # 2) For each i in num_transfers, transfer 200 uL from column i of the deep-well
    #    plate (slot 1) to column i of the 360 uL plate (slot 5) using the 8-channel.

    # Source and destination columns (each element is a list of 8 wells: A–H)
    source_columns = deep_plate.columns()[:num_transfers]
    dest_columns = dest_plate.columns()[:num_transfers]

    # Use one multi-channel tip for all column transfers (same reagent per column index).
    if not p300_multi.has_tip:
        p300_multi.pick_up_tip()

    p300_multi.transfer(
        200,                # 200 uL per well in the column
        source_columns,
        dest_columns,
        new_tip='never',    # keep the same tip across all columns
    )

    p300_multi.drop_tip()

    protocol.comment(
        f"Completed {num_transfers} column-wise transfers of 200 uL using P300 multi-channel."
    )
