from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Templated preparation of salt buffers in reservoirs and ligand dilutions in a deep-well mixing plate.'
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
        return list(default)
    return [cast(x.strip()) for x in s.split(';') if x.strip()]


def make_pool(entries, dead_volume_ul=100.0):
    return [{'well': well, 'remaining': float(volume), 'dead': float(dead_volume_ul), 'name': name}
            for well, volume, name in entries]


def pool_available(pool):
    return sum(max(0.0, item['remaining'] - item['dead']) for item in pool)


def move_from_pool_single(pipette, pool, volume_ul, dest, pool_name):
    remaining = float(volume_ul)
    epsilon = 0.000001
    while remaining > epsilon:
        current = None
        for item in pool:
            if item['remaining'] - item['dead'] > epsilon:
                current = item
                break
        if current is None:
            shortfall = remaining
            raise RuntimeError('Insufficient liquid in pool %s; shortfall %.2f uL.' % (pool_name, shortfall))
        available = current['remaining'] - current['dead']
        chunk = min(remaining, available, pipette.max_volume)
        if chunk <= epsilon:
            current['remaining'] = current['dead']
            continue
        pipette.aspirate(chunk, current['well'].bottom(2))
        pipette.dispense(chunk, dest.top())
        current['remaining'] -= chunk
        remaining -= chunk


def move_from_pool_multi(pipette, pool, volume_per_channel_ul, dest, pool_name):
    channels = 8.0
    actual_remaining = float(volume_per_channel_ul) * channels
    epsilon = 0.000001
    while actual_remaining > epsilon:
        current = None
        for item in pool:
            if item['remaining'] - item['dead'] > epsilon:
                current = item
                break
        if current is None:
            shortfall = actual_remaining
            raise RuntimeError('Insufficient liquid in pool %s; shortfall %.2f uL.' % (pool_name, shortfall))
        available_actual = current['remaining'] - current['dead']
        chunk_actual = min(actual_remaining, available_actual, pipette.max_volume * channels)
        if chunk_actual <= epsilon:
            current['remaining'] = current['dead']
            continue
        chunk_per_channel = chunk_actual / channels
        pipette.aspirate(chunk_per_channel, current['well'].bottom(2))
        pipette.dispense(chunk_per_channel, dest.top())
        current['remaining'] -= chunk_actual
        actual_remaining -= chunk_actual


