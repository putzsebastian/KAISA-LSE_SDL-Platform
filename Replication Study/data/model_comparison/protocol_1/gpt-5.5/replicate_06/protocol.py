from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare salt buffers in reservoirs and ligand dilutions in a deep-well mixing plate using placeholders.'
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
            return default
        return cast(float(s))

    def parse_list(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return list(default)
        return [cast(x.strip()) for x in s.split(';') if x.strip()]

    # Worst-case simulation defaults within the stated reservoir constraint:
    # 3 replicates x 4 salt concentrations = 12 wells in Reservoir 3.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 3, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)
    salt_concentrations = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0.0, 100.0, 200.0, 300.0],
        float
    )
    ligand_concentrations = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0.0, 0.1, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0],
        float
    )
    salt_stock_concentration = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_concentration = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
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
        raise ValueError('NUMBER_OF_SALT_CONCENTRATIONS does not match SALT_CONCENTRATIONS list length.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise ValueError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match LIGAND_CONCENTRATIONS list length.')
    if replicates * number_of_salt_concentrations > 12:
        raise ValueError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if number_of_salt_concentrations > 12:
        raise ValueError('At most 12 salt concentrations can be represented as mixing-plate columns.')
    if number_of_ligand_concentrations > 8:
        raise ValueError('At most 8 ligand concentrations can be represented as mixing-plate rows A-H.')
    if salt_stock_concentration <= 0:
        raise ValueError('SALT_STOCK_CONCENTRATION must be greater than 0.')
    if ligand_stock_concentration <= 0:
        raise ValueError('LIGAND_STOCK_CONCENTRATION must be greater than 0.')

    # -------------------------
    # Labware
    # -------------------------
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
            'using NEST 96 deep-well plate as a 96-well SIMULATION fallback only.'
        )
        filter_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 1)

    tiprack_extra = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------
    # Pipettes
    # -------------------------
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # -------------------------
    # Liquid labels and starting volumes
    # -------------------------
    low_salt_liquid = protocol.define_liquid(
        name='Low salt buffer',
        description='Low salt buffer with 0 salt',
        display_color='#66CCFF'
    )
    high_salt_liquid = protocol.define_liquid(
        name='High salt buffer',
        description='High salt buffer at SALT_STOCK_CONCENTRATION',
        display_color='#0033CC'
    )
    ligand_high_liquid = protocol.define_liquid(
        name='Ligand high stock',
        description='Ligand stock solution at LIGAND_STOCK_CONCENTRATION',
        display_color='#FF9900'
    )
    ligand_low_liquid = protocol.define_liquid(
        name='Ligand low stock',
        description='Ligand stock solution at LIGAND_STOCK_CONCENTRATION / 10',
        display_color='#FFCC66'
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

    # -------------------------
    # Reagent pools for manual volume budgeting
    # -------------------------
    DEAD_VOLUME_UL = 20.0
    MULTI_CHANNEL_COUNT = 8.0

    def pool_entry(well, volume_ul, label):
        return {'well': well, 'remaining': float(volume_ul), 'label': label}

    low_salt_pool = []
    for well in reservoir_2.wells()[:6]:
        low_salt_pool.append(pool_entry(well, 14000, 'low salt buffer'))
    for well in reservoir_1.wells()[2:7]:
        low_salt_pool.append(pool_entry(well, 14000, 'low salt buffer'))
    for well in reservoir_0.wells():
        low_salt_pool.append(pool_entry(well, 14000, 'low salt buffer'))

    high_salt_pool = []
    for well in reservoir_2.wells()[6:12]:
        high_salt_pool.append(pool_entry(well, 14000, 'high salt buffer'))
    for well in reservoir_1.wells()[7:12]:
        high_salt_pool.append(pool_entry(well, 14000, 'high salt buffer'))

    ligand_high_pool = [pool_entry(reservoir_1.wells()[0], 14000, 'ligand high stock')]
    ligand_low_pool = [pool_entry(reservoir_1.wells()[1], 14000, 'ligand low stock')]

    def available_total(pool):
        return sum(max(0.0, entry['remaining'] - DEAD_VOLUME_UL) for entry in pool)

    def move_multi_from_pool(pool, total_volume_ul, dest_well):
        remaining_request = float(total_volume_ul)
        if remaining_request <= 0:
            return
        if available_total(pool) + 0.01 < remaining_request:
            raise RuntimeError(
                'Insufficient reagent in pool "{}". Need {:.2f} uL, available {:.2f} uL.'.format(
                    pool[0]['label'] if pool else 'empty pool',
                    remaining_request,
                    available_total(pool)
                )
            )
        for entry in pool:
            if remaining_request <= 0.01:
                break
            usable = max(0.0, entry['remaining'] - DEAD_VOLUME_UL)
            while usable > 0.01 and remaining_request > 0.01:
                total_chunk = min(remaining_request, usable, p300_multi.max_volume * MULTI_CHANNEL_COUNT)
                per_channel_chunk = total_chunk / MULTI_CHANNEL_COUNT
                p300_multi.aspirate(per_channel_chunk, entry['well'])
                p300_multi.dispense(per_channel_chunk, dest_well)
                entry['remaining'] -= total_chunk
                usable -= total_chunk
                remaining_request -= total_chunk
        if remaining_request > 0.01:
            raise RuntimeError('Pool ran dry before completing a multi-channel transfer request.')

    def move_single_from_pool(pool, total_volume_ul, dest_well):
        remaining_request = float(total_volume_ul)
        if remaining_request <= 0:
            return
        if available_total(pool) + 0.01 < remaining_request:
            raise RuntimeError(
                'Insufficient reagent in pool "{}". Need {:.2f} uL, available {:.2f} uL.'.format(
                    pool[0]['label'] if pool else 'empty pool',
                    remaining_request,
                    available_total(pool)
                )
            )
        for entry in pool:
            if remaining_request <= 0.01:
                break
            usable = max(0.0, entry['remaining'] - DEAD_VOLUME_UL)
            while usable > 0.01 and remaining_request > 0.01:
                chunk = min(remaining_request, usable, p300_single.max_volume)
                p300_single.aspirate(chunk, entry['well'])
                p300_single.dispense(chunk, dest_well)
                entry['remaining'] -= chunk
                usable -= chunk
                remaining_request -= chunk
        if remaining_request > 0.01:
            raise RuntimeError('Pool ran dry before completing a single-channel transfer request.')

    # -------------------------
    # Step 2 and Step 3 calculations
    # -------------------------
    salt_target_volume_ul = 10000.0
    low_salt_requests = []
    high_salt_requests = []
    salt_mix_targets = []

    for salt_index, salt_conc in enumerate(salt_concentrations):
        high_fraction = salt_conc / salt_stock_concentration
        if high_fraction < -0.000001 or high_fraction > 1.000001:
            raise ValueError('Requested salt concentration exceeds SALT_STOCK_CONCENTRATION for Reservoir 3.')
        high_volume = salt_target_volume_ul * high_fraction
        low_volume = salt_target_volume_ul - high_volume
        for replicate_index in range(replicates):
            dest = reservoir_3.wells()[salt_index * replicates + replicate_index]
            low_salt_requests.append((low_volume, dest))
            high_salt_requests.append((high_volume, dest))
            salt_mix_targets.append(dest)

    for salt_index, salt_conc in enumerate(salt_concentrations):
        high_fraction = (2.0 * salt_conc) / salt_stock_concentration
        if high_fraction < -0.000001 or high_fraction > 1.000001:
            raise ValueError('2x requested salt concentration exceeds SALT_STOCK_CONCENTRATION for Reservoir 4.')
        high_volume = salt_target_volume_ul * high_fraction
        low_volume = salt_target_volume_ul - high_volume
        dest = reservoir_4.wells()[salt_index]
        low_salt_requests.append((low_volume, dest))
        high_salt_requests.append((high_volume, dest))
        salt_mix_targets.append(dest)

    # -------------------------
    # Step 2 and Step 3 liquid handling with P300 Multi-Channel
    # -------------------------
    protocol.comment('Preparing Reservoir 3 and Reservoir 4 salt buffers with the P300 8-Channel.')

    if any(volume > 0.01 for volume, _ in low_salt_requests):
        p300_multi.pick_up_tip()
        for volume, dest in low_salt_requests:
            move_multi_from_pool(low_salt_pool, volume, dest)
        p300_multi.drop_tip()

    if any(volume > 0.01 for volume, _ in high_salt_requests):
        p300_multi.pick_up_tip()
        for volume, dest in high_salt_requests:
            move_multi_from_pool(high_salt_pool, volume, dest)
        p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for dest in salt_mix_targets:
        p300_multi.mix(3, 250, dest)
    p300_multi.drop_tip()

    # -------------------------
    # Step 4 ligand dilution calculations
    # -------------------------
    mixing_volume_per_well = total_volume / 2.0 * replicates * 1.5
    if mixing_volume_per_well <= 0:
        raise ValueError('TOTAL_VOLUME and REPLICATES must create a positive mixing volume.')
    if mixing_volume_per_well > 2000:
        raise ValueError('Calculated mixing volume exceeds 2 mL deep-well capacity.')

    low_buffer_single_requests = []
    ligand_high_requests = []
    ligand_low_requests = []
    ligand_mix_targets_by_row = [[] for _ in ligand_concentrations]

    for row_index, ligand_conc in enumerate(ligand_concentrations):
        target_ligand_conc = 2.0 * ligand_conc
        chosen_stock_volume = 0.0
        chosen_stock = None
        if target_ligand_conc < -0.000001:
            raise ValueError('Ligand concentrations must not be negative.')
        if target_ligand_conc > 0:
            high_stock_volume = mixing_volume_per_well * target_ligand_conc / ligand_stock_concentration
            if high_stock_volume < 20.0:
                low_stock_concentration = ligand_stock_concentration / 10.0
                low_stock_volume = mixing_volume_per_well * target_ligand_conc / low_stock_concentration
                if low_stock_volume > mixing_volume_per_well + 0.000001:
                    raise ValueError('Low ligand stock is too dilute for a requested 2x ligand concentration.')
                chosen_stock = 'low'
                chosen_stock_volume = low_stock_volume
            else:
                if high_stock_volume > mixing_volume_per_well + 0.000001:
                    raise ValueError('Requested 2x ligand concentration exceeds ligand stock concentration.')
                chosen_stock = 'high'
                chosen_stock_volume = high_stock_volume

        buffer_volume = mixing_volume_per_well - chosen_stock_volume
        if buffer_volume < -0.000001:
            raise ValueError('Calculated ligand stock volume exceeds total mixing volume.')

        for col_index in range(number_of_salt_concentrations):
            dest = mixing_plate.rows()[row_index][col_index]
            ligand_mix_targets_by_row[row_index].append(dest)
            if buffer_volume > 0.01:
                low_buffer_single_requests.append((buffer_volume, dest))
            if chosen_stock == 'high' and chosen_stock_volume > 0.01:
                ligand_high_requests.append((chosen_stock_volume, dest))
            elif chosen_stock == 'low' and chosen_stock_volume > 0.01:
                ligand_low_requests.append((chosen_stock_volume, dest))

    # -------------------------
    # Step 4 liquid handling with P300 Single-Channel
    # -------------------------
    protocol.comment('Preparing 2x ligand dilutions in the deep-well mixing plate with the P300 Single-Channel.')

    if low_buffer_single_requests:
        p300_single.pick_up_tip()
        for volume, dest in low_buffer_single_requests:
            move_single_from_pool(low_salt_pool, volume, dest)
        p300_single.drop_tip()

    if ligand_high_requests:
        p300_single.pick_up_tip()
        for volume, dest in ligand_high_requests:
            move_single_from_pool(ligand_high_pool, volume, dest)
        p300_single.drop_tip()

    if ligand_low_requests:
        p300_single.pick_up_tip()
        for volume, dest in ligand_low_requests:
            move_single_from_pool(ligand_low_pool, volume, dest)
        p300_single.drop_tip()

    mix_volume = min(200.0, max(20.0, mixing_volume_per_well * 0.8))
    for row_targets in ligand_mix_targets_by_row:
        if row_targets:
            p300_single.pick_up_tip()
            for dest in row_targets:
                p300_single.mix(3, mix_volume, dest)
            p300_single.drop_tip()

    protocol.comment('Protocol complete. The custom filter plate in slot 1 was loaded for deck-layout compatibility and is not used in these preparation steps.')
