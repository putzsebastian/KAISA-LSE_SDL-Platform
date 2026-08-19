from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Gradient Equilibration Template',
    'author': 'User',
    'description': 'Templated protocol for loading different salt buffers into a filter plate on a Heater-Shaker using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (left as literal strings for the template engine to replace)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Detect unreplaced [[PLACEHOLDER]] tokens during simulation.

    Uses built brackets so that the literal '[[' never appears in code,
    avoiding substitution issues when this file is templated.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder value with a simulation fallback.

    - During real runs, the template engine should substitute numeric strings,
      which are cast to the requested type.
    - During simulation (unreplaced token), use the provided default.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder with a simulation fallback.

    Example substituted value: '0;100;200;500'.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ---------------------------------------------------------------------
    # Parse placeholders with simulation-friendly defaults (worst case style)
    # ---------------------------------------------------------------------
    # Use up to 8 salt concentrations as a safe upper bound for simulation.
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700],
        cast=float,
    )

    # Number of replicates per concentration
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=1, cast=float))

    # Volume per well in the filter plate (µL)
    equilibration_volume = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_VOLUME,
        default=200.0,
        cast=float,
    )

    # Heater-Shaker speed (RPM)
    shaker_speed = parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default=1200.0,
        cast=float,
    )

    # Heater-Shaker duration (minutes)
    equilibration_cycle_duration_min = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION,
        default=60.0,
        cast=float,
    )

    # Compute number of transfers = num_concentrations * replicates
    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    # 96-well filter plate has 12 columns; we use one column per transfer
    if num_transfers > 12:
        raise RuntimeError(
            'Number of transfers (concentrations * replicates) exceeds '
            '12 columns available on 96-well plate.'
        )

    # ---------------------------------------------------------------------
    # Modules
    # ---------------------------------------------------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # ---------------------------------------------------------------------
    # Labware
    # ---------------------------------------------------------------------
    # Filter plate on Heater-Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; using a '
            'standard plate as a SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tipracks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', '4')
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', '7')
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', '10')

    # Reservoirs (only Reservoir 3 is used in this equilibration step)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', '3')  # Reservoir 4
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', '6')  # Reservoir 3: salt buffers
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', '8')  # Reservoir 2
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', '9')  # Reservoir 1
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', '5')  # Reservoir 0

    # Mixing plate (not used in this specific step, but loaded per layout)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', '11')

    # ---------------------------------------------------------------------
    # Pipettes
    # ---------------------------------------------------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10],
    )

    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10],
    )

    # ---------------------------------------------------------------------
    # Step 2: Equilibration buffer transfers
    # ---------------------------------------------------------------------
    # Each transfer i uses:
    # - source: well i of Reservoir 3 (slot 6)
    # - destination: column i of filter plate on Heater-Shaker (slot 1)
    # Using an 8-channel pipette, each column is addressed via its A-row well.

    # Prepare Heater-Shaker for pipetting (latch must be closed)
    hs_mod.close_labware_latch()

    # Reservoir 3 wells: 0-11 correspond to A1-A12
    source_wells = reservoir_3.wells()[:num_transfers]
    dest_columns = filter_plate.columns()[:num_transfers]

    transfer_volume = equilibration_volume

    for idx in range(num_transfers):
        src = source_wells[idx]
        # Top well (row A) of the column for multi-channel targeting
        dest_col_top = dest_columns[idx][0]

        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        # Use transfer with new_tip='never' so we control pick-up/return;
        # destination uses a 7 mm bottom offset in the filter plate.
        p300_multi.transfer(
            transfer_volume,
            src,
            dest_col_top.bottom(z=7),
            new_tip='never',
        )

        # Return the tip after this column is filled
        p300_multi.return_tip()

    # ---------------------------------------------------------------------
    # Step 3: Shaking incubation
    # ---------------------------------------------------------------------
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=equilibration_cycle_duration_min)
    hs_mod.deactivate_shaker()

    # Open latch after shaking is complete
    hs_mod.open_labware_latch()
