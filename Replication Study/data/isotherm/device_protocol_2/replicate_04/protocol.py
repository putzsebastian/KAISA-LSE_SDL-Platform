from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration Template',
    'author': 'User',
    'description': 'Templated protocol for buffer equilibration on Cytiva 96 filter plate using heater-shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (these will be replaced by the wizard)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if a value is still a [[PLACEHOLDER]] token."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value: str, default: float, cast=float):
    """Parse scalar placeholder to number, using a simulation fallback if unreplaced.

    Cast goes via float so that string integers like "3" or "3.0" both work.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value: str, default, cast=float):
    """Parse list placeholder (semicolon-separated) to list of numbers.

    Uses a simulation fallback list if the token is unreplaced.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --- Parameter parsing from placeholders ---
    # SALT_CONCENTRATIONS is a list placeholder, e.g. "0;100;200;500"
    # For simulation we use 12 entries so that all 12 reservoir wells/plate columns are exercised.
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100],
    )

    # REPLICATES is scalar. We must ensure num_transfers <= 12 (12 reservoir wells and 12 columns).
    # With the default of 12 concentrations, a default of 1 replicate keeps num_transfers == 12.
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=1, cast=float))

    equil_vol = parse_scalar(PLACEHOLDER_EQUILIBRATION_VOLUME, default=200.0, cast=float)
    shaker_speed = int(parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0, cast=float))
    equil_time_min = parse_scalar(PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION, default=60.0, cast=float)

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    # Physical limits: 12 reservoir wells and 12 plate columns
    if num_transfers > 12:
        raise RuntimeError(
            f"num_transfers ({num_transfers}) exceeds available wells/columns (12). "
            f"Please reduce the number of salt concentrations and/or replicates."
        )

    protocol.comment(f"Number of salt concentrations: {num_concs}")
    protocol.comment(f"Replicates per concentration: {replicates}")
    protocol.comment(f"Total number of transfers / columns used: {num_transfers}")

    # --- Modules ---
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # --- Labware ---
    # Slot 1: Cytiva 96 filter plate directly on Heater Shaker (custom labware, with fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; using a standard 96-well plate '
            'as a SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tipracks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', '4')
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', '7')
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', '10')

    # Reservoirs (only Reservoir 3 is used in this step, the rest are loaded to match deck layout)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', '5')  # not used in this step
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', '9')  # not used in this step
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', '8')  # not used in this step
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', '6')
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', '3')  # not used in this step

    # Mixing plate (not used in this step, but loaded to match deck layout)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', '11')

    # --- Pipettes ---
    p300_single = protocol.load_instrument(
        'p300_single_gen2', mount='right', tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2', mount='left', tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # --- Step 1 & 2: Transfers from Reservoir 3 to filter plate columns ---
    # Close latch before any shaking or pipetting on the Heater-Shaker
    hs_mod.close_labware_latch()

    # Reservoir 3 well layout follows the description: each salt concentration has
    # `replicates` consecutive wells, in ascending concentration order.
    # We simply use the first `num_transfers` wells of the reservoir and first
    # `num_transfers` columns of the filter plate, in order.
    source_wells = reservoir_3.wells()[:num_transfers]
    dest_columns = filter_plate.columns()[:num_transfers]

    # For each i in num_transfers: transfer EQUILIBRATION_VOLUME from Well[i] of Reservoir 3
    # to each well in Column[i] of filter plate using the multi-channel pipette, with a
    # 7 mm offset from the bottom. Use a fresh tip for every transfer.
    for i in range(num_transfers):
        src = source_wells[i]
        # Multi-channel: address the column via its A-row well (index 0 of column list)
        dest_col_top = dest_columns[i][0]

        p300_multi.pick_up_tip()
        p300_multi.transfer(
            equil_vol,
            src,
            dest_col_top.bottom(7),
            new_tip='never'
        )
        p300_multi.drop_tip()

    # --- Step 3: Shaking for equilibration ---
    # Ensure latch is closed, shake at SHAKER_SPEED_INCUBATION for EQUILIBRATION_CYCLE_DURATION minutes,
    # then stop shaking and open latch.
    hs_mod.close_labware_latch()
    hs_mod.set_and_wait_for_shake_speed(rpm=shaker_speed)
    protocol.delay(minutes=equil_time_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()
