from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Plate Transfer Template',
    'author': 'User',
    'description': 'Templated heater-shaker deep-well to 360 uL plate transfer using placeholders'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (left as literals for the wizard to substitute)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a string is still a [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=int):
    """Parse a scalar placeholder as a number with a simulation fallback.

    The default is used ONLY when the placeholder has not yet been
    substituted (i.e., during OT-2 simulation). Once substituted, any
    parsing error should raise.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder with fallback.

    Example value after substitution: "0;100;200;300" -> [0.0, 100.0, 200.0, 300.0]
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(v) for v in s.split(';') if v.strip()]


def run(protocol: protocol_api.ProtocolContext) -> None:
    # ---------------------------------------------------------------------
    # Parse user-specified placeholders with simulation-time fallbacks
    # ---------------------------------------------------------------------
    # Use the LARGEST sensible defaults so simulation exercises worst case.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=int)
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float,
    )

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    # A 96-well plate has 12 columns; cap at 12 to keep indices valid
    if num_transfers > 12:
        num_transfers = 12

    protocol.comment(f"Replicates: {replicates}")
    protocol.comment(f"Salt concentrations (simulation or parsed): {salt_concs}")
    protocol.comment(f"Computed num_transfers (columns used): {num_transfers}")

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------
    # Slot 1: Heater Shaker Module Gen1 with NEST 96 Deep-Well Plate 2 mL
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Deep well plate directly on the heater shaker (no adapter)
    deep_well_plate = hs_mod.load_labware('nest_96_wellplate_2ml_deep')

    # ------------------------------------------------------------------
    # Labware (off-module)
    # ------------------------------------------------------------------
    # Slot 5: 360 uL 96-well plate
    plate_360ul = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # ------------------------------------------------------------------
    # Pipettes
    # ------------------------------------------------------------------
    # Right mount: P300 Single-Channel GEN2 (loaded but unused in this step)
    p300s = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_300])

    # Left mount: P300 8-Channel GEN2
    p300m = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_300])

    # ------------------------------------------------------------------
    # Heater-Shaker latch handling
    # ------------------------------------------------------------------
    # Latch must be closed for any pipetting on heater-shaker labware
    hs_mod.close_labware_latch()

    # ------------------------------------------------------------------
    # Step 1 & 2: Column-wise transfers with multi-channel pipette
    # ------------------------------------------------------------------
    # Deep well plate and 360 uL plate both have 12 columns; use first
    # num_transfers columns on each.
    source_columns = deep_well_plate.columns()[:num_transfers]
    dest_columns = plate_360ul.columns()[:num_transfers]

    transfer_volume = 200.0  # uL per well (per channel)

    # Use a single set of tips for all column transfers (tip economy)
    p300m.pick_up_tip()
    for src_col, dst_col in zip(source_columns, dest_columns):
        # Each src_col/dst_col is one column (8 wells); the multi-channel
        # pipette will handle the entire column at once.
        p300m.transfer(
            transfer_volume,
            src_col,
            dst_col,
            new_tip='never',
        )
    p300m.drop_tip()
