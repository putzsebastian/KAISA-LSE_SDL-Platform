from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Concentration Transfer Template',
    'author': 'User',
    'description': 'Templated protocol using [[REPLICATES]] and [[SALT_CONCENTRATIONS]] to define column-wise transfers from deep-well plate on Heater-Shaker to 360 uL plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (left as literal strings for the template engine to replace)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if the given string still looks like an unreplaced [[TOKEN]]."""
    s_str = str(s).strip()
    return s_str.startswith('[' * 2) and s_str.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder.

    During simulation, if the placeholder is unreplaced, fall back to the provided
    default (worst-case value). Once substituted, this will cast the real value.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a list placeholder like [[SALT_CONCENTRATIONS]].

    Values are expected to be semicolon-separated, e.g. "0;100;200".
    During simulation, if unreplaced, use the provided default list.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ----------------------------------------------------------------------
    # 1) Parse placeholders and compute num_transfers
    # ----------------------------------------------------------------------
    # Use large defaults for simulation so we test worst-case behavior.
    # Example: up to 8 salt concentrations, up to 12 replicates.
    default_replicates = 12
    default_salt_concs = [0, 100, 200, 300, 400, 500, 600, 700]

    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default_replicates, cast=int))
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default_salt_concs, cast=float)

    num_transfers = replicates * len(salt_concs)

    # Both plates have 12 columns; cap at this limit to avoid addressing
    # non-existent columns if the product exceeds 12.
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: Computed num_transfers={num_transfers} exceeds 12 columns; "
            f"capping to 12 for this run."
        )
        num_transfers = 12

    protocol.comment(f"Using replicates={replicates}, salt_concs={len(salt_concs)}, num_transfers={num_transfers}.")

    # ----------------------------------------------------------------------
    # 2) Load modules and labware (deck layout per user spec)
    # ----------------------------------------------------------------------
    # Slot 1: Heater Shaker Module GEN1 with NEST 96 Deep Well Plate 2 mL directly on module
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    deep_well_plate = hs_mod.load_labware('nest_96_wellplate_2ml_deep')

    # Slot 5: 360 uL 96-well plate
    flat_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Slot 7: Opentrons 96 Tiprack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # ----------------------------------------------------------------------
    # 3) Load pipettes
    # ----------------------------------------------------------------------
    # Right mount: P300 Single-Channel GEN2 (loaded but unused here, included per config)
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])

    # Left mount: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Ensure latch is closed before any pipetting on the Heater-Shaker labware
    hs_mod.close_labware_latch()

    # ----------------------------------------------------------------------
    # 4) Column-wise transfers with multichannel pipette
    # ----------------------------------------------------------------------
    transfer_volume = 200.0  # uL per well

    # Source and destination columns: one column per transfer
    # For multichannel, we address the column by its A-row well (index [0]).
    source_columns = deep_well_plate.columns()[:num_transfers]
    dest_columns = flat_plate.columns()[:num_transfers]

    # Perform the transfers: one 200 uL transfer per column (A-H together)
    p300_multi.transfer(
        transfer_volume,
        [col[0] for col in source_columns],
        [col[0] for col in dest_columns],
        new_tip='always'
    )

    protocol.comment(
        f"Completed {num_transfers} column transfers of {transfer_volume} uL "
        f"from deep-well plate on Heater-Shaker (slot 1) to 360 uL plate (slot 5)."
    )
