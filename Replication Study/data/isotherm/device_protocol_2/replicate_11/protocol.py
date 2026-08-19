from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration on Filter Plate',
    'author': 'User',
    'description': 'Templated protocol for equilibrating a filter plate with different salt buffers on a heater-shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# --- Placeholders (literal strings to be replaced upstream) ---
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder has not been substituted yet.

    Uses constructed bracket strings so that the literal '[[' and ']]' never
    appear in code positions that are later re-serialized.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder, with a simulation-only default.

    The default should be the worst-case (largest) value expected so that
    simulation exercises maximum loads on tips, volumes, and timings.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    # cast via float so that e.g. '3.0' is accepted for an int
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder, with simulation default."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --- Parse placeholders with simulation fallbacks ---
    # Use large/worst-case defaults for simulation
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=3))
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=[0, 100, 200, 500])
    equilibration_volume = parse_scalar(PLACEHOLDER_EQUILIBRATION_VOLUME, default=300.0)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0)
    equilibration_cycle_duration_min = parse_scalar(PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION, default=60.0)

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    # --- Modules and labware ---
    # Heater-Shaker Module Gen1 in slot 1 with filter plate directly on module
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Custom filter plate on heater-shaker; use simulation fallback if custom definition missing
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml', label='Filter Plate')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Wrong slot/stacking/etc. must surface
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; using a 96-well plate '
            'as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat', label='Filter Plate Fallback')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (deck positions as specified; only Reservoir 3 is used in this step)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3, label='Reservoir 4 (slot 3)')
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6, label='Reservoir 3 (slot 6, salt gradients)')
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8, label='Reservoir 2 (slot 8)')
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9, label='Reservoir 1 (slot 9)')
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5, label='Reservoir 0 (slot 5)')

    # Mixing plate in slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11, label='Mixing Plate')

    # --- Pipettes ---
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # --- Step 1: determine number of transfers ---
    protocol.comment(
        f'Replicates: {replicates}, Salt concentrations: {salt_concs}, '
        f'Num transfers (columns): {num_transfers}'
    )

    # Filter plate has 12 columns; guard against invalid placeholder combinations
    if num_transfers > 12:
        raise RuntimeError(
            f'Number of transfers (num_transfers={num_transfers}) exceeds number of '
            f'columns (12) in the 96-well filter plate.'
        )

    # --- Step 2: Transfer equilibration buffer from Reservoir 3 to filter plate columns ---
    # Reservoir 3 (slot 6) is a 12-well reservoir with salt buffers arranged by
    # concentration and replicate, as described in the prompt.
    reservoir_wells = reservoir_3.wells()  # 12 wells, index 0..11

    # Close latch before any pipetting to labware on the Heater-Shaker
    hs_mod.close_labware_latch()

    # Use tips from slot 4 for this equilibration step (start there explicitly)
    p300_multi.starting_tip = tiprack_4.wells()[0]

    for i in range(num_transfers):
        # Source: well i of Reservoir 3
        src_well = reservoir_wells[i]

        # Destination: column i of filter plate (multi-channel => row A addresses the column)
        dest_col_top = filter_plate.columns()[i][0]

        p300_multi.pick_up_tip()
        p300_multi.transfer(
            equilibration_volume,
            src_well,
            dest_col_top.bottom(z=7.0),  # 7 mm above bottom as requested
            new_tip='never',
            blow_out=True,
            blowout_location='destination well'
        )
        # Always return tips to rack in slot 4 per requirement
        p300_multi.return_tip()

    # --- Step 3: Shake for equilibration ---
    # Set shaking speed and duration
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)

    # Delay for equilibration duration (minutes)
    protocol.delay(minutes=equilibration_cycle_duration_min)

    # Stop shaking and open latch
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
