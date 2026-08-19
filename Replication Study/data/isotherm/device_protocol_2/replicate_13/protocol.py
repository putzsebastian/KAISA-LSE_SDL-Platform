from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration Template',
    'author': 'User',
    'description': 'Template protocol for salt equilibration using placeholders'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder literals (will be replaced by the wizard)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder token has not yet been substituted."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse scalar placeholder to number with simulation-time default.

    The default is used ONLY during simulation when the placeholder token
    has not yet been replaced.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse list placeholder "a;b;c" -> [cast(a), cast(b), cast(c)]."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --- Parse placeholders with simulation-safe worst-case defaults ---
    # Use high/worst-case values so simulation stresses tips/volumes.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, 8, cast=int))
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0, 100, 200, 300, 400, 500, 600, 700]
    )
    equilibration_volume = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_VOLUME,
        300.0,
        cast=float
    )
    shaker_speed = int(
        parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 1200, cast=int)
    )
    equilibration_cycle_duration = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION,
        60.0,
        cast=float
    )

    num_concentrations = len(salt_concs)
    num_transfers = replicates * num_concentrations

    # Safety for 96-well filter plate: max 12 columns
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: num_transfers ({num_transfers}) exceeds 12 columns; "
            f"capping to 12 for this run."
        )
        num_transfers = 12

    # --- Modules and labware ---
    # Slot 1: Heater Shaker Module GEN1 with custom Cytiva filter plate
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load custom filter plate directly on module, with simulation fallback
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; using NEST 96 flat '
            'plate as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (slot 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (slot 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (slot 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (slot 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (slot 5)

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # --- Pipettes ---
    p300_single = protocol.load_instrument(
        'p300_single_gen2', mount='right', tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2', mount='left', tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # --- Step 2: Transfers from Reservoir 3 to filter plate columns ---
    # Close latch before any pipetting on Heater-Shaker labware
    hs_mod.close_labware_latch()

    # Use only tips from slot 4 for this step
    p300_multi.tip_racks = [tiprack_4]

    reservoir_wells = reservoir_3.wells()  # 12 wells: indices 0..11

    for i in range(num_transfers):
        if i >= len(reservoir_wells):
            protocol.comment(
                f"WARNING: Requested transfer index {i} exceeds available "
                f"reservoir wells; stopping."
            )
            break

        # For each i, use well[i] of Reservoir 3 as source
        source_well = reservoir_wells[i]

        # For each i, use column[i] of filter plate as destination
        dest_column = filter_plate.columns()[i]

        # Multi-channel: use the top well of the column (row A)
        dest_location = dest_column[0].bottom(7)

        p300_multi.pick_up_tip()
        p300_multi.transfer(
            equilibration_volume,
            source_well,
            dest_location,
            new_tip='never',
            blow_out=True,
            blowout_location='destination well'
        )
        p300_multi.drop_tip()

    # --- Step 3: Shaking step ---
    hs_mod.set_and_wait_for_shake_speed(rpm=shaker_speed)
    protocol.delay(minutes=equilibration_cycle_duration)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()

    protocol.comment('Equilibration template protocol complete.')
