from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare templated salt buffers and ligand dilution series using placeholder values.'
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
    # Parameters, with simulation fallbacks for unreplaced placeholders.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 1000.0, float)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    number_of_salt_concentrations = parse_scalar(
        PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, 12, int)
    number_of_ligand_concentrations = parse_scalar(
        PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, 8, int)
    salt_concentrations = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0, 25, 50, 75, 100, 125, 150, 175, 200, 225, 250, 275],
        float)
    ligand_concentrations = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0, 1, 2, 5, 10, 20, 50, 100],
        float)

    salt_concentrations = sorted(salt_concentrations)
    ligand_concentrations = sorted(ligand_concentrations)

    if replicates < 1:
        raise ValueError('[[REPLICATES]] must be at least 1.')
    if total_volume <= 0:
        raise ValueError('[[TOTAL_VOLUME]] must be greater than 0.')
    if salt_stock_conc <= 0:
        raise ValueError('[[SALT_STOCK_CONCENTRATION]] must be greater than 0.')
    if ligand_stock_conc <= 0:
        raise ValueError('[[LIGAND_STOCK_CONCENTRATION]] must be greater than 0.')
    if len(salt_concentrations) != number_of_salt_concentrations:
        raise ValueError('[[NUMBER_OF_SALT_CONCENTRATIONS]] must match the number of values in [[SALT_CONCENTRATIONS]].')
    if len(ligand_concentrations) != number_of_ligand_concentrations:
        raise ValueError('[[NUMBER_OF_LIGAND_CONCENTRATIONS]] must match the number of values in [[LIGAND_CONCENTRATIONS]].')
    if number_of_salt_concentrations > 12:
        raise ValueError('No more than 12 salt concentrations can be prepared in Reservoir 4 or mixing-plate columns.')
    if number_of_ligand_concentrations > 8:
        raise ValueError('No more than 8 ligand concentrations can be prepared row-wise in the mixing plate.')
    if replicates * number_of_salt_concentrations > 12:
        raise ValueError('[[REPLICATES]] x [[NUMBER_OF_SALT_CONCENTRATIONS]] must not exceed 12 for Reservoir 3.')

    ligand_dilution_total = total_volume / 2.0 * replicates * 1.5
    if ligand_dilution_total > 1900:
        raise ValueError('Each mixing-plate well would exceed the practical 2 mL deep-well capacity.')

    # Labware.
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        message = str(exc).lower()
        if 'not found' not in message and 'filenotfounderror' not in message:
            raise
        protocol.comment('WARNING: custom labware definition cytiva_96_filterwellplate_1ml not available; using NEST 96 deep-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 1)

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes.
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # Liquid setup labels.
    low_salt_liquid = protocol.define_liquid(
        name='Low salt buffer',
        description='Low salt buffer with 0 salt',
        display_color='#50C7C7')
    high_salt_liquid = protocol.define_liquid(
        name='High salt buffer',
        description='High salt buffer at placeholder salt stock concentration',
        display_color='#0066CC')
    ligand_high_liquid = protocol.define_liquid(
        name='Ligand stock high',
        description='Ligand stock solution at placeholder high stock concentration',
        display_color='#FF9900')
    ligand_low_liquid = protocol.define_liquid(
        name='Ligand stock low',
        description='Ligand stock solution at one tenth of high stock concentration',
        display_color='#FFD580')

    for well in reservoir_2.wells()[0:6]:
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

    # Track usable source volumes in actual microliters. For the multi-channel pipette,
    # one command of V uL per channel consumes 8 x V uL from a single-row reservoir well.
    usable_reservoir_volume = 13800.0
    usable_ligand_volume = 13980.0
    low_salt_pool = []
    high_salt_pool = []
    for well in reservoir_0.wells():
        low_salt_pool.append({'well': well, 'remaining': usable_reservoir_volume})
    for well in reservoir_2.wells()[0:6]:
        low_salt_pool.append({'well': well, 'remaining': usable_reservoir_volume})
    for well in reservoir_1.wells()[2:7]:
        low_salt_pool.append({'well': well, 'remaining': usable_reservoir_volume})
    for well in reservoir_2.wells()[6:12]:
        high_salt_pool.append({'well': well, 'remaining': usable_reservoir_volume})
    for well in reservoir_1.wells()[7:12]:
        high_salt_pool.append({'well': well, 'remaining': usable_reservoir_volume})
    ligand_high_pool = [{'well': reservoir_1.wells()[0], 'remaining': usable_ligand_volume}]
    ligand_low_pool = [{'well': reservoir_1.wells()[1], 'remaining': usable_ligand_volume}]

    def pool_total(pool):
        return sum(entry['remaining'] for entry in pool)

    def take_next_source(pool, requested, label):
        for entry in pool:
            if entry['remaining'] > 0.000001:
                return entry
        raise RuntimeError('Insufficient volume in ' + label + ' pool. Shortfall: ' + str(round(requested, 2)) + ' uL.')

    def move_from_pool_multi(pool, dest, actual_volume_ul, label):
        remaining = float(actual_volume_ul)
        if remaining <= 0.000001:
            return
        if pool_total(pool) + 0.000001 < remaining:
            shortfall = remaining - pool_total(pool)
            raise RuntimeError('Insufficient volume in ' + label + ' pool. Shortfall: ' + str(round(shortfall, 2)) + ' uL.')
        while remaining > 0.000001:
            source_entry = take_next_source(pool, remaining, label)
            actual_chunk = min(remaining, source_entry['remaining'], p300_multi.max_volume * 8.0)
            per_channel_chunk = actual_chunk / 8.0
            p300_multi.aspirate(per_channel_chunk, source_entry['well'].bottom(2))
            p300_multi.dispense(per_channel_chunk, dest.bottom(5))
            source_entry['remaining'] -= actual_chunk
            remaining -= actual_chunk

    def move_from_pool_single(pool, dest, volume_ul, label):
        remaining = float(volume_ul)
        if remaining <= 0.000001:
            return
        if pool_total(pool) + 0.000001 < remaining:
            shortfall = remaining - pool_total(pool)
            raise RuntimeError('Insufficient volume in ' + label + ' pool. Shortfall: ' + str(round(shortfall, 2)) + ' uL.')
        while remaining > 0.000001:
            source_entry = take_next_source(pool, remaining, label)
            chunk = min(remaining, source_entry['remaining'], p300_single.max_volume)
            p300_single.aspirate(chunk, source_entry['well'].bottom(2))
            p300_single.dispense(chunk, dest.bottom(2))
            source_entry['remaining'] -= chunk
            remaining -= chunk

    def salt_mix_volumes(target_salt_concentration):
        if target_salt_concentration < 0:
            raise ValueError('Salt concentrations must not be negative.')
        if target_salt_concentration > salt_stock_conc:
            raise ValueError('Requested salt concentration exceeds the salt stock concentration.')
        high_volume = 10000.0 * target_salt_concentration / salt_stock_conc
        low_volume = 10000.0 - high_volume
        return low_volume, high_volume

    # Steps 2 and 3: prepare salt buffers in Reservoir 3 and 2x salt buffers in Reservoir 4.
    salt_targets = []
    for salt_index, salt_conc in enumerate(salt_concentrations):
        for rep in range(replicates):
            dest_index = salt_index * replicates + rep
            low_vol, high_vol = salt_mix_volumes(salt_conc)
            salt_targets.append({
                'dest': reservoir_3.wells()[dest_index],
                'low': low_vol,
                'high': high_vol,
                'label': 'Reservoir 3 well ' + str(dest_index + 1) + ' at ' + str(salt_conc)})

    for salt_index, salt_conc in enumerate(salt_concentrations):
        target_2x = 2.0 * salt_conc
        low_vol, high_vol = salt_mix_volumes(target_2x)
        salt_targets.append({
            'dest': reservoir_4.wells()[salt_index],
            'low': low_vol,
            'high': high_vol,
            'label': 'Reservoir 4 well ' + str(salt_index + 1) + ' at 2x ' + str(salt_conc)})

    protocol.comment('Dispensing low salt buffer for Reservoir 3 and Reservoir 4 salt-buffer preparations.')
    p300_multi.pick_up_tip()
    for target in salt_targets:
        move_from_pool_multi(low_salt_pool, target['dest'], target['low'], 'low salt buffer')
    p300_multi.drop_tip()

    protocol.comment('Dispensing high salt buffer and mixing Reservoir 3 and Reservoir 4 salt-buffer preparations.')
    p300_multi.pick_up_tip()
    for target in salt_targets:
        move_from_pool_multi(high_salt_pool, target['dest'], target['high'], 'high salt buffer')
    for target in salt_targets:
        p300_multi.mix(3, 250, target['dest'].bottom(5))
    p300_multi.drop_tip()

    # Step 4: prepare 2x ligand dilution series in the mixing plate.
    protocol.comment('Preparing 2x ligand dilution series in the mixing plate, rows A-H and salt columns 1-12 as required.')
    for ligand_index, ligand_conc in enumerate(ligand_concentrations):
        if ligand_conc < 0:
            raise ValueError('Ligand concentrations must not be negative.')
        for salt_index in range(number_of_salt_concentrations):
            dest = mixing_plate.rows()[ligand_index][salt_index]
            target_ligand_conc = 2.0 * ligand_conc
            if target_ligand_conc == 0:
                stock_volume = 0.0
                stock_pool = ligand_high_pool
                stock_label = 'high ligand stock'
            else:
                high_stock_volume = ligand_dilution_total * target_ligand_conc / ligand_stock_conc
                if high_stock_volume < 20.0:
                    low_stock_conc = ligand_stock_conc / 10.0
                    stock_volume = ligand_dilution_total * target_ligand_conc / low_stock_conc
                    stock_pool = ligand_low_pool
                    stock_label = 'low ligand stock'
                else:
                    stock_volume = high_stock_volume
                    stock_pool = ligand_high_pool
                    stock_label = 'high ligand stock'
            if stock_volume > ligand_dilution_total + 0.000001:
                raise ValueError('Requested 2x ligand concentration exceeds the selected ligand stock concentration.')
            buffer_volume = ligand_dilution_total - stock_volume
            p300_single.pick_up_tip()
            move_from_pool_single(low_salt_pool, dest, buffer_volume, 'low salt buffer')
            move_from_pool_single(stock_pool, dest, stock_volume, stock_label)
            if ligand_dilution_total >= 2.0:
                mix_volume = min(200.0, p300_single.max_volume, ligand_dilution_total * 0.8)
                p300_single.mix(3, mix_volume, dest.bottom(2))
            p300_single.drop_tip()

    protocol.comment('Protocol complete. Custom filter plate in slot 1 was loaded for downstream use but is not used by the specified preparation steps.')
