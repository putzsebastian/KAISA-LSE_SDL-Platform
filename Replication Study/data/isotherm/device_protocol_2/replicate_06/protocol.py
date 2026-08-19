from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration Template',
    'author': 'User',
    'description': 'Templated equilibration on heater-shaker with filter plate'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (templated values)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'  # semicolon-separated
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'  # uL
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'  # rpm
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'  # minutes


def _unreplaced(s: str) -> bool:
    """Return True if the string still looks like a [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder, with a worst-case simulation default."""
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder, with a worst-case default."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # -------------------------------------------------
    # Parse placeholders with simulation fallbacks
    # -------------------------------------------------
    # Worst-case style fallbacks to stress-test tips/volumes on simulation
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700]
    )
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=12, cast=int))
    equilibration_volume = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_VOLUME, default=300.0, cast=float
    )
    shaker_speed = int(
        parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=3000, cast=int)
    )
    equilibration_duration_min = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION,
        default=60.0,
        cast=float
    )

    # Step 1: number of transfers
    num_transfers = len(salt_concs) * replicates
    # Filter plate has 12 columns max
    if num_transfers > 12:
        protocol.comment(
            f"Requested {num_transfers} columns but plate has 12; truncating to 12."
        )
        num_transfers = 12

    # -------------------------------------------------
    # Modules
    # -------------------------------------------------
    # Slot 1: Heater Shaker Module Gen1 with filter plate directly on it
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Filter plate (custom labware) on HS, with simulation fallback
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
            'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # -------------------------------------------------
    # Labware (deck layout)
    # -------------------------------------------------
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0
    protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3
    protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4

    # Mixing plate in slot 11
    protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------------------------------
    # Pipettes
    # -------------------------------------------------
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # -------------------------------------------------
    # Step 2: Equilibration transfers
    # -------------------------------------------------
    # Reservoir 3 wells contain salt buffers arranged as described in the prompt.
    # We use well i of reservoir_3 for column i of the filter plate.
    hs_mod.close_labware_latch()

    bottom_offset_mm = 7.0  # offset from filter plate bottom
    source_wells = reservoir_3.wells()  # 0–11

    for i in range(num_transfers):
        src = source_wells[i]
        dest_col = filter_plate.columns()[i]

        # One column is one multichannel target, so address dest_col[0]
        p300_multi.pick_up_tip()
        p300_multi.transfer(
            equilibration_volume,
            src.bottom(1.0),
            dest_col[0].bottom(bottom_offset_mm),
            new_tip='never'
        )
        # Always return tips to rack in slot 4/7/10
        p300_multi.return_tip()

    # -------------------------------------------------
    # Step 3: Shaking incubation
    # -------------------------------------------------
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=equilibration_duration_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
