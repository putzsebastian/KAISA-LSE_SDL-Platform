from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Concentration Transfers',
    'author': 'User',
    'description': 'Templated protocol using [[REPLICATES]] and [[SALT_CONCENTRATIONS]] to control column-wise transfers from a deep-well plate on a Heater-Shaker to a 360 uL plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (templating tokens)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a value is still an unreplaced [[TOKEN]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder; fall back to a worst-case default for simulation.

    The default is used ONLY when the placeholder has not yet been substituted
    (i.e. still looks like [[TOKEN]]). Once substituted, a bad value should
    raise so that runs fail loudly instead of silently using a default.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a list placeholder (semicolon-separated); worst-case default for simulation."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # -------------------------
    # Parse templated parameters
    # -------------------------
    # Use large defaults so simulation exercises worst-case usage
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, 8, cast=float))
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0, 100, 200, 300, 400, 500, 600, 700],
        cast=float,
    )

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    # 96-well plates have 12 columns; cap to 12 and warn if exceeded
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: Computed num_transfers ({num_transfers}) exceeds 12 columns; "
            "only the first 12 will be used on a 96-well plate."
        )
        num_transfers = 12

    # -------------------------
    # Modules and labware
    # -------------------------
    # Slot 1: Heater Shaker Module GEN1 with NEST 96 Deep-Well Plate 2 mL directly on it
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    hs_mod.close_labware_latch()
    deep_well_plate = hs_mod.load_labware('nest_96_wellplate_2ml_deep')

    # Slot 5: 360 uL 96-well plate (Corning flat 360 uL)
    dest_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # -------------------------
    # Pipettes
    # -------------------------
    # Right mount: P300 Single-Channel GEN2 (loaded but unused here, per requirements)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300],
    )

    # Left mount: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300],
    )

    # Log computed parameters
    protocol.comment(f"Replicates: {replicates}")
    protocol.comment(f"Number of salt concentrations: {num_concs}")
    protocol.comment(f"Number of column transfers (num_transfers): {num_transfers}")

    # -------------------------
    # Column-wise transfers (step 2)
    # -------------------------
    # For each i in num_transfers, transfer 200 uL from column i of the deep-well plate
    # to column i of the 360 uL plate, using the 8-channel pipette.
    # columns()[0] corresponds to column 1, etc.; we slice up to num_transfers.
    source_columns = deep_well_plate.columns()[:num_transfers]
    dest_columns = dest_plate.columns()[:num_transfers]

    p300_multi.transfer(
        200,
        source_columns,
        dest_columns,
        new_tip='always',
    )
