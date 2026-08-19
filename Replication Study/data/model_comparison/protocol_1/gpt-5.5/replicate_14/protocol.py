from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare templated salt buffers and ligand dilutions using placeholders.'
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
    text = str(value).strip()
    return text.startswith('[' * 2) and text.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    text = str(value).strip()
    if _unreplaced(text):
        return default
    if cast is int:
        return int(float(text))
    return cast(text)


def parse_list(value, default, cast=float):
    text = str(value).strip()
    if _unreplaced(text):
        return list(default)
    return [cast(item.strip()) for item in text.split(';') if item.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # modules

    # labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using NEST 96 deep well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 1)

    tiprack_single_primary = protocol.load_labware('opentrons_96_tiprack_300ul', 10)
    tiprack_multi_primary = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_backup = protocol.load_labware('opentrons_96_tiprack_300ul', 4)

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single_primary, tiprack_backup])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi_primary, tiprack_backup])

    # liquids
    low_salt_liquid = protocol.define_liquid(name='Low salt buffer', description='0 salt buffer', display_color='#5DADE2')
    high_salt_liquid = protocol.define_liquid(name='High salt buffer', description='High salt stock buffer', display_color='#2E86C1')
    ligand_high_liquid = protocol.define_liquid(name='Ligand high stock', description='High concentration ligand stock', display_color='#F4D03F')
    ligand_low_liquid = protocol.define_liquid(name='Ligand low stock', description='Low concentration ligand stock', display_color='#F8C471')

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

    # parameters and required calculations
    default_salts = [0, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550]
    default_ligands = [0, 1, 5, 10, 25, 50, 100, 200]

    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 1000.0, float)
    salt_concentrations = sorted(parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default_salts, float))
    ligand_concentrations = sorted(parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, default_ligands, float))
    salt_stock_concentration = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1200.0, float)
    ligand_stock_concentration = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    number_of_salt_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(default_salts), int)
    number_of_ligand_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(default_ligands), int)

    if number_of_salt_concentrations != len(salt_concentrations):
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS does not match SALT_CONCENTRATIONS length.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match LIGAND_CONCENTRATIONS length.')
    if replicates < 1:
        raise ValueError('REPLICATES must be at least 1.')
    if len(ligand_concentrations) > 8:
        raise ValueError('At most 8 ligand concentrations can be prepared in rows A-H.')
    if len(salt_concentrations) > 12:
        raise ValueError('At most 12 salt concentrations can be prepared in columns 1-12 and Reservoir 4.')
    if replicates * len(salt_concentrations) > 12:
        raise ValueError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if salt_stock_concentration <= 0 or ligand_stock_concentration <= 0:
        raise ValueError('Stock concentrations must be greater than 0.')

    reservoir_target_total_ul = 10000.0
    reservoir_target_per_channel_ul = reservoir_target_total_ul / 8.0
    mixing_total_per_well_ul = total_volume / 2.0 * replicates * 1.5
    if mixing_total_per_well_ul <= 0:
        raise ValueError('Calculated mixing plate volume must be greater than 0.')
    if mixing_total_per_well_ul > 2000:
        raise ValueError('Calculated mixing plate volume exceeds the 2 mL well capacity.')

    # source volume tracking in total liquid uL per well. A 100 uL residual is kept in reservoirs,
    # and a 20 uL residual is kept in ligand stock wells.
    low_pool = []
    high_pool = []
    for well in reservoir_2.wells()[:6]:
        low_pool.append({'well': well, 'remaining': 13900.0, 'name': well.display_name})
    for well in reservoir_1.wells()[2:7]:
        low_pool.append({'well': well, 'remaining': 13900.0, 'name': well.display_name})
    for well in reservoir_0.wells():
        low_pool.append({'well': well, 'remaining': 13900.0, 'name': well.display_name})
    for well in reservoir_2.wells()[6:12]:
        high_pool.append({'well': well, 'remaining': 13900.0, 'name': well.display_name})
    for well in reservoir_1.wells()[7:12]:
        high_pool.append({'well': well, 'remaining': 13900.0, 'name': well.display_name})
    ligand_high_pool = [{'well': reservoir_1.wells()[0], 'remaining': 13980.0, 'name': reservoir_1.wells()[0].display_name}]
    ligand_low_pool = [{'well': reservoir_1.wells()[1], 'remaining': 13980.0, 'name': reservoir_1.wells()[1].display_name}]

    def take_from_pool(pool, requested_total_ul, pool_name):
        remaining_request = float(requested_total_ul)
        segments = []
        if remaining_request <= 0:
            return segments
        for source in pool:
            if remaining_request <= 0:
                break
            available = source['remaining']
            if available <= 0:
                continue
            take = min(available, remaining_request)
            source['remaining'] -= take
            remaining_request -= take
            segments.append((source['well'], take))
        if remaining_request > 0.0001:
            raise RuntimeError('Insufficient volume in ' + pool_name + '; short by ' + str(round(remaining_request, 2)) + ' uL.')
        return segments

    def multi_move_total(source_pool, requested_total_ul, dest_well, pool_name):
        segments = take_from_pool(source_pool, requested_total_ul, pool_name)
        for source_well, actual_total_ul in segments:
            per_channel_remaining = actual_total_ul / 8.0
            while per_channel_remaining > 0.0001:
                chunk = min(p300_multi.max_volume, per_channel_remaining)
                p300_multi.aspirate(chunk, source_well.bottom(2))
                p300_multi.dispense(chunk, dest_well.bottom(5))
                per_channel_remaining -= chunk

    def single_move_total(source_pool, requested_total_ul, dest_well, pool_name, dispense_location=None):
        segments = take_from_pool(source_pool, requested_total_ul, pool_name)
        for source_well, actual_total_ul in segments:
            remaining = actual_total_ul
            while remaining > 0.0001:
                chunk = min(p300_single.max_volume, remaining)
                p300_single.aspirate(chunk, source_well.bottom(2))
                if dispense_location is None:
                    p300_single.dispense(chunk, dest_well.bottom(3))
                else:
                    p300_single.dispense(chunk, dispense_location)
                remaining -= chunk

    # Calculate salt buffer recipes for Reservoir 3 and Reservoir 4.
    salt_groups = []
    for salt_index, salt_conc in enumerate(salt_concentrations):
        if salt_conc < 0:
            raise ValueError('Salt concentrations must be non-negative.')
        res3_high_total = reservoir_target_total_ul * salt_conc / salt_stock_concentration
        res3_low_total = reservoir_target_total_ul - res3_high_total
        res4_target_conc = 2.0 * salt_conc
        res4_high_total = reservoir_target_total_ul * res4_target_conc / salt_stock_concentration
        res4_low_total = reservoir_target_total_ul - res4_high_total
        if res3_low_total < -0.0001 or res4_low_total < -0.0001:
            raise ValueError('A requested salt concentration or 2x salt concentration exceeds the salt stock concentration.')

        group_targets = []
        for rep_index in range(replicates):
            target_index = salt_index * replicates + rep_index
            group_targets.append({'well': reservoir_3.wells()[target_index], 'low': max(0.0, res3_low_total), 'high': max(0.0, res3_high_total)})
        group_targets.append({'well': reservoir_4.wells()[salt_index], 'low': max(0.0, res4_low_total), 'high': max(0.0, res4_high_total)})
        salt_groups.append(group_targets)

    protocol.comment('Step 2 and Step 3: prepare salt buffers in Reservoir 3 and 2x salt buffers in Reservoir 4.')

    any_low_needed = any(target['low'] > 0 for group in salt_groups for target in group)
    if any_low_needed:
        p300_multi.pick_up_tip()
        for group in salt_groups:
            for target in group:
                if target['low'] > 0:
                    multi_move_total(low_pool, target['low'], target['well'], 'low salt buffer')
        p300_multi.drop_tip()

    for group in salt_groups:
        p300_multi.pick_up_tip()
        for target in group:
            if target['high'] > 0:
                multi_move_total(high_pool, target['high'], target['well'], 'high salt buffer')
        for target in group:
            mix_volume = min(200.0, reservoir_target_per_channel_ul)
            p300_multi.mix(3, mix_volume, target['well'].bottom(5))
            p300_multi.blow_out(target['well'].top())
        p300_multi.drop_tip()

    # Step 4: create ligand dilution plate. Rows are ligand concentrations A-H; columns are salt concentrations.
    protocol.comment('Step 4: prepare 2x ligand dilutions in the deep-well mixing plate.')
    mixing_targets = []
    for ligand_index, ligand_conc in enumerate(ligand_concentrations):
        if ligand_conc < 0:
            raise ValueError('Ligand concentrations must be non-negative.')
        target_ligand_conc = 2.0 * ligand_conc
        high_stock_volume = mixing_total_per_well_ul * target_ligand_conc / ligand_stock_concentration
        low_stock_volume = mixing_total_per_well_ul * target_ligand_conc / (ligand_stock_concentration / 10.0)
        use_low_stock = high_stock_volume < 20.0 and low_stock_volume <= mixing_total_per_well_ul and ligand_conc > 0
        stock_volume = low_stock_volume if use_low_stock else high_stock_volume
        stock_pool = ligand_low_pool if use_low_stock else ligand_high_pool
        stock_name = 'low ligand stock' if use_low_stock else 'high ligand stock'
        if stock_volume > mixing_total_per_well_ul + 0.0001:
            raise ValueError('Required ligand stock volume exceeds total well volume for ligand concentration ' + str(ligand_conc) + '.')
        buffer_volume = mixing_total_per_well_ul - stock_volume
        row_targets = []
        for salt_index in range(len(salt_concentrations)):
            dest_well = mixing_plate.rows()[ligand_index][salt_index]
            row_targets.append({'well': dest_well, 'buffer': max(0.0, buffer_volume), 'stock': max(0.0, stock_volume), 'stock_pool': stock_pool, 'stock_name': stock_name})
            mixing_targets.append(row_targets[-1])

    any_buffer_needed = any(target['buffer'] > 0 for target in mixing_targets)
    if any_buffer_needed:
        p300_single.pick_up_tip()
        for target in mixing_targets:
            if target['buffer'] > 0:
                single_move_total(low_pool, target['buffer'], target['well'], 'low salt buffer')
        p300_single.drop_tip()

    for ligand_index in range(len(ligand_concentrations)):
        row_targets = [target for target in mixing_targets if target['well'] in mixing_plate.rows()[ligand_index][:len(salt_concentrations)]]
        for target in row_targets:
            p300_single.pick_up_tip()
            if target['stock'] > 0:
                single_move_total(target['stock_pool'], target['stock'], target['well'], target['stock_name'], dispense_location=target['well'].top(-2))
            mix_volume = min(200.0, max(20.0, mixing_total_per_well_ul * 0.5))
            p300_single.mix(3, mix_volume, target['well'].bottom(3))
            p300_single.blow_out(target['well'].top())
            p300_single.drop_tip()

    protocol.comment('Protocol complete.')
