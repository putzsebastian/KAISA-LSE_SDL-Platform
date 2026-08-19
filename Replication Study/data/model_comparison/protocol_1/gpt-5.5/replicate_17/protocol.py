from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare templated salt buffer reservoirs and ligand dilutions using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # placeholders: these string literals are replaced by the templating system
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

    # modules: none

    # labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        err = str(exc).lower()
        if 'not found' not in err and 'not a known' not in err:
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_multi])

    # calculations and placeholder fallbacks for simulation
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)
    salt_stock_concentration = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_concentration = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 10000.0, float)
    salt_concentrations = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0, 25, 50, 75, 100, 125, 150, 175, 200, 225, 250, 300],
        float
    )
    ligand_concentrations = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0, 10, 20, 50, 100, 200, 300, 500],
        float
    )
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
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS must match the number of entries in SALT_CONCENTRATIONS.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS must match the number of entries in LIGAND_CONCENTRATIONS.')
    if replicates < 1:
        raise ValueError('REPLICATES must be at least 1.')
    if number_of_salt_concentrations * replicates > 12:
        raise ValueError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed the 12 wells of Reservoir 3.')
    if number_of_salt_concentrations > 12:
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12.')
    if number_of_ligand_concentrations > 8:
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS must not exceed 8 because the mixing plate has rows A-H.')
    if salt_stock_concentration <= 0 or ligand_stock_concentration <= 0:
        raise ValueError('Stock concentrations must be greater than zero.')

    salt_reservoir_total_ul = 10000.0
    ligand_mix_total_ul = total_volume / 2.0 * replicates * 1.5
    if ligand_mix_total_ul <= 0:
        raise ValueError('Calculated ligand dilution volume must be greater than zero.')
    if ligand_mix_total_ul > 1900.0:
        raise ValueError('Calculated ligand dilution volume exceeds safe working volume of the 2 mL deep-well plate.')

    # Track physical liquid in reservoir wells. For a multi-channel move into/from a single-row
    # reservoir, physical consumption/delivery is 8x the per-channel volume passed to the pipette.
    BUFFER_DEAD_VOLUME_UL = 100.0
    STOCK_DEAD_VOLUME_UL = 20.0

    def pool_entry(well, volume_ul, dead_volume_ul, name):
        return {'well': well, 'remaining': float(volume_ul), 'dead': float(dead_volume_ul), 'name': name}

    low_salt_pool = []
    for well in reservoir_0.wells():
        low_salt_pool.append(pool_entry(well, 14000.0, BUFFER_DEAD_VOLUME_UL, 'low salt buffer'))
    for well in reservoir_2.wells()[:6]:
        low_salt_pool.append(pool_entry(well, 14000.0, BUFFER_DEAD_VOLUME_UL, 'low salt buffer'))
    for well in reservoir_1.wells()[2:7]:
        low_salt_pool.append(pool_entry(well, 14000.0, BUFFER_DEAD_VOLUME_UL, 'low salt buffer'))

    high_salt_pool = []
    for well in reservoir_2.wells()[6:12]:
        high_salt_pool.append(pool_entry(well, 14000.0, BUFFER_DEAD_VOLUME_UL, 'high salt buffer'))
    for well in reservoir_1.wells()[7:12]:
        high_salt_pool.append(pool_entry(well, 14000.0, BUFFER_DEAD_VOLUME_UL, 'high salt buffer'))

    ligand_high_stock = pool_entry(reservoir_1.wells()[0], 14000.0, STOCK_DEAD_VOLUME_UL, 'high ligand stock')
    ligand_low_stock = pool_entry(reservoir_1.wells()[1], 14000.0, STOCK_DEAD_VOLUME_UL, 'low ligand stock')

    def pool_available(pool):
        return sum(max(0.0, entry['remaining'] - entry['dead']) for entry in pool)

    def dispense_from_pool_multi(pool, destination, physical_volume_ul, label):
        remaining = float(physical_volume_ul)
        if remaining <= 0:
            return
        if pool_available(pool) + 0.001 < remaining:
            raise RuntimeError('Insufficient volume in ' + label + ' pool. Shortfall: ' + str(round(remaining - pool_available(pool), 2)) + ' uL.')
        while remaining > 0.001:
            source_entry = None
            for entry in pool:
                if entry['remaining'] - entry['dead'] > 0.001:
                    source_entry = entry
                    break
            if source_entry is None:
                raise RuntimeError('Insufficient volume in ' + label + ' pool during transfer.')
            available_physical = source_entry['remaining'] - source_entry['dead']
            chunk_physical = min(remaining, available_physical, p300_multi.max_volume * 8.0)
            per_channel_volume = chunk_physical / 8.0
            p300_multi.aspirate(per_channel_volume, source_entry['well'].bottom(2))
            p300_multi.dispense(per_channel_volume, destination.bottom(4))
            source_entry['remaining'] -= chunk_physical
            remaining -= chunk_physical

    def dispense_from_pool_single(pool, destination, volume_ul, label):
        remaining = float(volume_ul)
        if remaining <= 0:
            return
        if pool_available(pool) + 0.001 < remaining:
            raise RuntimeError('Insufficient volume in ' + label + ' pool. Shortfall: ' + str(round(remaining - pool_available(pool), 2)) + ' uL.')
        while remaining > 0.001:
            source_entry = None
            for entry in pool:
                if entry['remaining'] - entry['dead'] > 0.001:
                    source_entry = entry
                    break
            if source_entry is None:
                raise RuntimeError('Insufficient volume in ' + label + ' pool during transfer.')
            available = source_entry['remaining'] - source_entry['dead']
            chunk = min(remaining, available, p300_single.max_volume)
            p300_single.aspirate(chunk, source_entry['well'].bottom(2))
            p300_single.dispense(chunk, destination.bottom(2))
            source_entry['remaining'] -= chunk
            remaining -= chunk

    def dispense_from_single_source(source_entry, destination, volume_ul, label):
        remaining = float(volume_ul)
        if remaining <= 0:
            return
        available_total = source_entry['remaining'] - source_entry['dead']
        if available_total + 0.001 < remaining:
            raise RuntimeError('Insufficient volume in ' + label + '. Shortfall: ' + str(round(remaining - available_total, 2)) + ' uL.')
        while remaining > 0.001:
            available = source_entry['remaining'] - source_entry['dead']
            chunk = min(remaining, available, p300_single.max_volume)
            p300_single.aspirate(chunk, source_entry['well'].bottom(2))
            p300_single.dispense(chunk, destination.bottom(2))
            source_entry['remaining'] -= chunk
            remaining -= chunk

    # Step 2: Create 10 mL salt buffers in Reservoir 3, ascending by well number, with replicates.
    reservoir_3_requests = []
    for salt_index, salt_concentration in enumerate(salt_concentrations):
        high_fraction = salt_concentration / salt_stock_concentration
        if high_fraction < -0.000001 or high_fraction > 1.000001:
            raise ValueError('Requested salt concentration for Reservoir 3 cannot be made from the salt stock concentration.')
        high_volume = salt_reservoir_total_ul * max(0.0, min(1.0, high_fraction))
        low_volume = salt_reservoir_total_ul - high_volume
        for rep in range(replicates):
            dest_index = salt_index * replicates + rep
            reservoir_3_requests.append({
                'destination': reservoir_3.wells()[dest_index],
                'low': low_volume,
                'high': high_volume
            })

    # Step 3: Create 10 mL 2x salt buffers in Reservoir 4, ascending by well number.
    reservoir_4_requests = []
    for salt_index, salt_concentration in enumerate(salt_concentrations):
        doubled_salt = 2.0 * salt_concentration
        high_fraction = doubled_salt / salt_stock_concentration
        if high_fraction < -0.000001 or high_fraction > 1.000001:
            raise ValueError('Requested 2x salt concentration for Reservoir 4 cannot be made from the salt stock concentration.')
        high_volume = salt_reservoir_total_ul * max(0.0, min(1.0, high_fraction))
        low_volume = salt_reservoir_total_ul - high_volume
        reservoir_4_requests.append({
            'destination': reservoir_4.wells()[salt_index],
            'low': low_volume,
            'high': high_volume
        })

    all_salt_requests = reservoir_3_requests + reservoir_4_requests

    protocol.comment('Adding low salt buffer to Reservoir 3 and Reservoir 4.')
    p300_multi.pick_up_tip()
    for request in all_salt_requests:
        dispense_from_pool_multi(low_salt_pool, request['destination'], request['low'], 'low salt buffer')
    p300_multi.drop_tip()

    protocol.comment('Adding high salt buffer to Reservoir 3 and Reservoir 4.')
    p300_multi.pick_up_tip()
    for request in all_salt_requests:
        dispense_from_pool_multi(high_salt_pool, request['destination'], request['high'], 'high salt buffer')
    p300_multi.drop_tip()

    protocol.comment('Mixing prepared salt buffers in Reservoir 3 and Reservoir 4.')
    p300_multi.pick_up_tip()
    for request in all_salt_requests:
        p300_multi.mix(3, 250, request['destination'].bottom(4))
        p300_multi.blow_out(request['destination'].top())
    p300_multi.drop_tip()

    # Step 4: Create 2x ligand concentration dilutions in the deep-well mixing plate.
    target_wells_by_ligand = []
    for ligand_index, ligand_concentration in enumerate(ligand_concentrations):
        row_wells = []
        for salt_index in range(number_of_salt_concentrations):
            row_wells.append(mixing_plate.rows()[ligand_index][salt_index])
        target_wells_by_ligand.append(row_wells)

    ligand_plan = []
    for ligand_concentration in ligand_concentrations:
        target_ligand_concentration = 2.0 * ligand_concentration
        if target_ligand_concentration < 0:
            raise ValueError('Ligand concentrations must not be negative.')
        if target_ligand_concentration == 0:
            stock_entry = None
            stock_volume = 0.0
            buffer_volume = ligand_mix_total_ul
            stock_label = 'no ligand stock'
        else:
            high_stock_volume = ligand_mix_total_ul * target_ligand_concentration / ligand_stock_concentration
            if high_stock_volume < 20.0:
                effective_stock_concentration = ligand_stock_concentration / 10.0
                stock_entry = ligand_low_stock
                stock_label = 'low ligand stock'
            else:
                effective_stock_concentration = ligand_stock_concentration
                stock_entry = ligand_high_stock
                stock_label = 'high ligand stock'
            if target_ligand_concentration > effective_stock_concentration + 0.000001:
                raise ValueError('A requested 2x ligand concentration exceeds the selected ligand stock concentration.')
            stock_volume = ligand_mix_total_ul * target_ligand_concentration / effective_stock_concentration
            buffer_volume = ligand_mix_total_ul - stock_volume
            if buffer_volume < -0.000001:
                raise ValueError('Calculated ligand stock volume exceeds the target well volume.')
        ligand_plan.append({
            'stock_entry': stock_entry,
            'stock_volume': max(0.0, stock_volume),
            'buffer_volume': max(0.0, buffer_volume),
            'stock_label': stock_label
        })

    protocol.comment('Adding low salt buffer to the ligand dilution wells in the mixing plate.')
    p300_single.pick_up_tip()
    for ligand_index, plan in enumerate(ligand_plan):
        for destination in target_wells_by_ligand[ligand_index]:
            dispense_from_pool_single(low_salt_pool, destination, plan['buffer_volume'], 'low salt buffer')
    p300_single.drop_tip()

    protocol.comment('Adding ligand stock solutions and mixing ligand dilution wells.')
    for ligand_index, plan in enumerate(ligand_plan):
        if plan['stock_volume'] <= 0:
            continue
        p300_single.pick_up_tip()
        for destination in target_wells_by_ligand[ligand_index]:
            dispense_from_single_source(plan['stock_entry'], destination, plan['stock_volume'], plan['stock_label'])
            p300_single.mix(3, min(100.0, ligand_mix_total_ul / 2.0, p300_single.max_volume), destination.bottom(2))
            p300_single.blow_out(destination.top())
        p300_single.drop_tip()

    protocol.comment('Protocol complete. Slot 1 filter plate was loaded but is not used in the requested liquid-handling steps.')
