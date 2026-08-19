from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Matrix Templated Setup',
    'author': 'User',
    'description': 'Templated protocol to combine salt buffers and ligands on a filter plate using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (left as literal strings for external substitution)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_N_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_N_LIGAND = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if string still looks like a [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder, with a simulation-only default.

    Values pass through float() first so that both integer-like and
    float-like strings are accepted. If the token is unreplaced, return
    the provided default.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_int(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    Example: '0;100;200;500' -> [0.0, 100.0, 200.0, 500.0]
    If unreplaced, returns a copy of the default list.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with simulation defaults (chosen as realistic upper bounds)
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCS, [0, 100, 200, 500])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCS, [1, 2, 3, 4, 5, 6, 7, 8])

    # Allow explicit counts to override list length, falling back to list lengths
    n_salt = parse_int(PLACEHOLDER_N_SALT, len(salt_concs))
    n_ligand = parse_int(PLACEHOLDER_N_LIGAND, len(ligand_concs))

    # Sanity: cap to plate dimensions
    n_salt = min(n_salt, 12)   # max columns in 96-well plate
    n_ligand = min(n_ligand, 8)  # max rows

    # Derived volume per addition (each of salt buffer and ligand is TOTAL_VOLUME / 2)
    add_volume = total_volume / 2.0

    # ---------------------- MODULES ----------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ---------------------- LABWARE ----------------------
    # Filter plate on heater-shaker: try custom labware, with simulation-only fallback
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard plate as a SIMULATION fallback only.')
        # Fallback uses a 96-well plate with same geometry (column/row layout)
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (not all are used in this specific workflow, but loaded per layout)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # contains salt buffers
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ---------------------- PIPETTES ----------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        'left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        'right'
    )

    # Ensure latch is closed before any pipetting to HS labware
    hs_mod.close_labware_latch()

    # ---------------------- STEP 1: Buffer Transfer ----------------------
    # Buffers with different salt concentrations are in Reservoir 4 (slot 3),
    # one well per salt concentration, 2x concentration. Well index matches
    # salt index, ascending.
    buffer_reservoir = reservoir_4

    # For salt i, use REPLICATES consecutive columns on the filter plate.
    # Example for REPLICATES=3:
    #   salt 0 -> columns 0,1,2 (A1–H1, A2–H2, A3–H3)
    #   salt 1 -> columns 3,4,5
    #   salt 2 -> columns 6,7,8
    #   salt 3 -> columns 9,10,11

    for i in range(n_salt):
        # Source well for this salt
        src_well = buffer_reservoir.wells()[i]

        # Determine destination column index range for this salt
        start_col = i * replicates
        end_col = start_col + replicates  # non-inclusive

        if start_col >= 12:
            break  # plate has only 12 columns

        dest_cols = filter_plate.columns()[start_col:end_col]
        if not dest_cols:
            continue

        # Pick up a tip for this salt group, reuse across its replicate columns
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        # IMPORTANT: A multichannel pipette treats each column as ONE target.
        # Here we direct to the column via col[0] (row A), with a 7 mm bottom offset
        # in the filter plate.
        p300_multi.transfer(
            add_volume,
            src_well,
            [col[0].bottom(7.0) for col in dest_cols],
            new_tip='never',
            blow_out=True,
            blowout_location='destination well'
        )

        # Return tip to rack for reuse in step 2
        p300_multi.return_tip()

    # ---------------------- STEP 2: Ligand Transfer ----------------------
    # In the mixing plate (slot 11), ligand dilutions are arranged as:
    #   rows A–H correspond to ascending ligand concentrations,
    #   columns correspond to salt concentrations (same ordering as above).
    # For each salt index i, one column is filled; each column must be
    # transferred into REPLICATES columns on the filter plate.

    for i in range(n_salt):
        if i >= len(mixing_plate.columns()):
            break

        src_col = mixing_plate.columns()[i]

        start_col = i * replicates
        end_col = start_col + replicates
        if start_col >= 12:
            break

        dest_cols = filter_plate.columns()[start_col:end_col]
        if not dest_cols:
            continue

        # Pick up tip(s) again, reusing the same tips as step 1 by returning
        # them to the rack above. The robot will pick fresh positions, but
        # physically these can be the same tips.
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        # Draw ligand from the column on the mixing plate and dispense into
        # the corresponding replicate columns on the filter plate, using a
        # 7 mm bottom offset in the filter plate.
        p300_multi.transfer(
            add_volume,
            src_col[0],
            [col[0].bottom(7.0) for col in dest_cols],
            new_tip='never',
            blow_out=True,
            blowout_location='destination well'
        )

        # Return tips for potential downstream reuse
        p300_multi.return_tip()

    protocol.comment('Templated salt-ligand matrix setup complete.')
