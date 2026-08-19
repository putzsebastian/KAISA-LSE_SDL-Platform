from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt Buffer and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare templated salt buffers in reservoirs and ligand dilutions in a deep-well mixing plate.'
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


def run(protocol: protocol_api.ProtocolContext):
    # labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1, label='Cytiva 96 Filter Well Plate')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using NEST 96 well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1, label='SIMULATION fallback for Cytiva 96 Filter Well Plate')

    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10, label='Single-channel tips')
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7, label='Multi-channel tips')
    extra_tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 4, label='Extra 300 uL tips')

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3, label='Reservoir 4 - 2x salt buffers')
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6, label='Reservoir 3 - salt buffers')
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8, label='Reservoir 2 - salt stocks')
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9, label='Reservoir 1 - ligand and buffers')
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5, label='Reservoir 0 - low salt buffer')
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11, label='Mixing Plate')

    # pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # parameters with simulation fallbacks
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 3, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    salt_concentrations = sorted(parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 100, 200, 300], float))
    ligand_concentrations = sorted(parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [0, 1, 3, 10, 30, 100, 300, 500], float))
    number_of_salt_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concentrations), int)
    number_of_ligand_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concentrations), int)

    if number_of_salt_concentrations != len(salt_concentrations):
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS does not match the number of values in SALT_CONCENTRATIONS.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match the number of values in LIGAND_CONCENTRATIONS.')
    if replicates * number_of_salt_concentrations > 12:
        raise ValueError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if number_of_salt_concentrations > 12:
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 4 and the mixing plate columns.')
    if number_of_ligand_concentrations > 8:
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS must not exceed 8 because the mixing plate has 8 rows.')

    mixing_well_volume = total_volume / 2.0 * replicates * 1.5
    if mixing_well_volume > 1900:
        raise ValueError('Calculated ligand dilution volume per mixing-plate well exceeds a conservative 1900 uL limit for the 2 mL deep-well plate.')
    if salt_stock_conc <= 0 or ligand_stock_conc <= 0:
        raise ValueError('Stock concentrations must be greater than zero.')

    # liquid labels
    low_salt_liquid = protocol.define_liquid(name='Low salt buffer', description='0 salt buffer', display_color='#4DA6FF')
    high_salt_liquid = protocol.define_liquid(name='High salt buffer', description='Salt stock buffer', display_color='#FFB347')
    ligand_high_liquid = protocol.define_liquid(name='Ligand high stock', description='High concentration ligand stock', display_color='#7D3C98')
    ligand_low_liquid = protocol.define_liquid(name='Ligand low stock', description='Low concentration ligand stock', display_color='#C39BD3')

    for well in reservoir_2.wells()[:6]:
        well.load_liquid(low_salt_liquid, 14000)
    for well in reservoir_2.wells()[6:12]:
        well.load_liquid(high_salt_liquid, 14000)
    reservoir_1.wells()[0].load_liquid(ligand_high_liquid, 14000)
    reservoir_1.wells()[1].load_liquid(ligand_low_liquid, 14000)
    for well in reservoir_1.wells()[2:7]:
        well.load_liquid(low_salt_liquid, 14000)
    for well in reservoir_1.wells()[7:12]:
        well.load_liquid(high_salt_liquid, 14000)
    for well in reservoir_0.wells():
        well.load_liquid(low_salt_liquid, 14000)

    dead_volume = 50.0
    low_salt_pool = []
    high_salt_pool = []
    for well in reservoir_2.wells()[:6]:
        low_salt_pool.append({'well': well, 'remaining': 14000.0})
    for well in reservoir_1.wells()[2:7]:
        low_salt_pool.append({'well': well, 'remaining': 14000.0})
    for well in reservoir_0.wells():
        low_salt_pool.append({'well': well, 'remaining': 14000.0})
    for well in reservoir_2.wells()[6:12]:
        high_salt_pool.append({'well': well, 'remaining': 14000.0})
    for well in reservoir_1.wells()[7:12]:
        high_salt_pool.append({'well': well, 'remaining': 14000.0})
    ligand_high_pool = [{'well': reservoir_1.wells()[0], 'remaining': 14000.0}]
    ligand_low_pool = [{'well': reservoir_1.wells()[1], 'remaining': 14000.0}]

    def available_in_pool(pool):
        return sum(max(0.0, source['remaining'] - dead_volume) for source in pool)

    def transfer_from_pool_multi(pool, additions, liquid_name):
        total_needed = sum(volume for _, volume in additions)
        if total_needed <= 0:
            return
        if available_in_pool(pool) + 0.001 < total_needed:
            raise RuntimeError('Insufficient ' + liquid_name + ': need ' + str(round(total_needed, 2)) + ' uL but only ' + str(round(available_in_pool(pool), 2)) + ' uL is available.')
        p300_multi.pick_up_tip()
        source_index = 0
        try:
            for dest, destination_total_volume in additions:
                remaining_total = destination_total_volume
                while remaining_total > 0.001:
                    while source_index < len(pool) and pool[source_index]['remaining'] <= dead_volume + 0.001:
                        source_index += 1
                    if source_index >= len(pool):
                        raise RuntimeError('Insufficient ' + liquid_name + ' while dispensing to ' + dest.well_name + '.')
                    source = pool[source_index]
                    source_available_total = source['remaining'] - dead_volume
                    move_total = min(remaining_total, source_available_total, p300_multi.max_volume * 8.0)
                    per_channel_volume = move_total / 8.0
                    p300_multi.aspirate(per_channel_volume, source['well'].bottom(2))
                    source['remaining'] -= move_total
                    p300_multi.dispense(per_channel_volume, dest.bottom(5))
                    remaining_total -= move_total
        finally:
            p300_multi.drop_tip()

    def mix_multi_destinations(destinations):
        if not destinations:
            return
        p300_multi.pick_up_tip()
        try:
            for dest in destinations:
                p300_multi.mix(1, 250, dest.bottom(5))
        finally:
            p300_multi.drop_tip()

    def transfer_from_pool_single(pool, additions, pipette, liquid_name, keep_one_tip=True):
        total_needed = sum(volume for _, volume in additions)
        if total_needed <= 0:
            return
        if available_in_pool(pool) + 0.001 < total_needed:
            raise RuntimeError('Insufficient ' + liquid_name + ': need ' + str(round(total_needed, 2)) + ' uL but only ' + str(round(available_in_pool(pool), 2)) + ' uL is available.')
        source_index = 0
        if keep_one_tip:
            pipette.pick_up_tip()
        try:
            for dest, destination_volume in additions:
                if destination_volume <= 0:
                    continue
                if not keep_one_tip:
                    pipette.pick_up_tip()
                remaining = destination_volume
                while remaining > 0.001:
                    while source_index < len(pool) and pool[source_index]['remaining'] <= dead_volume + 0.001:
                        source_index += 1
                    if source_index >= len(pool):
                        raise RuntimeError('Insufficient ' + liquid_name + ' while dispensing to ' + dest.well_name + '.')
                    source = pool[source_index]
                    source_available = source['remaining'] - dead_volume
                    move_volume = min(remaining, source_available, pipette.max_volume)
                    pipette.aspirate(move_volume, source['well'].bottom(2))
                    source['remaining'] -= move_volume
                    pipette.dispense(move_volume, dest.top(-2))
                    remaining -= move_volume
                if not keep_one_tip:
                    pipette.drop_tip()
        finally:
            if keep_one_tip:
                pipette.drop_tip()

    # Step 2: Create salt buffers in Reservoir 3, with replicate wells per concentration.
    reservoir_3_plan = []
    for salt_index, salt_conc in enumerate(salt_concentrations):
        if salt_conc < 0 or salt_conc > salt_stock_conc:
            raise ValueError('A requested salt concentration for Reservoir 3 is outside the range 0 to SALT_STOCK_CONCENTRATION.')
        high_total = 10000.0 * salt_conc / salt_stock_conc
        low_total = 10000.0 - high_total
        for rep in range(replicates):
            dest_index = salt_index * replicates + rep
            reservoir_3_plan.append({'dest': reservoir_3.wells()[dest_index], 'low': low_total, 'high': high_total})

    transfer_from_pool_multi(low_salt_pool, [(item['dest'], item['low']) for item in reservoir_3_plan], 'low salt buffer')
    transfer_from_pool_multi(high_salt_pool, [(item['dest'], item['high']) for item in reservoir_3_plan], 'high salt buffer')
    mix_multi_destinations([item['dest'] for item in reservoir_3_plan])

    # Step 3: Create 2x salt buffers in Reservoir 4, one well per salt concentration.
    reservoir_4_plan = []
    for salt_index, salt_conc in enumerate(salt_concentrations):
        target_conc = 2.0 * salt_conc
        if target_conc < 0 or target_conc > salt_stock_conc:
            raise ValueError('A requested 2x salt concentration for Reservoir 4 is outside the range 0 to SALT_STOCK_CONCENTRATION.')
        high_total = 10000.0 * target_conc / salt_stock_conc
        low_total = 10000.0 - high_total
        reservoir_4_plan.append({'dest': reservoir_4.wells()[salt_index], 'low': low_total, 'high': high_total})

    transfer_from_pool_multi(low_salt_pool, [(item['dest'], item['low']) for item in reservoir_4_plan], 'low salt buffer')
    transfer_from_pool_multi(high_salt_pool, [(item['dest'], item['high']) for item in reservoir_4_plan], 'high salt buffer')
    mix_multi_destinations([item['dest'] for item in reservoir_4_plan])

    # Step 4: Create 2x ligand dilution matrix in the deep-well mixing plate.
    ligand_plan = []
    for row_index, ligand_conc in enumerate(ligand_concentrations):
        if ligand_conc < 0:
            raise ValueError('Ligand concentrations must be non-negative.')
        target_ligand_conc = 2.0 * ligand_conc
        high_stock_volume = 0.0 if target_ligand_conc == 0 else mixing_well_volume * target_ligand_conc / ligand_stock_conc
        if target_ligand_conc == 0:
            source_type = 'none'
            ligand_volume = 0.0
        elif high_stock_volume < 20.0:
            source_type = 'low'
            ligand_volume = mixing_well_volume * target_ligand_conc / (ligand_stock_conc / 10.0)
        else:
            source_type = 'high'
            ligand_volume = high_stock_volume
        if ligand_volume > mixing_well_volume + 0.001:
            raise ValueError('A requested 2x ligand concentration exceeds the selected ligand stock concentration.')
        buffer_volume = mixing_well_volume - ligand_volume
        for col_index in range(number_of_salt_concentrations):
            dest = mixing_plate.rows()[row_index][col_index]
            ligand_plan.append({'dest': dest, 'row_index': row_index, 'ligand': ligand_volume, 'buffer': buffer_volume, 'source_type': source_type})

    low_ligand_additions = [(item['dest'], item['ligand']) for item in ligand_plan if item['source_type'] == 'low']
    high_ligand_additions = [(item['dest'], item['ligand']) for item in ligand_plan if item['source_type'] == 'high']
    buffer_additions = [(item['dest'], item['buffer']) for item in ligand_plan]

    transfer_from_pool_single(ligand_low_pool, low_ligand_additions, p300_single, 'low concentration ligand stock', keep_one_tip=True)
    transfer_from_pool_single(ligand_high_pool, high_ligand_additions, p300_single, 'high concentration ligand stock', keep_one_tip=True)
    transfer_from_pool_single(low_salt_pool, buffer_additions, p300_single, 'low salt buffer for ligand dilutions', keep_one_tip=True)

    for row_index in range(number_of_ligand_concentrations):
        row_wells_to_mix = mixing_plate.rows()[row_index][:number_of_salt_concentrations]
        p300_single.pick_up_tip()
        try:
            for well in row_wells_to_mix:
                mix_volume = min(250.0, max(20.0, mixing_well_volume * 0.5))
                p300_single.mix(3, mix_volume, well.bottom(3))
        finally:
            p300_single.drop_tip()

    protocol.comment('Protocol complete. Salt buffers and ligand dilutions were prepared using placeholder-controlled parameters.')
