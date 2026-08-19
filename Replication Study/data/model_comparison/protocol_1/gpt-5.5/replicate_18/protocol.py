from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare templated salt buffer reservoirs and ligand dilutions using placeholders.'
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
        message = str(exc)
        if ('not found' not in message) and ('FileNotFoundError' not in message) and ('No such file' not in message):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a 96-well standard plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # empty; receives 2x salt buffers
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # empty; receives replicate salt buffers
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # low/high salt stocks
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # ligand stocks plus buffers
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # low salt buffer
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # pipettes
    # The single-channel pipette starts with the requested slot 10 rack; slot 4 is loaded as backup if a fully nonzero 96-well ligand plan needs more than 96 tips.
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single, tiprack_slot4])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # placeholder-backed parameters; defaults are simulation-only worst-case values that fit the stated layout.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 1000.0, float)
    salt_concentrations = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0.0, 40.0, 80.0, 120.0, 160.0, 200.0, 240.0, 280.0, 320.0, 360.0, 400.0, 450.0],
        float
    )
    ligand_concentrations = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0],
        float
    )
    salt_stock_concentration = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_concentration = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    number_of_salt_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, 12, int)
    number_of_ligand_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, 8, int)

    salt_concentrations = sorted(salt_concentrations)
    ligand_concentrations = sorted(ligand_concentrations)

    if number_of_salt_concentrations != len(salt_concentrations):
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS does not match SALT_CONCENTRATIONS list length.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match LIGAND_CONCENTRATIONS list length.')
    if replicates < 1:
        raise ValueError('REPLICATES must be at least 1.')
    if len(salt_concentrations) > 12:
        raise ValueError('At most 12 salt concentrations can be prepared in Reservoir 4 and mixing plate columns.')
    if len(ligand_concentrations) > 8:
        raise ValueError('At most 8 ligand concentrations can be prepared row-wise in the 96-well mixing plate.')
    if replicates * len(salt_concentrations) > 12:
        raise ValueError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed the 12 wells of Reservoir 3.')
    if salt_stock_concentration <= 0:
        raise ValueError('SALT_STOCK_CONCENTRATION must be positive.')
    if ligand_stock_concentration <= 0:
        raise ValueError('LIGAND_STOCK_CONCENTRATION must be positive.')

    # source liquid bookkeeping in physical microliters. A multichannel move of V uL per channel
    # removes 8 x V uL from a single-row reservoir well, so the bookkeeping uses actual well volume.
    dead_volume = 100.0

    def pool_entry(well, volume_ul, label):
        return {'well': well, 'remaining': float(volume_ul), 'label': label}

    low_salt_pool = []
    high_salt_pool = []
    for index in range(12):
        low_salt_pool.append(pool_entry(reservoir_0.wells()[index], 14000.0, 'low salt buffer'))
    for index in range(6):
        low_salt_pool.append(pool_entry(reservoir_2.wells()[index], 14000.0, 'low salt buffer'))
    for index in range(2, 7):
        low_salt_pool.append(pool_entry(reservoir_1.wells()[index], 14000.0, 'low salt buffer'))
    for index in range(6, 12):
        high_salt_pool.append(pool_entry(reservoir_2.wells()[index], 14000.0, 'high salt buffer'))
    for index in range(7, 12):
        high_salt_pool.append(pool_entry(reservoir_1.wells()[index], 14000.0, 'high salt buffer'))

    ligand_high = pool_entry(reservoir_1.wells()[0], 14000.0, 'high ligand stock')
    ligand_low = pool_entry(reservoir_1.wells()[1], 14000.0, 'low ligand stock')

    def consume_from_pool(pool, requested_ul, label):
        remaining_request = float(requested_ul)
        pieces = []
        if remaining_request <= 0:
            return pieces
        for entry in pool:
            available = max(0.0, entry['remaining'] - dead_volume)
            if available <= 0:
                continue
            take = min(available, remaining_request)
            entry['remaining'] -= take
            pieces.append((entry['well'], take))
            remaining_request -= take
            if remaining_request <= 0.000001:
                return pieces
        raise RuntimeError('Insufficient %s: short by %.1f uL after checking the full reagent pool.' % (label, remaining_request))

    def consume_from_entry(entry, requested_ul):
        requested_ul = float(requested_ul)
        if requested_ul <= 0:
            return []
        available = max(0.0, entry['remaining'] - dead_volume)
        if requested_ul > available + 0.000001:
            raise RuntimeError('Insufficient %s: requested %.1f uL, available %.1f uL.' % (entry['label'], requested_ul, available))
        entry['remaining'] -= requested_ul
        return [(entry['well'], requested_ul)]

    def dispense_multi_actual(pipette, pieces, dest_well):
        for source_well, actual_volume in pieces:
            remaining_actual = float(actual_volume)
            while remaining_actual > 0.000001:
                chunk_actual = min(remaining_actual, pipette.max_volume * 8.0)
                per_channel_volume = chunk_actual / 8.0
                pipette.aspirate(per_channel_volume, source_well.bottom(2))
                pipette.dispense(per_channel_volume, dest_well.bottom(5))
                remaining_actual -= chunk_actual

    def dispense_single_actual(pipette, pieces, dest_well):
        for source_well, actual_volume in pieces:
            remaining_actual = float(actual_volume)
            while remaining_actual > 0.000001:
                chunk = min(remaining_actual, pipette.max_volume)
                pipette.aspirate(chunk, source_well.bottom(2))
                pipette.dispense(chunk, dest_well.bottom(4))
                remaining_actual -= chunk

    # Step 2 and Step 3 calculations.
    reservoir_3_plan = []
    reservoir_3_index = 0
    for salt_conc in salt_concentrations:
        high_volume = 10000.0 * salt_conc / salt_stock_concentration
        low_volume = 10000.0 - high_volume
        if high_volume < -0.000001 or low_volume < -0.000001:
            raise ValueError('Salt concentration %.4g is above the salt stock concentration.' % salt_conc)
        for rep in range(replicates):
            reservoir_3_plan.append({'well': reservoir_3.wells()[reservoir_3_index], 'low': low_volume, 'high': high_volume, 'salt': salt_conc})
            reservoir_3_index += 1

    reservoir_4_plan = []
    for index, salt_conc in enumerate(salt_concentrations):
        target_2x = 2.0 * salt_conc
        high_volume = 10000.0 * target_2x / salt_stock_concentration
        low_volume = 10000.0 - high_volume
        if high_volume < -0.000001 or low_volume < -0.000001:
            raise ValueError('Two-times salt concentration %.4g is above the salt stock concentration.' % target_2x)
        reservoir_4_plan.append({'well': reservoir_4.wells()[index], 'low': low_volume, 'high': high_volume, 'salt': target_2x})

    all_salt_plans = reservoir_3_plan + reservoir_4_plan

    # Step 2 and 3 execution: add all low salt buffer, then all high salt buffer, then mix completed wells.
    protocol.comment('Preparing Reservoir 3 replicate salt buffers and Reservoir 4 two-times salt buffers with the multi-channel pipette.')
    p300_multi.pick_up_tip()
    for item in all_salt_plans:
        if item['low'] > 0:
            pieces = consume_from_pool(low_salt_pool, item['low'], 'low salt buffer')
            dispense_multi_actual(p300_multi, pieces, item['well'])
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for item in all_salt_plans:
        if item['high'] > 0:
            pieces = consume_from_pool(high_salt_pool, item['high'], 'high salt buffer')
            dispense_multi_actual(p300_multi, pieces, item['well'])
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for item in all_salt_plans:
        p300_multi.mix(3, 250, item['well'].bottom(5))
    p300_multi.drop_tip()

    # Step 4 calculations for ligand dilution plate.
    ligand_final_volume = total_volume / 2.0 * replicates * 1.5
    if ligand_final_volume <= 0:
        raise ValueError('Calculated ligand dilution volume must be positive.')
    if ligand_final_volume > 2000.0:
        raise ValueError('Calculated ligand dilution volume %.1f uL exceeds the 2 mL deep-well capacity.' % ligand_final_volume)

    ligand_plan = []
    for row_index, ligand_conc in enumerate(ligand_concentrations):
        target_2x_ligand = 2.0 * ligand_conc
        high_stock_volume = 0.0 if target_2x_ligand == 0 else ligand_final_volume * target_2x_ligand / ligand_stock_concentration
        if high_stock_volume >= 20.0 or target_2x_ligand == 0:
            stock_entry = ligand_high
            stock_concentration_used = ligand_stock_concentration
            stock_volume = high_stock_volume
        else:
            stock_entry = ligand_low
            stock_concentration_used = ligand_stock_concentration / 10.0
            stock_volume = ligand_final_volume * target_2x_ligand / stock_concentration_used
        buffer_volume = ligand_final_volume - stock_volume
        if stock_volume < -0.000001 or buffer_volume < -0.000001:
            raise ValueError('Ligand concentration %.4g cannot be made from the selected stock concentration.' % ligand_conc)
        for column_index in range(len(salt_concentrations)):
            dest = mixing_plate.rows()[row_index][column_index]
            ligand_plan.append({
                'well': dest,
                'stock_entry': stock_entry,
                'stock_volume': stock_volume,
                'buffer_volume': buffer_volume,
                'ligand': ligand_conc,
                'stock_concentration_used': stock_concentration_used
            })

    # Step 4 execution: add low salt buffer to all ligand wells, then add ligand stock with fresh tips for stock additions and mix.
    protocol.comment('Preparing two-times ligand dilution series in the deep-well mixing plate with the single-channel pipette.')
    p300_single.pick_up_tip()
    for item in ligand_plan:
        if item['buffer_volume'] > 0:
            pieces = consume_from_pool(low_salt_pool, item['buffer_volume'], 'low salt buffer')
            dispense_single_actual(p300_single, pieces, item['well'])
    p300_single.drop_tip()

    mix_volume = min(300.0, max(20.0, ligand_final_volume * 0.5))
    for item in ligand_plan:
        if item['stock_volume'] > 0:
            p300_single.pick_up_tip()
            pieces = consume_from_entry(item['stock_entry'], item['stock_volume'])
            dispense_single_actual(p300_single, pieces, item['well'])
            p300_single.mix(3, mix_volume, item['well'].bottom(3))
            p300_single.drop_tip()

    protocol.comment('Protocol complete. Custom filter plate was loaded for deck consistency but is not used in the requested preparation steps.')
