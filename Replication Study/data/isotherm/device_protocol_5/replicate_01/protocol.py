from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Concentration Transfers (Templated)',
    'author': 'User',
    'description': 'Transfer from deep-well on Heater-Shaker to 360 uL plate using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (templated values)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string is still a [[PLACEHOLDER]] token.

    Uses built bracket strings so the literal '[[' never appears in code,
    which keeps downstream templating safe.
    """
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder value.

    For simulation (when the placeholder is unreplaced), falls back to a
    worst-case default so volume/tip budgeting is exercised. Once the
    placeholder is replaced on the real robot, the value must parse or
    the run will fail loudly (by design).
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder value.

    Example substituted form: "0;100;200;300".
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # 1. Parse placeholders
    # Use large defaults for simulation only (worst case within typical bounds)
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700]
    )
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=float))

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    protocol.comment(
        f'Parsed {num_concs} salt concentrations and {replicates} replicates; '
        f'total transfers (columns) = {num_transfers}.'
    )

    # A 96-well plate has 12 columns; limit to physical maximum
    if num_transfers > 12:
        protocol.comment(
            'WARNING: num_transfers exceeds number of plate columns (12). '
            'Only first 12 will be used.'
        )
        num_transfers = 12

    # 2. Modules and labware
    # Deep-well plate on Heater-Shaker in slot 1, no separate adapter load
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    deepwell = hs_mod.load_labware(
        'nest_96_wellplate_2ml_deep',
        label='Deepwell on HS'
    )

    # 360 uL 96-well plate in slot 5
    dest_plate = protocol.load_labware(
        'corning_96_wellplate_360ul_flat',
        5,
        label='360 uL plate'
    )

    # Tip rack in slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # 3. Pipettes
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        'right',
        tip_racks=[tiprack_300]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        'left',
        tip_racks=[tiprack_300]
    )

    # 4. Ensure Heater-Shaker latch is closed before pipetting on module
    hs_mod.close_labware_latch()

    # 5. Multi-channel column-wise transfers
    # Requirement step 2:
    # For each i in num_transfers transfer 200 uL each of wells in Column [i]
    # of the Deep Well Plate into wells of Column [i] of the 360 uL plate
    # using the 8-channel pipette.
    # With a multi-channel pipette, addressing the top well of a column
    # (row A) targets the full column.

    source_cols = [deepwell.columns()[i][0] for i in range(num_transfers)]
    dest_cols = [dest_plate.columns()[i][0] for i in range(num_transfers)]

    # Perform transfers: 200 uL per well in the column
    p300_multi.transfer(200, source_cols, dest_cols, new_tip='always')

    protocol.comment('Templated salt concentration transfer protocol complete.')
