from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration Templated Protocol',
    'author': 'User',
    'description': 'Templated protocol for buffer equilibration on filter plate using heater-shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder value.

    Uses `default` during simulation when the placeholder is not yet substituted.
    After substitution, casts to the requested type via `cast`.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    """Parse an integer placeholder, with simulation default."""
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    Returns `default` during simulation if the placeholder is unreplaced.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with conservative, worst-case-style defaults for simulation
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 100, 200, 500], float)
    equilibration_volume = parse_scalar(PLACEHOLDER_EQUILRATION_VOLUME if False else PLACEHOLDER_EQUILIBRATION_VOLUME, 200.0, float)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 2000.0, float)
    equilibration_duration_min = parse_scalar(PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION, 60.0, float)

    num_transfers = len(salt_concs) * replicates

    # -----------------------------
    # Modules
    # -----------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # -----------------------------
    # Labware
    # -----------------------------
    # Filter plate on heater shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware not found; using NEST 96 flat plate as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', '4')
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', '7')
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', '10')

    # Reservoirs
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', '3')  # user calls this Reservoir 4 in slot 3
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', '6')  # Reservoir 3 in slot 6 (salt buffers)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', '8')  # Reservoir 2 in slot 8
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', '9')  # Reservoir 1 in slot 9
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', '5')  # Reservoir 0 in slot 5

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', '11')

    # -----------------------------
    # Pipettes
    # -----------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # -----------------------------
    # Step 2: Transfers from Reservoir 3 to filter plate columns
    # -----------------------------
    # Close HS latch before any pipetting onto labware on the module
    hs_mod.close_labware_latch()

    # Ensure we do not address more than 12 columns
    if num_transfers > 12:
        raise RuntimeError(
            f'Number of transfers (concentrations x replicates) = {num_transfers} exceeds 12 columns of the filter plate.'
        )

    # Source wells in Reservoir 3 (slot 6) are arranged sequentially (0-11)
    source_wells = reservoir_3.wells()[:num_transfers]

    # Destination columns on filter plate; multi-channel uses row A in each column
    dest_columns = filter_plate.columns()[:num_transfers]

    # Use offset of 7 mm from bottom for destination
    dest_positions = [col[0].bottom(7.0) for col in dest_columns]

    # Perform transfers using multi-channel pipette, always returning tips
    p300_multi.transfer(
        equilibration_volume,
        [w.bottom(1.0) for w in source_wells],
        dest_positions,
        new_tip='always',
        blow_out=True,
        blowout_location='destination well'
    )

    # -----------------------------
    # Step 3: Shaking incubation
    # -----------------------------
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=equilibration_duration_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
