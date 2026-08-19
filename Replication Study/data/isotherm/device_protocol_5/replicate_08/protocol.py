from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Transfer Template',
    'author': 'User',
    'description': 'Templated transfer from deep-well plate on Heater-Shaker to 360 uL plate using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (will be substituted by external template system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder token like [[TOKEN]] has not been replaced yet."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder; fall back to default during simulation.

    The real run should substitute a concrete numeric value for the placeholder.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a list placeholder of the form "v1;v2;v3"; fallback used in simulation only."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ----------------------------------------------------------------------
    # Parse templated inputs
    # ----------------------------------------------------------------------
    # Use large defaults to exercise worst-case usage in simulation.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=int))
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float,
    )

    num_concentrations = len(salt_concs)
    num_transfers = num_concentrations * replicates

    # A 96-well plate has 12 columns; cap to 12 so indexing stays valid.
    if num_transfers > 12:
        protocol.comment(
            f"Requested num_transfers={num_transfers} exceeds 12 columns; "
            f"capping to 12 for execution."
        )
        num_transfers = 12

    # ----------------------------------------------------------------------
    # Modules
    # ----------------------------------------------------------------------
    # Slot 1: Heater-Shaker Module GEN1 with NEST 96 Deep-Well Plate 2 mL directly on it
    hs_module = protocol.load_module('heaterShakerModuleV1', 1)

    # ----------------------------------------------------------------------
    # Labware
    # ----------------------------------------------------------------------
    deep_well_plate = hs_module.load_labware('nest_96_wellplate_2ml_deep')
    plate_360 = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # ----------------------------------------------------------------------
    # Pipettes
    # ----------------------------------------------------------------------
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_300])
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_300])

    transfer_volume = 200.0

    # ----------------------------------------------------------------------
    # Heater-Shaker latch handling
    # ----------------------------------------------------------------------
    # Close the latch before any pipetting to labware on the Heater-Shaker.
    hs_module.close_labware_latch()

    # ----------------------------------------------------------------------
    # Column-wise transfers with 8-channel pipette
    # ----------------------------------------------------------------------
    # Requirement: for each i in num_transfers, transfer 200 uL from column i of
    # the deep-well plate to column i of the 360 uL plate, using the 8-channel.

    source_columns = deep_well_plate.columns()[:num_transfers]
    dest_columns = plate_360.columns()[:num_transfers]

    # One fresh tip per column transfer
    p300_multi.transfer(
        transfer_volume,
        source_columns,
        dest_columns,
        new_tip='always'
    )

    protocol.comment(
        f"Completed {num_transfers} column transfers of {transfer_volume} uL "
        f"from deep-well plate to 360 uL plate using P300 multi-channel."
    )
