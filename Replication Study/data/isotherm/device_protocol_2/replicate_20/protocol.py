from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration on Heater Shaker',
    'author': 'User',
    'description': 'Templated protocol for equilibrating filter plate with salt buffers'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# ----- Placeholders (literal strings for later substitution) -----
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if string still looks like a [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder; fall back to default in simulation.

    cast should be a numeric type (e.g. float). We always route via float
    so that strings like '3.0' also work when cast=int.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    """Parse an integer-like scalar placeholder; fall back to default."""
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder; fall back to default.

    Example: "0;100;200;500" -> [0.0, 100.0, 200.0, 500.0]
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Simulation-time defaults (chosen as upper-bound reasonable values).
    # These are ONLY used when the placeholders have not yet been
    # substituted (e.g. during simulation in this environment).
    default_replicates = 3
    default_salt_concentrations = [0, 100, 200, 500]
    default_equilibration_volume = 200.0  # uL per well in the column
    default_shaker_speed = 2000.0  # rpm
    default_equilibration_cycle_duration = 30.0  # minutes

    # Parse user-supplied (or yet-unsubstituted) placeholder values
    replicates = parse_int(PLACEHOLDER_REPLICATES, default_replicates)
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default_salt_concentrations,
        cast=float,
    )
    equilibration_volume = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_VOLUME,
        default_equilibration_volume,
        cast=float,
    )
    shaker_speed = parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default_shaker_speed,
        cast=float,
    )
    equilibration_cycle_duration = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION,
        default_equilibration_cycle_duration,
        cast=float,
    )

    num_transfers = replicates * len(salt_concs)

    protocol.comment(
        f"Parsed parameters (for simulation): replicates={replicates}, "
        f"num_concs={len(salt_concs)}, num_transfers={num_transfers}, "
        f"equil_vol={equilibration_volume} uL, "
        f"shaker_speed={shaker_speed} rpm, "
        f"equilibration_time={equilibration_cycle_duration} min"
    )

    # ----- Modules -----
    # Slot 1: Heater-Shaker Module GEN1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ----- Labware -----
    # Slot 1: Filter Plate (custom) directly on Heater Shaker (no adapter in code)
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

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    # Slot 6: Reservoir 3 (buffers with different salt concentrations)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)

    # Other labware per deck layout (not used in this step, but loaded for completeness)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # Reservoir 4
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # Reservoir 0
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # Reservoir 2
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # Reservoir 1
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ----- Pipettes -----
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10],
    )

    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
    )

    # ----- Step 1: compute num_transfers (already done above) -----
    # num_transfers = number of concentrations * number of replicates

    # Ensure we do not exceed the 12 available columns
    if num_transfers > 12:
        raise RuntimeError(
            f"num_transfers ({num_transfers}) exceeds number of available "
            f"columns (12) in a 96-well plate"
        )

    # ----- Step 2: Transfer equilibration buffer to filter plate -----
    # For each i in num_transfers:
    #   transfer [[EQUILIBRATION_VOLUME]] uL from Well[i] of Reservoir 3
    #   to each well in Column[i] of the filter plate, using 7 mm
    #   bottom offset, multi-channel pipette, tips from slots 4/7/10,
    #   and always return tips.

    # Heater-shaker latch must be closed before any pipetting on the module
    hs_mod.close_labware_latch()

    # Prepare sources and destinations
    source_wells = reservoir_3.wells()[:num_transfers]
    dest_columns = filter_plate.columns()[:num_transfers]

    for i in range(num_transfers):
        src = source_wells[i]
        dest_col = dest_columns[i]
        # Multi-channel: address the column via its A-row well, with offset
        dest = dest_col[0].bottom(z=7)

        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        # Use transfer so any volume > max_volume is automatically chunked
        p300_multi.transfer(equilibration_volume, src, dest, new_tip='never')

        # Always return tip after each column
        p300_multi.return_tip()

    # ----- Step 3: Shake at specified speed and duration -----
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=equilibration_cycle_duration)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
