from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt Equilibration on Heater-Shaker',
    'author': 'User',
    'description': 'Equilibrate filter plate with salt buffers using placeholders for key parameters.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# -------------------------
# Placeholders (literal strings for the wizard to replace)
# -------------------------

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder token has not yet been replaced.

    Uses constructed brackets so that the literal tokens remain available
    for the template engine to substitute.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder to a number with a simulation default.

    For simulation, if the placeholder is unreplaced, fall back to a
    worst-case default so tip usage and timing are maximally exercised.
    Once substituted on the real robot, the value must parse or the
    protocol will raise, which is desired.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder to a list of numbers.

    Example concrete value: "0;100;200;500".
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):

    # -------------------------
    # Parse placeholders
    # -------------------------

    # Use a high default for simulation so the protocol is stressed at
    # worst-case settings; real values are substituted later.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=int))

    # SALT_CONCENTRATIONS is a semicolon-separated list. For simulation,
    # use 8 concentrations so 8 * 12 = 96 potential columns (upper bound).
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float
    )

    # Volume per column transfer (µL)
    equilibration_volume = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_VOLUME,
        default=200.0,
        cast=float
    )

    # Heater-shaker speed (RPM)
    shaker_speed = parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default=1200.0,
        cast=float
    )

    # Equilibration cycle duration (minutes)
    equilibration_cycle_duration = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION,
        default=60.0,
        cast=float
    )

    # Number of source wells / destination columns required
    num_transfers = len(salt_concs) * replicates

    # The filter plate has 12 columns; cap at 12 if the template values
    # would exceed this physical limit.
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: Computed num_transfers={num_transfers} exceeds 12 columns. "
            "Only first 12 will be used on a 96-well plate."
        )
        num_transfers = 12

    # -------------------------
    # Load modules and labware
    # -------------------------

    # Heater-Shaker Module GEN1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # Slot 1: Filter Plate ('cytiva_96_filterwellplate_1ml') mounted directly
    # on the Heater-Shaker (no adapter). Use a simulation fallback since the
    # custom definition is not available to the simulator.
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            # Any error other than missing custom definition must surface.
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; '
            'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', '4')
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', '7')
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', '10')

    # Reservoirs
    # NOTE: Slot numbering here follows the user-specified deck layout.
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', '3')  # Slot 3: Reservoir 4
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', '6')  # Slot 6: Reservoir 3
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', '8')  # Slot 8: Reservoir 2
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', '9')  # Slot 9: Reservoir 1
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', '5')  # Slot 5: Reservoir 0

    # Mixing plate (slot 11)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', '11')

    # -------------------------
    # Load pipettes
    # -------------------------

    # Right Mount: P300 Single-Channel GEN2
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # Left Mount: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # -------------------------
    # Step 2: Equilibration transfers
    # -------------------------

    # Latch must be closed before pipetting or shaking on the Heater-Shaker.
    hs_mod.close_labware_latch()

    # Each well in Reservoir 3 (slot 6) corresponds to one column of the
    # filter plate. We use wells[0:num_transfers] and columns[0:num_transfers].
    source_wells = reservoir_3.wells()[:num_transfers]
    dest_columns = filter_plate.columns()[:num_transfers]

    # For each i in num_transfers, transfer [[EQUILIBRATION_VOLUME]] µL from
    # reservoir_3 well[i] to each well in column[i] of the filter plate
    # at a 7 mm bottom offset, using the multi-channel pipette.
    # Use tips from slot 4/7/10 and always return them.

    for src, dest_col in zip(source_wells, dest_columns):
        # dest_col is a list of 8 wells (A–H). For an 8-channel pipette,
        # addressing the column via the A-row well is sufficient, and the
        # API will apply the command to the full column.
        top_well = dest_col[0]  # row A of this column

        p300_multi.pick_up_tip()
        p300_multi.transfer(
            equilibration_volume,
            src,
            top_well.bottom(7.0),  # 7 mm above bottom in the filter plate
            new_tip='never'
        )
        # Return the tip to the rack (as requested) instead of discarding.
        p300_multi.return_tip()

    # -------------------------
    # Step 3: Shaking step
    # -------------------------

    # Ensure latch is closed before shaking.
    hs_mod.close_labware_latch()

    # Set the Heater-Shaker to the desired speed and wait until reached.
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)

    # Shake for [[EQUILIBRATION_CYCLE_DURATION]] minutes.
    protocol.delay(minutes=equilibration_cycle_duration)

    # Stop shaking. Using deactivate_shaker() avoids specifying an
    # explicit 0 rpm, which is outside the valid 200–3000 rpm range.
    hs_mod.deactivate_shaker()

    # Open latch after shaking is complete.
    hs_mod.open_labware_latch()
