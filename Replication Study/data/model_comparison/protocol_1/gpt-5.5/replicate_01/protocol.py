from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare templated salt buffers in reservoirs and ligand dilutions in a deep-well mixing plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol):
    # -------------------------
    # Placeholder declarations
    # -------------------------
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

    # Simulation defaults exercise a full Reservoir 3 layout: 4 salt concentrations x 3 replicates = 12 wells.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 3, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 100.0, float)
    salt_concentrations = sorted(parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 100.0, 200.0, 300.0], float))
    ligand_concentrations = sorted(parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [0.0, 1.0, 10.0, 50.0, 100.0, 200.0, 300.0, 400.0], float))
    salt_stock_concentration = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_concentration = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    number_of_salt_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concentrations), int)
    number_of_ligand_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concentrations), int)

    # -------------------------
    # Modules: none
    # Labware
    # -------------------------
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1, label='Cytiva 96 Filter Well Plate 1 mL')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using NEST 96 Well Plate 200 uL Flat as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1, label='SIMULATION fallback for Cytiva 96 Filter Well Plate')

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4, label='Opentrons 300 uL Tips Slot 4')
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7, label='Opentrons 300 uL Tips Slot 7 - Multi')
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10, label='Opentrons 300 uL Tips Slot 10 - Single')

    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3, label='Reservoir 4 - 2x Salt Buffers')
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6, label='Reservoir 3 - Salt Buffers')
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8, label='Reservoir 2 - Source Buffers')
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9, label='Reservoir 1 - Ligand Stocks and Source Buffers')
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5, label='Reservoir 0 - Low Salt Buffer')
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11, label='Mixing Plate')

    # -------------------------
    # Pipettes
    # -------------------------
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # Keep slot 4 tip rack loaded as specified, but use slot 7 for multi-channel work and slot 10 for single-channel work.
    _ = filter_plate
    _ = tiprack_slot4

    # -------------------------
    # Validations and calculations
    # -------------------------
    if replicates < 1:
        raise RuntimeError('[[REPLICATES]] must be at least 1.')
    if len(salt_concentrations) != number_of_salt_concentrations:
        raise RuntimeError('[[NUMBER_OF_SALT_CONCENTRATIONS]] does not match the number of entries in [[SALT_CONCENTRATIONS]].')
    if len(ligand_concentrations) != number_of_ligand_concentrations:
        raise RuntimeError('[[NUMBER_OF_LIGAND_CONCENTRATIONS]] does not match the number of entries in [[LIGAND_CONCENTRATIONS]].')
    if len(salt_concentrations) * replicates > 12:
        raise RuntimeError('[[REPLICATES]] x [[NUMBER_OF_SALT_CONCENTRATIONS]] must not exceed 12 for Reservoir 3.')
    if len(salt_concentrations) > 12:
        raise RuntimeError('At most 12 salt concentrations can be prepared in Reservoir 4 and in mixing plate columns.')
    if len(ligand_concentrations) > 8:
        raise RuntimeError('At most 8 ligand concentrations can be prepared row-wise in the mixing plate rows A-H.')
    if total_volume <= 0:
        raise RuntimeError('[[TOTAL_VOLUME]] must be greater than 0.')
    if salt_stock_concentration <= 0:
        raise RuntimeError('[[SALT_STOCK_CONCENTRATION]] must be greater than 0.')
    if ligand_stock_concentration <= 0:
        raise RuntimeError('[[LIGAND_STOCK_CONCENTRATION]] must be greater than 0.')

    salt_buffer_target_total_ul = 10000.0
    ligand_well_total_ul = total_volume / 2.0 * replicates * 1.5
    if ligand_well_total_ul > 2000.0:
        raise RuntimeError('Each mixing-plate well would exceed the 2 mL well capacity. Reduce [[TOTAL_VOLUME]] or [[REPLICATES]].')

    # Pools track total liquid in each physical reservoir well. A multi-channel move into/from a single-row
    # reservoir well consumes/delivers 8x the per-channel volume commanded to the API.
    dead_volume_ul = 100.0

    def make_pool(entries):
        return [{'well': well, 'remaining': float(volume_ul), 'name': name} for well, volume_ul, name in entries]

    low_salt_pool = make_pool(
        [(reservoir2.wells()[i], 14000.0, 'Reservoir 2 low salt') for i in range(0, 6)] +
        [(reservoir1.wells()[i], 14000.0, 'Reservoir 1 low salt') for i in range(2, 7)] +
        [(reservoir0.wells()[i], 14000.0, 'Reservoir 0 low salt') for i in range(0, 12)]
    )
    high_salt_pool = make_pool(
        [(reservoir2.wells()[i], 14000.0, 'Reservoir 2 high salt') for i in range(6, 12)] +
        [(reservoir1.wells()[i], 14000.0, 'Reservoir 1 high salt') for i in range(7, 12)]
    )
    high_ligand_stock_pool = make_pool([(reservoir1.wells()[0], 14000.0, 'Reservoir 1 high ligand stock')])
    low_ligand_stock_pool = make_pool([(reservoir1.wells()[1], 14000.0, 'Reservoir 1 low ligand stock')])

    def pool_available(pool):
        return sum(max(0.0, item['remaining'] - dead_volume_ul) for item in pool)

    def dispense_from_pool_single(pipette, pool, dest, total_volume_ul, pool_label):
        remaining_total = float(total_volume_ul)
        if remaining_total <= 0:
            return
        if pool_available(pool) + 0.0001 < remaining_total:
            raise RuntimeError('Insufficient liquid in ' + pool_label + '. Shortfall: ' + str(round(remaining_total - pool_available(pool), 2)) + ' uL.')
        while remaining_total > 0.0001:
            source_item = None
            for item in pool:
                if item['remaining'] - dead_volume_ul > 0.0001:
                    source_item = item
                    break
            if source_item is None:
                raise RuntimeError('Insufficient liquid in ' + pool_label + '. Shortfall: ' + str(round(remaining_total, 2)) + ' uL.')
            available = source_item['remaining'] - dead_volume_ul
            take_from_this_well = min(available, remaining_total)
            while take_from_this_well > 0.0001:
                chunk = min(pipette.max_volume, take_from_this_well)
                pipette.aspirate(chunk, source_item['well'])
                pipette.dispense(chunk, dest)
                source_item['remaining'] -= chunk
                take_from_this_well -= chunk
                remaining_total -= chunk

    def dispense_from_pool_multi_to_reservoir(pipette, pool, dest, total_volume_ul, pool_label):
        remaining_total = float(total_volume_ul)
        if remaining_total <= 0:
            return
        if pool_available(pool) + 0.0001 < remaining_total:
            raise RuntimeError('Insufficient liquid in ' + pool_label + '. Shortfall: ' + str(round(remaining_total - pool_available(pool), 2)) + ' uL.')
        while remaining_total > 0.0001:
            source_item = None
            for item in pool:
                if item['remaining'] - dead_volume_ul >= 8.0:
                    source_item = item
                    break
            if source_item is None:
                raise RuntimeError('Insufficient liquid in ' + pool_label + '. Shortfall: ' + str(round(remaining_total, 2)) + ' uL.')
            available_total = source_item['remaining'] - dead_volume_ul
            take_total = min(available_total, remaining_total)
            while take_total > 0.0001:
                per_channel_chunk = min(pipette.max_volume, take_total / 8.0)
                if per_channel_chunk <= 0.0001:
                    break
                pipette.aspirate(per_channel_chunk, source_item['well'])
                pipette.dispense(per_channel_chunk, dest)
                consumed_total = per_channel_chunk * 8.0
                source_item['remaining'] -= consumed_total
                take_total -= consumed_total
                remaining_total -= consumed_total

    # -------------------------
    # Step 2: prepare 1x salt buffers in Reservoir 3.
    # -------------------------
    reservoir3_targets = []
    reservoir3_recipes = []
    dest_index = 0
    for salt_conc in salt_concentrations:
        high_fraction = salt_conc / salt_stock_concentration
        if high_fraction < -0.0001 or high_fraction > 1.0001:
            raise RuntimeError('Requested salt concentration ' + str(salt_conc) + ' exceeds the salt stock concentration.')
        high_volume = max(0.0, min(salt_buffer_target_total_ul, high_fraction * salt_buffer_target_total_ul))
        low_volume = salt_buffer_target_total_ul - high_volume
        for _rep in range(replicates):
            target = reservoir3.wells()[dest_index]
            reservoir3_targets.append(target)
            reservoir3_recipes.append((target, low_volume, high_volume))
            dest_index += 1

    p300_multi.pick_up_tip()
    for target, low_volume, _high_volume in reservoir3_recipes:
        dispense_from_pool_multi_to_reservoir(p300_multi, low_salt_pool, target, low_volume, 'low salt buffer pool')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target, _low_volume, high_volume in reservoir3_recipes:
        dispense_from_pool_multi_to_reservoir(p300_multi, high_salt_pool, target, high_volume, 'high salt buffer pool')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target in reservoir3_targets:
        p300_multi.mix(5, 300.0, target)
    p300_multi.drop_tip()

    # -------------------------
    # Step 3: prepare 2x salt buffers in Reservoir 4.
    # -------------------------
    reservoir4_targets = []
    reservoir4_recipes = []
    for index, salt_conc in enumerate(salt_concentrations):
        two_x_salt_conc = 2.0 * salt_conc
        high_fraction = two_x_salt_conc / salt_stock_concentration
        if high_fraction < -0.0001 or high_fraction > 1.0001:
            raise RuntimeError('Requested 2x salt concentration ' + str(two_x_salt_conc) + ' exceeds the salt stock concentration.')
        high_volume = max(0.0, min(salt_buffer_target_total_ul, high_fraction * salt_buffer_target_total_ul))
        low_volume = salt_buffer_target_total_ul - high_volume
        target = reservoir4.wells()[index]
        reservoir4_targets.append(target)
        reservoir4_recipes.append((target, low_volume, high_volume))

    p300_multi.pick_up_tip()
    for target, low_volume, _high_volume in reservoir4_recipes:
        dispense_from_pool_multi_to_reservoir(p300_multi, low_salt_pool, target, low_volume, 'low salt buffer pool')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target, _low_volume, high_volume in reservoir4_recipes:
        dispense_from_pool_multi_to_reservoir(p300_multi, high_salt_pool, target, high_volume, 'high salt buffer pool')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target in reservoir4_targets:
        p300_multi.mix(5, 300.0, target)
    p300_multi.drop_tip()

    # -------------------------
    # Step 4: prepare 2x ligand dilutions in the deep-well mixing plate.
    # Rows A-H correspond to ascending ligand concentrations, columns to salt concentrations.
    # -------------------------
    target_wells = []
    high_stock_additions = []
    low_stock_additions = []
    low_buffer_additions = []

    for ligand_row_index, ligand_conc in enumerate(ligand_concentrations):
        target_ligand_conc = 2.0 * ligand_conc
        high_stock_volume = 0.0 if target_ligand_conc == 0 else target_ligand_conc / ligand_stock_concentration * ligand_well_total_ul
        use_low_ligand_stock = target_ligand_conc > 0 and high_stock_volume < 20.0
        if use_low_ligand_stock:
            stock_concentration_used = ligand_stock_concentration / 10.0
            stock_volume = target_ligand_conc / stock_concentration_used * ligand_well_total_ul
            stock_pool = low_ligand_stock_pool
        else:
            stock_volume = high_stock_volume
            stock_pool = high_ligand_stock_pool
        if stock_volume - ligand_well_total_ul > 0.0001:
            raise RuntimeError('Ligand concentration ' + str(ligand_conc) + ' cannot be prepared because the required stock volume exceeds the target well volume.')
        buffer_volume = ligand_well_total_ul - stock_volume
        for salt_col_index in range(len(salt_concentrations)):
            dest = mixing_plate.rows()[ligand_row_index][salt_col_index]
            target_wells.append(dest)
            low_buffer_additions.append((dest, buffer_volume))
            if stock_volume > 0:
                if stock_pool is high_ligand_stock_pool:
                    high_stock_additions.append((dest, stock_volume))
                else:
                    low_stock_additions.append((dest, stock_volume))

    p300_single.pick_up_tip()
    for dest, buffer_volume in low_buffer_additions:
        dispense_from_pool_single(p300_single, low_salt_pool, dest, buffer_volume, 'low salt buffer pool')
    p300_single.drop_tip()

    if high_stock_additions:
        p300_single.pick_up_tip()
        for dest, stock_volume in high_stock_additions:
            dispense_from_pool_single(p300_single, high_ligand_stock_pool, dest, stock_volume, 'high ligand stock pool')
        p300_single.drop_tip()

    if low_stock_additions:
        p300_single.pick_up_tip()
        for dest, stock_volume in low_stock_additions:
            dispense_from_pool_single(p300_single, low_ligand_stock_pool, dest, stock_volume, 'low ligand stock pool')
        p300_single.drop_tip()

    p300_single.pick_up_tip()
    mix_volume = min(200.0, max(1.0, ligand_well_total_ul * 0.8))
    for dest in target_wells:
        p300_single.mix(3, mix_volume, dest)
    p300_single.drop_tip()

    protocol.comment('Protocol complete. Salt buffers and ligand dilution mixing plate have been prepared with placeholder-controlled parameters.')
