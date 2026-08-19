from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Templated preparation of salt buffers in reservoirs and ligand dilutions in a deep-well mixing plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol):
    # modules: none

    # labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_300_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300_slot10])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300_slot7])

    # placeholders: the wizard substitutes these literal strings.
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
        if cast is int:
            return int(float(s))
        return cast(float(s))

    def parse_list(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return list(default)
        return [cast(float(x.strip())) for x in s.split(';') if x.strip()]

    # Simulation defaults are chosen to exercise the largest plate layout: 12 salt columns and 8 ligand rows.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    salt_concentrations = sorted(parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 25, 50, 75, 100, 150, 200, 250, 300, 350, 400, 500], float))
    ligand_concentrations = sorted(parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [0, 1, 2, 5, 10, 20, 40, 80], float))
    number_of_salt_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concentrations), int)
    number_of_ligand_concentrations = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concentrations), int)

    if number_of_salt_concentrations != len(salt_concentrations):
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS does not match the number of values in SALT_CONCENTRATIONS.')
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match the number of values in LIGAND_CONCENTRATIONS.')
    if replicates < 1:
        raise RuntimeError('REPLICATES must be at least 1.')
    if number_of_salt_concentrations < 1 or number_of_salt_concentrations > 12:
        raise RuntimeError('The number of salt concentrations must be between 1 and 12.')
    if number_of_ligand_concentrations < 1 or number_of_ligand_concentrations > 8:
        raise RuntimeError('The number of ligand concentrations must be between 1 and 8, because ligand dilutions are arranged in rows A-H.')
    if replicates * number_of_salt_concentrations > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed the 12 wells of Reservoir 3.')
    if salt_stock_conc <= 0 or ligand_stock_conc <= 0:
        raise RuntimeError('Stock concentrations must be greater than zero.')

    final_ligand_well_volume = total_volume / 2.0 * replicates * 1.5
    if final_ligand_well_volume > 2000:
        raise RuntimeError('Calculated ligand dilution volume exceeds the 2 mL capacity of the mixing plate wells.')
    if final_ligand_well_volume <= 0:
        raise RuntimeError('Calculated ligand dilution volume must be greater than zero.')

    # Liquid bookkeeping uses physical microliters in each source well.
    DEAD_VOLUME = 20.0
    INITIAL_RESERVOIR_VOLUME = 14000.0

    low_buffer_sources = [
        *reservoir_2.wells()[:6],
        *reservoir_1.wells()[2:7],
        *reservoir_0.wells()[:12]
    ]
    high_salt_sources = [
        *reservoir_2.wells()[6:12],
        *reservoir_1.wells()[7:12]
    ]
    ligand_high_source = reservoir_1.wells()[0]
    ligand_low_source = reservoir_1.wells()[1]

    remaining = {}
    for well in low_buffer_sources + high_salt_sources + [ligand_high_source, ligand_low_source]:
        remaining[well] = INITIAL_RESERVOIR_VOLUME

    # Optional liquid labels for the run log and app setup view.
    try:
        low_liquid = protocol.define_liquid(name='Low salt buffer', description='0 salt buffer', display_color='#7ED6FF')
        high_liquid = protocol.define_liquid(name='High salt buffer', description='Salt stock buffer', display_color='#FFB000')
        ligand_high_liquid = protocol.define_liquid(name='Ligand high stock', description='High concentration ligand stock', display_color='#AA00FF')
        ligand_low_liquid = protocol.define_liquid(name='Ligand low stock', description='Low concentration ligand stock', display_color='#DD88FF')
        for well in low_buffer_sources:
            well.load_liquid(low_liquid, INITIAL_RESERVOIR_VOLUME)
        for well in high_salt_sources:
            well.load_liquid(high_liquid, INITIAL_RESERVOIR_VOLUME)
        ligand_high_source.load_liquid(ligand_high_liquid, INITIAL_RESERVOIR_VOLUME)
        ligand_low_source.load_liquid(ligand_low_liquid, INITIAL_RESERVOIR_VOLUME)
    except Exception:
        protocol.comment('Liquid annotation is not available in this simulation environment; continuing without liquid labels.')

    def available_volume(source_well):
        return max(0.0, remaining[source_well] - DEAD_VOLUME)

    def assert_pool_has_volume(pool, request_volume, pool_name):
        total_available = sum(available_volume(well) for well in pool)
        if total_available + 0.0001 < request_volume:
            shortfall = request_volume - total_available
            raise RuntimeError(f'Not enough {pool_name}; short by {shortfall:.1f} uL.')

    def multi_deliver_physical(pool, requests, pool_name):
        """Deliver physical uL from a source pool to single-row reservoir wells using the P300 multi.

        The volume passed to the API is per channel. Because the reservoirs are single-row troughs,
        physical volume moved is 8 x the API volume.
        """
        total_requested = sum(vol for dest, vol in requests if vol > 0)
        if total_requested <= 0:
            return
        assert_pool_has_volume(pool, total_requested, pool_name)
        p300m.pick_up_tip()
        source_index = 0
        max_physical_per_move = p300m.max_volume * 8.0
        for dest, requested_volume in requests:
            request_left = requested_volume
            while request_left > 0.0001:
                while source_index < len(pool) and available_volume(pool[source_index]) <= 0.0001:
                    source_index += 1
                if source_index >= len(pool):
                    raise RuntimeError(f'Not enough {pool_name} while dispensing; pool is exhausted.')
                source = pool[source_index]
                chunk_physical = min(request_left, available_volume(source), max_physical_per_move)
                api_volume = chunk_physical / 8.0
                p300m.aspirate(api_volume, source.bottom(1))
                p300m.dispense(api_volume, dest.bottom(2))
                remaining[source] -= chunk_physical
                request_left -= chunk_physical
        p300m.drop_tip()

    def single_deliver_physical(pool, requests, pool_name):
        """Deliver physical uL with the P300 single, splitting across source wells and tip-sized chunks."""
        total_requested = sum(vol for dest, vol in requests if vol > 0)
        if total_requested <= 0:
            return
        assert_pool_has_volume(pool, total_requested, pool_name)
        p300s.pick_up_tip()
        source_index = 0
        max_per_move = p300s.max_volume
        for dest, requested_volume in requests:
            request_left = requested_volume
            while request_left > 0.0001:
                while source_index < len(pool) and available_volume(pool[source_index]) <= 0.0001:
                    source_index += 1
                if source_index >= len(pool):
                    raise RuntimeError(f'Not enough {pool_name} while dispensing; pool is exhausted.')
                source = pool[source_index]
                chunk = min(request_left, available_volume(source), max_per_move)
                p300s.aspirate(chunk, source.bottom(1))
                p300s.dispense(chunk, dest.bottom(2))
                remaining[source] -= chunk
                request_left -= chunk
        p300s.drop_tip()

    def stock_batch_dispense(source, requests, source_name):
        """Aspirate stock once per batch, dispense to buffered wells, then drop the tip before returning to stock."""
        expanded = []
        for dest, volume in requests:
            vol_left = volume
            while vol_left > 0.0001:
                chunk = min(vol_left, p300s.max_volume)
                expanded.append((dest, chunk))
                vol_left -= chunk
        if not expanded:
            return
        total_requested = sum(chunk for dest, chunk in expanded)
        assert_pool_has_volume([source], total_requested, source_name)
        index = 0
        while index < len(expanded):
            batch = []
            batch_total = 0.0
            while index < len(expanded) and batch_total + expanded[index][1] <= p300s.max_volume + 0.0001:
                batch.append(expanded[index])
                batch_total += expanded[index][1]
                index += 1
            if not batch:
                batch.append(expanded[index])
                batch_total = expanded[index][1]
                index += 1
            p300s.pick_up_tip()
            p300s.aspirate(batch_total, source.bottom(1))
            remaining[source] -= batch_total
            for dest, chunk in batch:
                p300s.dispense(chunk, dest.bottom(2))
            p300s.drop_tip()

    # Step 2 and Step 3 calculation: prepare salt buffers in Reservoir 3 and 2x salt buffers in Reservoir 4.
    salt_preparation_total_physical = 10000.0
    salt_low_requests = []
    salt_high_requests = []
    salt_mix_targets = []

    reservoir_3_index = 0
    for salt_conc in salt_concentrations:
        if salt_conc < 0:
            raise RuntimeError('Salt concentrations must not be negative.')
        if salt_conc > salt_stock_conc:
            raise RuntimeError('A requested salt concentration exceeds the salt stock concentration.')
        high_volume = salt_preparation_total_physical * salt_conc / salt_stock_conc
        low_volume = salt_preparation_total_physical - high_volume
        for rep in range(replicates):
            dest = reservoir_3.wells()[reservoir_3_index]
            reservoir_3_index += 1
            salt_low_requests.append((dest, low_volume))
            salt_high_requests.append((dest, high_volume))
            salt_mix_targets.append(dest)

    for index, salt_conc in enumerate(salt_concentrations):
        target_conc = 2.0 * salt_conc
        if target_conc > salt_stock_conc:
            raise RuntimeError('A requested 2x salt concentration exceeds the salt stock concentration.')
        high_volume = salt_preparation_total_physical * target_conc / salt_stock_conc
        low_volume = salt_preparation_total_physical - high_volume
        dest = reservoir_4.wells()[index]
        salt_low_requests.append((dest, low_volume))
        salt_high_requests.append((dest, high_volume))
        salt_mix_targets.append(dest)

    protocol.comment('Preparing Reservoir 3 and Reservoir 4 salt buffers with the P300 multi-channel pipette.')
    multi_deliver_physical(low_buffer_sources, salt_low_requests, 'low salt buffer')
    multi_deliver_physical(high_salt_sources, salt_high_requests, 'high salt buffer')

    p300m.pick_up_tip()
    for dest in salt_mix_targets:
        p300m.mix(3, 200, dest.bottom(3))
    p300m.drop_tip()

    # Step 4 calculation: prepare ligand dilutions in the deep-well mixing plate.
    ligand_buffer_requests = []
    ligand_high_requests = []
    ligand_low_requests = []
    ligand_mix_targets_by_row = [[] for _ in range(number_of_ligand_concentrations)]

    for ligand_row_index, ligand_conc in enumerate(ligand_concentrations):
        if ligand_conc < 0:
            raise RuntimeError('Ligand concentrations must not be negative.')
        target_ligand_conc = 2.0 * ligand_conc
        high_stock_volume = 0.0 if target_ligand_conc == 0 else final_ligand_well_volume * target_ligand_conc / ligand_stock_conc
        use_low_stock = target_ligand_conc > 0 and high_stock_volume < 20.0
        if use_low_stock:
            stock_concentration_used = ligand_stock_conc / 10.0
            stock_volume = final_ligand_well_volume * target_ligand_conc / stock_concentration_used
            if stock_volume > final_ligand_well_volume + 0.0001:
                raise RuntimeError('A requested ligand concentration is too high for the low ligand stock selected by the <20 uL rule.')
        else:
            stock_concentration_used = ligand_stock_conc
            stock_volume = high_stock_volume
            if stock_volume > final_ligand_well_volume + 0.0001:
                raise RuntimeError('A requested ligand concentration exceeds the ligand stock concentration.')
        buffer_volume = final_ligand_well_volume - stock_volume
        for salt_column_index in range(number_of_salt_concentrations):
            dest = mixing_plate.rows()[ligand_row_index][salt_column_index]
            ligand_buffer_requests.append((dest, buffer_volume))
            if stock_volume > 0.0001:
                if use_low_stock:
                    ligand_low_requests.append((dest, stock_volume))
                else:
                    ligand_high_requests.append((dest, stock_volume))
            ligand_mix_targets_by_row[ligand_row_index].append(dest)

    protocol.comment('Preparing ligand dilutions in the deep-well mixing plate with the P300 single-channel pipette.')
    single_deliver_physical(low_buffer_sources, ligand_buffer_requests, 'low salt buffer')
    stock_batch_dispense(ligand_high_source, ligand_high_requests, 'high concentration ligand stock')
    stock_batch_dispense(ligand_low_source, ligand_low_requests, 'low concentration ligand stock')

    mix_volume = min(200.0, max(20.0, final_ligand_well_volume * 0.7))
    for row_targets in ligand_mix_targets_by_row:
        p300s.pick_up_tip()
        for dest in row_targets:
            p300s.mix(3, mix_volume, dest.bottom(2))
        p300s.drop_tip()

    protocol.comment('Protocol complete.')
