from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Transfer Template',
    'author': 'User',
    'description': 'Template protocol using placeholders for salt concentrations and replicates.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder literals for external substitution
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if the given string is still an unreplaced [[PLACEHOLDER]].

    Uses bracket repetition to avoid writing '[[' / ']]' literally,
    which is important for template substitution.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder or literal.

    During simulation, if the placeholder is not yet substituted
    (still of the form [[...]]), return the provided default.
    Once substituted, cast the value.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a list placeholder or literal separated by ';'.

    During simulation, if the placeholder is not yet substituted,
    return the provided default list. Once substituted, split on
    ';' and cast each entry.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Resolve parameters from placeholders with simulation-safe defaults.
    # Defaults represent an upper-bound style scenario so that tip usage
    # and column counts are exercised in simulation.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=8, cast=float))
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float
    )

    # 1) Compute number of transfers per user specification
    num_concentrations = len(salt_concs)
    num_transfers = replicates * num_concentrations

    protocol.comment(f"Replicates (simulated or provided): {replicates}")
    protocol.comment(
        f"Number of salt concentrations (simulated or provided): {num_concentrations}"
    )
    protocol.comment(
        f"Total number of transfers (replicates x concentrations): {num_transfers}"
    )

    # 2) Deck layout
    # Slot 1: NEST 96 Deep-Well Plate 2 mL mounted directly on Heater Shaker Module V1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    deep_well_plate = hs_mod.load_labware(
        'nest_96_wellplate_2ml_deep',
        label='Deep Well Plate'
    )

    # Close the Heater-Shaker labware latch before any pipetting to/from it
    hs_mod.close_labware_latch()

    # Slot 5: 360 uL 96-well plate
    assay_plate = protocol.load_labware(
        'corning_96_wellplate_360ul_flat',
        5,
        label='360 uL Plate'
    )

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware(
        'opentrons_96_tiprack_300ul',
        7,
        label='P300 Tiprack 300 uL'
    )

    # Pipettes
    # Right Mount: P300 Single GEN2 (loaded but not used in this template)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )

    # Left Mount: P300 8-Channel GEN2 (used for the transfers)
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    # 3) Perform transfers according to num_transfers.
    # The requirement is: for each i in num_transfers, transfer 200 uL
    # from column [i] of the deep-well plate to column [i] of the 360 uL plate
    # using the 8-channel pipette.
    #
    # A 96-well plate has 12 columns, so in practice only columns 1–12 exist.
    max_columns = 12
    cols_to_use = min(num_transfers, max_columns)

    if cols_to_use < num_transfers:
        protocol.comment(
            'WARNING: num_transfers exceeds available columns; only the first '
            '12 transfers (columns) will be executed in this template.'
        )

    # Use one tip per column transfer, since there is a single source column
    # and a matching destination column.
    for i in range(cols_to_use):
        # Each element of .columns()[i] is a well object; index [0] is row A
        src_column = deep_well_plate.columns()[i]
        dest_column = assay_plate.columns()[i]

        src_well = src_column[0]   # A-row well representing the column for multi-channel
        dest_wells = dest_column   # list of 8 wells (A–H) in that column

        # 4) Transfer 200 uL from the deep-well column to the corresponding assay column
        p300_multi.transfer(
            200,
            src_well,
            dest_wells,
            new_tip='always'
        )

    protocol.comment('Salt transfer template protocol complete.')
