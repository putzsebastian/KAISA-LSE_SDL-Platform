from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration on Filter Plate',
    'author': 'User',
    'description': 'Templated equilibration of filter plate with different salt buffers using Heater-Shaker'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (templated values to be filled by external system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder string has not yet been substituted."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder value with a simulation-time default.

    Uses the largest reasonable default values so simulation exercises
    worst-case resource usage. On real runs, placeholders must be
    substituted with valid numeric strings.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    Returns a list of cast() values; if placeholder is unreplaced,
    returns a copy of the provided default list.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ------------------------------------------------------------------
    # Parse templated parameters with worst-case simulation defaults
    # ------------------------------------------------------------------
    # Assume up to 8 different salt concentrations as a reasonable upper bound
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float,
    )
    # Worst-case: up to 12 replicates (12 wells in the reservoir row)
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=int))
    # Worst-case equilibration volume close to pipette max
    equil_vol = parse_scalar(PLACEHOLDER_EQUILIBRATION_VOLUME, default=300.0, cast=float)
    # Worst-case shaker speed and duration
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0, cast=float)
    equil_duration_min = parse_scalar(PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION, default=60.0, cast=float)

    # Derived counts
    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    # Reservoir 3 and a 96-well plate row both have at most 12 positions/columns.
    # Cap num_transfers at 12 for safety, but still warn in the runlog.
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: Computed num_transfers ({num_transfers}) exceeds 12; "
            f"capping to 12 for this run. Adjust [[REPLICATES]] and/or [[SALT_CONCENTRATIONS]]."
        )
        num_transfers = 12

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------
    # Heater Shaker Module GEN1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ------------------------------------------------------------------
    # Labware
    # ------------------------------------------------------------------
    # Slot 1: Filter Plate on Heater-Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Any error other than a missing definition must surface
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
            'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (salt buffers)

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ------------------------------------------------------------------
    # Pipettes
    # ------------------------------------------------------------------
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10],
    )

    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10],
    )

    # ------------------------------------------------------------------
    # Step 2: Buffer transfer from Reservoir 3 to filter plate columns
    # ------------------------------------------------------------------
    # Ensure latch is closed before any pipetting on the Heater-Shaker
    hs_mod.close_labware_latch()

    # Use the first row of reservoir_3 wells (A1-A12) as sources
    source_wells = reservoir_3.wells()[:num_transfers]
    # Use the corresponding columns on the filter plate as destinations
    dest_columns = filter_plate.columns()[:num_transfers]

    for src, dest_col in zip(source_wells, dest_columns):
        # For an 8-channel pipette, use the A-row well of the target column
        col_top_well = dest_col[0]
        p300_multi.pick_up_tip()
        p300_multi.transfer(
            equil_vol,
            src,
            col_top_well.bottom(z=7.0),  # 7 mm above bottom as requested
            new_tip='never',
        )
        # Always return tip to rack
        p300_multi.return_tip()

    # ------------------------------------------------------------------
    # Step 3: Shaking incubation
    # ------------------------------------------------------------------
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=equil_duration_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
