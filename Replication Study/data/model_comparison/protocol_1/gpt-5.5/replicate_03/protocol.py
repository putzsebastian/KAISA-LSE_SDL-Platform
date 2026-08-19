from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare salt buffer reservoirs and ligand dilution mixing plate from placeholder parameters.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
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
            return cast(default)
        return cast(float(s))

    def parse_list(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return [cast(x) for x in default]
        return [cast(x.strip()) for x in s.split(';') if x.strip()]

    # Worst-case simulation fallbacks: 12 salt columns x 8 ligand rows, with 1 replicate
    # so Reservoir 3 exactly fits 12 wells.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)
    salt_concentrations = sorted(parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0, 360.0, 405.0, 450.0, 500.0],
        float
    ))
    ligand_concentrations = sorted(parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0],
        float
    ))
    salt_stock_concentration = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_concentration = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    number_of_salt_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, 12, int)
    number_of_ligand_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, 8, int)

    # -------------------------
    # Labware
    # -------------------------
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc).lower():
            raise
        protocol.comment('WARNING: custom labware definition cytiva_96_filterwellplate_1ml not available; using NEST 96 deep-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 1)

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # empty, for 2x salt buffers
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # empty, for replicated salt buffers
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # source reservoir
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # source reservoir with ligand stocks
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # source reservoir
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------
    # Pipettes
    # -------------------------
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_slot10, tiprack_slot4])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_slot7])

    # -------------------------
    # Validation and calculations
    # -------------------------
    if number_of_salt_concentrations != len(salt_concentrations):
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS must match the number of values in SALT_CONCENTRATIONS.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS must match the number of values in LIGAND_CONCENTRATIONS.')
    if replicates < 1:
        raise ValueError('REPLICATES must be at least 1.')
    if len(salt_concentrations) < 1 or len(salt_concentrations) > 12:
        raise ValueError('The number of salt concentrations must be between 1 and 12.')
    if len(ligand_concentrations) < 1 or len(ligand_concentrations) > 8:
        raise ValueError('The number of ligand concentrations must be between 1 and 8.')
    if replicates * len(salt_concentrations) > 12:
        raise ValueError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if salt_stock_concentration <= 0:
        raise ValueError('SALT_STOCK_CONCENTRATION must be greater than 0.')
    if ligand_stock_concentration <= 0:
        raise ValueError('LIGAND_STOCK_CONCENTRATION must be greater than 0.')
    if total_volume <= 0:
        raise ValueError('TOTAL_VOLUME must be greater than 0.')

    buffer_target_total_ul = 10000.0
    ligand_mix_total_ul = total_volume / 2.0 * replicates * 1.5
    if ligand_mix_total_ul > 1900.0:
        raise ValueError('Calculated ligand dilution volume exceeds safe capacity of the 2 mL deep-well plate.')

    # Source pools track TOTAL liquid in each well. For multi-channel moves involving a single-row
    # reservoir, the protocol passes per-channel uL to the API, but the pool removes 8x that amount.
    dead_volume_ul = 100.0

    def make_pool(wells, label):
        return [{'well': well, 'remaining': 14000.0, 'label': label + ' ' + well.well_name} for well in wells]

    low_salt_pool = []
    low_salt_pool += make_pool(reservoir2.wells()[0:6], 'Reservoir 2 low salt')
    low_salt_pool += make_pool(reservoir1.wells()[2:7], 'Reservoir 1 low salt')
    low_salt_pool += make_pool(reservoir0.wells()[0:12], 'Reservoir 0 low salt')

    high_salt_pool = []
    high_salt_pool += make_pool(reservoir2.wells()[6:12], 'Reservoir 2 high salt')
    high_salt_pool += make_pool(reservoir1.wells()[7:12], 'Reservoir 1 high salt')

    ligand_high_pool = make_pool([reservoir1.wells()[0]], 'Reservoir 1 high ligand stock')
    ligand_low_pool = make_pool([reservoir1.wells()[1]], 'Reservoir 1 low ligand stock')

    def consume_from_pool(pool, total_ul, label):
        available = sum(max(0.0, entry['remaining'] - dead_volume_ul) for entry in pool)
        if total_ul > available + 0.01:
            raise RuntimeError('Insufficient volume in ' + label + '; shortfall is ' + str(round(total_ul - available, 2)) + ' uL.')

    def multi_dispense_from_pool(pool, total_ul, dest, label):
        remaining_request = float(total_ul)
        consume_from_pool(pool, remaining_request, label)
        while remaining_request > 0.01:
            source_entry = None
            for entry in pool:
                if entry['remaining'] > dead_volume_ul + 0.01:
                    source_entry = entry
                    break
            if source_entry is None:
                raise RuntimeError('Insufficient volume in ' + label + ' while dispensing.')
            usable_total = source_entry['remaining'] - dead_volume_ul
            chunk_total = min(remaining_request, usable_total, p300m.max_volume * 8.0)
            per_channel = chunk_total / 8.0
            if per_channel <= 0:
                raise RuntimeError('Calculated non-positive multi-channel transfer volume for ' + label + '.')
            p300m.aspirate(per_channel, source_entry['well'].bottom(2))
            p300m.dispense(per_channel, dest.bottom(5))
            source_entry['remaining'] -= chunk_total
            remaining_request -= chunk_total

    def single_dispense_from_pool(pool, total_ul, dest, label):
        remaining_request = float(total_ul)
        consume_from_pool(pool, remaining_request, label)
        while remaining_request > 0.01:
            source_entry = None
            for entry in pool:
                if entry['remaining'] > dead_volume_ul + 0.01:
                    source_entry = entry
                    break
            if source_entry is None:
                raise RuntimeError('Insufficient volume in ' + label + ' while dispensing.')
            usable_total = source_entry['remaining'] - dead_volume_ul
            chunk = min(remaining_request, usable_total, p300s.max_volume)
            if chunk <= 0:
                raise RuntimeError('Calculated non-positive single-channel transfer volume for ' + label + '.')
            p300s.aspirate(chunk, source_entry['well'].bottom(2))
            p300s.dispense(chunk, dest.bottom(2))
            source_entry['remaining'] -= chunk
            remaining_request -= chunk

    # -------------------------
    # Steps 2 and 3: prepare salt buffers in Reservoirs 3 and 4
    # -------------------------
    reservoir3_plans = []
    next_well_index = 0
    for salt_conc in salt_concentrations:
        if salt_conc < 0 or salt_conc > salt_stock_concentration:
            raise ValueError('Each Reservoir 3 salt concentration must be between 0 and SALT_STOCK_CONCENTRATION.')
        high_total = buffer_target_total_ul * salt_conc / salt_stock_concentration
        low_total = buffer_target_total_ul - high_total
        for rep in range(replicates):
            reservoir3_plans.append({'dest': reservoir3.wells()[next_well_index], 'low': low_total, 'high': high_total})
            next_well_index += 1

    reservoir4_plans = []
    for index, salt_conc in enumerate(salt_concentrations):
        doubled_conc = 2.0 * salt_conc
        if doubled_conc < 0 or doubled_conc > salt_stock_concentration:
            raise ValueError('Each 2x Reservoir 4 salt concentration must be between 0 and SALT_STOCK_CONCENTRATION; reduce SALT_CONCENTRATIONS or increase SALT_STOCK_CONCENTRATION.')
        high_total = buffer_target_total_ul * doubled_conc / salt_stock_concentration
        low_total = buffer_target_total_ul - high_total
        reservoir4_plans.append({'dest': reservoir4.wells()[index], 'low': low_total, 'high': high_total})

    all_salt_plans = reservoir3_plans + reservoir4_plans

    protocol.comment('Dispensing low-salt buffer components for Reservoirs 3 and 4 with the P300 multi-channel pipette.')
    p300m.pick_up_tip()
    for plan in all_salt_plans:
        if plan['low'] > 0.01:
            multi_dispense_from_pool(low_salt_pool, plan['low'], plan['dest'], 'low salt buffer pool')
    p300m.drop_tip()

    protocol.comment('Dispensing high-salt buffer components for Reservoirs 3 and 4 with the P300 multi-channel pipette.')
    p300m.pick_up_tip()
    for plan in all_salt_plans:
        if plan['high'] > 0.01:
            multi_dispense_from_pool(high_salt_pool, plan['high'], plan['dest'], 'high salt buffer pool')
    p300m.drop_tip()

    protocol.comment('Mixing completed salt buffer wells in Reservoirs 3 and 4.')
    p300m.pick_up_tip()
    for plan in all_salt_plans:
        p300m.mix(5, 250, plan['dest'].bottom(5))
    p300m.drop_tip()

    # -------------------------
    # Step 4: ligand dilutions in the deep-well mixing plate
    # -------------------------
    ligand_plans = []
    low_ligand_stock_concentration = ligand_stock_concentration / 10.0
    for row_index, ligand_conc in enumerate(ligand_concentrations):
        if ligand_conc < 0:
            raise ValueError('Ligand concentrations must not be negative.')
        desired_ligand_conc = 2.0 * ligand_conc
        high_stock_vol = 0.0
        low_stock_vol = 0.0
        ligand_pool = None
        ligand_label = None
        if desired_ligand_conc > 0:
            high_stock_vol = ligand_mix_total_ul * desired_ligand_conc / ligand_stock_concentration
            if high_stock_vol < 20.0:
                low_stock_vol = ligand_mix_total_ul * desired_ligand_conc / low_ligand_stock_concentration
                if low_stock_vol > ligand_mix_total_ul + 0.01:
                    raise ValueError('Low ligand stock is not concentrated enough for one or more requested ligand concentrations.')
                ligand_pool = ligand_low_pool
                ligand_label = 'low ligand stock pool'
            else:
                if high_stock_vol > ligand_mix_total_ul + 0.01:
                    raise ValueError('High ligand stock is not concentrated enough for one or more requested ligand concentrations.')
                low_stock_vol = high_stock_vol
                ligand_pool = ligand_high_pool
                ligand_label = 'high ligand stock pool'
        buffer_vol = ligand_mix_total_ul - low_stock_vol
        for col_index in range(len(salt_concentrations)):
            dest_well = mixing_plate.rows()[row_index][col_index]
            ligand_plans.append({
                'dest': dest_well,
                'buffer': buffer_vol,
                'ligand': low_stock_vol,
                'pool': ligand_pool,
                'label': ligand_label,
                'total': ligand_mix_total_ul
            })

    protocol.comment('Adding low-salt buffer to ligand dilution wells with the P300 single-channel pipette.')
    p300s.pick_up_tip()
    for plan in ligand_plans:
        if plan['buffer'] > 0.01:
            single_dispense_from_pool(low_salt_pool, plan['buffer'], plan['dest'], 'low salt buffer pool')
    p300s.drop_tip()

    protocol.comment('Adding ligand stock to ligand dilution wells with fresh single-channel tips.')
    for plan in ligand_plans:
        if plan['ligand'] > 0.01:
            p300s.pick_up_tip()
            single_dispense_from_pool(plan['pool'], plan['ligand'], plan['dest'], plan['label'])
            mix_volume = min(200.0, max(20.0, plan['total'] * 0.8), p300s.max_volume)
            if mix_volume < plan['total']:
                p300s.mix(5, mix_volume, plan['dest'].bottom(2))
            else:
                p300s.mix(5, p300s.max_volume, plan['dest'].bottom(2))
            p300s.drop_tip()

    protocol.comment('Protocol complete. Custom filter plate was loaded in slot 1 as specified; no liquid handling into it was requested.')
