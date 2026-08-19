from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration Template',
    'author': 'User',
    'description': 'Templated equilibration protocol using placeholders for salt concentrations, replicates, and timing.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder literals (replaced by external template system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if a value is still a [[PLACEHOLDER]] during simulation.

    Uses constructed brackets so the literal [[...]] never appears here and
    cannot confuse the external substitution system.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value: str, default: float, cast=float):
    """Parse a scalar placeholder to a number.

    During simulation (when the placeholder is not substituted yet), fall back
    to the provided default *upper-bound* value so the protocol can be
    validated at worst-case usage.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value: str, default, cast=float):
    """Parse a list placeholder of the form 'v1;v2;v3'.

    During simulation (when still a placeholder), return the provided default
    list, again chosen as a worst-case length within deck limits.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ----------------------------------------------------------------------
    # Parse placeholders with simulation-friendly worst-case defaults
    # ----------------------------------------------------------------------
    # Worst-case for simulation: up to 8 salt concentrations (limited by a 96
    # well plate's 12 columns when multiplied by replicates).
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700]
    )

    # Use a default of 1 replicate for simulation but keep logic generic.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=1, cast=int))

    # Use near-maximal volumes/speeds/durations for validation.
    equilibration_volume = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_VOLUME,
        default=300.0,
        cast=float,
    )
    shaker_speed = parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default=2000.0,
        cast=float,
    )
    equilibration_time_min = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION,
        default=120.0,
        cast=float,
    )

    # 1. Multiply number of concentrations by replicates to get num_transfers.
    num_transfers = len(salt_concs) * replicates

    # Deck constraint: Reservoir 3 has 12 wells and the filter plate has 12
    # columns, so cap at 12 in case the template is configured larger.
    if num_transfers > 12:
        protocol.comment(
            f"Requested num_transfers={num_transfers} exceeds 12; "
            f"capping at 12 for available wells/columns."
        )
        num_transfers = 12

    protocol.comment(f"Number of salt concentrations: {len(salt_concs)}")
    protocol.comment(f"Replicates per concentration: {replicates}")
    protocol.comment(f"Total transfers (columns/wells used): {num_transfers}")

    # ----------------------------------------------------------------------
    # 1. Modules
    # ----------------------------------------------------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # Filter plate (custom labware) directly on the Heater-Shaker (no adapter)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # Only fall back if the definition is actually missing; re-raise
        # stack/position errors so they are visible.
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition for '
            'cytiva_96_filterwellplate_1ml not found; using NEST 96 Well '
            'Plate 200uL Flat as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # ----------------------------------------------------------------------
    # 2. Labware
    # ----------------------------------------------------------------------
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoir layout as named by user
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (buffers)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ----------------------------------------------------------------------
    # 3. Pipettes
    # ----------------------------------------------------------------------
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_slot4, tiprack_slot7, tiprack_slot10],
    )

    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_slot4, tiprack_slot7, tiprack_slot10],
    )

    # Optional: set starting tip for reproducible simulation
    p300_multi.starting_tip = tiprack_slot4['A1']

    # ----------------------------------------------------------------------
    # 2. Equilibration transfers
    # For each i in num_transfers: transfer [[EQUILIBRATION_VOLUME]] uL from
    # Well[i] of Reservoir 3 to Column[i] of the filter plate on the Heater
    # Shaker, using the multi-channel pipette and a 7 mm bottom offset in the
    # filter plate. Always return tips.
    # ----------------------------------------------------------------------
    hs_mod.close_labware_latch()

    reservoir3_wells = reservoir3.wells()      # 12 wells, index 0..11
    plate_columns = filter_plate.columns()     # 12 columns, index 0..11

    for i in range(num_transfers):
        src = reservoir3_wells[i]
        dest_col = plate_columns[i]
        # Multi-channel target: row A well of the column, with 7 mm bottom offset
        dest_location = dest_col[0].bottom(7)

        p300_multi.pick_up_tip()
        p300_multi.transfer(
            equilibration_volume,
            src,
            dest_location,
            new_tip='never',
        )
        # Return tip to its original position in the rack
        p300_multi.return_tip()

    # ----------------------------------------------------------------------
    # 3. Shaking step
    # Set the Heater-Shaker to [[SHAKER_SPEED_INCUBATION]] RPM for
    # [[EQUILIBRATION_CYCLE_DURATION]] minutes, then stop shaking and open
    # the latch.
    # ----------------------------------------------------------------------
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=equilibration_time_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
