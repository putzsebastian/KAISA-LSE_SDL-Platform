from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Gradient Equilibration Template',
    'author': 'User',
    'description': 'Templated equilibration protocol using placeholders for salt concentrations, replicates, and incubation parameters.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings; replaced by the workflow engine)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if s still looks like a [[PLACEHOLDER]] token.

    Uses built brackets so the literal [[ never appears in source,
    keeping the substitution system robust.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder, falling back to `default` for simulation.

    The default is only used in simulation when the placeholder is still
    unreplaced; on a real run, a bad value should raise.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    Example string: "0;100;200;500" -> [0.0, 100.0, 200.0, 500.0]
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ----------------------------
    # Parse placeholders with simulation-safe defaults
    # ----------------------------
    # Defaults respect the 12 columns of the 96-well filter plate.
    # On the real robot, the placeholders must be set such that
    # (replicates x number_of_concentrations) <= 12.
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=3)
    salt_concentrations = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 500]
    )
    equilibration_volume_ul = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_VOLUME,
        default=200.0
    )
    shaker_speed_rpm = parse_scalar(
        PLACEHOLDER_SHAKER_SPEED_INCUBATION,
        default=800.0
    )
    equilibration_cycle_min = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION,
        default=30.0
    )

    num_concs = len(salt_concentrations)
    num_transfers = replicates * num_concs

    # ----------------------------
    # Modules & Labware
    # ----------------------------
    # Heater-Shaker Module GEN1 in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Slot 1: Cytiva 96-well filter plate directly on Heater Shaker (no adapter)
    # Use custom labware with simulation fallback.
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition for cytiva_96_filterwellplate_1ml not found; '
            'using nest_96_wellplate_200ul_flat as a SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Slot 5: Reservoir 0
    reservoir_0 = protocol.load_labware(
        'nest_12_reservoir_15ml', 5, label='Reservoir 0'
    )

    # Slot 9: Reservoir 1
    reservoir_1 = protocol.load_labware(
        'nest_12_reservoir_15ml', 9, label='Reservoir 1'
    )

    # Slot 8: Reservoir 2
    reservoir_2 = protocol.load_labware(
        'nest_12_reservoir_15ml', 8, label='Reservoir 2'
    )

    # Slot 6: Reservoir 3 (salt buffers as described)
    reservoir_3 = protocol.load_labware(
        'nest_12_reservoir_15ml', 6, label='Reservoir 3 (Salt buffers)'
    )

    # Slot 3: Reservoir 4
    reservoir_4 = protocol.load_labware(
        'nest_12_reservoir_15ml', 3, label='Reservoir 4'
    )

    # Slot 11: Mixing plate
    mixing_plate = protocol.load_labware(
        'nest_96_wellplate_2ml_deep', 11, label='Mixing Plate'
    )

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # ----------------------------
    # Pipettes
    # ----------------------------
    # Left: P300 8-channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # Right: P300 single-channel GEN2 (not used in this step but loaded per spec)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # ----------------------------
    # Step 1: Compute and validate number of transfers
    # ----------------------------
    # Each transfer corresponds to one column on the 96-well filter plate.
    if num_transfers > 12:
        raise RuntimeError(
            f"Number of transfers (replicates x concentrations = {num_transfers}) "
            "exceeds the 12 available columns of the 96-well filter plate."
        )

    protocol.comment(
        f'Beginning equilibration transfer: {num_concs} concentrations, '
        f'{replicates} replicates each, total {num_transfers} columns.'
    )

    # Ensure latch is closed before pipetting on Heater Shaker
    hs_mod.close_labware_latch()

    # ----------------------------
    # Step 2: Equilibration buffer transfer
    # ----------------------------
    # For each i in num_transfers, transfer from well i of Reservoir 3
    # to each well in column i of the filter plate, 7 mm above bottom.
    # Multi-channel pipette: one command per column.

    columns = filter_plate.columns()  # list of 12 columns, each a list of 8 wells

    for i in range(num_transfers):
        src_well = reservoir_3.wells()[i]
        dest_col = columns[i]

        # Use tips from slot 4, one column per multi-channel pickup, always returned.
        p300_multi.pick_up_tip(tiprack_4.columns()[i][0])

        # Target column via its A-row well and specify 7 mm bottom offset.
        p300_multi.transfer(
            equilibration_volume_ul,
            src_well,
            dest_col[0].bottom(7.0),
            new_tip='never',
            blow_out=True,
            blowout_location='destination well'
        )

        p300_multi.drop_tip()

    # ----------------------------
    # Step 3: Shaking incubation
    # ----------------------------
    protocol.comment(
        f'Starting shaking at {shaker_speed_rpm} rpm for {equilibration_cycle_min} minutes.'
    )

    hs_mod.close_labware_latch()
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=equilibration_cycle_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()

    protocol.comment('Equilibration cycle complete.')
