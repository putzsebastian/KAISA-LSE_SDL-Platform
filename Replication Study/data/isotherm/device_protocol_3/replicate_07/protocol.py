from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Titration Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt/ligand titrations in a filter plate on Heater-Shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder literals (must remain exactly as written for templating)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_N_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_N_LIGAND = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Detect unreplaced [[PLACEHOLDER]] tokens during simulation.

    Uses constructed brackets so that the literal '[[' never appears in code
    positions that will be re-emitted, avoiding JSON-escape issues.
    """
    s2 = str(s).strip()
    return s2.startswith('[' * 2) and s2.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder to a float, with simulation fallback.

    - `value` is the placeholder string
    - `default` should be the *worst-case* value for simulation
    - `cast` is a callable used for the final cast after float conversion
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    # Route through float so "3.0" is valid even for int-like values
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    Returns a list of casted values. `default` should be a worst-case list.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    parts = [p for p in s.split(';') if p.strip()]
    return [cast(p) for p in parts]


def run(protocol: protocol_api.ProtocolContext):
    # ------------------------------------------------------------------
    # Parse placeholders with simulation fallbacks (worst-case examples)
    # ------------------------------------------------------------------
    # Use upper-bound defaults so simulation stresses tips/volumes.
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # uL per well TOTAL
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0, 100, 200, 500],  # up to 4 salt concentrations
        cast=float,
    )
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [1, 2, 3, 4, 5, 6, 7, 8],  # up to 8 ligand concentrations
        cast=float,
    )

    # The numbers of salt/ligand concentrations are explicit placeholders
    n_salt = parse_int(PLACEHOLDER_N_SALT, len(salt_concs))
    n_ligand = parse_int(PLACEHOLDER_N_LIGAND, len(ligand_concs))

    # Safety: constrain by plate geometry
    n_salt = min(n_salt, 12)   # 96-well plate has 12 columns
    n_ligand = min(n_ligand, 8)  # 8 rows A-H

    # Each step uses half of TOTAL_VOLUME
    per_step_volume = total_volume / 2.0

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ------------------------------------------------------------------
    # Labware
    # ------------------------------------------------------------------
    # Custom filter plate on Heater-Shaker with simulation fallback
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # Only fall back if the problem is a missing custom definition
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom filter plate definition not found; '
            'using nest_96_wellplate_200ul_flat as SIMULATION ONLY fallback.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    # Slot 3: Reservoir 4 — buffers with different 2x salt concentrations
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    # Other reservoirs present on deck but unused in this specific procedure
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ------------------------------------------------------------------
    # Pipettes
    # ------------------------------------------------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        'left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        'right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # Close Heater-Shaker latch before any pipetting to module labware
    hs_mod.close_labware_latch()

    # ------------------------------------------------------------------
    # Helper structures
    # ------------------------------------------------------------------
    # Salt buffers: ascending concentration with ascending well index in reservoir_4
    salt_source_wells = reservoir_4.wells()[:n_salt]

    # Columns of filter plate and mixing plate
    filter_columns = filter_plate.columns()          # 12 columns
    mixing_columns = mixing_plate.columns()[:n_salt]  # one column per salt concentration

    # Choose one column of tips in tiprack_4 to reuse for all transfers
    tip_columns = tiprack_4.columns()
    if len(tip_columns) < 1:
        raise RuntimeError(
            'Not enough tip columns available in tiprack 4 for multi-channel tips.'
        )
    reusable_tip_col = tip_columns[0]

    # ------------------------------------------------------------------
    # STEP 1: Transfer buffers from Reservoir 4 to filter plate
    # ------------------------------------------------------------------
    protocol.comment('Step 1: Transfer buffers from Reservoir 4 to filter plate.')

    # Reuse the same tips for all buffer transfers, then return them
    p300_multi.pick_up_tip(reusable_tip_col[0])

    for salt_idx in range(n_salt):
        src_well = salt_source_wells[salt_idx]

        # Each salt condition occupies `replicates` columns
        start_col = salt_idx * replicates
        end_col = start_col + replicates
        if end_col > 12:
            protocol.comment(
                f'Skipping salt index {salt_idx} in buffer transfer because '
                'destination columns exceed plate width.'
            )
            break

        dest_cols = filter_columns[start_col:end_col]

        # Multi-channel: use the A-row well (index 0) of each destination column,
        # with a 7 mm bottom offset as requested.
        dest_wells = [col[0].bottom(7.0) for col in dest_cols]

        # Single source well into multiple destination columns, reusing the same tips
        p300_multi.transfer(
            per_step_volume,
            src_well,
            dest_wells,
            new_tip='never',
            blow_out=True,
            blowout_location='source well'
        )

    # Return tips to original rack position
    p300_multi.drop_tip()

    # ------------------------------------------------------------------
    # STEP 2: Transfer ligands from Mixing Plate to filter plate
    # ------------------------------------------------------------------
    protocol.comment('Step 2: Transfer ligands from Mixing Plate to filter plate.')

    # Reuse the same column of tips from tiprack_4 for ligand transfers as well
    p300_multi.pick_up_tip(reusable_tip_col[0])

    for salt_idx in range(n_salt):
        src_col = mixing_columns[salt_idx]
        # Use A-row as the multi-channel source for each column, as specified
        src_well = src_col[0]

        start_col = salt_idx * replicates
        end_col = start_col + replicates
        if end_col > 12:
            protocol.comment(
                f'Skipping salt index {salt_idx} in ligand transfer because '
                'destination columns exceed plate width.'
            )
            break

        dest_cols = filter_columns[start_col:end_col]
        dest_wells = [col[0].bottom(7.0) for col in dest_cols]

        p300_multi.transfer(
            per_step_volume,
            src_well,
            dest_wells,
            new_tip='never',
            blow_out=True,
            blowout_location='source well'
        )

    p300_multi.drop_tip()

    # ------------------------------------------------------------------
    # Wrap up Heater-Shaker module
    # ------------------------------------------------------------------
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
