from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare templated salt buffers in reservoirs and ligand dilutions in a deep-well mixing plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder declarations. The templating system replaces these literal strings.
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
    # -----------------------------
    # Modules
    # -----------------------------
    # No modules are used in this protocol.

    # -----------------------------
    # Labware
    # -----------------------------
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc) and 'FileNotFoundError' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition for cytiva_96_filterwellplate_1ml '
            'is not available in simulation; using NEST 96 Well Plate 200 uL Flat '
            'as a 96-well SIMULATION fallback only.'
        )
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_spare = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # empty, final 2x salt buffers
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # empty, replicate salt buffers
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # source reservoir 2
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # source reservoir 1
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # source reservoir 0
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -----------------------------
    # Pipettes
    # -----------------------------
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_single])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_multi])

    # -----------------------------
    # Parameter parsing and validation
    # Defaults are simulation fallbacks for unreplaced placeholders.
    # -----------------------------
    default_salt_concs = [0.0, 100.0, 200.0, 300.0]
    default_ligand_concs = [0.0, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0]

    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 3, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 10000.0, float)

    salt_concs_all = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default_salt_concs, float)
    ligand_concs_all = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, default_ligand_concs, float)

    number_salt = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(default_salt_concs), int)
    number_ligand = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(default_ligand_concs), int)

    if replicates < 1:
        raise RuntimeError('[[REPLICATES]] must be at least 1.')
    if number_salt < 1 or number_salt > 12:
        raise RuntimeError('[[NUMBER_OF_SALT_CONCENTRATIONS]] must be between 1 and 12.')
    if number_ligand < 1 or number_ligand > 8:
        raise RuntimeError('[[NUMBER_OF_LIGAND_CONCENTRATIONS]] must be between 1 and 8.')
    if len(salt_concs_all) != number_salt:
        raise RuntimeError('[[SALT_CONCENTRATIONS]] length must match [[NUMBER_OF_SALT_CONCENTRATIONS]].')
    if len(ligand_concs_all) != number_ligand:
        raise RuntimeError('[[LIGAND_CONCENTRATIONS]] length must match [[NUMBER_OF_LIGAND_CONCENTRATIONS]].')
    if replicates * number_salt > 12:
        raise RuntimeError('[[REPLICATES]] x [[NUMBER_OF_SALT_CONCENTRATIONS]] must not exceed 12.')
    if salt_stock_conc <= 0 or ligand_stock_conc <= 0:
        raise RuntimeError('Stock concentrations must be greater than zero.')
    if total_volume <= 0:
        raise RuntimeError('[[TOTAL_VOLUME]] must be greater than zero.')

    salt_concs = sorted(salt_concs_all)
    ligand_concs = sorted(ligand_concs_all)

    reservoir_total_volume = 10000.0  # uL total actual liquid per reservoir well to prepare
    per_channel_reservoir_total = reservoir_total_volume / 8.0
    dilution_total_volume = total_volume / 2.0 * replicates * 1.5

    if dilution_total_volume > 2000.0:
        raise RuntimeError('Each mixing-plate well would exceed 2000 uL. Reduce TOTAL_VOLUME or REPLICATES.')

    for salt in salt_concs:
        if salt < 0:
            raise RuntimeError('Salt concentrations must not be negative.')
        if salt > salt_stock_conc:
            raise RuntimeError('A requested salt concentration exceeds the salt stock concentration.')
        if 2.0 * salt > salt_stock_conc:
            raise RuntimeError('A requested 2x salt concentration exceeds the salt stock concentration.')

    low_ligand_stock_conc = ligand_stock_conc / 10.0
    for ligand in ligand_concs:
        if ligand < 0:
            raise RuntimeError('Ligand concentrations must not be negative.')
        if 2.0 * ligand > ligand_stock_conc:
            raise RuntimeError('A requested 2x ligand concentration exceeds the high ligand stock concentration.')

    # -----------------------------
    # Liquids and initial source volumes
    # -----------------------------
    low_salt_liquid = protocol.define_liquid(
        name='Low salt buffer',
        description='0 salt buffer',
        display_color='#66CCFF'
    )
    high_salt_liquid = protocol.define_liquid(
        name='High salt buffer',
        description='High salt buffer at stock concentration',
        display_color='#FFCC33'
    )
    ligand_high_liquid = protocol.define_liquid(
        name='Ligand stock high',
        description='High concentration ligand stock solution',
        display_color='#CC66FF'
    )
    ligand_low_liquid = protocol.define_liquid(
        name='Ligand stock low',
        description='Low concentration ligand stock solution',
        display_color='#9966FF'
    )

    def pool_entry(well, remaining_ul, dead_ul, label, liquid=None):
        if liquid is not None:
            well.load_liquid(liquid=liquid, volume=remaining_ul)
        return {'well': well, 'remaining': float(remaining_ul), 'dead': float(dead_ul), 'label': label}

    low_pool = []
    high_pool = []

    # Reservoir 2: wells 0-5 low, wells 6-11 high.
    for i in range(0, 6):
        low_pool.append(pool_entry(reservoir2.wells()[i], 14000.0, 100.0, 'low salt buffer', low_salt_liquid))
    for i in range(6, 12):
        high_pool.append(pool_entry(reservoir2.wells()[i], 14000.0, 100.0, 'high salt buffer', high_salt_liquid))

    # Reservoir 1: well 0 high ligand, well 1 low ligand, wells 2-6 low, wells 7-11 high.
    ligand_high_pool = [pool_entry(reservoir1.wells()[0], 14000.0, 20.0, 'high ligand stock', ligand_high_liquid)]
    ligand_low_pool = [pool_entry(reservoir1.wells()[1], 14000.0, 20.0, 'low ligand stock', ligand_low_liquid)]
    for i in range(2, 7):
        low_pool.append(pool_entry(reservoir1.wells()[i], 14000.0, 100.0, 'low salt buffer', low_salt_liquid))
    for i in range(7, 12):
        high_pool.append(pool_entry(reservoir1.wells()[i], 14000.0, 100.0, 'high salt buffer', high_salt_liquid))

    # Reservoir 0: all wells low salt buffer.
    for i in range(0, 12):
        low_pool.append(pool_entry(reservoir0.wells()[i], 14000.0, 100.0, 'low salt buffer', low_salt_liquid))

    def available_from_pool(pool):
        return sum(max(0.0, entry['remaining'] - entry['dead']) for entry in pool)

    def next_available_entry(pool, required_label):
        for entry in pool:
            if entry['remaining'] > entry['dead'] + 0.01:
                return entry
        raise RuntimeError(
            'Insufficient volume in pool: ' + required_label +
            '. Pool is exhausted before all requested transfers could be completed.'
        )

    def consume_multi_pool_one_tip(pool, requests, label):
        active_requests = [(dest, float(volume_pc)) for dest, volume_pc in requests if volume_pc > 0.01]
        if not active_requests:
            return
        protocol.comment('Multi-channel transfer from pool: ' + label)
        p300_multi.pick_up_tip()
        try:
            for dest, volume_pc_remaining in active_requests:
                while volume_pc_remaining > 0.01:
                    entry = next_available_entry(pool, label)
                    available_pc = (entry['remaining'] - entry['dead']) / 8.0
                    chunk_pc = min(p300_multi.max_volume, volume_pc_remaining, available_pc)
                    if chunk_pc <= 0.01:
                        entry['remaining'] = entry['dead']
                        continue
                    p300_multi.aspirate(chunk_pc, entry['well'].bottom(2))
                    p300_multi.dispense(chunk_pc, dest.bottom(5))
                    entry['remaining'] -= chunk_pc * 8.0
                    volume_pc_remaining -= chunk_pc
        finally:
            p300_multi.drop_tip()

    def consume_single_pool_current_tip(pool, volume_ul, dest, pipette, label):
        remaining = float(volume_ul)
        if remaining <= 0.01:
            return
        if available_from_pool(pool) + 0.01 < remaining:
            raise RuntimeError('Insufficient volume in pool: ' + label + '.')
        while remaining > 0.01:
            entry = next_available_entry(pool, label)
            available = entry['remaining'] - entry['dead']
            chunk = min(pipette.max_volume, remaining, available)
            if chunk <= 0.01:
                entry['remaining'] = entry['dead']
                continue
            pipette.aspirate(chunk, entry['well'].bottom(2))
            pipette.dispense(chunk, dest.bottom(2))
            entry['remaining'] -= chunk
            remaining -= chunk

    def mix_multi_targets(targets):
        if not targets:
            return
        p300_multi.pick_up_tip()
        try:
            for target in targets:
                p300_multi.mix(5, 250.0, target.bottom(5))
        finally:
            p300_multi.drop_tip()

    # -----------------------------
    # Steps 2 and 3: calculate reservoir salt buffer recipes and prepare them.
    # -----------------------------
    res3_low_requests = []
    res3_high_requests = []
    res3_targets = []

    for salt_index, salt in enumerate(salt_concs):
        high_total_ul = reservoir_total_volume * salt / salt_stock_conc
        low_total_ul = reservoir_total_volume - high_total_ul
        high_pc = high_total_ul / 8.0
        low_pc = low_total_ul / 8.0
        for rep_index in range(replicates):
            dest_index = salt_index * replicates + rep_index
            dest = reservoir3.columns()[dest_index][0]
            res3_targets.append(dest)
            res3_low_requests.append((dest, low_pc))
            res3_high_requests.append((dest, high_pc))
            protocol.comment(
                'Reservoir 3 ' + dest.well_name + ': prepare ' + str(salt) +
                ' salt, total 10000 uL actual volume.'
            )

    res4_low_requests = []
    res4_high_requests = []
    res4_targets = []

    for salt_index, salt in enumerate(salt_concs):
        target_2x_salt = 2.0 * salt
        high_total_ul = reservoir_total_volume * target_2x_salt / salt_stock_conc
        low_total_ul = reservoir_total_volume - high_total_ul
        high_pc = high_total_ul / 8.0
        low_pc = low_total_ul / 8.0
        dest = reservoir4.columns()[salt_index][0]
        res4_targets.append(dest)
        res4_low_requests.append((dest, low_pc))
        res4_high_requests.append((dest, high_pc))
        protocol.comment(
            'Reservoir 4 ' + dest.well_name + ': prepare 2x salt ' + str(target_2x_salt) +
            ', total 10000 uL actual volume.'
        )

    consume_multi_pool_one_tip(low_pool, res3_low_requests + res4_low_requests, 'low salt buffer')
    consume_multi_pool_one_tip(high_pool, res3_high_requests + res4_high_requests, 'high salt buffer')
    mix_multi_targets(res3_targets + res4_targets)

    # -----------------------------
    # Step 4: prepare 2x ligand dilutions in the deep-well mixing plate.
    # Rows A-H are ligand concentrations ascending; columns follow salt concentrations.
    # -----------------------------
    for row_index, ligand in enumerate(ligand_concs):
        target_2x_ligand = 2.0 * ligand
        high_stock_volume = 0.0
        low_stock_volume = 0.0
        ligand_pool = None
        ligand_label = ''

        if target_2x_ligand > 0.0:
            high_stock_volume = dilution_total_volume * target_2x_ligand / ligand_stock_conc
            if high_stock_volume < 20.0:
                low_stock_volume = dilution_total_volume * target_2x_ligand / low_ligand_stock_conc
                if low_stock_volume > dilution_total_volume + 0.01:
                    raise RuntimeError('Low ligand stock is not concentrated enough for a requested dilution.')
                ligand_pool = ligand_low_pool
                ligand_label = 'low ligand stock'
                ligand_volume = low_stock_volume
            else:
                ligand_pool = ligand_high_pool
                ligand_label = 'high ligand stock'
                ligand_volume = high_stock_volume
        else:
            ligand_volume = 0.0
            ligand_label = 'no ligand'

        buffer_volume = dilution_total_volume - ligand_volume
        if buffer_volume < -0.01:
            raise RuntimeError('Calculated ligand stock volume exceeds the requested final dilution volume.')
        if buffer_volume < 0.0:
            buffer_volume = 0.0

        for col_index in range(number_salt):
            dest = mixing_plate.rows()[row_index][col_index]
            protocol.comment(
                'Mixing plate ' + dest.well_name + ': prepare 2x ligand concentration ' +
                str(target_2x_ligand) + ', final volume ' + str(dilution_total_volume) +
                ' uL, using ' + ligand_label + '.'
            )
            p300_single.pick_up_tip()
            try:
                # Add buffer before ligand so the tip never returns to a buffer source after touching ligand.
                if buffer_volume > 0.01:
                    consume_single_pool_current_tip(low_pool, buffer_volume, dest, p300_single, 'low salt buffer')
                if ligand_volume > 0.01:
                    consume_single_pool_current_tip(ligand_pool, ligand_volume, dest, p300_single, ligand_label)
                mix_volume = min(200.0, max(10.0, dilution_total_volume * 0.5))
                p300_single.mix(5, mix_volume, dest.bottom(2))
            finally:
                p300_single.drop_tip()

    protocol.comment('Protocol complete. Reservoir source volumes were tracked in software during the run.')
