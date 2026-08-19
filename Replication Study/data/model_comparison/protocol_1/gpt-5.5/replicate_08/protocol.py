from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare templated salt buffers and ligand dilution series using placeholders.'
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


def run(protocol):
    # labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        msg = str(exc).lower()
        if 'not found' not in msg and 'filenotfounderror' not in msg:
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
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # placeholders and simulation fallbacks
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 1000.0, float)
    salt_stock_concentration = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_concentration = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    salt_concentrations = sorted(parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 40, 80, 120, 160, 200, 240, 280, 320, 360, 400, 440], float))
    ligand_concentrations = sorted(parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [0, 10, 20, 30, 40, 50, 60, 70], float))
    number_of_salt_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concentrations), int)
    number_of_ligand_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concentrations), int)

    if number_of_salt_concentrations != len(salt_concentrations):
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS must match the count in SALT_CONCENTRATIONS.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS must match the count in LIGAND_CONCENTRATIONS.')
    if replicates * number_of_salt_concentrations > 12:
        raise ValueError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if number_of_salt_concentrations > 12:
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 4 and the mixing plate columns.')
    if number_of_ligand_concentrations > 8:
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS must not exceed 8 for rows A-H of the mixing plate.')
    if salt_stock_concentration <= 0 or ligand_stock_concentration <= 0:
        raise ValueError('Stock concentrations must be greater than zero.')

    # liquid definitions and initial liquid labels
    low_salt_liquid = protocol.define_liquid('Low salt buffer', '0 salt buffer', '#88CCFF')
    high_salt_liquid = protocol.define_liquid('High salt buffer', 'Salt stock buffer', '#3366FF')
    ligand_high_liquid = protocol.define_liquid('Ligand stock high', 'High concentration ligand stock', '#CC66FF')
    ligand_low_liquid = protocol.define_liquid('Ligand stock low', 'Low concentration ligand stock', '#FF99CC')

    initial_reservoir_volume = 14000.0
    dead_volume = 50.0

    # Low salt in Reservoir 2 wells 0-5
    for well in reservoir_2.wells()[:6]:
        well.load_liquid(low_salt_liquid, initial_reservoir_volume)
    # High salt in Reservoir 2 wells 6-11
    for well in reservoir_2.wells()[6:12]:
        well.load_liquid(high_salt_liquid, initial_reservoir_volume)
    # Ligand and buffers in Reservoir 1
    reservoir_1.wells()[0].load_liquid(ligand_high_liquid, initial_reservoir_volume)
    reservoir_1.wells()[1].load_liquid(ligand_low_liquid, initial_reservoir_volume)
    for well in reservoir_1.wells()[2:7]:
        well.load_liquid(low_salt_liquid, initial_reservoir_volume)
    for well in reservoir_1.wells()[7:12]:
        well.load_liquid(high_salt_liquid, initial_reservoir_volume)
    # Low salt in Reservoir 0 wells 0-11
    for well in reservoir_0.wells():
        well.load_liquid(low_salt_liquid, initial_reservoir_volume)

    low_salt_pool = []
    for well in reservoir_2.wells()[:6] + reservoir_1.wells()[2:7] + reservoir_0.wells():
        low_salt_pool.append({'well': well, 'remaining': initial_reservoir_volume - dead_volume})
    high_salt_pool = []
    for well in reservoir_2.wells()[6:12] + reservoir_1.wells()[7:12]:
        high_salt_pool.append({'well': well, 'remaining': initial_reservoir_volume - dead_volume})
    ligand_high_pool = [{'well': reservoir_1.wells()[0], 'remaining': initial_reservoir_volume - dead_volume}]
    ligand_low_pool = [{'well': reservoir_1.wells()[1], 'remaining': initial_reservoir_volume - dead_volume}]

    def _pool_remaining(pool):
        return sum(entry['remaining'] for entry in pool)

    def move_from_pool_multi(pool, dest_well, actual_volume_ul):
        # actual_volume_ul is total liquid delivered to the one reservoir well.
        # The P300 multi-channel API volume is per channel, so each move of V removes/delivers 8 x V.
        remaining_to_move = float(actual_volume_ul)
        if remaining_to_move <= 0:
            return
        if _pool_remaining(pool) + 1e-6 < remaining_to_move:
            raise RuntimeError('Insufficient multi-channel reagent pool volume. Shortfall: %.2f uL.' % (remaining_to_move - _pool_remaining(pool)))
        for entry in pool:
            while entry['remaining'] > 1e-6 and remaining_to_move > 1e-6:
                actual_chunk = min(entry['remaining'], remaining_to_move, p300_multi.max_volume * 8.0)
                per_channel_chunk = actual_chunk / 8.0
                p300_multi.aspirate(per_channel_chunk, entry['well'])
                p300_multi.dispense(per_channel_chunk, dest_well)
                entry['remaining'] -= actual_chunk
                remaining_to_move -= actual_chunk
            if remaining_to_move <= 1e-6:
                break

    def move_from_pool_single(pool, dest_well, volume_ul):
        remaining_to_move = float(volume_ul)
        if remaining_to_move <= 0:
            return
        if _pool_remaining(pool) + 1e-6 < remaining_to_move:
            raise RuntimeError('Insufficient single-channel reagent pool volume. Shortfall: %.2f uL.' % (remaining_to_move - _pool_remaining(pool)))
        for entry in pool:
            while entry['remaining'] > 1e-6 and remaining_to_move > 1e-6:
                chunk = min(entry['remaining'], remaining_to_move, p300_single.max_volume)
                p300_single.aspirate(chunk, entry['well'])
                p300_single.dispense(chunk, dest_well)
                entry['remaining'] -= chunk
                remaining_to_move -= chunk
            if remaining_to_move <= 1e-6:
                break

    # calculate Reservoir 3 and 4 target recipes
    reservoir3_targets = []
    next_res3_well = 0
    for salt_conc in salt_concentrations:
        if salt_conc < 0:
            raise ValueError('Salt concentrations must be non-negative.')
        if salt_conc > salt_stock_concentration:
            raise ValueError('A Reservoir 3 target salt concentration exceeds the salt stock concentration.')
        high_vol = 10000.0 * salt_conc / salt_stock_concentration
        low_vol = 10000.0 - high_vol
        for _rep in range(replicates):
            reservoir3_targets.append({'well': reservoir_3.wells()[next_res3_well], 'low': low_vol, 'high': high_vol, 'salt': salt_conc})
            next_res3_well += 1

    reservoir4_targets = []
    for index, salt_conc in enumerate(salt_concentrations):
        target_conc = 2.0 * salt_conc
        if target_conc > salt_stock_concentration:
            raise ValueError('A Reservoir 4 2x target salt concentration exceeds the salt stock concentration.')
        high_vol = 10000.0 * target_conc / salt_stock_concentration
        low_vol = 10000.0 - high_vol
        reservoir4_targets.append({'well': reservoir_4.wells()[index], 'low': low_vol, 'high': high_vol, 'salt': target_conc})

    all_salt_targets = reservoir3_targets + reservoir4_targets

    protocol.comment('Step 2/3: dispensing low salt buffer into Reservoir 3 and Reservoir 4 targets with the multi-channel pipette.')
    p300_multi.pick_up_tip()
    for target in all_salt_targets:
        move_from_pool_multi(low_salt_pool, target['well'], target['low'])
        if target['high'] <= 0:
            p300_multi.mix(3, 250, target['well'])
    p300_multi.drop_tip()

    protocol.comment('Step 2/3: dispensing high salt buffer into Reservoir 3 and Reservoir 4 targets and mixing completed salt buffers.')
    p300_multi.pick_up_tip()
    for target in all_salt_targets:
        move_from_pool_multi(high_salt_pool, target['well'], target['high'])
        p300_multi.mix(3, 250, target['well'])
    p300_multi.drop_tip()

    # Step 4 calculations: ligand dilution wells in the deep-well mixing plate.
    mixing_wells = []
    mixing_well_total = total_volume / 2.0 * replicates * 1.5
    if mixing_well_total <= 0:
        raise ValueError('Calculated mixing-plate well volume must be greater than zero.')
    if mixing_well_total > 1900:
        raise ValueError('Calculated mixing-plate well volume exceeds a conservative 1900 uL limit for the 2 mL deep-well plate.')

    for row_index, ligand_conc in enumerate(ligand_concentrations):
        if ligand_conc < 0:
            raise ValueError('Ligand concentrations must be non-negative.')
        target_ligand_conc = 2.0 * ligand_conc
        high_stock_vol = mixing_well_total * target_ligand_conc / ligand_stock_concentration
        if high_stock_vol < 20.0 and ligand_conc > 0:
            stock_pool_name = 'low'
            stock_concentration = ligand_stock_concentration / 10.0
            stock_vol = mixing_well_total * target_ligand_conc / stock_concentration
        else:
            stock_pool_name = 'high'
            stock_concentration = ligand_stock_concentration
            stock_vol = high_stock_vol
        if stock_vol > mixing_well_total + 1e-6:
            raise ValueError('A requested 2x ligand concentration exceeds the selected ligand stock concentration.')
        buffer_vol = mixing_well_total - stock_vol
        for col_index in range(number_of_salt_concentrations):
            mixing_wells.append({
                'well': mixing_plate.rows()[row_index][col_index],
                'buffer': buffer_vol,
                'stock': stock_vol,
                'stock_pool': stock_pool_name,
                'ligand': ligand_conc
            })

    protocol.comment('Step 4: dispensing low salt buffer to ligand dilution wells in the mixing plate with the single-channel pipette.')
    p300_single.pick_up_tip()
    for target in mixing_wells:
        move_from_pool_single(low_salt_pool, target['well'], target['buffer'])
    p300_single.drop_tip()

    protocol.comment('Step 4: dispensing high-concentration ligand stock where required.')
    high_stock_targets = [target for target in mixing_wells if target['stock_pool'] == 'high' and target['stock'] > 0]
    if high_stock_targets:
        p300_single.pick_up_tip()
        for target in high_stock_targets:
            move_from_pool_single(ligand_high_pool, target['well'], target['stock'])
        p300_single.drop_tip()

    protocol.comment('Step 4: dispensing low-concentration ligand stock where high-stock transfer volume would be below 20 uL.')
    low_stock_targets = [target for target in mixing_wells if target['stock_pool'] == 'low' and target['stock'] > 0]
    if low_stock_targets:
        p300_single.pick_up_tip()
        for target in low_stock_targets:
            move_from_pool_single(ligand_low_pool, target['well'], target['stock'])
        p300_single.drop_tip()

    protocol.comment('Salt buffers and ligand dilution series have been prepared. The custom filter plate is loaded but not used by the specified steps.')
