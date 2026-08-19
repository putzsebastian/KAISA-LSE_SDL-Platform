from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Buffer Equilibration Template',
    'author': 'User',
    'description': 'Templated protocol for equilibrating filter plate with salt buffers using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder literals (for external templating)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder like [[TOKEN]] has not been replaced yet."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder; fall back to default during simulation.

    Once the placeholder is substituted, this will cast to the desired type
    (via float first, to allow numeric strings like '3.0').
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    """Parse an integer placeholder; fall back to default during simulation."""
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder; fall back to default.

    Example placeholder value after substitution:
    '0;100;200;500' -> [0.0, 100.0, 200.0, 500.0]
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    items = [x for x in s.split(';') if x.strip()]
    return [cast(x) for x in items]


def run(protocol: protocol_api.ProtocolContext):
    # Parse template parameters with conservative worst-case defaults for simulation
    # Defaults are chosen as upper bounds to stress-test tips and volumes.
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=8)
    salt_concentrations = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float
    )
    equilibration_volume = parse_scalar(PLACEHOLDER_EQUILIBRATION_VOLUME, default=200.0)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=1200.0)
    equilibration_cycle_duration = parse_scalar(PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION, default=60.0)

    # Step 1: num_transfers = (#concentrations) * REPLICATES
    num_concs = len(salt_concentrations)
    num_transfers = replicates * num_concs

    # Safety for simulation: we only have 12 wells in reservoir row A and 12 columns on the plate
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: requested num_transfers={num_transfers} exceeds available 12 wells/columns. "
            f"Simulation will only execute first 12 transfers."
        )
        num_transfers = 12

    # 1. Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # 2. Labware
    # Filter Plate on Heater Shaker in Slot 1 (custom labware, with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (0–4) and mixing plate, to match deck layout even if unused in this step
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (buffers)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)  # Mixing Plate

    # 3. Pipettes
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # Ensure latch is closed before any pipetting on HS
    hs_mod.close_labware_latch()

    # Step 2: For each i in num_transfers, transfer [[EQUILIBRATION_VOLUME]] µL
    # from Reservoir 3 well i to Column i of the filter plate, using multi-channel.

    # Destination columns on filter plate
    dest_columns = filter_plate.columns()[:num_transfers]

    # Source wells in Reservoir 3 (NEST 12-well reservoir row A1–A12)
    source_wells = reservoir3.wells()[:num_transfers]

    # Perform transfers, one column per iteration, always returning tips
    for i in range(num_transfers):
        src = source_wells[i]
        dest_col = dest_columns[i]
        dest_top_well = dest_col[0]  # A-row well represents the full column for multichannel

        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        # Transfer with 7 mm offset from the bottom of the filter plate
        p300_multi.transfer(
            equilibration_volume,
            src,
            dest_top_well.bottom(z=7.0),
            new_tip='never'
        )

        p300_multi.drop_tip()

    # Step 3: Heater shaker incubation
    # Close latch (already closed, but this is idempotent) and start shaking
    hs_mod.close_labware_latch()

    # Enforce valid shake speed range (200–3000 rpm) for safety
    target_speed = max(200.0, min(shaker_speed, 3000.0))
    hs_mod.set_and_wait_for_shake_speed(target_speed)

    # Equilibration duration in minutes
    protocol.delay(minutes=equilibration_cycle_duration)

    # Stop shaking
    hs_mod.deactivate_shaker()

    # Open latch at the end
    hs_mod.open_labware_latch()
