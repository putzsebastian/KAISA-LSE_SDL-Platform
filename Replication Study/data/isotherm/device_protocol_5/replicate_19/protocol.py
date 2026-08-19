from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Transfer Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for replicates and salt concentrations.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (left as literal strings for external substitution)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCS = '[[SALT_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string still looks like an unreplaced [[PLACEHOLDER]]."""
    s_str = str(s).strip()
    return s_str.startswith('[' * 2) and s_str.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder.

    During simulation, when the placeholder is still unreplaced, use the
    provided default (worst-case reasonable value). After substitution
    on the real robot, this will cast the provided value.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    During simulation, when the placeholder is still unreplaced, use the
    provided default list (worst-case length). After substitution on the
    real robot, this will split on ';' and cast elements.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ------------------------------------------------------------------
    # Parse placeholders
    # ------------------------------------------------------------------
    # Use an 8-concentration default to exercise up to 8 columns during
    # simulation; real values are provided by the template system.
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float
    )
    # Use a conservative default of 1 replicate for simulation.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=1, cast=int))

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    protocol.comment(f"Number of salt concentrations: {num_concs}")
    protocol.comment(f"Replicates: {replicates}")
    protocol.comment(f"Total number of transfers (columns used): {num_transfers}")

    # ------------------------------------------------------------------
    # Modules and labware (deck layout)
    # ------------------------------------------------------------------
    # Slot 1: Heater Shaker Module GEN1 with NEST 96 Deep-Well Plate 2 mL
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)
    deep_well_plate = hs_mod.load_labware('nest_96_wellplate_2ml_deep')

    # Slot 5: Corning 96 Well Plate 360 uL Flat
    dest_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 5)

    # Slot 7: Opentrons 96 Tip Rack 300 uL
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # ------------------------------------------------------------------
    # Pipettes
    # ------------------------------------------------------------------
    # Left: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300]
    )

    # Right: P300 Single-Channel GEN2 (loaded but unused in this step set)
    protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300]
    )

    # Close latch before any pipetting/shaking on Heater Shaker
    hs_mod.close_labware_latch()

    # ------------------------------------------------------------------
    # Step logic
    # ------------------------------------------------------------------
    # Safety: limit to available 12 columns on a 96-well plate
    if num_transfers > 12:
        protocol.comment(
            'WARNING: num_transfers exceeds 12 columns; only the first 12 will be used.'
        )
        num_transfers = 12

    # Step 2: For each i in num_transfers, transfer 200 uL from column i
    # of the deep-well plate to column i of the 360 uL plate using
    # the 8-channel pipette.
    source_columns = deep_well_plate.columns()[:num_transfers]
    dest_columns = dest_plate.columns()[:num_transfers]

    p300_multi.transfer(
        200,
        source_columns,
        dest_columns,
        new_tip='always'
    )
