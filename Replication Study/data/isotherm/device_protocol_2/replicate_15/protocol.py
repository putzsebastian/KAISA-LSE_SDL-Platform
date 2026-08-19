from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration Templated Protocol',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt buffer equilibration on a filter plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    s2 = str(s).strip()
    return s2.startswith('[' * 2) and s2.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # -----------------------------
    # Parameters from placeholders (simulation fallbacks use upper-bound style values)
    # -----------------------------
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, default=8, cast=float))
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS,
                            default=[0, 100, 200, 300, 400, 500, 600, 700],
                            cast=float)
    equil_vol = parse_scalar(PLACEHOLDER_EQUILIBRATION_VOLUME, default=300.0, cast=float)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0, cast=float)
    equil_duration_min = parse_scalar(PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION, default=60.0, cast=float)

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    # -----------------------------
    # Modules
    # -----------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # -----------------------------
    # Labware
    # -----------------------------
    # Filter plate directly on Heater-Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Slot mapping per user description
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 in slot 3
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 in slot 6
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 in slot 8
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 in slot 9
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 in slot 5

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -----------------------------
    # Pipettes
    # -----------------------------
    # Right mount: P300 Single GEN2 (not used in these steps but loaded per requirements)
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right',
                                           tip_racks=[tiprack_4, tiprack_7, tiprack_10])

    # Left mount: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left',
                                          tip_racks=[tiprack_4, tiprack_7, tiprack_10])

    # -----------------------------
    # Step 2: Transfers from Reservoir 3 to Filter Plate columns
    # -----------------------------
    # Close latch before pipetting on Heater-Shaker
    hs_mod.close_labware_latch()

    # Ensure we do not exceed 12 columns on the filter plate
    if num_transfers > 12:
        protocol.comment(f'WARNING: num_transfers ({num_transfers}) exceeds 12 columns; '
                         'only first 12 will be processed in this run.')
        num_transfers = 12

    # Use only tips from slot 4 as requested
    p300_multi.default_tipracks = [tiprack_4]

    for i in range(num_transfers):
        # Reservoir 3: each well i holds the buffer for transfer i
        src_well = reservoir_3.wells()[i]
        # Filter plate: use column i (index-based) for transfer i
        dest_column = filter_plate.columns()[i]

        # One pick-up per column, return tip after use
        p300_multi.pick_up_tip()
        # Transfer [[EQUILIBRATION_VOLUME]] uL from reservoir well i to each well
        # in column i of the filter plate, using a 7 mm bottom offset in the filter plate.
        for dest in dest_column:
            p300_multi.transfer(equil_vol,
                                src_well.bottom(1.0),
                                dest.bottom(7.0),
                                new_tip='never')
        p300_multi.return_tip()

    # -----------------------------
    # Step 3: Shaking incubation
    # -----------------------------
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=equil_duration_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()

    protocol.comment('Templated salt equilibration protocol complete.')
