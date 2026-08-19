from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Concentration Replicate Transfers',
    'author': 'User',
    'description': 'Templated protocol using placeholders for replicates and salt concentrations.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (these will be replaced by the workflow system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder string has not yet been substituted."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder value with a simulation-time default.

    The default should reflect the *maximum* expected value so that
    simulation stresses tip usage and deck resources.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder with a simulation default list."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ------------------------
    # Placeholder resolution
    # ------------------------
    # Use large defaults so simulation is run at the upper bounds.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=float))
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float
    )

    # Step 1: num_transfers = (# of concentrations) * (replicates)
    num_concentrations = len(salt_concs)
    num_transfers = num_concentrations * replicates

    # We only have 12 columns on a 96‑well plate; cap to 12 for execution.
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: Computed num_transfers={num_transfers} exceeds 12 columns; "
            f"only the first 12 columns will be used."
        )
        num_transfers = 12

    # ------------------------
    # Modules
    # ------------------------
    heater_shaker = protocol.load_module('heaterShakerModuleV1', 1)
    # Ensure latch is closed before pipetting on the module
    heater_shaker.close_labware_latch()

    # ------------------------
    # Labware
    # ------------------------
    # Slot 1: NEST 96 Deep-Well Plate 2 mL on Heater Shaker (no adapter)
    deep_well_plate = heater_shaker.load_labware('nest_96_wellplate_2ml_deep')

    # Slot 5: 360 uL 96-well plate (Corning 96 Well Plate 360 uL Flat)
    dest_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # ------------------------
    # Pipettes
    # ------------------------
    # Right Mount: P300 Single-Channel GEN2 (loaded but not used in this protocol)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )

    # Left Mount: P300 8-Channel GEN2 (used for column-wise transfers)
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    # ------------------------
    # Step 2: Column-wise transfers with 8‑channel pipette
    # ------------------------
    transfer_volume = 200.0  # uL

    # For each i in num_transfers, transfer 200 uL from column i of deep‑well
    # to column i of the 360 uL plate using the multichannel pipette.
    # columns()[index] is 0-indexed; columns are 1..12 logically.
    for col_index in range(num_transfers):
        source_col = deep_well_plate.columns()[col_index]
        dest_col = dest_plate.columns()[col_index]

        p300_multi.pick_up_tip()
        p300_multi.transfer(
            transfer_volume,
            source_col,
            dest_col,
            new_tip='never'
        )
        p300_multi.drop_tip()

    protocol.comment(
        f"Completed {num_transfers} column-wise transfers of {transfer_volume} uL "
        f"from deep-well plate on Heater-Shaker (slot 1) to 360 uL plate in slot 5 "
        f"using the P300 multi-channel on the left mount."
    )
