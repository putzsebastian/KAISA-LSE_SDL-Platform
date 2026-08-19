from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration Template',
    'author': 'User',
    'description': 'Templated equilibration protocol with placeholders for salt concentrations, replicates, volumes, and shaker settings.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (left as literal strings for external substitution)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if the given string still looks like a [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder to a float (or other cast), with simulation fallback.

    The default is only used during simulation, when the placeholder has not yet
    been substituted. On the real robot, a bad substitution should raise.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    """Parse an integer placeholder with a worst-case simulation default."""
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default_cast=str):
    """Parse a semicolon-separated list placeholder.

    If the placeholder is unreplaced, fall back to a worst-case salt list for
    simulation (8 concentrations).
    """
    s = str(value).strip()
    if _unreplaced(s):
        # default: 8 concentrations as worst-case for simulation
        fallback = '0;100;200;300;400;500;600;700'
        items = [x for x in fallback.split(';') if x.strip()]
        return [default_cast(x) for x in items]
    items = [x for x in s.split(';') if x.strip()]
    return [default_cast(x) for x in items]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with simulation-safe fallbacks (worst-case reasonable values)
    salt_concentrations = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default_cast=str)
    # Worst-case: assume up to 12 replicates so num_transfers can reach 12
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=12)
    equilibration_vol = parse_scalar(PLACEHOLDER_EQUILIBRATION_VOLUME, default=200.0)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0)
    equilibration_time_min = parse_scalar(PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION, default=60.0)

    # Step 1: num_transfers = number_of_concentrations * replicates
    num_transfers = len(salt_concentrations) * replicates

    # Filter plate has 12 columns max; cap for safety/logging
    if num_transfers > 12:
        protocol.comment('WARNING: num_transfers exceeds 12 columns; limiting to first 12 columns.')
        num_transfers = 12

    # --- Modules ---
    # Heater Shaker Module Gen1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # --- Labware ---
    # Filter plate (custom labware) directly on Heater Shaker, no adapter
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml', label='Filter Plate')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Real error (wrong slot/stack etc.) must surface
            raise
        protocol.comment('WARNING: custom labware not found; using NEST 96 flat as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat', label='Filter Plate SIM Fallback')

    # Tipracks
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6, label='Reservoir 3 (salt buffers)')
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3, label='Reservoir 4')
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8, label='Reservoir 2')
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9, label='Reservoir 1')
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5, label='Reservoir 0')

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11, label='Mixing Plate')

    # --- Pipettes ---
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_slot4, tiprack_slot7, tiprack_slot10]
    )

    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_slot4, tiprack_slot7, tiprack_slot10]
    )

    protocol.comment(f'Salt concentrations list: {salt_concentrations}')
    protocol.comment(f'Replicates (per concentration): {replicates}')
    protocol.comment(f'Equilibration volume (uL): {equilibration_vol}')
    protocol.comment(f'Shaker speed (RPM): {shaker_speed}')
    protocol.comment(f'Equilibration time (min): {equilibration_time_min}')
    protocol.comment(f'Num transfers (columns): {num_transfers}')

    # Ensure latch is closed before pipetting or shaking on HS
    hs_mod.close_labware_latch()

    # --- Step 2 ---
    # For each i in num_transfers, transfer equilibration volume from
    # reservoir3 well i to column i of the filter plate using the multi-channel.

    source_wells = reservoir3.wells()[:num_transfers]
    dest_columns = filter_plate.columns()[:num_transfers]

    for i in range(num_transfers):
        src = source_wells[i]
        dest_col = dest_columns[i]
        # For multi-channel, target the A-row well of the column
        dest = dest_col[0]

        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        p300_multi.transfer(
            equilibration_vol,
            src,
            dest.bottom(7),  # 7 mm above bottom as requested
            new_tip='never',
            blow_out=True,
            blowout_location='destination well'
        )
        # Always return the tip to its original position
        p300_multi.return_tip()

    # --- Step 3 ---
    # Set the heater shaker to shake at the specified speed and duration
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=equilibration_time_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
    protocol.comment('Equilibration shaking complete.')
