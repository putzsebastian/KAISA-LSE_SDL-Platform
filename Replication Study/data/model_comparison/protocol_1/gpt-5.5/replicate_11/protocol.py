from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare salt buffers in reservoirs and ligand dilutions in a deep-well mixing plate using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Placeholders are declared as literal strings so the templating layer can replace them.
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
        return [cast(float(x.strip())) for x in s.split(';') if x.strip()]

    # Worst-case simulation fallbacks: 12 salt columns, 8 ligand rows, and large mixing volume
    # while keeping the 2 mL mixing wells within capacity for the fallback replicate count.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 1000.0, float)
    salt_concentrations = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0, 45, 90, 135, 180, 225, 270, 315, 360, 405, 450, 500],
        float
    )
    ligand_concentrations = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0, 10, 20, 30, 40, 50, 75, 100],
        float
    )
    salt_stock_concentration = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_concentration = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    number_of_salt_concentrations = parse_scalar(
        PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS,
        len(salt_concentrations),
        int
    )
    number_of_ligand_concentrations = parse_scalar(
        PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS,
        len(ligand_concentrations),
        int
    )

    salt_concentrations = sorted(salt_concentrations)
    ligand_concentrations = sorted(ligand_concentrations)

    if number_of_salt_concentrations != len(salt_concentrations):
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS must match the number of values in SALT_CONCENTRATIONS.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS must match the number of values in LIGAND_CONCENTRATIONS.')
    if replicates < 1:
        raise RuntimeError('REPLICATES must be at least 1.')
    if number_of_salt_concentrations < 1 or number_of_salt_concentrations > 12:
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS must be between 1 and 12.')
    if number_of_ligand_concentrations < 1 or number_of_ligand_concentrations > 8:
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS must be between 1 and 8 for row-wise A-H layout.')
    if replicates * number_of_salt_concentrations > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if salt_stock_concentration <= 0:
        raise RuntimeError('SALT_STOCK_CONCENTRATION must be greater than 0.')
    if ligand_stock_concentration <= 0:
        raise RuntimeError('LIGAND_STOCK_CONCENTRATION must be greater than 0.')
    if total_volume <= 0:
        raise RuntimeError('TOTAL_VOLUME must be greater than 0.')

    # Labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc).lower():
            raise
        protocol.comment('WARNING: custom labware definition not available; using a 96-well standard plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    multi_tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    single_tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # Empty; receives 2x salt buffers
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # Empty; receives 1x salt buffers
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # Low/high salt buffer stocks
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # Ligand stocks plus salt buffers
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # Low salt buffer stocks
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[single_tiprack])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[multi_tiprack])

    # Liquid definitions and initial liquid loading.
    low_salt_liquid = protocol.define_liquid(
        name='Low salt buffer',
        description='Low salt buffer with 0 salt',
        display_color='#66CCFF'
    )
    high_salt_liquid = protocol.define_liquid(
        name='High salt buffer',
        description='High salt buffer stock',
        display_color='#004C99'
    )
    ligand_high_liquid = protocol.define_liquid(
        name='Ligand high stock',
        description='Ligand stock solution at high concentration',
        display_color='#FF9900'
    )
    ligand_low_liquid = protocol.define_liquid(
        name='Ligand low stock',
        description='Ligand stock solution at one tenth concentration',
        display_color='#FFCC66'
    )

    INITIAL_RESERVOIR_VOLUME = 14000.0
    UNUSABLE_REMAINDER = 50.0
    TARGET_SALT_BUFFER_TOTAL = 10000.0
    MULTI_CHANNEL_FACTOR = 8.0
    EPSILON = 0.0001

    # Load liquids in Reservoir 2.
    for well in reservoir_2.wells()[:6]:
        well.load_liquid(low_salt_liquid, INITIAL_RESERVOIR_VOLUME)
    for well in reservoir_2.wells()[6:12]:
        well.load_liquid(high_salt_liquid, INITIAL_RESERVOIR_VOLUME)

    # Load liquids in Reservoir 1.
    reservoir_1.wells()[0].load_liquid(ligand_high_liquid, INITIAL_RESERVOIR_VOLUME)
    reservoir_1.wells()[1].load_liquid(ligand_low_liquid, INITIAL_RESERVOIR_VOLUME)
    for well in reservoir_1.wells()[2:7]:
        well.load_liquid(low_salt_liquid, INITIAL_RESERVOIR_VOLUME)
    for well in reservoir_1.wells()[7:12]:
        well.load_liquid(high_salt_liquid, INITIAL_RESERVOIR_VOLUME)

    # Load liquids in Reservoir 0.
    for well in reservoir_0.wells():
        well.load_liquid(low_salt_liquid, INITIAL_RESERVOIR_VOLUME)

    def make_pool(wells):
        return [{'well': well, 'remaining': INITIAL_RESERVOIR_VOLUME - UNUSABLE_REMAINDER} for well in wells]

    low_salt_pool = make_pool(
        reservoir_0.wells()
        + reservoir_2.wells()[:6]
        + reservoir_1.wells()[2:7]
    )
    high_salt_pool = make_pool(
        reservoir_2.wells()[6:12]
        + reservoir_1.wells()[7:12]
    )
    ligand_high_pool = make_pool([reservoir_1.wells()[0]])
    ligand_low_pool = make_pool([reservoir_1.wells()[1]])

    def pool_remaining(pool):
        return sum(item['remaining'] for item in pool)

    def next_available_source(pool):
        for item in pool:
            if item['remaining'] > EPSILON:
                return item
        return None

    def execute_multi_pool_requests(pool, requests, label):
        if not requests:
            return
        p300_multi.pick_up_tip()
        for dest, total_well_volume in requests:
            remaining_per_channel = total_well_volume / MULTI_CHANNEL_FACTOR
            while remaining_per_channel > EPSILON:
                source = next_available_source(pool)
                if source is None:
                    shortfall = remaining_per_channel * MULTI_CHANNEL_FACTOR
                    p300_multi.drop_tip()
                    raise RuntimeError('Insufficient ' + label + '; short by at least ' + str(round(shortfall, 2)) + ' uL.')
                source_available_per_channel = source['remaining'] / MULTI_CHANNEL_FACTOR
                if source_available_per_channel <= EPSILON:
                    source['remaining'] = 0
                    continue
                chunk = min(p300_multi.max_volume, remaining_per_channel, source_available_per_channel)
                p300_multi.aspirate(chunk, source['well'])
                p300_multi.dispense(chunk, dest)
                source['remaining'] -= chunk * MULTI_CHANNEL_FACTOR
                remaining_per_channel -= chunk
        p300_multi.drop_tip()

    def execute_single_pool_requests(pool, requests, label):
        if not requests:
            return
        p300_single.pick_up_tip()
        for dest, volume in requests:
            remaining = volume
            while remaining > EPSILON:
                source = next_available_source(pool)
                if source is None:
                    p300_single.drop_tip()
                    raise RuntimeError('Insufficient ' + label + '; short by at least ' + str(round(remaining, 2)) + ' uL.')
                if source['remaining'] <= EPSILON:
                    source['remaining'] = 0
                    continue
                chunk = min(p300_single.max_volume, remaining, source['remaining'])
                p300_single.aspirate(chunk, source['well'])
                p300_single.dispense(chunk, dest)
                source['remaining'] -= chunk
                remaining -= chunk
        p300_single.drop_tip()

    # Steps 2 and 3 calculations: prepare salt buffers in Reservoirs 3 and 4.
    low_salt_requests = []
    high_salt_requests = []
    salt_buffer_targets_to_mix = []

    res3_dest_index = 0
    for salt_concentration in salt_concentrations:
        high_volume = TARGET_SALT_BUFFER_TOTAL * salt_concentration / salt_stock_concentration
        low_volume = TARGET_SALT_BUFFER_TOTAL - high_volume
        if high_volume < -EPSILON or low_volume < -EPSILON:
            raise RuntimeError('Cannot prepare salt concentration ' + str(salt_concentration) + ' because it exceeds the high salt stock concentration.')
        for _ in range(replicates):
            dest = reservoir_3.wells()[res3_dest_index]
            res3_dest_index += 1
            if low_volume > EPSILON:
                low_salt_requests.append((dest, low_volume))
            if high_volume > EPSILON:
                high_salt_requests.append((dest, high_volume))
            salt_buffer_targets_to_mix.append(dest)

    for index, salt_concentration in enumerate(salt_concentrations):
        high_volume = TARGET_SALT_BUFFER_TOTAL * (2.0 * salt_concentration) / salt_stock_concentration
        low_volume = TARGET_SALT_BUFFER_TOTAL - high_volume
        if high_volume < -EPSILON or low_volume < -EPSILON:
            raise RuntimeError('Cannot prepare 2x salt concentration ' + str(2.0 * salt_concentration) + ' because it exceeds the high salt stock concentration.')
        dest = reservoir_4.wells()[index]
        if low_volume > EPSILON:
            low_salt_requests.append((dest, low_volume))
        if high_volume > EPSILON:
            high_salt_requests.append((dest, high_volume))
        salt_buffer_targets_to_mix.append(dest)

    protocol.comment('Preparing Reservoir 3 and Reservoir 4 salt buffers with the multi-channel pipette.')
    execute_multi_pool_requests(low_salt_pool, low_salt_requests, 'low salt buffer')
    execute_multi_pool_requests(high_salt_pool, high_salt_requests, 'high salt buffer')

    p300_multi.pick_up_tip()
    for target in salt_buffer_targets_to_mix:
        p300_multi.mix(3, 250, target)
    p300_multi.drop_tip()

    # Step 4 calculations: prepare ligand dilutions in the deep-well mixing plate.
    mixing_well_total_volume = total_volume / 2.0 * replicates * 1.5
    if mixing_well_total_volume > 1900.0:
        raise RuntimeError('Calculated mixing-plate well volume is ' + str(round(mixing_well_total_volume, 2)) + ' uL; reduce TOTAL_VOLUME or REPLICATES to stay within the 2 mL deep-well plate capacity.')

    low_buffer_to_mixing_requests = []
    ligand_high_requests = []
    ligand_low_requests = []
    mixing_plate_targets_to_mix = []

    for ligand_index, ligand_concentration in enumerate(ligand_concentrations):
        target_ligand_concentration = 2.0 * ligand_concentration
        if target_ligand_concentration < -EPSILON:
            raise RuntimeError('Ligand concentrations must be non-negative.')

        if target_ligand_concentration <= EPSILON:
            stock_volume = 0.0
            use_low_ligand_stock = False
        else:
            high_stock_volume = mixing_well_total_volume * target_ligand_concentration / ligand_stock_concentration
            if high_stock_volume < 20.0:
                use_low_ligand_stock = True
                stock_volume = mixing_well_total_volume * target_ligand_concentration / (ligand_stock_concentration / 10.0)
            else:
                use_low_ligand_stock = False
                stock_volume = high_stock_volume

        buffer_volume = mixing_well_total_volume - stock_volume
        if stock_volume < -EPSILON or buffer_volume < -EPSILON:
            raise RuntimeError('Cannot prepare 2x ligand concentration ' + str(target_ligand_concentration) + ' from the selected ligand stock concentration.')

        for salt_index in range(number_of_salt_concentrations):
            dest = mixing_plate.rows()[ligand_index][salt_index]
            if buffer_volume > EPSILON:
                low_buffer_to_mixing_requests.append((dest, buffer_volume))
            if stock_volume > EPSILON:
                if use_low_ligand_stock:
                    ligand_low_requests.append((dest, stock_volume))
                else:
                    ligand_high_requests.append((dest, stock_volume))
            mixing_plate_targets_to_mix.append(dest)

    protocol.comment('Preparing ligand dilutions in the mixing plate with the single-channel pipette.')
    execute_single_pool_requests(low_salt_pool, low_buffer_to_mixing_requests, 'low salt buffer for ligand dilutions')
    execute_single_pool_requests(ligand_high_pool, ligand_high_requests, 'high-concentration ligand stock')
    execute_single_pool_requests(ligand_low_pool, ligand_low_requests, 'low-concentration ligand stock')

    p300_single.pick_up_tip()
    mix_volume = min(200.0, max(20.0, mixing_well_total_volume * 0.5))
    for target in mixing_plate_targets_to_mix:
        p300_single.mix(3, mix_volume, target)
    p300_single.drop_tip()

    protocol.comment('Remaining low salt buffer pool volume: ' + str(round(pool_remaining(low_salt_pool), 2)) + ' uL.')
    protocol.comment('Remaining high salt buffer pool volume: ' + str(round(pool_remaining(high_salt_pool), 2)) + ' uL.')
    protocol.comment('Remaining high ligand stock volume: ' + str(round(pool_remaining(ligand_high_pool), 2)) + ' uL.')
    protocol.comment('Remaining low ligand stock volume: ' + str(round(pool_remaining(ligand_low_pool), 2)) + ' uL.')
