from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare salt buffers in reservoirs and ligand dilutions in a deep-well mixing plate using templated placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # -----------------------------
    # Placeholder declarations
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
            return cast(default)
        return cast(float(s))

    def parse_list(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return [cast(x) for x in default]
        return [cast(x.strip()) for x in s.split(';') if x.strip()]

    # Worst-case simulation defaults: 12 salt columns/targets and 8 ligand rows.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 1000.0, float)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 10000.0, float)
    salt_concs = sorted(parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110],
        float
    ))
    ligand_concs = sorted(parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0, 10, 20, 30, 40, 50, 60, 70],
        float
    ))
    number_of_salt_concs = parse_scalar(
        PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concs), int
    )
    number_of_ligand_concs = parse_scalar(
        PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concs), int
    )

    if number_of_salt_concs != len(salt_concs):
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS does not match SALT_CONCENTRATIONS.')
    if number_of_ligand_concs != len(ligand_concs):
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match LIGAND_CONCENTRATIONS.')
    if replicates < 1:
        raise ValueError('REPLICATES must be at least 1.')
    if len(salt_concs) * replicates > 12:
        raise ValueError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if len(salt_concs) > 12:
        raise ValueError('At most 12 salt concentrations can be represented as reservoir wells / mixing plate columns.')
    if len(ligand_concs) > 8:
        raise ValueError('At most 8 ligand concentrations can be represented in rows A-H of the mixing plate.')
    if salt_stock_conc <= 0 or ligand_stock_conc <= 0:
        raise ValueError('Stock concentrations must be greater than 0.')
    if total_volume <= 0:
        raise ValueError('TOTAL_VOLUME must be greater than 0.')

    mixing_well_total = total_volume / 2.0 * replicates * 1.5
    if mixing_well_total > 2000:
        raise ValueError('Calculated mixing-plate volume per well exceeds the 2 mL well capacity.')

    # -----------------------------
    # Labware
    # -----------------------------
    tiprack_single_extra = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc).lower():
            raise
        protocol.comment('WARNING: custom labware definition not available; using a 96-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # empty; 2x salt buffers
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # empty; salt buffers with replicates
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # low/high salt stocks
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # ligand stocks + buffers
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # low salt buffer
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -----------------------------
    # Pipettes
    # -----------------------------
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_single, tiprack_single_extra])
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_multi])

    # -----------------------------
    # Liquid definitions and initial deck liquids
    # -----------------------------
    low_salt_liquid = protocol.define_liquid(
        name='Low salt buffer',
        description='0 salt buffer',
        display_color='#5DADE2'
    )
    high_salt_liquid = protocol.define_liquid(
        name='High salt buffer',
        description='High salt stock buffer',
        display_color='#1F618D'
    )
    ligand_high_liquid = protocol.define_liquid(
        name='Ligand stock high',
        description='Ligand stock solution at high concentration',
        display_color='#AF7AC5'
    )
    ligand_low_liquid = protocol.define_liquid(
        name='Ligand stock low',
        description='Ligand stock solution at one-tenth concentration',
        display_color='#D7BDE2'
    )

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

    # -----------------------------
    # Reagent pools with explicit remaining-volume tracking, in actual uL per reservoir well.
    # Multi-channel operations on a reservoir well remove/deposit 8 x the passed per-channel volume.
    # -----------------------------
    def make_pool(wells, start_volume=14000.0, dead_volume=200.0):
        return [{'well': well, 'remaining': float(start_volume), 'dead': float(dead_volume)} for well in wells]

    low_salt_pool = make_pool(
        reservoir_2.wells()[:6] + reservoir_1.wells()[2:7] + reservoir_0.wells(),
        14000.0,
        200.0
    )
    high_salt_pool = make_pool(
        reservoir_2.wells()[6:12] + reservoir_1.wells()[7:12],
        14000.0,
        200.0
    )
    ligand_high_pool = make_pool([reservoir_1.wells()[0]], 14000.0, 20.0)
    ligand_low_pool = make_pool([reservoir_1.wells()[1]], 14000.0, 20.0)

    def pool_available(pool):
        return sum(max(0.0, item['remaining'] - item['dead']) for item in pool)

    def consume_multi_pool(pipette, pool, dest, actual_total_ul, pool_name):
        remaining = float(actual_total_ul)
        if remaining < -0.000001:
            raise ValueError('Negative transfer volume requested from ' + pool_name)
        while remaining > 0.000001:
            source_item = None
            for item in pool:
                if item['remaining'] - item['dead'] > 0.000001:
                    source_item = item
                    break
            if source_item is None:
                shortfall = remaining
                raise RuntimeError(pool_name + ' pool exhausted; shortfall %.2f uL.' % shortfall)

            available_actual = source_item['remaining'] - source_item['dead']
            move_actual = min(remaining, available_actual, pipette.max_volume * 8.0)
            per_channel = move_actual / 8.0
            pipette.aspirate(per_channel, source_item['well'])
            pipette.dispense(per_channel, dest)
            source_item['remaining'] -= move_actual
            remaining -= move_actual

    def consume_single_pool(pipette, pool, dest, actual_total_ul, pool_name):
        remaining = float(actual_total_ul)
        if remaining < -0.000001:
            raise ValueError('Negative transfer volume requested from ' + pool_name)
        while remaining > 0.000001:
            source_item = None
            for item in pool:
                if item['remaining'] - item['dead'] > 0.000001:
                    source_item = item
                    break
            if source_item is None:
                shortfall = remaining
                raise RuntimeError(pool_name + ' pool exhausted; shortfall %.2f uL.' % shortfall)

            available_actual = source_item['remaining'] - source_item['dead']
            move_actual = min(remaining, available_actual, pipette.max_volume)
            pipette.aspirate(move_actual, source_item['well'])
            pipette.dispense(move_actual, dest)
            source_item['remaining'] -= move_actual
            remaining -= move_actual

    def salt_component_volumes(final_salt_conc, final_total_ul):
        high = final_total_ul * final_salt_conc / salt_stock_conc
        low = final_total_ul - high
        if high < -0.000001 or low < -0.000001:
            raise ValueError('Requested salt concentration cannot be made from the supplied salt stock concentration.')
        return max(0.0, low), max(0.0, high)

    # -----------------------------
    # Step 2: Prepare replicated salt buffers in Reservoir 3, ascending by well number.
    # Each target reservoir well receives 10 mL total actual liquid.
    # -----------------------------
    protocol.comment('Step 2: Preparing replicated salt buffers in Reservoir 3.')
    reservoir_3_targets = []
    reservoir_3_formulas = []
    for salt_conc in salt_concs:
        for _rep in range(replicates):
            target_index = len(reservoir_3_targets)
            reservoir_3_targets.append(reservoir_3.wells()[target_index])
            reservoir_3_formulas.append(salt_component_volumes(salt_conc, 10000.0))

    p300_multi.pick_up_tip()
    for target, formula in zip(reservoir_3_targets, reservoir_3_formulas):
        low_vol, _high_vol = formula
        if low_vol > 0:
            consume_multi_pool(p300_multi, low_salt_pool, target, low_vol, 'Low salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target, formula in zip(reservoir_3_targets, reservoir_3_formulas):
        _low_vol, high_vol = formula
        if high_vol > 0:
            consume_multi_pool(p300_multi, high_salt_pool, target, high_vol, 'High salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target in reservoir_3_targets:
        p300_multi.mix(5, 250, target)
        p300_multi.blow_out(target)
    p300_multi.drop_tip()

    # -----------------------------
    # Step 3: Prepare one 2x salt buffer per salt concentration in Reservoir 4.
    # Each target reservoir well receives 10 mL total actual liquid.
    # -----------------------------
    protocol.comment('Step 3: Preparing 2x salt buffers in Reservoir 4.')
    reservoir_4_targets = reservoir_4.wells()[:len(salt_concs)]
    reservoir_4_formulas = [salt_component_volumes(2.0 * salt_conc, 10000.0) for salt_conc in salt_concs]

    p300_multi.pick_up_tip()
    for target, formula in zip(reservoir_4_targets, reservoir_4_formulas):
        low_vol, _high_vol = formula
        if low_vol > 0:
            consume_multi_pool(p300_multi, low_salt_pool, target, low_vol, 'Low salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target, formula in zip(reservoir_4_targets, reservoir_4_formulas):
        _low_vol, high_vol = formula
        if high_vol > 0:
            consume_multi_pool(p300_multi, high_salt_pool, target, high_vol, 'High salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target in reservoir_4_targets:
        p300_multi.mix(5, 250, target)
        p300_multi.blow_out(target)
    p300_multi.drop_tip()

    protocol.comment('Remaining low salt buffer usable volume: %.2f uL.' % pool_available(low_salt_pool))
    protocol.comment('Remaining high salt buffer usable volume: %.2f uL.' % pool_available(high_salt_pool))

    # -----------------------------
    # Step 4: Prepare 2x ligand dilutions in the deep-well mixing plate.
    # Rows A-H correspond to ascending ligand concentrations; columns correspond to salt concentrations.
    # Dilutions are prepared with low salt buffer.
    # -----------------------------
    protocol.comment('Step 4: Preparing ligand dilution matrix in the deep-well mixing plate.')
    ligand_targets_by_row = []
    ligand_formulas_by_row = []
    for row_index, ligand_conc in enumerate(ligand_concs):
        final_ligand_conc = 2.0 * ligand_conc
        high_stock_volume = mixing_well_total * final_ligand_conc / ligand_stock_conc
        if high_stock_volume < 20.0 and final_ligand_conc > 0:
            stock_pool = ligand_low_pool
            stock_name = 'Low ligand stock'
            stock_conc = ligand_stock_conc / 10.0
            stock_volume = mixing_well_total * final_ligand_conc / stock_conc
        else:
            stock_pool = ligand_high_pool
            stock_name = 'High ligand stock'
            stock_volume = high_stock_volume
        if stock_volume > mixing_well_total + 0.000001:
            raise ValueError('Ligand concentration cannot be made because required stock volume exceeds total well volume.')
        low_buffer_volume = mixing_well_total - stock_volume
        row_targets = mixing_plate.rows()[row_index][:len(salt_concs)]
        ligand_targets_by_row.append(row_targets)
        ligand_formulas_by_row.append({
            'stock_pool': stock_pool,
            'stock_name': stock_name,
            'stock_volume': max(0.0, stock_volume),
            'low_buffer_volume': max(0.0, low_buffer_volume)
        })

    p300_single.pick_up_tip()
    for row_targets, formula in zip(ligand_targets_by_row, ligand_formulas_by_row):
        low_buffer_volume = formula['low_buffer_volume']
        if low_buffer_volume > 0:
            for target in row_targets:
                consume_single_pool(p300_single, low_salt_pool, target, low_buffer_volume, 'Low salt buffer')
    p300_single.drop_tip()

    for row_targets, formula in zip(ligand_targets_by_row, ligand_formulas_by_row):
        stock_volume = formula['stock_volume']
        if stock_volume > 0:
            p300_single.pick_up_tip()
            for target in row_targets:
                consume_single_pool(
                    p300_single,
                    formula['stock_pool'],
                    target,
                    stock_volume,
                    formula['stock_name']
                )
            p300_single.drop_tip()

    for row_targets in ligand_targets_by_row:
        p300_single.pick_up_tip()
        mix_volume = min(250.0, mixing_well_total / 2.0)
        for target in row_targets:
            if mix_volume > 0:
                p300_single.mix(5, mix_volume, target)
                p300_single.blow_out(target)
        p300_single.drop_tip()

    protocol.comment('Ligand dilution preparation complete.')
    protocol.comment('Remaining low salt buffer usable volume: %.2f uL.' % pool_available(low_salt_pool))
    protocol.comment('Remaining high ligand stock usable volume: %.2f uL.' % pool_available(ligand_high_pool))
    protocol.comment('Remaining low ligand stock usable volume: %.2f uL.' % pool_available(ligand_low_pool))
