from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Concentration Plate Mapping',
    'author': 'User',
    'description': 'Template protocol mapping deep-well plate columns to 360 uL plate using placeholders'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings for external substitution)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder string has not been substituted yet."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value: str, default: float, cast=float):
    """Parse a scalar placeholder; use `default` during simulation if unreplaced.

    The cast is applied via the provided `cast` callable (e.g. int, float).
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    # Route through float so strings like '3.0' parse for int as well
    return cast(float(s))


def parse_list(value: str, default, cast=float):
    """Parse a semicolon-separated list placeholder; use `default` if unreplaced."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # 1) Parse templated inputs
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, 1, int))
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0, 100, 200, 300],
        float,
    )

    num_concentrations = len(salt_concs)
    num_transfers = num_concentrations * replicates

    # 2) Deck setup
    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware
    # Slot 1: NEST 96 Deep-Well Plate 2 mL directly on Heater-Shaker (no adapter)
    deep_well_plate = hs_mod.load_labware('nest_96_wellplate_2ml_deep')

    # Slot 5: Corning 96-well plate 360 uL flat
    flat_96_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    # Left: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    # Right: P300 Single-Channel GEN2 (not used, but loaded per specification)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )

    # 3) Ensure Heater-Shaker latch is closed before pipetting on its labware
    hs_mod.close_labware_latch()

    # 4) Perform column-wise transfers with the 8-channel pipette
    transfer_volume = 200.0  # uL per well

    # Build lists of source and destination wells for each column index i
    source_wells = []
    dest_wells = []

    # Note: no explicit 12-column cap here; the wizard should ensure
    # num_transfers <= 12 in real use.
    for i in range(num_transfers):
        source_col = deep_well_plate.columns()[i]
        dest_col = flat_96_plate.columns()[i]
        # Multi-channel: use the A-row well as the column address
        source_wells.append(source_col[0])
        dest_wells.append(dest_col[0])

    # One multi-channel transfer call for all column pairs
    p300_multi.transfer(
        transfer_volume,
        source_wells,
        dest_wells,
        new_tip='always'
    )
