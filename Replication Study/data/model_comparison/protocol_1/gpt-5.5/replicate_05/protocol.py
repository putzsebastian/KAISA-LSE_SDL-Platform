from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare templated salt buffers and ligand dilution series using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol):
    # -----------------------------
    # Placeholders
    # -----------------------------
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
            return default
        return cast(float(s))

    def parse_list(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return list(default)
        return [cast(x.strip()) for x in s.split(';') if x.strip()]

    # Simulation fallbacks exercise the maximum Reservoir 3 allocation:
    # 3 replicates x 4 salt concentrations = 12 wells.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 3, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 100.0, 200.0, 300.0], float)
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS,
                              [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0], float)
    number_of_salt_concs = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS,
                                         len(salt_concs), int)
    number_of_ligand_concs = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS,
                                           len(ligand_concs), int)

    salt_concs = sorted(salt_concs)
    ligand_concs = sorted(ligand_concs)

    if len(salt_concs) != number_of_salt_concs:
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS does not match SALT_CONCENTRATIONS length.')
    if len(ligand_concs) != number_of_ligand_concs:
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match LIGAND_CONCENTRATIONS length.')
    if replicates * number_of_salt_concs > 12:
        raise ValueError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if number_of_salt_concs > 12:
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12.')
    if number_of_ligand_concs > 8:
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS must not exceed 8 for rows A-H.')
    if salt_stock_conc <= 0 or ligand_stock_conc <= 0:
        raise ValueError('Stock concentrations must be greater than zero.')

    mixing_well_volume = total_volume / 2.0 * replicates * 1.5
    if mixing_well_volume > 2000:
        raise ValueError('Calculated mixing-plate well volume exceeds the 2 mL deep-well capacity.')

    # -----------------------------
    # Labware
    # -----------------------------
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10, label='Single-channel tips')
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7, label='Multi-channel tips')
    protocol.load_labware('opentrons_96_tiprack_300ul', 4, label='Additional 300 uL tiprack')

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3, label='Reservoir 4')
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6, label='Reservoir 3')
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8, label='Reservoir 2')
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9, label='Reservoir 1')
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5, label='Reservoir 0')
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11, label='Mixing Plate')

    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1, label='Custom filter plate')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a 96-well standard plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1, label='Custom filter plate simulation fallback')

    # -----------------------------
    # Pipettes
    # -----------------------------
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # -----------------------------
    # Liquid labels and initial volumes
    # -----------------------------
    low_salt_liquid = protocol.define_liquid(
        name='Low salt buffer',
        description='Low salt buffer with 0 salt',
        display_color='#66CCFF'
    )
    high_salt_liquid = protocol.define_liquid(
        name='High salt buffer',
        description='High salt buffer stock',
        display_color='#3366FF'
    )
    ligand_high_liquid = protocol.define_liquid(
        name='Ligand high stock',
        description='Ligand stock solution at high concentration',
        display_color='#FF9900'
    )
    ligand_low_liquid = protocol.define_liquid(
        name='Ligand low stock',
        description='Ligand stock solution at one tenth high-stock concentration',
        display_color='#FFCC66'
    )

    for index in range(6):
        reservoir_2.wells()[index].load_liquid(low_salt_liquid, 14000)
    for index in range(6, 12):
        reservoir_2.wells()[index].load_liquid(high_salt_liquid, 14000)

    reservoir_1.wells()[0].load_liquid(ligand_high_liquid, 14000)
    reservoir_1.wells()[1].load_liquid(ligand_low_liquid, 14000)
    for index in range(2, 7):
        reservoir_1.wells()[index].load_liquid(low_salt_liquid, 14000)
    for index in range(7, 12):
        reservoir_1.wells()[index].load_liquid(high_salt_liquid, 14000)

    for index in range(12):
        reservoir_0.wells()[index].load_liquid(low_salt_liquid, 14000)

    # -----------------------------
    # Manual liquid accounting
    # -----------------------------
    DEAD_VOLUME = 200.0
    MULTI_CHANNELS = 8.0
    EPSILON = 0.01

    def make_pool(wells, starting_volume=14000.0):
        return [{'well': well, 'remaining': float(starting_volume)} for well in wells]

    low_salt_pool = make_pool(
        reservoir_2.wells()[:6] + reservoir_1.wells()[2:7] + reservoir_0.wells()
    )
    high_salt_pool = make_pool(
        reservoir_2.wells()[6:12] + reservoir_1.wells()[7:12]
    )
    ligand_high_pool = make_pool([reservoir_1.wells()[0]])
    ligand_low_pool = make_pool([reservoir_1.wells()[1]])

    def next_available(pool):
        for entry in pool:
            available = entry['remaining'] - DEAD_VOLUME
            if available > EPSILON:
                return entry, available
        return None, 0.0

    def checked_fraction(numerator, denominator, label):
        fraction = numerator / denominator
        if fraction < -EPSILON or fraction > 1.0 + EPSILON:
            raise ValueError(label + ' requires a stock fraction outside 0-1.')
        if fraction < 0:
            fraction = 0.0
        if fraction > 1:
            fraction = 1.0
        return fraction

    def add_from_pool_multi(pool, destinations_and_physical_volumes, label):
        if not destinations_and_physical_volumes:
            return
        p300_multi.pick_up_tip()
        for destination, physical_volume in destinations_and_physical_volumes:
            remaining_request = float(physical_volume)
            while remaining_request > EPSILON:
                entry, available = next_available(pool)
                if entry is None:
                    raise RuntimeError('Insufficient ' + label + ' volume for multi-channel transfer; request cannot be completed.')
                physical_chunk = min(remaining_request, available, p300_multi.max_volume * MULTI_CHANNELS)
                per_channel_volume = physical_chunk / MULTI_CHANNELS
                p300_multi.aspirate(per_channel_volume, entry['well'])
                p300_multi.dispense(per_channel_volume, destination)
                entry['remaining'] -= physical_chunk
                remaining_request -= physical_chunk
        p300_multi.drop_tip()

    def mix_reservoir_wells_multi(wells):
        if not wells:
            return
        p300_multi.pick_up_tip()
        for well in wells:
            p300_multi.mix(3, 250, well)
        p300_multi.drop_tip()

    def add_from_pool_single(pool, destinations_and_volumes, label):
        if not destinations_and_volumes:
            return
        p300_single.pick_up_tip()
        for destination, volume in destinations_and_volumes:
            remaining_request = float(volume)
            while remaining_request > EPSILON:
                entry, available = next_available(pool)
                if entry is None:
                    raise RuntimeError('Insufficient ' + label + ' volume for single-channel transfer; request cannot be completed.')
                chunk = min(remaining_request, available, p300_single.max_volume)
                p300_single.aspirate(chunk, entry['well'])
                p300_single.dispense(chunk, destination)
                entry['remaining'] -= chunk
                remaining_request -= chunk
        p300_single.drop_tip()

    def mix_plate_row_single(wells):
        if not wells:
            return
        p300_single.pick_up_tip()
        for well in wells:
            p300_single.mix(3, min(200.0, mixing_well_volume * 0.5), well)
        p300_single.drop_tip()

    # -----------------------------
    # Step 2: prepare 10 mL salt buffers in Reservoir 3 with replicates
    # -----------------------------
    protocol.comment('Step 2: Preparing salt-buffer replicates in Reservoir 3.')
    res3_low_requests = []
    res3_high_requests = []
    res3_targets_to_mix = []

    for salt_index, salt_conc in enumerate(salt_concs):
        high_fraction = checked_fraction(salt_conc, salt_stock_conc, 'Reservoir 3 salt concentration')
        high_physical_volume = 10000.0 * high_fraction
        low_physical_volume = 10000.0 - high_physical_volume
        for replicate_index in range(replicates):
            well_index = salt_index * replicates + replicate_index
            target = reservoir_3.wells()[well_index]
            res3_targets_to_mix.append(target)
            if low_physical_volume > EPSILON:
                res3_low_requests.append((target, low_physical_volume))
            if high_physical_volume > EPSILON:
                res3_high_requests.append((target, high_physical_volume))

    add_from_pool_multi(low_salt_pool, res3_low_requests, 'low salt buffer')
    add_from_pool_multi(high_salt_pool, res3_high_requests, 'high salt buffer')
    mix_reservoir_wells_multi(res3_targets_to_mix)

    # -----------------------------
    # Step 3: prepare 10 mL 2x salt buffers in Reservoir 4
    # -----------------------------
    protocol.comment('Step 3: Preparing 2x salt buffers in Reservoir 4.')
    res4_low_requests = []
    res4_high_requests = []
    res4_targets_to_mix = []

    for salt_index, salt_conc in enumerate(salt_concs):
        high_fraction = checked_fraction(2.0 * salt_conc, salt_stock_conc, 'Reservoir 4 2x salt concentration')
        high_physical_volume = 10000.0 * high_fraction
        low_physical_volume = 10000.0 - high_physical_volume
        target = reservoir_4.wells()[salt_index]
        res4_targets_to_mix.append(target)
        if low_physical_volume > EPSILON:
            res4_low_requests.append((target, low_physical_volume))
        if high_physical_volume > EPSILON:
            res4_high_requests.append((target, high_physical_volume))

    add_from_pool_multi(low_salt_pool, res4_low_requests, 'low salt buffer')
    add_from_pool_multi(high_salt_pool, res4_high_requests, 'high salt buffer')
    mix_reservoir_wells_multi(res4_targets_to_mix)

    # -----------------------------
    # Step 4: prepare 2x ligand dilution series in the deep-well mixing plate
    # -----------------------------
    protocol.comment('Step 4: Preparing ligand dilution series in the mixing plate.')

    plate_rows = mixing_plate.rows()
    for ligand_index, ligand_conc in enumerate(ligand_concs):
        target_row_wells = plate_rows[ligand_index][:number_of_salt_concs]
        target_ligand_conc = 2.0 * ligand_conc
        high_stock_volume = mixing_well_volume * target_ligand_conc / ligand_stock_conc

        if high_stock_volume > 0 and high_stock_volume < 20.0:
            stock_pool = ligand_low_pool
            stock_label = 'low-concentration ligand stock'
            effective_stock_conc = ligand_stock_conc / 10.0
            stock_volume = mixing_well_volume * target_ligand_conc / effective_stock_conc
        else:
            stock_pool = ligand_high_pool
            stock_label = 'high-concentration ligand stock'
            stock_volume = high_stock_volume

        if stock_volume > mixing_well_volume + EPSILON:
            raise ValueError('Ligand concentration requires more stock than the final mixing-well volume.')

        buffer_volume = mixing_well_volume - stock_volume

        if stock_volume > EPSILON:
            stock_requests = [(well, stock_volume) for well in target_row_wells]
            add_from_pool_single(stock_pool, stock_requests, stock_label)
        if buffer_volume > EPSILON:
            buffer_requests = [(well, buffer_volume) for well in target_row_wells]
            add_from_pool_single(low_salt_pool, buffer_requests, 'low salt buffer')
        mix_plate_row_single(target_row_wells)

    protocol.comment('Protocol complete. Filter plate is loaded for downstream use but is not liquid-handled in these specified steps.')
