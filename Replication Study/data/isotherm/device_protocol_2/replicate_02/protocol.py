from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Gradient Equilibration Template',
    'author': 'User',
    'description': 'Templated protocol for equilibrating a Cytiva 96-well filter plate on a Heater-Shaker using salt gradient buffers with placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# --- Placeholders (left as literal strings for templating) ---
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string is still an unreplaced [[TOKEN]]."""
    s_stripped = str(s).strip()
    return s_stripped.startswith('[' * 2) and s_stripped.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder or use a simulation fallback.

    - value: placeholder string (e.g. '[[EQUILIBRATION_VOLUME]]')
    - default: numeric default used only during simulation when token is unreplaced
    - cast: numeric type to cast to (float by default)
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_int(value, default):
    """Parse an integer placeholder or use a simulation fallback."""
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder or use a simulation fallback.

    Returns a list of casted numeric values.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    parts = [p for p in s.split(';') if p.strip()]
    return [cast(float(p)) for p in parts]


def run(protocol: protocol_api.ProtocolContext):
    # --- Parse placeholder values with simulation fallbacks (worst-case style) ---
    # Use reasonably large worst-case defaults to stress-test volumes/tips in simulation.
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=3)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=[0, 100, 200, 500])
    equilibration_volume = parse_scalar(PLACEHOLDER_EQUILIBRATION_VOLUME, default=200.0)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=1200.0)
    equilibration_cycle_duration = parse_scalar(PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION, default=10.0)

    num_concs = len(salt_concs)
    num_transfers = replicates * num_concs

    # 12 available wells in reservoir row and 12 columns in 96-well plate
    if num_transfers > 12:
        raise RuntimeError(
            f"Number of total transfers (replicates x concentrations = {num_transfers}) "
            "exceeds number of available columns/wells in a 12-column reservoir/plate. "
            "Reduce REPLICATES or number of SALT_CONCENTRATIONS."
        )

    protocol.comment(
        f"Parsed parameters: replicates={replicates}, "
        f"num_concentrations={num_concs}, num_transfers={num_transfers}, "
        f"equilibration_volume={equilibration_volume} uL, "
        f"shaker_speed={shaker_speed} rpm, "
        f"equilibration_cycle_duration={equilibration_cycle_duration} min"
    )

    # --- Modules ---
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # --- Labware ---
    # Slot 1: Filter Plate ('cytiva_96_filterwellplate_1ml') directly on Heater-Shaker (no adapter)
    # Use a custom labware fallback for simulation.
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
            'using nest_96_wellplate_200ul_flat as a SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Slot 4, 7, 10: Opentrons 96 Tiprack 300 uL
    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', '4')
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', '7')
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', '10')

    # Slot 3: NEST 12-Well 15 mL Reservoir (Reservoir 4)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', '3')

    # Slot 6: NEST 12-Well 15 mL Reservoir (Reservoir 3) – salt gradient buffers
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', '6')

    # Slot 8: NEST 12-Well 15 mL Reservoir (Reservoir 2)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', '8')

    # Slot 9: NEST 12-Well 15 mL Reservoir (Reservoir 1)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', '9')

    # Slot 5: NEST 12-Well 15 mL Reservoir (Reservoir 0)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', '5')

    # Slot 11: NEST 96 Deep-Well Plate 2 mL (Mixing Plate)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', '11')

    # --- Pipettes ---
    # Left Mount: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    # Right Mount: P300 Single-Channel GEN2 (not used in this specific procedure, but loaded as specified)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3]
    )

    # --- Step 1: Calculate number of transfers ---
    protocol.comment(f"Step 1: Number of transfers (num_transfers) = {num_transfers}")

    # --- Step 2: Transfer equilibration buffer from Reservoir 3 to filter plate columns ---
    protocol.comment(
        'Step 2: Equilibration buffer transfer from Reservoir 3 to filter plate columns'
    )

    # Ensure labware is latched before any shaking or pipetting on the module
    hs_mod.close_labware_latch()

    # Each i corresponds to well i in Reservoir 3 and column i in the filter plate.
    # Use an offset of 7 mm from the bottom of the filter plate. Use the multi-channel pipette.
    reservoir_wells = reservoir_3.wells()[:num_transfers]
    filter_columns = filter_plate.columns()[:num_transfers]

    for i, (src_well, dest_column) in enumerate(zip(reservoir_wells, filter_columns)):
        protocol.comment(
            f'Transfer {i + 1}/{num_transfers}: from Reservoir 3 well '
            f'{src_well.display_name} to Filter Plate column index {i}'
        )
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        # Transfer [[EQUILIBRATION_VOLUME]] uL from reservoir well i to every well in column i
        # with a 7 mm offset from the bottom in the filter plate.
        p300_multi.transfer(
            equilibration_volume,
            src_well,
            [w.bottom(7.0) for w in dest_column],
            new_tip='never'
        )

        # Always return tips (drop to trash) after each column transfer
        p300_multi.drop_tip()

    # --- Step 3: Shaking incubation on Heater-Shaker ---
    protocol.comment('Step 3: Shaking for equilibration')

    # Ensure latch is closed for shaking
    hs_mod.close_labware_latch()

    # Set the Heater-Shaker to shake at [[SHAKER_SPEED_INCUBATION]] RPM
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)

    # Maintain shaking for [[EQUILIBRATION_CYCLE_DURATION]] minutes
    protocol.delay(minutes=equilibration_cycle_duration)

    # Stop shaking
    hs_mod.deactivate_shaker()

    # Open the latch at the end of the cycle
    hs_mod.open_labware_latch()

    protocol.comment('Equilibration cycle completed.')
