from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Transfer Template',
    'author': 'User',
    'description': 'Template protocol using placeholders for salt concentrations and replicates.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (templating tokens)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a value is still an unreplaced [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder or return a default for simulation.

    - value: incoming value (may be a placeholder string)
    - default: numeric default used ONLY when value is still a placeholder
    - cast: callable to convert the numeric string (typically float or int via float)

    During simulation this allows the script to run with worst-/reasonable-case
    values. On the real robot the templating system should replace the
    [[PLACEHOLDER]] strings with concrete numbers before execution.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder or return a default list.

    - value: incoming value (may be [[SALT_CONCENTRATIONS]])
    - default: list used ONLY when value is still a placeholder
    - cast: callable for each element
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # 1) Parse placeholders for simulation
    # Use defaults that keep the example within 12 columns.
    # On the real robot, a templating system should replace the
    # placeholder strings with concrete values before execution.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=1, cast=float))
    salt_concentrations = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300],
        cast=float
    )

    # Number of transfers = number of concentrations * replicates
    num_transfers = len(salt_concentrations) * replicates

    max_columns = 12  # 96-well plate has 12 columns

    # Each transfer index maps to a column. Cannot exceed 12 columns.
    # For safety in simulation we cap at 12; the templating system
    # should ensure real values also respect the 12-column limit.
    num_columns_to_use = min(num_transfers, max_columns)

    protocol.comment(f"Replicates (simulated): {replicates}")
    protocol.comment(f"Salt concentrations (simulated): {salt_concentrations}")
    protocol.comment(f"Total number of transfers requested (num_transfers): {num_transfers}")
    protocol.comment(f"Number of columns that will actually be used (capped at 12): {num_columns_to_use}")

    # 2) Modules and labware
    # Slot 1: NEST 96 Deep-Well Plate 2 mL on Heater-Shaker Module GEN1 (no adapter)
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    deep_well = hs_mod.load_labware('nest_96_wellplate_2ml_deep')

    # Close Heater-Shaker labware latch before any pipetting to/from it
    hs_mod.close_labware_latch()

    # Slot 5: 360 uL 96-well plate
    destination_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # 3) Pipettes
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    transfer_volume = 200.0  # µL per well

    # 4) Map num_transfers to columns
    # User logic requirement:
    #   For each i in num_transfers, transfer 200 uL from column[i] of the
    #   deep-well plate to column[i] of the 360 uL plate using the 8-channel.
    # Here we treat each transfer index as one column index (0-based).
    columns_to_use = list(range(num_columns_to_use))

    protocol.comment(f"Using columns (0-based index) for transfers: {columns_to_use}")

    # For a multi-channel pipette, target the A-row (index 0) of each column
    # so that each transfer operates on an entire column (A–H) at once.
    source_wells = [deep_well.columns()[i][0] for i in columns_to_use]
    dest_wells = [destination_plate.columns()[i][0] for i in columns_to_use]

    # 5) Perform transfers using the P300 8-channel pipette
    # Each list entry corresponds to a full column source and destination.
    p300_multi.transfer(
        transfer_volume,
        source_wells,
        dest_wells,
        new_tip='always'
    )
