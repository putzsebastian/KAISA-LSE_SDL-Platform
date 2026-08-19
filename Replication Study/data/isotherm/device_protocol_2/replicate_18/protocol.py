from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration Template',
    'author': 'User',
    'description': 'Templated protocol for equilibrating filter plate with salt buffers using heater-shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with simulation fallbacks (worst-case reasonable defaults)
    # Assume up to 8 different salt concentrations with up to 12 columns available
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=[0, 100, 200, 300, 400, 500, 600, 700])
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=1, cast=float))

    # For equilibration volume, assume upper bound 300 uL for simulation
    equilibration_volume = parse_scalar(PLACEHOLDER_EQUILIBRATION_VOLUME, default=300.0, cast=float)

    # Shaker speed (RPM), use high but reasonable default, e.g. 2000 rpm
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0, cast=float)

    # Equilibration duration (minutes), use upper bound default 60 minutes
    equilibration_duration_min = parse_scalar(PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION, default=60.0, cast=float)

    # Derived parameters
    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    if num_transfers > 12:
        raise RuntimeError('Number of transfers (concentrations x replicates) exceeds available columns (12).')

    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware
    # Filter plate on heater-shaker - custom labware with simulation fallback
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_4, tiprack_7, tiprack_10])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_4, tiprack_7, tiprack_10])

    # We only use the multi-channel pipette in this protocol per requirements

    # Heater-Shaker: make sure latch is closed before pipetting/shaking
    hs_mod.close_labware_latch()

    # Step 2: For each i in num_transfers, transfer equilibration buffer from reservoir_3 well i to filter plate column i
    # Use 7 mm offset from bottom of filter plate, multi-channel pipette, tips from slot 4, always return tips.

    # Define reservoir 3 wells in order (0-11 correspond to A1-H1, A2-H2, etc.)
    reservoir3_wells = reservoir_3.wells()  # 0-based list, 12 wells

    # Columns on filter plate, accessed via A-row for multichannel
    filter_columns = [filter_plate.columns()[i][0] for i in range(num_transfers)]

    # Multi-channel tip rack columns in slot 4 for dedicated use in this step
    tiprack4_columns = tiprack_4.columns()  # each entry is list of 8 wells

    if num_transfers > len(tiprack4_columns):
        raise RuntimeError('Not enough tip columns in slot 4 tiprack for the requested number of transfers.')

    for i in range(num_transfers):
        src_well = reservoir3_wells[i]
        dest_col_top = filter_columns[i]

        # Pick up tip from corresponding column in tiprack slot 4
        tip_column = tiprack4_columns[i]
        p300_multi.pick_up_tip(tip_column[0])

        # Transfer equilibration buffer with 7 mm bottom offset in filter plate
        p300_multi.transfer(
            equilibration_volume,
            src_well,
            dest_col_top.bottom(z=7.0),
            new_tip='never'
        )

        # Return tip to same position
        p300_multi.return_tip()

    # Step 3: Shaking on heater-shaker
    # Set shaking speed and wait until target speed reached
    hs_mod.set_and_wait_for_shake_speed(rpm=shaker_speed)
    # Maintain shaking for the specified duration
    protocol.delay(seconds=equilibration_duration_min * 60.0)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
