from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Transfer Template',
    'author': 'User',
    'description': 'Templated salt transfer using placeholders for replicates and salt concentrations.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (these will be replaced by the template system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a value is still a [[PLACEHOLDER]] token.

    Implemented without writing '[[' or ']]' literally in comparisons,
    so the template engine can safely substitute into this file.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder to a number.

    If the placeholder is not yet replaced, return the provided default
    (used only for simulation).
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    # parse through float so that e.g. "12" or "12.0" both work
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a list placeholder of the form "a;b;c" into a list of numbers.

    If the placeholder is not yet replaced, return the provided default
    (used only for simulation).
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --------------------------------------------------------------------
    # 1) Resolve templated parameters
    # --------------------------------------------------------------------
    # Use worst‑case defaults for simulation so that the protocol can
    # be validated even before the template placeholders are filled.
    #
    # [[REPLICATES]]: scalar (integer)
    # [[SALT_CONCENTRATIONS]]: semicolon‑separated list (e.g. "0;100;200;300")
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=float))
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float,
    )

    num_concs = len(salt_concs)
    num_transfers = replicates * num_concs

    # A 96‑well plate has 12 columns; for safety, cap at 12 so that
    # simulation cannot address non‑existent columns. On the real run,
    # you should ensure that replicates * number_of_concentrations <= 12.
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: Computed num_transfers={num_transfers} exceeds 12 columns of a 96-well plate. "
            "Capping to 12 columns. Ensure REPLICATES * number_of_concentrations <= 12 in the template."
        )
        num_transfers = 12

    # --------------------------------------------------------------------
    # 2) Modules
    # --------------------------------------------------------------------
    # Heater-Shaker Module GEN1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    # Close latch before any pipetting on module labware
    hs_mod.close_labware_latch()

    # --------------------------------------------------------------------
    # 3) Labware
    # --------------------------------------------------------------------
    # Slot 1: NEST 96 Deep-Well Plate 2 mL directly on Heater Shaker
    deep_well_plate = hs_mod.load_labware(
        'nest_96_wellplate_2ml_deep',
        label='Salt Deep Well Plate'
    )

    # Slot 5: 360 uL 96-well plate
    dest_plate = protocol.load_labware(
        'corning_96_wellplate_360ul_flat',
        5,
        label='360uL Plate'
    )

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware(
        'opentrons_96_tiprack_300ul',
        7
    )

    # --------------------------------------------------------------------
    # 4) Pipettes
    # --------------------------------------------------------------------
    # Right mount: P300 Single-Channel GEN2
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        'right',
        tip_racks=[tiprack_300]
    )

    # Left mount: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        'left',
        tip_racks=[tiprack_300]
    )

    # --------------------------------------------------------------------
    # 5) Transfers
    # --------------------------------------------------------------------
    # Step 1 (already computed above):
    #   num_transfers = len([[SALT_CONCENTRATIONS]]) * [[REPLICATES]]
    #
    # Step 2: For each i in num_transfers, transfer 200 uL from column i of
    # the deep-well plate (slot 1) to column i of the 360 uL plate (slot 5)
    # using the 8‑channel (multi‑channel) pipette.

    transfer_volume = 200  # uL per well (single dispense volume per column)

    # With a multi‑channel pipette, each column (A–H) is one target.
    # We therefore slice the first num_transfers columns from each plate.
    source_columns = deep_well_plate.columns()[:num_transfers]
    dest_columns = dest_plate.columns()[:num_transfers]

    # Execute the column‑wise transfers using the P300 8‑channel pipette.
    p300_multi.transfer(
        transfer_volume,
        source_columns,
        dest_columns,
        new_tip='always'
    )