def run(protocol):
    # labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc).lower():
            raise
        protocol.comment('WARNING: custom labware definition cytiva_96_filterwellplate_1ml not available; using nest_96_wellplate_200ul_flat as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # parameters and calculations
    default_salts = [0, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550]
    default_ligands = [0, 1, 2, 5, 10, 20, 50, 100]

    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)
    salt_concentrations = sorted(parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default_salts, float))
    ligand_concentrations = sorted(parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, default_ligands, float))
    salt_stock_concentration = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 2000.0, float)
    ligand_stock_concentration = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 10000.0, float)
    number_of_salt_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(default_salts), int)
    number_of_ligand_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(default_ligands), int)

    if number_of_salt_concentrations != len(salt_concentrations):
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS does not match SALT_CONCENTRATIONS list length.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match LIGAND_CONCENTRATIONS list length.')
    if replicates < 1:
        raise RuntimeError('REPLICATES must be at least 1.')
    if len(salt_concentrations) * replicates > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if len(salt_concentrations) > 12:
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 4 and mixing plate columns.')
    if len(ligand_concentrations) > 8:
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS must not exceed 8 because the mixing plate has 8 rows.')
    if salt_stock_concentration <= 0:
        raise RuntimeError('SALT_STOCK_CONCENTRATION must be greater than zero.')
    if ligand_stock_concentration <= 0:
        raise RuntimeError('LIGAND_STOCK_CONCENTRATION must be greater than zero.')
    if total_volume <= 0:
        raise RuntimeError('TOTAL_VOLUME must be greater than zero.')

    # Source pools. Each listed reservoir source well initially contains 14 mL.
    low_salt_entries = []
    for i, well in enumerate(reservoir2.wells()[:6]):
        low_salt_entries.append((well, 14000.0, 'Reservoir 2 well %d low salt buffer' % i))
    for i, well in enumerate(reservoir1.wells()[2:7], start=2):
        low_salt_entries.append((well, 14000.0, 'Reservoir 1 well %d low salt buffer' % i))
    for i, well in enumerate(reservoir0.wells()):
        low_salt_entries.append((well, 14000.0, 'Reservoir 0 well %d low salt buffer' % i))

    high_salt_entries = []
    for i, well in enumerate(reservoir2.wells()[6:12], start=6):
        high_salt_entries.append((well, 14000.0, 'Reservoir 2 well %d high salt buffer' % i))
    for i, well in enumerate(reservoir1.wells()[7:12], start=7):
        high_salt_entries.append((well, 14000.0, 'Reservoir 1 well %d high salt buffer' % i))

    low_salt_pool = make_pool(low_salt_entries)
    high_salt_pool = make_pool(high_salt_entries)
    ligand_high_pool = make_pool([(reservoir1.wells()[0], 14000.0, 'Reservoir 1 well 0 high ligand stock')], dead_volume_ul=20.0)
    ligand_low_pool = make_pool([(reservoir1.wells()[1], 14000.0, 'Reservoir 1 well 1 low ligand stock')], dead_volume_ul=20.0)

    # Steps 2 and 3: prepare salt buffers in Reservoir 3 and 2x salt buffers in Reservoir 4.
    salt_targets = []
    well_index = 0
    for salt_conc in salt_concentrations:
        for rep in range(replicates):
            salt_targets.append({'dest': reservoir3.wells()[well_index], 'target_conc': salt_conc, 'label': 'Reservoir 3'})
            well_index += 1
    for idx, salt_conc in enumerate(salt_concentrations):
        salt_targets.append({'dest': reservoir4.wells()[idx], 'target_conc': 2.0 * salt_conc, 'label': 'Reservoir 4'})

    for target in salt_targets:
        if target['target_conc'] < 0:
            raise RuntimeError('Salt concentrations must not be negative.')
        if target['target_conc'] > salt_stock_concentration:
            raise RuntimeError('Requested salt concentration %.4f exceeds salt stock concentration %.4f.' % (target['target_conc'], salt_stock_concentration))
        target['high_total_ul'] = 10000.0 * target['target_conc'] / salt_stock_concentration
        target['low_total_ul'] = 10000.0 - target['high_total_ul']
        target['high_per_channel_ul'] = target['high_total_ul'] / 8.0
        target['low_per_channel_ul'] = target['low_total_ul'] / 8.0

    if pool_available(low_salt_pool) < sum(t['low_total_ul'] for t in salt_targets):
        raise RuntimeError('Insufficient low salt buffer for salt-buffer preparation steps.')
    if pool_available(high_salt_pool) < sum(t['high_total_ul'] for t in salt_targets):
        raise RuntimeError('Insufficient high salt buffer for salt-buffer preparation steps.')

    p300_multi.pick_up_tip()
    for target in salt_targets:
        if target['low_per_channel_ul'] > 0:
            move_from_pool_multi(p300_multi, low_salt_pool, target['low_per_channel_ul'], target['dest'], 'low salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target in salt_targets:
        if target['high_per_channel_ul'] > 0:
            move_from_pool_multi(p300_multi, high_salt_pool, target['high_per_channel_ul'], target['dest'], 'high salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target in salt_targets:
        p300_multi.mix(3, 200.0, target['dest'].bottom(3))
    p300_multi.drop_tip()

    # Step 4: prepare row-wise 2x ligand dilutions in the deep-well mixing plate.
    mixing_total_per_well = (total_volume / 2.0) * replicates * 1.5
    low_ligand_stock_concentration = ligand_stock_concentration / 10.0
    row_names = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    ligand_plan = []

    for lig_idx, ligand_conc in enumerate(ligand_concentrations):
        if ligand_conc < 0:
            raise RuntimeError('Ligand concentrations must not be negative.')
        target_ligand_conc = 2.0 * ligand_conc
        high_stock_volume = 0.0
        if target_ligand_conc > 0:
            high_stock_volume = mixing_total_per_well * target_ligand_conc / ligand_stock_concentration
        use_low_stock = high_stock_volume < 20.0 and target_ligand_conc <= low_ligand_stock_concentration
        if use_low_stock:
            stock_name = 'low'
            stock_concentration = low_ligand_stock_concentration
        else:
            stock_name = 'high'
            stock_concentration = ligand_stock_concentration
        if target_ligand_conc > stock_concentration:
            raise RuntimeError('Requested 2x ligand concentration %.4f exceeds selected ligand stock concentration %.4f.' % (target_ligand_conc, stock_concentration))
        ligand_volume = 0.0
        if target_ligand_conc > 0:
            ligand_volume = mixing_total_per_well * target_ligand_conc / stock_concentration
        buffer_volume = mixing_total_per_well - ligand_volume
        if buffer_volume < -0.000001:
            raise RuntimeError('Calculated ligand stock volume exceeds total mixing volume.')
        for salt_idx in range(len(salt_concentrations)):
            dest_name = row_names[lig_idx] + str(salt_idx + 1)
            ligand_plan.append({
                'dest': mixing_plate.wells_by_name()[dest_name],
                'ligand_volume': max(0.0, ligand_volume),
                'buffer_volume': max(0.0, buffer_volume),
                'stock': stock_name
            })

    total_buffer_needed = sum(item['buffer_volume'] for item in ligand_plan)
    total_high_ligand_needed = sum(item['ligand_volume'] for item in ligand_plan if item['stock'] == 'high')
    total_low_ligand_needed = sum(item['ligand_volume'] for item in ligand_plan if item['stock'] == 'low')
    if pool_available(low_salt_pool) < total_buffer_needed:
        raise RuntimeError('Insufficient low salt buffer for ligand dilution preparation.')
    if pool_available(ligand_high_pool) < total_high_ligand_needed:
        raise RuntimeError('Insufficient high ligand stock solution for ligand dilution preparation.')
    if pool_available(ligand_low_pool) < total_low_ligand_needed:
        raise RuntimeError('Insufficient low ligand stock solution for ligand dilution preparation.')

    p300_single.pick_up_tip()
    for item in ligand_plan:
        if item['buffer_volume'] > 0:
            move_from_pool_single(p300_single, low_salt_pool, item['buffer_volume'], item['dest'], 'low salt buffer')
    p300_single.drop_tip()

    high_items = [item for item in ligand_plan if item['stock'] == 'high' and item['ligand_volume'] > 0]
    if high_items:
        p300_single.pick_up_tip()
        for item in high_items:
            move_from_pool_single(p300_single, ligand_high_pool, item['ligand_volume'], item['dest'], 'high ligand stock')
        p300_single.drop_tip()

    low_items = [item for item in ligand_plan if item['stock'] == 'low' and item['ligand_volume'] > 0]
    if low_items:
        p300_single.pick_up_tip()
        for item in low_items:
            move_from_pool_single(p300_single, ligand_low_pool, item['ligand_volume'], item['dest'], 'low ligand stock')
        p300_single.drop_tip()

    protocol.comment('Protocol complete. Custom filter plate was loaded but not used by the specified liquid-handling steps.')
