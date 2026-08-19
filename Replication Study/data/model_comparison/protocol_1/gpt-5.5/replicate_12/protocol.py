from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare templated salt buffers in reservoirs and ligand dilutions in a deep-well mixing plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # placeholders declared as literal strings for templating
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

    # Simulation defaults exercise a full 96-well ligand layout and all 12 reservoir wells.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume_ul = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 1000.0, float)
    salt_concentrations = sorted(parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0, 25, 50, 75, 100, 125, 150, 175, 200, 225, 250, 275],
        float
    ))
    ligand_concentrations = sorted(parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0, 1, 2, 3, 4, 5, 6, 7],
        float
    ))
    salt_stock_concentration = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_concentration = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 100.0, float)
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

    if number_of_salt_concentrations != len(salt_concentrations):
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS does not match SALT_CONCENTRATIONS.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match LIGAND_CONCENTRATIONS.')
    if replicates < 1:
        raise RuntimeError('REPLICATES must be at least 1.')
    if len(salt_concentrations) < 1 or len(ligand_concentrations) < 1:
        raise RuntimeError('At least one salt and one ligand concentration are required.')
    if replicates * len(salt_concentrations) > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if len(salt_concentrations) > 12:
        raise RuntimeError('At most 12 salt concentrations can be mapped to the mixing plate columns.')
    if len(ligand_concentrations) > 8:
        raise RuntimeError('At most 8 ligand concentrations can be mapped to rows A-H of the mixing plate.')
    if salt_stock_concentration <= 0 or ligand_stock_concentration <= 0:
        raise RuntimeError('Stock concentrations must be greater than zero.')

    ligand_final_volume_ul = total_volume_ul / 2.0 * replicates * 1.5
    if ligand_final_volume_ul <= 0:
        raise RuntimeError('Calculated ligand dilution volume must be greater than zero.')
    if ligand_final_volume_ul > 2000:
        raise RuntimeError('Calculated ligand dilution volume exceeds the 2 mL deep-well plate capacity.')

    # labware
    def load_custom_96_filter_plate():
        try:
            return protocol.load_labware('cytiva_96_filterwellplate_1ml', 1, label='Custom Cytiva 96 Filter Well Plate')
        except Exception as exc:
            msg = str(exc).lower()
            if 'not found' not in msg and 'filenotfounderror' not in msg and 'unable to find' not in msg:
                raise
            protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
            return protocol.load_labware('nest_96_wellplate_200ul_flat', 1, label='SIMULATION fallback for Cytiva filter plate')

    filter_plate = load_custom_96_filter_plate()
    tiprack_single_spare = protocol.load_labware('opentrons_96_tiprack_300ul', 4, label='300 uL Tips Slot 4')
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7, label='300 uL Tips Slot 7 for P300 Multi')
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10, label='300 uL Tips Slot 10 for P300 Single')
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3, label='Reservoir 4 - 2x Salt Buffers')
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6, label='Reservoir 3 - 1x Salt Buffers')
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8, label='Reservoir 2 - Salt Buffer Stocks')
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9, label='Reservoir 1 - Ligand and Salt Stocks')
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5, label='Reservoir 0 - Low Salt Buffer')
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11, label='Mixing Plate')

    # pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # Liquid setup annotations.
    low_salt_liquid = protocol.define_liquid('Low salt buffer', '0 salt buffer', '#77C9FF')
    high_salt_liquid = protocol.define_liquid('High salt buffer', 'Salt stock buffer', '#1F77B4')
    ligand_high_liquid = protocol.define_liquid('Ligand high stock', 'Ligand stock solution', '#FF7F0E')
    ligand_low_liquid = protocol.define_liquid('Ligand low stock', 'Ligand stock solution diluted 10-fold', '#FFD27F')

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

    # Manual source-volume tracking. Leave an unusable remainder in each source well.
    dead_volume_ul = 200.0

    low_salt_pool = []
    high_salt_pool = []
    ligand_high_pool = [{'well': reservoir_1.wells()[0], 'remaining': 14000.0, 'name': 'high ligand stock'}]
    ligand_low_pool = [{'well': reservoir_1.wells()[1], 'remaining': 14000.0, 'name': 'low ligand stock'}]

    for well in reservoir_2.wells()[:6]:
        low_salt_pool.append({'well': well, 'remaining': 14000.0, 'name': 'low salt buffer'})
    for well in reservoir_1.wells()[2:7]:
        low_salt_pool.append({'well': well, 'remaining': 14000.0, 'name': 'low salt buffer'})
    for well in reservoir_0.wells():
        low_salt_pool.append({'well': well, 'remaining': 14000.0, 'name': 'low salt buffer'})
    for well in reservoir_2.wells()[6:12]:
        high_salt_pool.append({'well': well, 'remaining': 14000.0, 'name': 'high salt buffer'})
    for well in reservoir_1.wells()[7:12]:
        high_salt_pool.append({'well': well, 'remaining': 14000.0, 'name': 'high salt buffer'})

    def available_in_pool(pool):
        return sum(max(0.0, item['remaining'] - dead_volume_ul) for item in pool)

    def get_next_source(pool, pool_name):
        for item in pool:
            if item['remaining'] > dead_volume_ul:
                return item
        raise RuntimeError('Insufficient volume in ' + pool_name + ' pool.')

    def move_multi_pool_to_destinations(pool, pool_name, requests):
        """Move per-channel volumes with the 8-channel pipette.

        requests is a list of (destination_well, volume_per_channel_ul). Source reservoir
        accounting subtracts 8 x volume because all channels aspirate from one reservoir well.
        """
        total_effective_needed = sum(max(0.0, vol) * 8.0 for _dest, vol in requests)
        if available_in_pool(pool) < total_effective_needed:
            raise RuntimeError('Insufficient volume in ' + pool_name + ' pool for multi-channel transfers.')
        p300_multi.pick_up_tip()
        for dest, requested_per_channel in requests:
            remaining_per_channel = float(requested_per_channel)
            while remaining_per_channel > 0.000001:
                source_item = get_next_source(pool, pool_name)
                available_per_channel = (source_item['remaining'] - dead_volume_ul) / 8.0
                chunk = min(p300_multi.max_volume, remaining_per_channel, available_per_channel)
                if chunk <= 0.000001:
                    source_item['remaining'] = dead_volume_ul
                    continue
                p300_multi.aspirate(chunk, source_item['well'].bottom(2))
                p300_multi.dispense(chunk, dest.bottom(3))
                source_item['remaining'] -= chunk * 8.0
                remaining_per_channel -= chunk
        p300_multi.drop_tip()

    def move_single_pool_to_destination(pool, pool_name, dest, volume_ul):
        if volume_ul <= 0.000001:
            return
        if available_in_pool(pool) < volume_ul:
            raise RuntimeError('Insufficient volume in ' + pool_name + ' pool for single-channel transfer.')
        remaining = float(volume_ul)
        while remaining > 0.000001:
            source_item = get_next_source(pool, pool_name)
            available = source_item['remaining'] - dead_volume_ul
            chunk = min(p300_single.max_volume, remaining, available)
            if chunk <= 0.000001:
                source_item['remaining'] = dead_volume_ul
                continue
            p300_single.aspirate(chunk, source_item['well'].bottom(2))
            p300_single.dispense(chunk, dest.bottom(2))
            source_item['remaining'] -= chunk
            remaining -= chunk

    # Steps 2 and 3: make 1x salt buffers in Reservoir 3 and 2x salt buffers in Reservoir 4.
    target_total_physical_ul = 10000.0
    target_total_per_channel_ul = target_total_physical_ul / 8.0
    low_requests = []
    high_requests = []
    salt_mix_targets = []

    reservoir_3_wells = reservoir_3.wells()
    target_index = 0
    for salt_conc in salt_concentrations:
        ratio = salt_conc / salt_stock_concentration
        if ratio < -0.000001 or ratio > 1.000001:
            raise RuntimeError('A requested 1x salt concentration is outside the range of the salt stock.')
        ratio = max(0.0, min(1.0, ratio))
        high_per_channel = target_total_per_channel_ul * ratio
        low_per_channel = target_total_per_channel_ul - high_per_channel
        for _rep in range(replicates):
            dest = reservoir_3_wells[target_index]
            if low_per_channel > 0.000001:
                low_requests.append((dest, low_per_channel))
            if high_per_channel > 0.000001:
                high_requests.append((dest, high_per_channel))
            salt_mix_targets.append(dest)
            target_index += 1

    reservoir_4_wells = reservoir_4.wells()
    for idx, salt_conc in enumerate(salt_concentrations):
        ratio = (2.0 * salt_conc) / salt_stock_concentration
        if ratio < -0.000001 or ratio > 1.000001:
            raise RuntimeError('A requested 2x salt concentration is outside the range of the salt stock.')
        ratio = max(0.0, min(1.0, ratio))
        high_per_channel = target_total_per_channel_ul * ratio
        low_per_channel = target_total_per_channel_ul - high_per_channel
        dest = reservoir_4_wells[idx]
        if low_per_channel > 0.000001:
            low_requests.append((dest, low_per_channel))
        if high_per_channel > 0.000001:
            high_requests.append((dest, high_per_channel))
        salt_mix_targets.append(dest)

    protocol.comment('Preparing salt buffers in Reservoir 3 and Reservoir 4.')
    move_multi_pool_to_destinations(low_salt_pool, 'low salt buffer', low_requests)
    move_multi_pool_to_destinations(high_salt_pool, 'high salt buffer', high_requests)

    p300_multi.pick_up_tip()
    for target in salt_mix_targets:
        p300_multi.mix(3, 250, target.bottom(3))
    p300_multi.drop_tip()

    # Step 4: prepare 2x ligand dilutions in the deep-well mixing plate.
    protocol.comment('Preparing ligand dilutions in the deep-well mixing plate.')
    for ligand_row_index, ligand_conc in enumerate(ligand_concentrations):
        high_stock_volume_ul = ligand_final_volume_ul * (2.0 * ligand_conc / ligand_stock_concentration)
        use_low_ligand_stock = high_stock_volume_ul > 0.000001 and high_stock_volume_ul < 20.0
        if use_low_ligand_stock:
            ligand_stock_volume_ul = ligand_final_volume_ul * (2.0 * ligand_conc / (ligand_stock_concentration / 10.0))
            ligand_pool = ligand_low_pool
            ligand_pool_name = 'low ligand stock'
        else:
            ligand_stock_volume_ul = high_stock_volume_ul
            ligand_pool = ligand_high_pool
            ligand_pool_name = 'high ligand stock'
        if ligand_stock_volume_ul < -0.000001 or ligand_stock_volume_ul > ligand_final_volume_ul + 0.000001:
            raise RuntimeError('A requested 2x ligand concentration is outside the range of the selected ligand stock.')
        ligand_stock_volume_ul = max(0.0, min(ligand_final_volume_ul, ligand_stock_volume_ul))
        low_salt_volume_ul = ligand_final_volume_ul - ligand_stock_volume_ul

        for salt_column_index, _salt_conc in enumerate(salt_concentrations):
            dest = mixing_plate.rows()[ligand_row_index][salt_column_index]
            p300_single.pick_up_tip()
            move_single_pool_to_destination(low_salt_pool, 'low salt buffer', dest, low_salt_volume_ul)
            move_single_pool_to_destination(ligand_pool, ligand_pool_name, dest, ligand_stock_volume_ul)
            mix_volume = min(200.0, max(1.0, ligand_final_volume_ul / 2.0))
            p300_single.mix(3, mix_volume, dest.bottom(2))
            p300_single.drop_tip()

    protocol.comment('Protocol complete.')
