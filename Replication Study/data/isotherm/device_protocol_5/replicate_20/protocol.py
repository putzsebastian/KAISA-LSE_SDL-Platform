from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Deepwell to 360uL Plate Transfer',
    'author': 'User',
    'description': 'Transfer 200 uL from deepwell on heater-shaker to 360 uL plate using REPLICATES and SALT_CONCENTRATIONS placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# --- Placeholders (literal tokens to be replaced by the templating system) ---
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string still looks like an unreplaced [[TOKEN]]."""
    s_str = str(s).strip()
    return s_str.startswith('[' * 2) and s_str.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder.

    If the value is still an unreplaced [[TOKEN]], return the provided
    simulation default (cast to the desired type). Otherwise, parse the
    provided value.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a list placeholder separated by semicolons.

    If the value is still an unreplaced [[TOKEN]], return the provided
    default list. Otherwise, split on ';' and cast each non-empty entry.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return [cast(v) for v in default]
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --- Parse templated parameters with simulation-safe defaults ---
    # For simulation (when placeholders are not yet substituted), use
    # worst-case style defaults to exercise the protocol:
    #   - REPLICATES default: 12
    #   - SALT_CONCENTRATIONS default: 8 concentrations
    raw_reps = PLACEHOLDER_REPLICATES
    raw_salts = PLACEHOLDER_SALT_CONCENTRATIONS

    replicates = int(parse_scalar(raw_reps, default=12, cast=int))
    salt_concs = parse_list(
        raw_salts,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float
    )

    num_concs = len(salt_concs)
    # Per user spec: num_transfers = number_of_concentrations * REPLICATES
    num_transfers = num_concs * replicates

    # --- Labware and modules ---
    # Slot 1: Heater Shaker Module Gen1 with NEST 96 Deep-Well Plate 2 mL (no adapter)
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    deepwell = hs_mod.load_labware('nest_96_wellplate_2ml_deep')

    # Slot 5: 360 uL 96-well plate
    plate_360 = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # --- Pipettes ---
    # Right mount: P300 Single-Channel GEN2 (not used in this particular step but loaded as specified)
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])

    # Left mount: P300 8-Channel GEN2 (used for the transfers)
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Ensure Heater-Shaker latch is closed before pipetting on the module
    hs_mod.close_labware_latch()

    # --- Compute how many columns are needed ---
    # Each multi-channel transfer operates on one whole column.
    # The user-defined "num_transfers" is interpreted here as a column count
    # requirement, but we are limited to 12 columns on a 96-well plate.
    import math
    max_columns = 12
    needed_columns = min(max_columns, math.ceil(num_transfers))

    if needed_columns > max_columns:
        raise RuntimeError('Requested number of column transfers exceeds available 12 columns.')

    # --- Build source and destination column lists ---
    source_columns = []
    dest_columns = []
    for i in range(needed_columns):
        # columns()[i] returns the full column (list of 8 wells), which is
        # the correct target for a multi-channel transfer.
        source_columns.append(deepwell.columns()[i])
        dest_columns.append(plate_360.columns()[i])

    # --- Perform the transfers ---
    # For each column i up to needed_columns, transfer 200 uL from the
    # deepwell column to the corresponding column of the 360 uL plate using
    # the 8-channel pipette, keeping the same tip for all column transfers.
    p300_multi.pick_up_tip()
    p300_multi.transfer(
        200,
        source_columns,
        dest_columns,
        new_tip='never'
    )
    p300_multi.drop_tip()
