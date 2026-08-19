from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Concentration Transfers Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for replicates and salt concentrations.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (these will be replaced by the workflow engine)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string still contains an unreplaced [[TOKEN]]."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder value with a simulation-time default.

    The default should reflect the *maximum* expected value within
    the 12-column constraint when possible.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder with a default list.

    Example substituted value: "0;100;200;300".
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):

    # ------------------------------------------------------------------
    # 1) Derive num_transfers = (# salt concentrations) * (replicates)
    # ------------------------------------------------------------------
    # For simulation we choose defaults that keep num_transfers <= 12
    # on a 96-well plate (12 columns).
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=3, cast=int))
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300],
        cast=float,
    )

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    # Guard against exceeding available columns.
    if num_transfers > 12:
        raise RuntimeError(
            f"Number of transfers (columns) {num_transfers} exceeds 12; "
            f"adjust [[REPLICATES]] and [[SALT_CONCENTRATIONS]]."
        )

    protocol.comment(f"Replicates: {replicates}")
    protocol.comment(f"Salt concentrations (count): {num_concs}")
    protocol.comment(f"Total column transfers (num_transfers): {num_transfers}")

    # ------------------------------------------------------------------
    # 2) Deck layout
    # ------------------------------------------------------------------
    # Slot 1: NEST 96 Deep-Well Plate 2 mL on Heater Shaker Module V1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    deep_well_plate = hs_mod.load_labware(
        'nest_96_wellplate_2ml_deep',
        label='Deep Well Plate'
    )

    # Slot 5: 360 uL 96-well plate
    flat_96_plate = protocol.load_labware(
        'corning_96_wellplate_360ul_flat',
        5,
        label='360 uL Plate'
    )

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware(
        'opentrons_96_tiprack_300ul',
        7
    )

    # ------------------------------------------------------------------
    # 3) Pipettes
    # ------------------------------------------------------------------
    # Right mount: P300 Single-Channel GEN2 (not used in the main step
    # but loaded as per configuration requirement)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )

    # Left mount: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    # Make sure the Heater-Shaker latch is closed before pipetting
    hs_mod.close_labware_latch()

    # ------------------------------------------------------------------
    # 4) Column-wise transfers with 8-channel pipette
    # ------------------------------------------------------------------
    # Requirement: For each i in num_transfers, transfer 200 uL from
    # column i of the Deep Well Plate to column i of the 360 uL plate.
    #
    # With an 8-channel pipette, addressing A-row wells (col[0]) allows
    # the pipette to span the entire column (A–H) in one command.
    # ------------------------------------------------------------------

    transfer_volume = 200.0  # uL per well

    # Select the first num_transfers columns from each plate.
    source_columns = deep_well_plate.columns()[:num_transfers]
    dest_columns = flat_96_plate.columns()[:num_transfers]

    # For multi-channel, pass the A-row well from each column as the
    # target; the instrument spans the full column.
    source_wells = [col[0] for col in source_columns]
    dest_wells = [col[0] for col in dest_columns]

    protocol.comment(
        f"Transferring {transfer_volume} uL per well using P300 multi across "
        f"{num_transfers} columns."
    )

    # Use one fresh tip per column transfer
    p300_multi.transfer(
        transfer_volume,
        source_wells,
        dest_wells,
        new_tip='always'
    )

    # ------------------------------------------------------------------
    # 5) Clean-up
    # ------------------------------------------------------------------
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
