from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare salt buffers in reservoirs and ligand dilutions in a deep-well mixing plate using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(value):
    s = str(value).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return [cast(x) for x in default]
    return [cast(x.strip()) for x in s.split(';') if x.strip()]


def run(protocol):
    # -------------------------
    # Modules
    # -------------------------

    # -------------------------
    # Labware
    # -------------------------
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1, label='Custom Cytiva 96 Filter Well Plate')
    except Exception as exc:
        msg = str(exc).lower()
        if 'not found' not in msg and 'filenotfounderror' not in msg:
            raise
        protocol.comment('WARNING: custom labware definition not available; using NEST 96 deep-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 1, label='SIMULATION fallback for Cytiva 96 Filter Well Plate')

    tiprack_spare = protocol.load_labware('opentrons_96_tiprack_300ul', 4, label='Opentrons 300 uL Tips - Spare')
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7, label='Opentrons 300 uL Tips - Multi-Channel')
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10, label='Opentrons 300 uL Tips - Single-Channel')

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3, label='Reservoir 4 - 2x Salt Buffers')
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6, label='Reservoir 3 - Salt Buffers')
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8, label='Reservoir 2 - Salt Stocks')
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9, label='Reservoir 1 - Ligand and Buffers')
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5, label='Reservoir 0 - Low Salt Buffer')
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11, label='Mixing Plate')

    # -------------------------
    # Pipettes
    # -------------------------
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # -------------------------
    # Parameters and validation
    # Simulation fallbacks are chosen to exercise the high-use layout described in the prompt.
    # -------------------------
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 3, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    salt_concs = sorted(parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 100.0, 200.0, 300.0], float))
    ligand_concs = sorted(parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [0.0, 1.0, 10.0, 50.0, 100.0, 200.0, 300.0, 400.0], float))
    number_of_salts = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concs), int)
    number_of_ligands = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concs), int)

    if number_of_salts != len(salt_concs):
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS does not match the number of values in SALT_CONCENTRATIONS.')
    if number_of_ligands != len(ligand_concs):
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match the number of values in LIGAND_CONCENTRATIONS.')
    if replicates < 1:
        raise RuntimeError('REPLICATES must be at least 1.')
    if number_of_salts < 1 or number_of_salts > 12:
        raise RuntimeError('The number of salt concentrations must be between 1 and 12.')
    if number_of_ligands < 1 or number_of_ligands > 8:
        raise RuntimeError('The number of ligand concentrations must be between 1 and 8 because the mixing plate uses rows A-H.')
    if replicates * number_of_salts > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if salt_stock_conc <= 0:
        raise RuntimeError('SALT_STOCK_CONCENTRATION must be greater than 0.')
    if ligand_stock_conc <= 0:
        raise RuntimeError('LIGAND_STOCK_CONCENTRATION must be greater than 0.')
    if total_volume <= 0:
        raise RuntimeError('TOTAL_VOLUME must be greater than 0.')

    salt_buffer_total_actual_ul = 10000.0
    ligand_final_volume_ul = total_volume / 2.0 * replicates * 1.5
    if ligand_final_volume_ul > 2000.0:
        raise RuntimeError('Calculated ligand dilution volume exceeds the 2 mL mixing plate well capacity.')

    # -------------------------
    # Initial liquid labels and internal volume pools
    # -------------------------
    low_salt_liquid = protocol.define_liquid(name='Low salt buffer', description='0 salt buffer', display_color='#66CCFF')
    high_salt_liquid = protocol.define_liquid(name='High salt buffer', description='Salt stock buffer', display_color='#FFCC66')
    ligand_high_liquid = protocol.define_liquid(name='Ligand stock high', description='High concentration ligand stock', display_color='#CC66FF')
    ligand_low_liquid = protocol.define_liquid(name='Ligand stock low', description='Low concentration ligand stock', display_color='#9966CC')

    initial_reservoir_volume = 14000.0
    dead_volume = 50.0
    ligand_dead_volume = 20.0

    for idx in range(6):
        reservoir_2.wells()[idx].load_liquid(low_salt_liquid, initial_reservoir_volume)
    for idx in range(6, 12):
        reservoir_2.wells()[idx].load_liquid(high_salt_liquid, initial_reservoir_volume)
    reservoir_1.wells()[0].load_liquid(ligand_high_liquid, initial_reservoir_volume)
    reservoir_1.wells()[1].load_liquid(ligand_low_liquid, initial_reservoir_volume)
    for idx in range(2, 7):
        reservoir_1.wells()[idx].load_liquid(low_salt_liquid, initial_reservoir_volume)
    for idx in range(7, 12):
        reservoir_1.wells()[idx].load_liquid(high_salt_liquid, initial_reservoir_volume)
    for idx in range(12):
        reservoir_0.wells()[idx].load_liquid(low_salt_liquid, initial_reservoir_volume)

    def make_entry(well, volume):
        return {'well': well, 'remaining': float(volume)}

    low_salt_pool = []
    for idx in range(6):
        low_salt_pool.append(make_entry(reservoir_2.wells()[idx], initial_reservoir_volume))
    for idx in range(2, 7):
        low_salt_pool.append(make_entry(reservoir_1.wells()[idx], initial_reservoir_volume))
    for idx in range(12):
        low_salt_pool.append(make_entry(reservoir_0.wells()[idx], initial_reservoir_volume))

    high_salt_pool = []
    for idx in range(6, 12):
        high_salt_pool.append(make_entry(reservoir_2.wells()[idx], initial_reservoir_volume))
    for idx in range(7, 12):
        high_salt_pool.append(make_entry(reservoir_1.wells()[idx], initial_reservoir_volume))

    ligand_high_pool = [make_entry(reservoir_1.wells()[0], initial_reservoir_volume)]
    ligand_low_pool = [make_entry(reservoir_1.wells()[1], initial_reservoir_volume)]

    def available_from_entry(entry, reserve):
        return max(0.0, entry['remaining'] - reserve)

    def move_multi_actual(pipette, pool, dest, actual_volume_ul, reserve_ul, pool_name):
        remaining = float(actual_volume_ul)
        epsilon = 0.0001
        while remaining > epsilon:
            moved = False
            for entry in pool:
                available_actual = available_from_entry(entry, reserve_ul)
                if available_actual <= epsilon:
                    continue
                chunk_actual = min(remaining, pipette.max_volume * 8.0, available_actual)
                per_channel_volume = chunk_actual / 8.0
                if per_channel_volume <= epsilon:
                    continue
                pipette.aspirate(per_channel_volume, entry['well'])
                pipette.dispense(per_channel_volume, dest)
                entry['remaining'] -= chunk_actual
                remaining -= chunk_actual
                moved = True
                break
            if not moved:
                raise RuntimeError('Insufficient volume in ' + pool_name + ' pool; shortfall is ' + str(round(remaining, 2)) + ' uL.')

    def move_single_actual(pipette, pool, dest, actual_volume_ul, reserve_ul, pool_name):
        remaining = float(actual_volume_ul)
        epsilon = 0.0001
        while remaining > epsilon:
            moved = False
            for entry in pool:
                available_actual = available_from_entry(entry, reserve_ul)
                if available_actual <= epsilon:
                    continue
                chunk = min(remaining, pipette.max_volume, available_actual)
                if chunk <= epsilon:
                    continue
                pipette.aspirate(chunk, entry['well'])
                pipette.dispense(chunk, dest)
                entry['remaining'] -= chunk
                remaining -= chunk
                moved = True
                break
            if not moved:
                raise RuntimeError('Insufficient volume in ' + pool_name + ' pool; shortfall is ' + str(round(remaining, 2)) + ' uL.')

    # -------------------------
    # Step 2: Prepare salt buffers in Reservoir 3, ascending by well number.
    # -------------------------
    reservoir_3_targets = []
    reservoir_3_low_requests = []
    reservoir_3_high_requests = []
    for salt_conc in salt_concs:
        if salt_conc < 0 or salt_conc > salt_stock_conc:
            raise RuntimeError('A requested Reservoir 3 salt concentration is outside the range 0 to SALT_STOCK_CONCENTRATION.')
        high_actual = salt_buffer_total_actual_ul * salt_conc / salt_stock_conc
        low_actual = salt_buffer_total_actual_ul - high_actual
        for _rep in range(replicates):
            target = reservoir_3.wells()[len(reservoir_3_targets)]
            reservoir_3_targets.append(target)
            reservoir_3_low_requests.append((target, low_actual))
            reservoir_3_high_requests.append((target, high_actual))

    p300_multi.pick_up_tip()
    for target, low_actual in reservoir_3_low_requests:
        if low_actual > 0:
            move_multi_actual(p300_multi, low_salt_pool, target, low_actual, dead_volume, 'low salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target, high_actual in reservoir_3_high_requests:
        if high_actual > 0:
            move_multi_actual(p300_multi, high_salt_pool, target, high_actual, dead_volume, 'high salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target in reservoir_3_targets:
        p300_multi.mix(1, 250, target)
    p300_multi.drop_tip()

    # -------------------------
    # Step 3: Prepare 2x salt buffers in Reservoir 4, ascending by well number.
    # -------------------------
    reservoir_4_targets = []
    reservoir_4_low_requests = []
    reservoir_4_high_requests = []
    for salt_conc in salt_concs:
        target_conc = 2.0 * salt_conc
        if target_conc < 0 or target_conc > salt_stock_conc:
            raise RuntimeError('A requested 2x salt concentration for Reservoir 4 exceeds SALT_STOCK_CONCENTRATION.')
        high_actual = salt_buffer_total_actual_ul * target_conc / salt_stock_conc
        low_actual = salt_buffer_total_actual_ul - high_actual
        target = reservoir_4.wells()[len(reservoir_4_targets)]
        reservoir_4_targets.append(target)
        reservoir_4_low_requests.append((target, low_actual))
        reservoir_4_high_requests.append((target, high_actual))

    p300_multi.pick_up_tip()
    for target, low_actual in reservoir_4_low_requests:
        if low_actual > 0:
            move_multi_actual(p300_multi, low_salt_pool, target, low_actual, dead_volume, 'low salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target, high_actual in reservoir_4_high_requests:
        if high_actual > 0:
            move_multi_actual(p300_multi, high_salt_pool, target, high_actual, dead_volume, 'high salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target in reservoir_4_targets:
        p300_multi.mix(1, 250, target)
    p300_multi.drop_tip()

    # -------------------------
    # Step 4: Prepare 2x ligand dilution series in the mixing plate.
    # Rows A-H are ligand concentrations, columns are salt concentration conditions.
    # -------------------------
    mixing_destinations_by_ligand = []
    all_mixing_destinations = []
    for row_index, ligand_conc in enumerate(ligand_concs):
        row_destinations = []
        for col_index in range(number_of_salts):
            dest = mixing_plate.rows()[row_index][col_index]
            row_destinations.append(dest)
            all_mixing_destinations.append(dest)
        mixing_destinations_by_ligand.append((ligand_conc, row_destinations))

    # Add ligand stock to empty mixing wells, one tip per ligand concentration row.
    for ligand_conc, row_destinations in mixing_destinations_by_ligand:
        target_ligand_conc = 2.0 * ligand_conc
        if target_ligand_conc < 0 or target_ligand_conc > ligand_stock_conc:
            raise RuntimeError('A requested 2x ligand concentration exceeds LIGAND_STOCK_CONCENTRATION.')
        high_stock_volume = 0.0
        if ligand_stock_conc > 0:
            high_stock_volume = ligand_final_volume_ul * target_ligand_conc / ligand_stock_conc
        if high_stock_volume == 0:
            continue
        if high_stock_volume < 20.0:
            selected_stock_pool = ligand_low_pool
            selected_stock_conc = ligand_stock_conc / 10.0
            selected_pool_name = 'low concentration ligand stock'
            ligand_volume = ligand_final_volume_ul * target_ligand_conc / selected_stock_conc
        else:
            selected_stock_pool = ligand_high_pool
            selected_pool_name = 'high concentration ligand stock'
            ligand_volume = high_stock_volume
        if ligand_volume > ligand_final_volume_ul:
            raise RuntimeError('Calculated ligand stock volume exceeds the final dilution volume.')

        p300_single.pick_up_tip()
        for dest in row_destinations:
            move_single_actual(p300_single, selected_stock_pool, dest, ligand_volume, ligand_dead_volume, selected_pool_name)
        p300_single.drop_tip()

    # Add low salt buffer to bring every mixing well to the calculated final volume.
    p300_single.pick_up_tip()
    for ligand_conc, row_destinations in mixing_destinations_by_ligand:
        target_ligand_conc = 2.0 * ligand_conc
        high_stock_volume = ligand_final_volume_ul * target_ligand_conc / ligand_stock_conc
        if high_stock_volume == 0:
            ligand_volume = 0.0
        elif high_stock_volume < 20.0:
            ligand_volume = ligand_final_volume_ul * target_ligand_conc / (ligand_stock_conc / 10.0)
        else:
            ligand_volume = high_stock_volume
        buffer_volume = ligand_final_volume_ul - ligand_volume
        if buffer_volume < -0.0001:
            raise RuntimeError('Calculated low salt buffer volume for a ligand dilution is negative.')
        for dest in row_destinations:
            if buffer_volume > 0:
                move_single_actual(p300_single, low_salt_pool, dest, buffer_volume, dead_volume, 'low salt buffer')
    p300_single.drop_tip()

    # Mix each completed ligand dilution once.
    p300_single.pick_up_tip()
    mix_volume = min(200.0, max(20.0, ligand_final_volume_ul / 2.0))
    for dest in all_mixing_destinations:
        p300_single.mix(1, mix_volume, dest)
    p300_single.drop_tip()

    protocol.comment('Protocol complete: salt buffers and ligand dilution mixing plate have been prepared from templated inputs.')
