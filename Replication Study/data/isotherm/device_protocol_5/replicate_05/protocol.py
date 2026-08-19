from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Concentration Transfer Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt concentrations and replicates.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# --- Placeholders (must remain literal for the templating system) ---
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if the given string is still a [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value_str: str, default: float, cast=float) -> float:
    """Parse a scalar placeholder value, with a simulation-only default.

    The default should represent the *worst case* allowed by the method
    so that simulation stresses volumes and tip usage.
    """
    s = str(value_str).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value_str: str, default, cast=float):
    """Parse a semicolon-separated list placeholder (e.g. "0;100;200").

    Returns a list of casted values. If unreplaced, returns a copy of
    the provided default list (simulation-only fallback).
    """
    s = str(value_str).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --- Resolve placeholders ---
    # For simulation, use worst-case defaults:
    #   replicates: 12 (max columns use)
    #   salt concentrations: 8 entries (e.g. 0..700 mM)
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=int))
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float,
    )

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    protocol.comment(
        f'Resolved replicates={replicates}, num_concs={num_concs}, num_transfers={num_transfers}'
    )

    # --- Deck layout ---
    # Slot 1: Heater Shaker Module Gen1 with NEST 96 Deep-Well Plate 2 mL (no adapter)
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    deepwell_plate = hs_mod.load_labware(
        'nest_96_wellplate_2ml_deep',
        label='Deepwell Salt Plate'
    )

    # Close latch before any pipetting on the module
    hs_mod.close_labware_latch()

    # Slot 5: 360 uL 96-well plate (Corning flat 360 µL)
    dest_plate = protocol.load_labware(
        'corning_96_wellplate_360ul_flat',
        5,
        label='Destination 360uL Plate'
    )

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # --- Pipettes ---
    # Left: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    # Right: P300 Single-Channel GEN2 (not used here, but loaded as specified)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )

    # --- Step 1: compute num_transfers ---
    # Already computed above as num_transfers = num_concs * replicates

    # --- Step 2: Column-wise transfers with 8-channel pipette ---
    # Each transfer uses one column index i (0-based), but the plate has only 12 columns.
    if num_transfers > 12:
        protocol.comment(
            'WARNING: num_transfers exceeds 12 available columns; only the first 12 will be used.'
        )
        num_columns_to_use = 12
    else:
        num_columns_to_use = num_transfers

    # For a multi-channel pipette, use full columns as single targets
    source_columns = [deepwell_plate.columns()[i] for i in range(num_columns_to_use)]
    dest_columns = [dest_plate.columns()[i] for i in range(num_columns_to_use)]

    # Perform 200 µL transfers, column i -> column i, using the 8-channel pipette.
    # One tip per column for simplicity and clarity.
    for src_col, dst_col in zip(source_columns, dest_columns):
        p300_multi.pick_up_tip()
        p300_multi.transfer(
            200,
            src_col,
            dst_col,
            new_tip='never'
        )
        p300_multi.drop_tip()

    protocol.comment('Salt concentration transfer template completed.')
