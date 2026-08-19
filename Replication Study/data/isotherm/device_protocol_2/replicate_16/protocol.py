from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration Templated',
    'author': 'User',
    'description': 'Templated equilibration protocol with placeholders for salt concentrations, replicates, and equilibration parameters.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings for external substitution)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string is an unreplaced [[PLACEHOLDER]]."""
    s2 = str(s).strip()
    return s2.startswith('[' * 2) and s2.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder.

    During simulation, when value is still a [[TOKEN]], return the provided
    worst-case default. After substitution on the real robot, the cast must
    succeed or the run should fail loudly.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder into a list of numbers.

    During simulation, when value is still a [[TOKEN]], return the provided
    worst-case default list.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ------------------------------------------------------------------
    # 1) Parse placeholders with simulation fallbacks (worst-case style)
    # ------------------------------------------------------------------
    # Use a long default list so simulation exercises many transfers.
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700]
    )
    # Use an upper-bound default for replicates so num_transfers is large.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=float))
    # Equilibration volume in µL per well; use a large but realistic default.
    equilibration_vol = parse_scalar(
        PLACEHOLDER_EQUILRATION_VOLUME if False else PLACEHOLDER_EQUILIBRATION_VOLUME,
        default=250.0,
        cast=float
    )
    # Shaker speed (RPM), default to a high but valid value for HS.
    shaker_speed = int(parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default=2000.0,
        cast=float
    ))
    # Equilibration time in minutes, use a long default to stress timing.
    equilibration_time_min = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION,
        default=60.0,
        cast=float
    )

    # Step 1: number of transfers (buffer-to-column pairs)
    num_transfers = len(salt_concs) * replicates

    protocol.comment(
        f'Simulation parameters: {num_transfers} transfers, '
        f'{equilibration_vol} uL per column, speed {shaker_speed} rpm, '
        f'time {equilibration_time_min} min.'
    )

    # ------------------------------------------------------------------
    # 2) Modules
    # ------------------------------------------------------------------
    # Heater-Shaker Module GEN1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ------------------------------------------------------------------
    # 3) Labware
    # ------------------------------------------------------------------
    # Slot 1: Filter Plate on Heater Shaker (no adapter) - custom labware
    # Use a simulation fallback if custom definition is unavailable.
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Real error (bad stacking, wrong slot, etc.) should still surface.
            raise
        protocol.comment(
            'WARNING: custom labware definition "cytiva_96_filterwellplate_1ml" '
            'not available; using NEST 96 flat plate as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (for completeness, even if only Reservoir 3 is used here)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (salt buffers)

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ------------------------------------------------------------------
    # 4) Pipettes
    # ------------------------------------------------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right'
    )

    # Ensure latch is closed before any shaking or pipetting on module labware
    hs_mod.close_labware_latch()

    # ------------------------------------------------------------------
    # 5) Step 2 – Equilibration buffer transfer
    # ------------------------------------------------------------------
    # Reservoir 3 holds the salt buffers organized as described in the prompt.
    reservoir3_wells = reservoir3.wells()  # 12 wells, indexed 0–11
    filter_columns = filter_plate.columns()  # 12 columns on 96-well plate

    # There are only 12 wells in the reservoir and 12 columns in the plate.
    # The conceptual num_transfers may be larger but we are constrained to 12.
    if num_transfers > 12:
        protocol.comment(
            'WARNING: num_transfers (concentrations × replicates) exceeds 12; '
            'truncating to the first 12 wells/columns to match the 96‑well format.'
        )
        num_cols = 12
    else:
        num_cols = int(num_transfers)

    # Ensure we never index past available wells/columns
    num_cols = min(num_cols, len(reservoir3_wells), len(filter_columns))

    source_wells = []
    dest_wells = []

    # For each i, map reservoir3 well i → column i of filter plate, using top well (A) for multichannel
    for i in range(num_cols):
        src = reservoir3_wells[i]
        # Multi-channel pipette addresses column via the A-row well, then we apply a 7 mm bottom offset
        dest = filter_columns[i][0].bottom(7)
        source_wells.append(src)
        dest_wells.append(dest)

    # Execute the transfers with the multi-channel using tips from slot 4/7/10
    if source_wells and dest_wells:
        p300_multi.transfer(
            equilibration_vol,
            source_wells,
            dest_wells,
            new_tip='always',
            blow_out=True,
            blowout_location='destination well'
        )
        # Tips are automatically dropped to trash; no explicit return-to-rack is used
        # because they are single-use in this equilibration step.

    # ------------------------------------------------------------------
    # 6) Step 3 – Shaking incubation
    # ------------------------------------------------------------------
    hs_mod.set_and_wait_for_shake_speed(rpm=shaker_speed)
    protocol.delay(minutes=equilibration_time_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()

    protocol.comment('Equilibration step completed.')
