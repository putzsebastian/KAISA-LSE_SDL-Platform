from opentrons import protocol_api
import math

metadata = {
    'protocolName': 'Salt and Ligand Preparation Template',
    'author': 'Lab 167',
    'description': "Prepare salt gradients and ligand dilutions using placeholders for templating"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # helper to detect unreplaced placeholders (build brackets by repetition)
    def _unreplaced(s: str) -> bool:
        return str(s).startswith('[' * 2) and str(s).endswith(']' * 2)

    def parse_scalar(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return cast(default)
        return cast(s)

    def parse_list(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return list(default)
        return [cast(x) for x in s.split(';') if x.strip()]

    # Placeholders (literal strings for templating)
    PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
    PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
    PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
    PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
    PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
    PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

    # Simulation fallbacks
    REPLICATES = parse_scalar(PLACEHOLDER_REPLICATES, 2, int)
    NUMBER_OF_SALT_CONCENTRATIONS = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, 6, int)
    NUMBER_OF_LIGAND_CONCENTRATIONS = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, 8, int)

    SALT_CONCS = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 50.0, 100.0, 200.0, 400.0, 800.0], float)
    LIGAND_CONCS = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [0.1 * (i + 1) for i in range(NUMBER_OF_LIGAND_CONCENTRATIONS)], float)

    SALT_STOCK_CONC = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    LIGAND_STOCK_CONC = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 100.0, float)

    TOTAL_VOLUME = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 1000.0, float)  # uL; fallback 1000 uL

    # Validate counts
    if REPLICATES * len(SALT_CONCS) > 12:
        raise RuntimeError('Requested REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS exceeds 12 wells in Reservoir 3')

    # Load labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a simulation fallback.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (slot 6)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (slot 3)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (slot 8)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (slot 9)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (slot 5)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack7, tiprack4])
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack10])

    # Initialize volume trackers (uL)
    def init_reservoir_tracker(reservoir, initial_uL=14000):
        return {w: initial_uL for w in reservoir.wells()}

    vol_res1 = init_reservoir_tracker(reservoir1)
    vol_res2 = init_reservoir_tracker(reservoir2)
    vol_res0 = init_reservoir_tracker(reservoir0)

    vol_res3 = {w: 0 for w in reservoir3.wells()}
    vol_res4 = {w: 0 for w in reservoir4.wells()}

    # Helper to withdraw from a pool of source wells (assumes tip already picked up for multi-channel)
    def withdraw_to_dest_multi_assume_tip(total_uL, source_wells, source_vol_map, dest_well, pipette):
        remaining = total_uL
        src_idx = 0
        while remaining > 0 and src_idx < len(source_wells):
            src = source_wells[src_idx]
            avail = source_vol_map.get(src, 0)
            if avail <= 0:
                src_idx += 1
                continue
            take = min(avail, remaining)
            per_channel = take / 8.0
            if per_channel <= 0:
                break
            pipette.transfer(per_channel, src, dest_well, new_tip='never')
            source_vol_map[src] = avail - take
            remaining -= take
            if source_vol_map[src] <= 0:
                src_idx += 1
        if remaining > 0:
            raise RuntimeError(f'Pool ran dry while trying to deliver {total_uL} uL to {dest_well}')

    def withdraw_to_dest_single_assume_tip(total_uL, source_wells, source_vol_map, dest_well, pipette):
        remaining = total_uL
        src_idx = 0
        while remaining > 0 and src_idx < len(source_wells):
            src = source_wells[src_idx]
            avail = source_vol_map.get(src, 0)
            if avail <= 0:
                src_idx += 1
                continue
            take = min(avail, remaining)
            pipette.transfer(take, src, dest_well, new_tip='never')
            source_vol_map[src] = avail - take
            remaining -= take
            if source_vol_map[src] <= 0:
                src_idx += 1
        if remaining > 0:
            raise RuntimeError(f'Pool ran dry while trying to deliver {total_uL} uL to {dest_well}')

    protocol.comment('Starting salt and ligand preparation (templated)')

    # Step 2: prepare Reservoir 3
    total_target_uL = 10000
    # high salt wells are indices 6-11 in reservoir2 and reservoir1
    high_salt_sources = [reservoir2.wells()[i] for i in range(6, 12)] + [reservoir1.wells()[i] for i in range(6, 12)]
    # low salt sources: reservoir2 wells 0-5, reservoir1 wells 2-5 (exclude wells 0-1 which are ligand stocks), reservoir0 wells 0-11
    low_salt_sources = [reservoir2.wells()[i] for i in range(0, 6)] + [reservoir1.wells()[i] for i in range(2, 6)] + [reservoir0.wells()[i] for i in range(0, 12)]

    vol_high_pool = {}
    for w in high_salt_sources:
        if w in vol_res2:
            vol_high_pool[w] = vol_res2[w]
        elif w in vol_res1:
            vol_high_pool[w] = vol_res1[w]
        else:
            vol_high_pool[w] = 14000

    vol_low_pool = {}
    for w in low_salt_sources:
        if w in vol_res2:
            vol_low_pool[w] = vol_res2[w]
        elif w in vol_res1:
            vol_low_pool[w] = vol_res1[w]
        elif w in vol_res0:
            vol_low_pool[w] = vol_res0[w]
        else:
            vol_low_pool[w] = 14000

    protocol.comment('Preparing Reservoir 3 salt series')
    res3_targets = reservoir3.wells()
    target_idx = 0
    salt_concs_sorted = sorted(SALT_CONCS)
    for conc in salt_concs_sorted:
        for rep in range(REPLICATES):
            if target_idx >= len(res3_targets):
                raise RuntimeError('Not enough wells in Reservoir 3')
            dest = res3_targets[target_idx]
            vol_high = total_target_uL * (conc / SALT_STOCK_CONC)
            vol_low = total_target_uL - vol_high
            # pick up one multi-channel tip and perform both high and low transfers, then mix and drop
            p300m.pick_up_tip()
            withdraw_to_dest_multi_assume_tip(vol_high, list(vol_high_pool.keys()), vol_high_pool, dest, p300m)
            withdraw_to_dest_multi_assume_tip(vol_low, list(vol_low_pool.keys()), vol_low_pool, dest, p300m)
            # update dest volume
            vol_res3[dest] += total_target_uL
            # mix with same tip
            try:
                p300m.mix(3, min(p300m.max_volume, 200), dest)
                p300m.blow_out(dest)
            except Exception:
                protocol.comment(f'Could not perform multi-channel mix on {dest} - simulation fallback')
            p300m.drop_tip()
            target_idx += 1

    # Step 3: prepare Reservoir 4 with 2x concentrations
    protocol.comment('Preparing Reservoir 4 2x salt series')
    res4_targets = reservoir4.wells()
    target_idx = 0
    for conc in salt_concs_sorted:
        if target_idx >= len(res4_targets):
            raise RuntimeError('Not enough wells in Reservoir 4')
        dest = res4_targets[target_idx]
        desired = min(2 * conc, SALT_STOCK_CONC)
        vol_high = total_target_uL * (desired / SALT_STOCK_CONC)
        vol_low = total_target_uL - vol_high
        p300m.pick_up_tip()
        withdraw_to_dest_multi_assume_tip(vol_high, list(vol_high_pool.keys()), vol_high_pool, dest, p300m)
        withdraw_to_dest_multi_assume_tip(vol_low, list(vol_low_pool.keys()), vol_low_pool, dest, p300m)
        vol_res4[dest] += total_target_uL
        try:
            p300m.mix(3, min(p300m.max_volume, 200), dest)
            p300m.blow_out(dest)
        except Exception:
            protocol.comment(f'Could not perform multi-channel mix on {dest} - simulation fallback')
        p300m.drop_tip()
        target_idx += 1

    # Update source trackers
    for w in list(vol_high_pool.keys()):
        if w in vol_res2:
            vol_res2[w] = vol_high_pool[w]
        if w in vol_res1:
            vol_res1[w] = vol_high_pool[w]
    for w in list(vol_low_pool.keys()):
        if w in vol_res2:
            vol_res2[w] = vol_low_pool[w]
        if w in vol_res1:
            vol_res1[w] = vol_low_pool[w]
        if w in vol_res0:
            vol_res0[w] = vol_low_pool[w]

    # Step 4: ligand dilutions in mixing plate
    protocol.comment('Preparing ligand dilutions in mixing plate')
    ligand_concs_sorted = sorted(LIGAND_CONCS)
    total_per_well = (TOTAL_VOLUME / 2.0) * REPLICATES * 1.5

    ligand_stock_high = reservoir1.wells()[0]
    ligand_stock_low = reservoir1.wells()[1]
    low_buffer_pool = [reservoir0.wells()[i] for i in range(0, 12)]

    for col_idx, salt_conc in enumerate(salt_concs_sorted):
        if col_idx >= 12:
            protocol.comment('Skipping extra salt concentrations beyond 12 columns')
            break
        for row_idx in range(8):
            if row_idx >= len(ligand_concs_sorted):
                break
            ligand_conc = ligand_concs_sorted[row_idx]
            dest = mixing_plate.rows()[row_idx][col_idx]
            desired_ligand = 2 * ligand_conc
            vol_from_stock = total_per_well * (desired_ligand / LIGAND_STOCK_CONC)
            stock_source = ligand_stock_high
            stock_pool = vol_res1
            if vol_from_stock < 20:
                LIGAND_STOCK_LOW = LIGAND_STOCK_CONC / 10.0
                vol_from_stock = total_per_well * (desired_ligand / LIGAND_STOCK_LOW)
                stock_source = ligand_stock_low
            vol_buffer = total_per_well - vol_from_stock
            if vol_buffer < 0:
                protocol.comment(f'Requested ligand stock volume for {dest} exceeds total volume; adjusting')
                vol_from_stock = total_per_well
                vol_buffer = 0
            # pick up one single-channel tip, perform stock and buffer transfers, mix and drop
            p300s.pick_up_tip()
            withdraw_to_dest_single_assume_tip(vol_from_stock, [stock_source], stock_pool, dest, p300s)
            withdraw_to_dest_single_assume_tip(vol_buffer, low_buffer_pool, vol_res0, dest, p300s)
            try:
                mix_vol = min(p300s.max_volume, max(50, total_per_well * 0.5))
                p300s.mix(3, mix_vol, dest)
                p300s.blow_out(dest)
            except Exception:
                protocol.comment(f'Could not mix {dest} - simulation fallback')
            p300s.drop_tip()

    protocol.comment('Protocol completed (templated).')
