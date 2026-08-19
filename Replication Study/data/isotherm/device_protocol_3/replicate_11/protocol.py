from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt/Ligand Matrix on Filter Plate',
    'author': 'User',
    'description': 'Templated protocol for combining salt buffers and ligands on a filter plate using Heater-Shaker and multi-channel pipetting.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# ---- Placeholders (literal strings, replaced by external templating) ----
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIGAND = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder token has not been substituted yet.

    During real runs, the external system replaces tokens with concrete
    values. During simulation here, they remain in [[BRACKETS]] and we
    must fall back to safe defaults.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar numeric placeholder as float (or via cast).

    Uses `default` if the placeholder is still unreplaced.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse semicolon-separated numeric list placeholder.

    Example: '0;100;200;500' -> [0.0, 100.0, 200.0, 500.0]
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    parts = [p for p in s.split(';') if p.strip()]
    return [cast(float(p)) for p in parts]


def run(protocol: protocol_api.ProtocolContext):
    # ---- Parse placeholders with defaults for simulation ----
    # Use upper-bound style defaults so simulation exercises worst case.
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=3)
    total_volume_ul = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=200.0)

    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0.0, 100.0, 200.0, 500.0]
    )
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        default=[0.0, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0]
    )

    num_salt = parse_int(PLACEHOLDER_NUM_SALT, default=len(salt_concs))
    num_ligand = parse_int(PLACEHOLDER_NUM_LIGAND, default=len(ligand_concs))

    # Plate constraints (96-well: 12 columns x 8 rows)
    if num_salt > 12:
        protocol.comment('WARNING: NUMBER_OF_SALT_CONCENTRATIONS exceeds 12; capping to 12.')
        num_salt = 12
    if num_ligand > 8:
        protocol.comment('WARNING: NUMBER_OF_LIGAND_CONCENTRATIONS exceeds 8; capping to 8.')
        num_ligand = 8

    # Ensure replicates * num_salt fits into 12 columns
    total_target_columns = replicates * num_salt
    if total_target_columns > 12:
        protocol.comment(
            'WARNING: replicates * NUMBER_OF_SALT_CONCENTRATIONS exceeds 12; '
            'capping replicates to fit plate.'
        )
        replicates = 12 // max(1, num_salt)
        total_target_columns = replicates * num_salt

    half_volume_ul = total_volume_ul / 2.0

    # ---- Modules ----
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ---- Labware ----
    # Slot 1: Filter Plate on Heater-Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
            'using NEST 96 flat plate as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks: Slots 4, 7, 10
    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs:
    # Slot 3: Reservoir 4 (buffers with different salt concentrations)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    # Slot 5: Reservoir 0 (not used in this protocol, but loaded per layout)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    # Slot 6: Reservoir 3
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    # Slot 8: Reservoir 2
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    # Slot 9: Reservoir 1
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)

    # Slot 11: Mixing Plate (NEST 96 Deep-Well Plate 2 mL)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ---- Pipettes ----
    # Left mount: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    # Right mount: P300 Single-Channel GEN2 (not used in this protocol but loaded per spec)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    # Ensure Heater-Shaker latch is closed before any pipetting on module
    hs_mod.close_labware_latch()

    # ------------------------------------------------------------------
    # STEP 1: Transfer buffers from Reservoir 4 to Filter Plate
    # ------------------------------------------------------------------
    protocol.comment('Step 1: Transfer salt buffers from Reservoir 4 to filter plate.')

    # Reservoir 4 contains 2x concentration buffers in ascending wells for each salt conc.
    # For each salt concentration index, allocate `replicates` columns on the filter plate.
    for salt_index in range(num_salt):
        # Source buffer well in Reservoir 4 (single-row reservoir: wells() index matches well number)
        source_well = reservoir_4.wells()[salt_index]

        # Destination column indices for this buffer
        # Example for replicates=3:
        #   salt_index 0 -> columns 0,1,2
        #   salt_index 1 -> columns 3,4,5, etc.
        start_col = salt_index * replicates
        end_col = start_col + replicates

        if start_col >= 12:
            protocol.comment(
                f'Skipping salt index {salt_index}: start_col {start_col} is beyond plate columns.'
            )
            continue
        end_col = min(end_col, 12)

        dest_columns = filter_plate.columns()[start_col:end_col]
        if not dest_columns:
            continue

        # Use the 8-channel pipette; one pick-up per salt concentration.
        # Tips from slot 4 will be picked automatically as needed.
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        # Transfer [[TOTAL_VOLUME]]/2 (half_volume_ul) per well to each destination column.
        # Multi-channel semantics: source_well is a single well; dest_columns is list of columns.
        p300_multi.transfer(
            half_volume_ul,
            source_well,
            dest_columns,
            new_tip='never',
            blow_out=False,
            mix_before=None
        )

        # Move to 7 mm above bottom of the last destination well (top of column A)
        last_col = dest_columns[-1]
        last_well = last_col[0]  # A-row well (multi-channel safe)
        p300_multi.move_to(last_well.bottom(z=7))

        # Return the tip to the rack (tips will be reused conceptually in Step 2)
        p300_multi.return_tip()

    # ------------------------------------------------------------------
    # STEP 2: Transfer ligands from Mixing Plate (Slot 11) to Filter Plate
    # ------------------------------------------------------------------
    protocol.comment('Step 2: Transfer ligands from mixing plate to filter plate.')

    # For each salt concentration (each column in mixing plate), transfer ligands to
    # the same replicate column block on the filter plate as used in Step 1.
    for salt_index in range(num_salt):
        if salt_index >= 12:
            break

        # Source column in mixing plate for this salt concentration
        # mixing_plate.columns()[i] is a list of 8 wells A..H for column i+1.
        source_column = mixing_plate.columns()[salt_index]

        # Destination columns on filter plate
        start_col = salt_index * replicates
        end_col = min(start_col + replicates, 12)
        dest_columns = filter_plate.columns()[start_col:end_col]

        if not dest_columns:
            continue

        # Use 8-channel pipette; pick up tip for each salt block.
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        # Transfer [[TOTAL_VOLUME]]/2 (half_volume_ul) per well from source column to
        # replicate columns on the filter plate.
        p300_multi.transfer(
            half_volume_ul,
            source_column,
            dest_columns,
            new_tip='never',
            blow_out=False,
            mix_before=None
        )

        # Move to 7 mm above bottom of last destination well
        last_col = dest_columns[-1]
        last_well = last_col[0]
        p300_multi.move_to(last_well.bottom(z=7))

        # Return the tip, conceptually reusing tips from slot 4 per instructions
        p300_multi.return_tip()

    protocol.comment('Protocol complete.')
