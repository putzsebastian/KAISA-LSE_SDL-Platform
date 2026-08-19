from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Concentration Plate Transfer',
    'author': 'User',
    'description': 'Template protocol using placeholders for replicates and salt concentrations.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (these will be substituted by the templating system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if string still contains an unreplaced [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder; fall back to default during simulation.

    Once the placeholder is replaced, this must parse cleanly or the protocol
    should raise, so we do not catch conversion errors here.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a list placeholder like "0;100;200"; fall back to default for sim."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    """Main protocol run function.

    Deck layout (OT-2):
    - Slot 1: NEST 96 Deep-Well Plate 2 mL (simulates plate on Heater Shaker).
    - Slot 5: Corning 96 Well Plate 360 uL Flat.
    - Slot 7: Opentrons OT-2 96 Tip Rack 300 uL.

    Pipettes:
    - Right: P300 Single-Channel GEN2.
    - Left: P300 8-Channel GEN2 (used for transfers).

    Logic:
    1. num_transfers = (# salt concentrations) * (replicates).
    2. For each i in [0, num_transfers), transfer 200 uL from column i+1 of
       deep-well plate to column i+1 of 360 uL plate, using multi-channel.
       (Capped at 12 columns because a 96-well plate has 12 columns.)
    """

    # --- Parse placeholder parameters ---
    # Use simulation-safe WORST-CASE defaults: up to 8 concentrations, 12 replicates
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 1, 2, 3, 4, 5, 6, 7],
    )
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=float))

    num_transfers = len(salt_concs) * replicates

    # --- Labware ---
    # NOTE: OT-2 API does not support the Heater Shaker module; we load the
    # deep-well plate directly in slot 1 for simulation. On the real system
    # with a Heater Shaker, this plate should be mounted directly on that
    # module in slot 1 without an adapter, as per user instructions.
    deepwell_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 1)

    dest_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # --- Pipettes ---
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_300])

    # --- Compute columns to use based on num_transfers ---
    # There are 12 columns in a 96-well plate; one multi-channel transfer per column.
    max_columns = 12
    columns_to_use = min(num_transfers, max_columns)

    protocol.comment(f"Number of salt concentrations: {len(salt_concs)}")
    protocol.comment(f"Replicates: {replicates}")
    protocol.comment(f"Computed num_transfers: {num_transfers}")
    protocol.comment(f"Using {columns_to_use} columns for transfer (capped at 12).")

    # --- Multi-channel column-wise transfers ---
    if columns_to_use <= 0:
        protocol.comment('No transfers requested (columns_to_use <= 0).')
        return

    if not p300_multi.has_tip:
        p300_multi.pick_up_tip()

    for col_index in range(columns_to_use):
        # columns()[i] is a list of 8 wells (A–H) representing one full column
        source_column = deepwell_plate.columns()[col_index]
        dest_column = dest_plate.columns()[col_index]

        # Transfer 200 uL to each well in the column.
        # new_tip='never' so we reuse the same tip for all columns, per requirements.
        p300_multi.transfer(
            200,
            source_column,
            dest_column,
            new_tip='never',
            blow_out=True,
            blow_out_location='destination well'
        )

    if p300_multi.has_tip:
        p300_multi.drop_tip()
