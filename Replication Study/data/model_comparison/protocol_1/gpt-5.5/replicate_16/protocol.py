from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Template protocol for preparing salt buffers and ligand dilutions using placeholders.'
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
        return default
    return cast(float(s))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x.strip()) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        exc_text = str(exc)
        if ('not found' not in exc_text and 'FileNotFoundError' not in exc_text
                and 'Unable to find' not in exc_text):
            raise
        protocol.comment('WARNING: custom labware definition cytiva_96_filterwellplate_1ml not available; '
                         'using NEST 96 deep-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 1)

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_slot10, tiprack_slot4])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_slot7])

    # parameters; defaults are for simulation before literal placeholder substitution.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1200.0, float)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    salt_concentrations = sorted(parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0.0, 50.0, 100.0, 150.0, 200.0, 250.0, 300.0, 350.0, 400.0, 450.0, 500.0, 550.0],
        float
    ))
    ligand_concentrations = sorted(parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0],
        float
    ))
    number_of_salt_concentrations = parse_scalar(
        PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concentrations), int)
    number_of_ligand_concentrations = parse_scalar(
        PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concentrations), int)

    if number_of_salt_concentrations != len(salt_concentrations):
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS does not match the number of values in SALT_CONCENTRATIONS.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match the number of values in LIGAND_CONCENTRATIONS.')
    if replicates < 1:
        raise RuntimeError('REPLICATES must be at least 1.')
    if number_of_salt_concentrations < 1 or number_of_salt_concentrations > 12:
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS must be between 1 and 12.')
    if number_of_ligand_concentrations < 1 or number_of_ligand_concentrations > 8:
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS must be between 1 and 8 for row-wise A-H layout.')
    if replicates * number_of_salt_concentrations > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if salt_stock_conc <= 0 or ligand_stock_conc <= 0:
        raise RuntimeError('Stock concentrations must be greater than zero.')
    if total_volume <= 0:
        raise RuntimeError('TOTAL_VOLUME must be greater than zero.')
    if max(salt_concentrations) * 2.0 > salt_stock_conc:
        raise RuntimeError('2x salt concentration exceeds SALT_STOCK_CONCENTRATION for at least one Reservoir 4 well.')

    reservoir_final_physical_ul = 10000.0
    reservoir_dead_ul = 100.0
    low_salt_liquid = protocol.define_liquid(
        name='Low salt buffer',
        description='0 salt buffer',
        display_color='#66CCFF'
    )
    high_salt_liquid = protocol.define_liquid(
        name='High salt buffer',
        description='Salt stock buffer',
        display_color='#3366FF'
    )
    ligand_high_liquid = protocol.define_liquid(
        name='Ligand high stock',
        description='Ligand stock solution at high concentration',
        display_color='#FF66CC'
    )
    ligand_low_liquid = protocol.define_liquid(
        name='Ligand low stock',
        description='Ligand stock solution at one tenth high concentration',
        display_color='#FFCC66'
    )

    low_pool = []
    high_pool = []
    ligand_high_pool = []
    ligand_low_pool = []

    def add_source(pool, well, liquid, initial_volume_ul):
        well.load_liquid(liquid=liquid, volume=initial_volume_ul)
        pool.append({'well': well, 'remaining': float(initial_volume_ul)})

    # Reservoir 2, slot 8: wells 0-5 low salt, wells 6-11 high salt.
    for i in range(6):
        add_source(low_pool, reservoir_2.wells()[i], low_salt_liquid, 14000.0)
    for i in range(6, 12):
        add_source(high_pool, reservoir_2.wells()[i], high_salt_liquid, 14000.0)

    # Reservoir 1, slot 9: wells 0-1 ligand; wells 2-6 low salt; wells 7-11 high salt.
    add_source(ligand_high_pool, reservoir_1.wells()[0], ligand_high_liquid, 14000.0)
    add_source(ligand_low_pool, reservoir_1.wells()[1], ligand_low_liquid, 14000.0)
    for i in range(2, 7):
        add_source(low_pool, reservoir_1.wells()[i], low_salt_liquid, 14000.0)
    for i in range(7, 12):
        add_source(high_pool, reservoir_1.wells()[i], high_salt_liquid, 14000.0)

    # Reservoir 0, slot 5: all wells low salt.
    for i in range(12):
        add_source(low_pool, reservoir_0.wells()[i], low_salt_liquid, 14000.0)

    def pool_available(pool):
        return sum(max(0.0, entry['remaining'] - reservoir_dead_ul) for entry in pool)

    def next_entry(pool):
        for entry in pool:
            if entry['remaining'] > reservoir_dead_ul + 0.01:
                return entry
        return None

    def multi_add_requests(pool, requests, label, mix_after_destinations=None):
        if mix_after_destinations is None:
            mix_after_destinations = set()
        positive_total = sum(max(0.0, vol) for dest, vol in requests)
        if positive_total <= 0.01:
            return
        if pool_available(pool) + 0.01 < positive_total:
            raise RuntimeError('Insufficient ' + label + ': need ' + str(round(positive_total, 2))
                               + ' uL but only ' + str(round(pool_available(pool), 2)) + ' uL available.')
        p300m.pick_up_tip()
        for dest, requested_physical_ul in requests:
            remaining_request = float(requested_physical_ul)
            while remaining_request > 0.01:
                entry = next_entry(pool)
                if entry is None:
                    raise RuntimeError('Insufficient ' + label + ' while dispensing to ' + str(dest) + '.')
                available_from_well = max(0.0, entry['remaining'] - reservoir_dead_ul)
                move_physical = min(remaining_request, available_from_well, p300m.max_volume * 8.0)
                per_channel_volume = move_physical / 8.0
                p300m.aspirate(per_channel_volume, entry['well'].bottom(2))
                p300m.dispense(per_channel_volume, dest.bottom(5))
                entry['remaining'] -= move_physical
                remaining_request -= move_physical
            if dest in mix_after_destinations:
                p300m.mix(3, 200, dest.bottom(5))
        p300m.drop_tip()

    def single_add_requests(pool, requests, label):
        positive_total = sum(max(0.0, vol) for dest, vol in requests)
        if positive_total <= 0.01:
            return
        if pool_available(pool) + 0.01 < positive_total:
            raise RuntimeError('Insufficient ' + label + ': need ' + str(round(positive_total, 2))
                               + ' uL but only ' + str(round(pool_available(pool), 2)) + ' uL available.')
        p300s.pick_up_tip()
        for dest, requested_ul in requests:
            remaining_request = float(requested_ul)
            while remaining_request > 0.01:
                entry = next_entry(pool)
                if entry is None:
                    raise RuntimeError('Insufficient ' + label + ' while dispensing to ' + str(dest) + '.')
                available_from_well = max(0.0, entry['remaining'] - reservoir_dead_ul)
                move_volume = min(remaining_request, available_from_well, p300s.max_volume)
                p300s.aspirate(move_volume, entry['well'].bottom(2))
                p300s.dispense(move_volume, dest.bottom(2))
                entry['remaining'] -= move_volume
                remaining_request -= move_volume
        p300s.drop_tip()

    def add_ligand_to_one_destination(pool, dest, requested_ul, label, mix_volume_ul):
        if requested_ul <= 0.01:
            return
        if pool_available(pool) + 0.01 < requested_ul:
            raise RuntimeError('Insufficient ' + label + ': need ' + str(round(requested_ul, 2))
                               + ' uL but only ' + str(round(pool_available(pool), 2)) + ' uL available.')
        p300s.pick_up_tip()
        remaining_request = float(requested_ul)
        while remaining_request > 0.01:
            entry = next_entry(pool)
            if entry is None:
                raise RuntimeError('Insufficient ' + label + ' while dispensing to ' + str(dest) + '.')
            available_from_well = max(0.0, entry['remaining'] - reservoir_dead_ul)
            move_volume = min(remaining_request, available_from_well, p300s.max_volume)
            p300s.aspirate(move_volume, entry['well'].bottom(2))
            p300s.dispense(move_volume, dest.bottom(2))
            entry['remaining'] -= move_volume
            remaining_request -= move_volume
        if mix_volume_ul > 1.0:
            p300s.mix(3, min(mix_volume_ul, p300s.max_volume), dest.bottom(2))
        p300s.drop_tip()

    # Steps 2 and 3: create 1x salt buffers in Reservoir 3 and 2x salt buffers in Reservoir 4.
    multi_low_requests = []
    multi_high_requests = []
    zero_high_destinations = set()

    def append_salt_buffer_request(dest, target_salt_conc):
        high_physical = reservoir_final_physical_ul * target_salt_conc / salt_stock_conc
        low_physical = reservoir_final_physical_ul - high_physical
        if high_physical < -0.01 or low_physical < -0.01:
            raise RuntimeError('Salt buffer calculation produced a negative component volume.')
        if low_physical > 0.01:
            multi_low_requests.append((dest, low_physical))
        if high_physical > 0.01:
            multi_high_requests.append((dest, high_physical))
        else:
            zero_high_destinations.add(dest)

    for salt_index, salt_conc in enumerate(salt_concentrations):
        for replicate_index in range(replicates):
            dest_index = salt_index * replicates + replicate_index
            append_salt_buffer_request(reservoir_3.wells()[dest_index], salt_conc)

    for salt_index, salt_conc in enumerate(salt_concentrations):
        append_salt_buffer_request(reservoir_4.wells()[salt_index], 2.0 * salt_conc)

    multi_add_requests(low_pool, multi_low_requests, 'low salt buffer', mix_after_destinations=zero_high_destinations)
    high_destinations = set(dest for dest, vol in multi_high_requests)
    multi_add_requests(high_pool, multi_high_requests, 'high salt buffer', mix_after_destinations=high_destinations)

    # Step 4: create 2x ligand dilutions in the deep-well mixing plate.
    mixing_well_total_volume = total_volume / 2.0 * replicates * 1.5
    if mixing_well_total_volume > 1900.0:
        raise RuntimeError('Calculated mixing-plate well volume exceeds the 2 mL deep-well plate working capacity.')

    buffer_requests = []
    ligand_plan = []
    for ligand_index, ligand_conc in enumerate(ligand_concentrations):
        target_ligand_conc = 2.0 * ligand_conc
        high_stock_volume = 0.0
        if ligand_stock_conc > 0:
            high_stock_volume = mixing_well_total_volume * target_ligand_conc / ligand_stock_conc
        use_low_stock = high_stock_volume > 0.0 and high_stock_volume < 20.0
        stock_conc_used = ligand_stock_conc / 10.0 if use_low_stock else ligand_stock_conc
        stock_volume = 0.0 if target_ligand_conc == 0.0 else mixing_well_total_volume * target_ligand_conc / stock_conc_used
        if stock_volume > mixing_well_total_volume + 0.01:
            raise RuntimeError('Ligand stock concentration is too low for the requested 2x ligand dilution.')
        buffer_volume = mixing_well_total_volume - stock_volume
        for salt_index in range(number_of_salt_concentrations):
            dest = mixing_plate.rows()[ligand_index][salt_index]
            if buffer_volume > 0.01:
                buffer_requests.append((dest, buffer_volume))
            ligand_plan.append((dest, stock_volume, use_low_stock))

    single_add_requests(low_pool, buffer_requests, 'low salt buffer for ligand dilutions')

    for dest, stock_volume, use_low_stock in ligand_plan:
        if stock_volume <= 0.01:
            continue
        pool = ligand_low_pool if use_low_stock else ligand_high_pool
        label = 'low-concentration ligand stock' if use_low_stock else 'high-concentration ligand stock'
        add_ligand_to_one_destination(pool, dest, stock_volume, label, mixing_well_total_volume / 2.0)

    protocol.comment('Protocol complete. Placeholder values are parsed at runtime after template substitution.')
