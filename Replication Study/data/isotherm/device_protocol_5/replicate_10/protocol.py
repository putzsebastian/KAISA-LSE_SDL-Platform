from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Gradient Transfer Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for replicates and salt concentrations to transfer from deep-well plate on Heater-Shaker to 360 uL plate'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# -----------------------------
# PLACEHOLDERS (literal tokens)
# -----------------------------
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string still looks like an unreplaced [[TOKEN]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder or return a worst-case default for simulation.

    The placeholder is expected to be a single number once substituted.
    `default` should be the *maximum* expected value so simulation stresses
    tip usage and volume usage.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated placeholder list or return a default list.

    The placeholder is expected to be like "0;100;200;300" once substituted.
    `default` should be a worst-case-length list for simulation.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # -----------------------------
    # Parse placeholder values
    # -----------------------------
    # [[SALT_CONCENTRATIONS]] is a semicolon-separated list, e.g. "0;100;200;300".
    # Use WORST-CASE default of 8 concentrations if not yet substituted.
    salt_conc_list = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 50, 100, 150, 200, 250, 300, 350],
        cast=float
    )

    # [[REPLICATES]] is the number of replicates per concentration (single scalar).
    # Use a WORST-CASE default of 12 replicates for simulation.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=float))

    # Number of concentrations
    num_conc = len(salt_conc_list)

    # -----------------------------
    # Compute number of transfers
    # -----------------------------
    # Per user spec:
    #   num_transfers = (# of salt concentrations) * (replicates)
    num_transfers = num_conc * replicates

    # We only have 12 columns on a 96-well plate. If num_transfers
    # exceeds this, cap at 12 so the protocol remains physically valid.
    if num_transfers > 12:
        protocol.comment(
            f"Requested num_transfers={num_transfers} exceeds 12 columns; "
            f"capping to 12 for this run."
        )
        num_transfers = 12

    protocol.comment(
        f"Using {num_conc} salt concentrations, {replicates} replicates -> "
        f"num_transfers={num_transfers}"
    )

    # -----------------------------
    # Load modules and labware
    # -----------------------------
    # Heater-Shaker Module V1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # USER REQUIREMENT: "mounted directly on Heater Shaker Module V1 (no adapter)".
    # Therefore, load the NEST 96 Deep-Well Plate 2 mL directly on the module.
    deepwell_plate = hs_mod.load_labware('nest_96_wellplate_2ml_deep')

    # Destination: 360 uL 96-well plate in slot 5
    # Using Corning 96 Well Plate 360 uL Flat as the 360 uL plate.
    dest_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Tiprack in slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # -----------------------------
    # Load pipettes
    # -----------------------------
    # Right mount: P300 Single-Channel Gen2 (loaded but not used in this step)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )

    # Left mount: P300 8-Channel Gen2 (used for the transfers)
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    # -----------------------------
    # Prepare for transfer
    # -----------------------------
    source_columns = deepwell_plate.columns()
    dest_columns = dest_plate.columns()

    transfer_volume = 200  # uL per well in the column

    # Close the Heater-Shaker latch before any pipetting to the module labware
    hs_mod.close_labware_latch()

    # -----------------------------
    # Perform column-wise transfers with the 8-channel pipette
    # -----------------------------
    # Per user spec:
    #   For each i in num_transfers, transfer 200 uL from column i of the
    #   deep-well plate (slot 1) to column i of the 360 uL plate (slot 5)
    #   using the 8-channel pipette.

    for i in range(num_transfers):
        src_col = source_columns[i]
        dst_col = dest_columns[i]

        protocol.comment(
            f"Transferring {transfer_volume} uL from column {i+1} of deep-well plate "
            f"to column {i+1} of 360 uL plate"
        )

        # Pass the whole column lists so that the multi-channel head addresses
        # all 8 wells of the column in a single logical transfer.
        p300_multi.transfer(
            transfer_volume,
            src_col,
            dst_col,
            new_tip='always'
        )

    protocol.comment('Salt gradient transfer step completed.')
