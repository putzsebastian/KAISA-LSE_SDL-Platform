from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare salt buffers in reservoirs and ligand dilution series in a deep-well mixing plate using placeholder parameters.'
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
        if cast is int:
            return int(float(s))
        return cast(float(s))

    def parse_list(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return list(default)
        return [cast(float(x.strip())) for x in s.split(';') if x.strip()]

    # Worst-case simulation defaults: 4 salt concentrations x 3 replicates fills all 12 wells
    # of Reservoir 3, and 8 ligand concentrations fills rows A-H of the mixing plate.
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 3, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 100.0, 200.0, 300.0], float)
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [0.0, 1.0, 10.0, 20.0, 50.0, 100.0, 200.0, 400.0], float)
    number_of_salt_concs = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concs), int)
    number_of_ligand_concs = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concs), int)

    salt_concs = sorted(salt_concs)
    ligand_concs = sorted(ligand_concs)

    if number_of_salt_concs != len(salt_concs):
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS does not match SALT_CONCENTRATIONS length.')
    if number_of_ligand_concs != len(ligand_concs):
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS does not match LIGAND_CONCENTRATIONS length.')
    if replicates < 1:
        raise RuntimeError('REPLICATES must be at least 1.')
    if replicates * number_of_salt_concs > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 for Reservoir 3.')
    if number_of_salt_concs > 12:
        raise RuntimeError('At most 12 salt concentrations can be represented as mixing-plate columns.')
    if number_of_ligand_concs > 8:
        raise RuntimeError('At most 8 ligand concentrations can be represented as mixing-plate rows A-H.')
    if salt_stock_conc <= 0 or ligand_stock_conc <= 0:
        raise RuntimeError('Stock concentrations must be greater than 0.')

    ligand_well_total = total_volume / 2.0 * replicates * 1.5
    if ligand_well_total > 2000.0:
        raise RuntimeError('Calculated ligand dilution volume exceeds 2 mL deep-well capacity.')
    if ligand_well_total < 0:
        raise RuntimeError('Calculated ligand dilution volume must not be negative.')

    # -------------------------
    # Labware
    # -------------------------
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        text = str(exc).lower()
        if 'not found' not in text and 'filenotfounderror' not in text and 'no such file' not in text:
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; using a 96-well standard plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # empty, for 2x salt buffers
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # empty, for replicate salt buffers
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # low and high salt buffer stocks
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # ligand stocks plus buffers
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # low salt buffer stocks
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------
    # Pipettes
    # -------------------------
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # Keep slot 4 loaded per deck layout; liquid handling requirements specify slot 7 for multi
    # and slot 10 for single-channel operations.
    _ = tiprack_slot4
    _ = filter_plate

    # -------------------------
    # Source pools and helpers
    # -------------------------
    INITIAL_SOURCE_VOLUME_UL = 14000.0
    LOW_DEAD_VOLUME_UL = 0.0

    def make_pool(entries):
        return [{'well': well, 'remaining': INITIAL_SOURCE_VOLUME_UL - dead} for well, dead in entries]

    low_pool = make_pool(
        [(well, LOW_DEAD_VOLUME_UL) for well in reservoir2.wells()[:6]] +
        [(well, LOW_DEAD_VOLUME_UL) for well in reservoir1.wells()[2:7]] +
        [(well, LOW_DEAD_VOLUME_UL) for well in reservoir0.wells()[:12]]
    )
    high_salt_pool = make_pool(
        [(well, LOW_DEAD_VOLUME_UL) for well in reservoir2.wells()[6:12]] +
        [(well, LOW_DEAD_VOLUME_UL) for well in reservoir1.wells()[7:12]]
    )
    ligand_high_pool = make_pool([(reservoir1.wells()[0], LOW_DEAD_VOLUME_UL)])
    ligand_low_pool = make_pool([(reservoir1.wells()[1], LOW_DEAD_VOLUME_UL)])

    def pool_available(pool):
        return sum(entry['remaining'] for entry in pool)

    def ensure_pool(pool, needed, label):
        available = pool_available(pool)
        if available + 0.000001 < needed:
            raise RuntimeError(label + ' pool is short by ' + str(round(needed - available, 3)) + ' uL.')

    def consume_from_pool(pool, total_ul, label):
        if total_ul <= 0:
            return []
        ensure_pool(pool, total_ul, label)
        remaining = total_ul
        plan = []
        for entry in pool:
            if remaining <= 0:
                break
            if entry['remaining'] <= 0:
                continue
            take = min(entry['remaining'], remaining)
            entry['remaining'] -= take
            remaining -= take
            plan.append((entry['well'], take))
        if remaining > 0.000001:
            raise RuntimeError(label + ' pool is unexpectedly exhausted.')
        return plan

    def multi_deliver_total(pool, total_ul, dest, label):
        if total_ul <= 0:
            return
        plan = consume_from_pool(pool, total_ul, label)
        channel_factor = 8.0
        for source, planned_total in plan:
            remaining_total = planned_total
            while remaining_total > 0.000001:
                chunk_total = min(remaining_total, p300_multi.max_volume * channel_factor)
                per_channel = chunk_total / channel_factor
                p300_multi.aspirate(per_channel, source)
                p300_multi.dispense(per_channel, dest)
                remaining_total -= chunk_total

    def single_deliver_total(pool, total_ul, dest, label):
        if total_ul <= 0:
            return
        plan = consume_from_pool(pool, total_ul, label)
        for source, planned_total in plan:
            remaining = planned_total
            while remaining > 0.000001:
                chunk = min(remaining, p300_single.max_volume)
                p300_single.aspirate(chunk, source)
                p300_single.dispense(chunk, dest)
                remaining -= chunk

    def salt_mix_volumes(target_conc, final_total_ul, source_stock_conc):
        if target_conc < 0:
            raise RuntimeError('Salt concentrations must not be negative.')
        fraction_high = target_conc / source_stock_conc
        if fraction_high < -0.000001 or fraction_high > 1.000001:
            raise RuntimeError('Requested salt concentration ' + str(target_conc) + ' exceeds available salt stock concentration ' + str(source_stock_conc) + '.')
        high_ul = final_total_ul * fraction_high
        low_ul = final_total_ul - high_ul
        if low_ul < 0 and low_ul > -0.000001:
            low_ul = 0.0
        return low_ul, high_ul

    # -------------------------
    # Steps 2 and 3 calculations
    # -------------------------
    salt_targets_res3 = []
    target_index = 0
    for salt_conc in salt_concs:
        low_ul, high_ul = salt_mix_volumes(salt_conc, 10000.0, salt_stock_conc)
        for rep in range(replicates):
            salt_targets_res3.append({'well': reservoir3.wells()[target_index], 'low_ul': low_ul, 'high_ul': high_ul, 'label': 'Reservoir 3 salt ' + str(salt_conc)})
            target_index += 1

    salt_targets_res4 = []
    for index, salt_conc in enumerate(salt_concs):
        low_ul, high_ul = salt_mix_volumes(2.0 * salt_conc, 10000.0, salt_stock_conc)
        salt_targets_res4.append({'well': reservoir4.wells()[index], 'low_ul': low_ul, 'high_ul': high_ul, 'label': 'Reservoir 4 2x salt ' + str(2.0 * salt_conc)})

    all_salt_targets = salt_targets_res3 + salt_targets_res4
    total_low_needed = sum(item['low_ul'] for item in all_salt_targets)
    total_high_needed = sum(item['high_ul'] for item in all_salt_targets)
    ensure_pool(low_pool, total_low_needed, 'Low salt buffer')
    ensure_pool(high_salt_pool, total_high_needed, 'High salt buffer')

    protocol.comment('Preparing Reservoir 3 and Reservoir 4 salt buffers with the multi-channel pipette.')
    p300_multi.pick_up_tip()
    for item in all_salt_targets:
        multi_deliver_total(low_pool, item['low_ul'], item['well'], 'Low salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for item in all_salt_targets:
        multi_deliver_total(high_salt_pool, item['high_ul'], item['well'], 'High salt buffer')
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for item in all_salt_targets:
        p300_multi.mix(3, 250.0, item['well'])
    p300_multi.drop_tip()

    # -------------------------
    # Step 4: ligand dilution plate calculations and preparation
    # -------------------------
    ligand_targets = []
    for row_index, ligand_conc in enumerate(ligand_concs):
        target_ligand_conc = 2.0 * ligand_conc
        if target_ligand_conc < 0:
            raise RuntimeError('Ligand concentrations must not be negative.')
        if target_ligand_conc == 0:
            ligand_source_pool = None
            ligand_ul = 0.0
        else:
            high_stock_ul = ligand_well_total * target_ligand_conc / ligand_stock_conc
            if high_stock_ul >= 20.0:
                ligand_source_pool = ligand_high_pool
                ligand_ul = high_stock_ul
            else:
                low_stock_conc = ligand_stock_conc / 10.0
                ligand_source_pool = ligand_low_pool
                ligand_ul = ligand_well_total * target_ligand_conc / low_stock_conc
        if ligand_ul > ligand_well_total + 0.000001:
            raise RuntimeError('Requested 2x ligand concentration ' + str(target_ligand_conc) + ' exceeds selected ligand stock concentration.')
        buffer_ul = ligand_well_total - ligand_ul
        for col_index, salt_conc in enumerate(salt_concs):
            ligand_targets.append({
                'well': mixing_plate.rows()[row_index][col_index],
                'buffer_ul': buffer_ul,
                'ligand_ul': ligand_ul,
                'ligand_pool': ligand_source_pool,
                'ligand_conc': ligand_conc,
                'salt_conc': salt_conc
            })

    total_low_for_ligands = sum(item['buffer_ul'] for item in ligand_targets)
    total_ligand_high = sum(item['ligand_ul'] for item in ligand_targets if item['ligand_pool'] is ligand_high_pool)
    total_ligand_low = sum(item['ligand_ul'] for item in ligand_targets if item['ligand_pool'] is ligand_low_pool)
    ensure_pool(low_pool, total_low_for_ligands, 'Low salt buffer')
    ensure_pool(ligand_high_pool, total_ligand_high, 'High concentration ligand stock')
    ensure_pool(ligand_low_pool, total_ligand_low, 'Low concentration ligand stock')

    protocol.comment('Preparing 2x ligand dilutions in the deep-well mixing plate with the single-channel pipette.')
    for item in ligand_targets:
        if item['buffer_ul'] > 0:
            p300_single.pick_up_tip()
            single_deliver_total(low_pool, item['buffer_ul'], item['well'], 'Low salt buffer')
            p300_single.drop_tip()

    for item in ligand_targets:
        if item['ligand_ul'] > 0:
            p300_single.pick_up_tip()
            single_deliver_total(item['ligand_pool'], item['ligand_ul'], item['well'], 'Ligand stock')
            mix_vol = min(200.0, max(1.0, ligand_well_total * 0.5))
            p300_single.mix(3, mix_vol, item['well'])
            p300_single.drop_tip()

    # Mix ligand wells containing no ligand stock after buffer-only addition.
    for item in ligand_targets:
        if item['ligand_ul'] == 0 and item['buffer_ul'] > 0:
            p300_single.pick_up_tip()
            mix_vol = min(200.0, max(1.0, ligand_well_total * 0.5))
            p300_single.mix(3, mix_vol, item['well'])
            p300_single.drop_tip()

    protocol.comment('Protocol complete.')
