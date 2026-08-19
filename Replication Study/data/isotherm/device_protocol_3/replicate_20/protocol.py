from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Matrix Templated',
    'author': 'User',
    'description': 'Templated protocol for combining salt buffers and ligands in a filter plate on a Heater-Shaker using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# -------------------------
# Placeholder declarations
# -------------------------
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


# -------------------------
# Helper parsing functions
# -------------------------

def _unreplaced(s: str) -> bool:
    """Return True if the given string still looks like a [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder with a simulation fallback.

    - If the value is still a placeholder like '[[TOTAL_VOLUME]]', return
      the provided default (already chosen as a worst-case reasonable value).
    - Otherwise, cast it via float(), then through the provided cast.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default=default, cast=float))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    Example placeholder value: '0;100;200;500'
    Returns: [0.0, 100.0, 200.0, 500.0] (cast applied to each entry).
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


# -------------------------
# Main protocol
# -------------------------

def run(protocol: protocol_api.ProtocolContext):
    # ------------------------------------------------------------------
    # Parse placeholders with simulation fallbacks (worst-case estimates)
    # ------------------------------------------------------------------
    # These defaults are only used during simulation when the
    # placeholders have not yet been substituted.
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=200.0)

    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 500]
    )
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        default=[1, 2, 3, 4, 5, 6, 7, 8]
    )

    num_salt = parse_int(PLACEHOLDER_NUM_SALT, default=len(salt_concs))
    num_lig = parse_int(PLACEHOLDER_NUM_LIG, default=len(ligand_concs))

    # Basic sanity check: number of column groups must fit on 96-well plate
    if num_salt * replicates > 12:
        raise RuntimeError(
            'Number of salt concentrations * replicates exceeds 12 columns '
            f'(num_salt={num_salt}, replicates={replicates}).'
        )

    # Volume per component (buffer and ligand) per well
    vol_each = total_volume / 2.0

    # ------------------------------------------------------------------
    # 1. Modules
    # ------------------------------------------------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ------------------------------------------------------------------
    # 2. Labware
    # ------------------------------------------------------------------
    # Filter plate on Heater-Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware(
            'cytiva_96_filterwellplate_1ml',
            label='Filter Plate'
        )
    except Exception as exc:
        if 'not found' not in str(exc):
            # If the error is not "definition not found", re-raise so
            # that real stacking/slot errors are not hidden.
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; '
            'using a standard 96-well plate as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware(
            'nest_96_wellplate_200ul_flat',
            label='Filter Plate Fallback'
        )

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (slots as specified; only Reservoir 4 is used in this script)
    reservoir_4 = protocol.load_labware(
        'nest_12_reservoir_15ml', 3, label='Reservoir 4 (Buffers)'
    )
    reservoir_3 = protocol.load_labware(
        'nest_12_reservoir_15ml', 6, label='Reservoir 3'
    )
    reservoir_2 = protocol.load_labware(
        'nest_12_reservoir_15ml', 8, label='Reservoir 2'
    )
    reservoir_1 = protocol.load_labware(
        'nest_12_reservoir_15ml', 9, label='Reservoir 1'
    )
    reservoir_0 = protocol.load_labware(
        'nest_12_reservoir_15ml', 5, label='Reservoir 0'
    )

    # Mixing plate (NEST 96 deep-well 2 mL) in slot 11
    mixing_plate = protocol.load_labware(
        'nest_96_wellplate_2ml_deep', 11, label='Mixing Plate'
    )

    # ------------------------------------------------------------------
    # 3. Pipettes
    # ------------------------------------------------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        'left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )
    # Single-channel is loaded as requested, but not used in this protocol
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        'right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # ------------------------------------------------------------------
    # 4. Helper mapping functions
    # ------------------------------------------------------------------
    # For a given salt index i (0-based), the corresponding replicate
    # columns on the filter plate are:
    #   i * replicates, ..., i * replicates + (replicates - 1)
    # Example: num_salt=4, replicates=3 ->
    #   salt 0 -> cols 0,1,2
    #   salt 1 -> cols 3,4,5
    #   salt 2 -> cols 6,7,8
    #   salt 3 -> cols 9,10,11

    def salt_to_filter_columns(salt_index: int):
        start = salt_index * replicates
        end = start + replicates
        return filter_plate.columns()[start:end]

    # Ligand column in mixing plate is aligned with salt index
    def ligand_column(salt_index: int):
        return mixing_plate.columns()[salt_index]

    # Ensure latch is closed before any pipetting to the module labware
    hs_mod.close_labware_latch()

    # ------------------------------------------------------------------
    # Step 1: Transfer buffers from Reservoir 4 to Filter Plate
    # ------------------------------------------------------------------
    # Each 2x salt buffer is in reservoir_4.wells()[i]. For each salt
    # concentration, transfer vol_each (i.e. [[TOTAL_VOLUME]]/2) into all
    # wells of the corresponding replicate columns on the filter plate.
    # Use the 8-channel pipette with a 7 mm offset from the bottom.
    # Tips from slot 4 are picked up and returned, then reused.

    buffer_wells = reservoir_4.wells()  # index corresponds to salt index

    for salt_idx in range(num_salt):
        src_buffer = buffer_wells[salt_idx]
        dest_cols = salt_to_filter_columns(salt_idx)

        for col in dest_cols:
            # For multi-channel, use the A-row well of this column as handle
            dest_well = col[0]
            if not p300_multi.has_tip:
                p300_multi.pick_up_tip()
            p300_multi.transfer(
                vol_each,
                src_buffer,
                dest_well.bottom(7.0),
                new_tip='never'
            )
            # Return tip so it can be reused in the next step
            p300_multi.return_tip()

    # ------------------------------------------------------------------
    # Step 2: Transfer ligands from Mixing Plate to Filter Plate
    # ------------------------------------------------------------------
    # For each salt condition, take the corresponding column from the
    # mixing plate (containing 2x ligand dilutions across rows A–H) and
    # transfer vol_each into the same replicate columns on the filter
    # plate as used for the buffer. Again, use a 7 mm bottom offset.
    # Tips from slot 4 are reused by picking up and returning.

    for salt_idx in range(num_salt):
        src_col = ligand_column(salt_idx)
        src_well = src_col[0]  # A-row, multi-channel acts on whole column
        dest_cols = salt_to_filter_columns(salt_idx)

        for col in dest_cols:
            dest_well = col[0]
            if not p300_multi.has_tip:
                p300_multi.pick_up_tip()
            p300_multi.transfer(
                vol_each,
                src_well,
                dest_well.bottom(7.0),
                new_tip='never'
            )
            p300_multi.return_tip()

    # End of protocol
