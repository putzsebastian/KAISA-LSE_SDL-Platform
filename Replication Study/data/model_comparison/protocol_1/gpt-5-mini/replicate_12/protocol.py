from opentrons import protocol_api

metadata = {
    'protocolName': 'Template: Salt and Ligand Preparation',
    'author': 'Automated Protocol Generator',
    'description': 'Prepare salt gradients and ligand dilutions using placeholders for templating'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


# Placeholders (must remain literal in file for external substitution)
SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
REPLICATES = '[[REPLICATES]]'
TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Detect an unreplaced token without writing literal '[[' in the file."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    if s == '':
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Labware
    # Custom labware in slot 1 with fallback
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a simulation fallback.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tip racks
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)

    # Reservoirs
    res3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (target for step 2)
    res4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (target for step 3)
    res2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (slot 8) - contains buffers and highs
    res1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (slot 9) - ligand stocks + buffers
    res0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (slot 5) - low salt buffers

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    # Multi-channel should have access to multiple tipracks
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_slot7, tiprack_slot4, tiprack_slot10])
    # Single channel primarily uses slot 10 tips
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_slot10])

    # Parse placeholders (with simulation fallbacks)
    salt_concs = parse_list(SALT_CONCENTRATIONS, default=['0', '100', '200', '400'])
    ligand_concs = parse_list(LIGAND_CONCENTRATIONS, default=['0', '1', '10', '100', '200', '300', '400', '500'])
    replicates = parse_scalar(REPLICATES, default=3, cast=int)
    total_volume_ul = parse_scalar(TOTAL_VOLUME, default=200, cast=float)  # in uL
    salt_stock = parse_scalar(SALT_STOCK_CONCENTRATION, default=1000.0, cast=float)
    ligand_stock = parse_scalar(LIGAND_STOCK_CONCENTRATION, default=1000.0, cast=float)

    # Count placeholders
    num_salt = parse_scalar(NUMBER_OF_SALT_CONCENTRATIONS, default=len(salt_concs), cast=int)
    num_ligand = parse_scalar(NUMBER_OF_LIGAND_CONCENTRATIONS, default=len(ligand_concs), cast=int)

    if len(salt_concs) != num_salt:
        salt_concs = salt_concs[:num_salt]
    if len(ligand_concs) != num_ligand:
        ligand_concs = ligand_concs[:num_ligand]

    # Basic validation
    if replicates * num_salt > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12')

    protocol.comment(f'Parsed {num_salt} salt concentrations and {num_ligand} ligand concentrations')

    # Initialize reservoir pool tracking (uL)
    def make_pool(res_labware, name_prefix):
        return {i: {'labware': res_labware, 'well': res_labware.wells()[i], 'remaining': 14000.0, 'name': f"{name_prefix}_well_{i}"} for i in range(12)}

    pool_res2 = make_pool(res2, 'res2')
    pool_res1 = make_pool(res1, 'res1')
    pool_res0 = make_pool(res0, 'res0')

    # Helper to allocate volume from a pool (uL). Returns list of (well_obj, vol_uL) until amount is satisfied.
    def allocate_from_pool(pool, amount_uL):
        allocations = []
        remaining = amount_uL
        for idx in sorted(pool.keys()):
            if remaining <= 0:
                break
            available = pool[idx]['remaining']
            if available <= 0:
                continue
            take = min(available, remaining)
            pool[idx]['remaining'] -= take
            allocations.append((pool[idx]['well'], take))
            remaining -= take
        if remaining > 0:
            raise RuntimeError(f'Pool ran dry while trying to allocate {amount_uL} uL; {remaining} uL could not be allocated')
        return allocations

    total_per_target_ul = 10000.0  # 10 mL

    protocol.comment('STEP 2: Creating salt dilutions in Reservoir 3')
    res3_wells = res3.wells()
    res3_target_wells = []
    for i in range(num_salt):
        for r in range(replicates):
            res3_target_wells.append(res3_wells[i * replicates + r])

    idx = 0
    # Use one tip for left multi-channel for the whole step to conserve tips in simulation
    p300m.pick_up_tip()
    for i, c in enumerate(salt_concs):
        target_conc = float(c)
        frac_high = target_conc / float(salt_stock) if salt_stock != 0 else 0.0
        vol_high = total_per_target_ul * frac_high
        vol_low = total_per_target_ul - vol_high
        per_ch_high = vol_high / 8.0
        per_ch_low = vol_low / 8.0

        dests = [res3_target_wells[idx + r] for r in range(replicates)]
        idx += replicates

        protocol.comment(f'Preparing salt concentration {target_conc} (fraction high {frac_high:.4f})')

        for dest in dests:
            # allocate low, prefer res2 wells 0-5 then res0
            need_low = per_ch_low * 8.0
            remaining_low = need_low
            low_allocs = []
            for k in range(0, 6):
                if remaining_low <= 0:
                    break
                avail = pool_res2[k]['remaining']
                if avail <= 0:
                    continue
                take = min(avail, remaining_low)
                pool_res2[k]['remaining'] -= take
                low_allocs.append((pool_res2[k]['well'], take))
                remaining_low -= take
            if remaining_low > 0:
                for k in range(12):
                    if remaining_low <= 0:
                        break
                    avail = pool_res0[k]['remaining']
                    if avail <= 0:
                        continue
                    take = min(avail, remaining_low)
                    pool_res0[k]['remaining'] -= take
                    low_allocs.append((pool_res0[k]['well'], take))
                    remaining_low -= take
            if remaining_low > 0:
                raise RuntimeError('Not enough low buffer to prepare salt dilutions')
            # perform low transfers using current tip
            for src_well, vol in low_allocs:
                per_ch_chunk = vol / 8.0
                p300m.transfer(per_ch_chunk, src_well, dest, new_tip='never')

            # allocate high from pool_res2 (wells 6-11) then pool_res1
            need_high = per_ch_high * 8.0
            remaining_high = need_high
            high_allocs = []
            for k in range(6, 12):
                if remaining_high <= 0:
                    break
                avail = pool_res2[k]['remaining']
                if avail <= 0:
                    continue
                take = min(avail, remaining_high)
                pool_res2[k]['remaining'] -= take
                high_allocs.append((pool_res2[k]['well'], take))
                remaining_high -= take
            if remaining_high > 0:
                for k in range(7, 12):
                    if remaining_high <= 0:
                        break
                    avail = pool_res1[k]['remaining']
                    if avail <= 0:
                        continue
                    take = min(avail, remaining_high)
                    pool_res1[k]['remaining'] -= take
                    high_allocs.append((pool_res1[k]['well'], take))
                    remaining_high -= take
            if remaining_high > 0:
                raise RuntimeError('Not enough high buffer to prepare salt dilutions')
            for src_well, vol in high_allocs:
                per_ch_chunk = vol / 8.0
                p300m.transfer(per_ch_chunk, src_well, dest, new_tip='never')
            # mix target once
            mix_vol = min(p300m.max_volume, max(per_ch_low, per_ch_high))
            if mix_vol > 0:
                p300m.mix(3, mix_vol, dest)
    p300m.drop_tip()

    protocol.comment('STEP 3: Preparing 2x salt solutions in Reservoir 4')
    res4_wells = res4.wells()
    # pick up one tip for step 3 as well
    p300m.pick_up_tip()
    for i, c in enumerate(salt_concs):
        target_conc = float(c) * 2.0
        frac_high = target_conc / float(salt_stock) if salt_stock != 0 else 0.0
        vol_high = total_per_target_ul * frac_high
        vol_low = total_per_target_ul - vol_high
        per_ch_high = vol_high / 8.0
        per_ch_low = vol_low / 8.0
        dest = res4_wells[i]
        protocol.comment(f'Preparing Reservoir 4 well {i} at {target_conc} (2x)')
        remaining_low = per_ch_low * 8.0
        low_allocs = []
        for k in range(0, 6):
            if remaining_low <= 0:
                break
            avail = pool_res2[k]['remaining']
            if avail <= 0:
                continue
            take = min(avail, remaining_low)
            pool_res2[k]['remaining'] -= take
            low_allocs.append((pool_res2[k]['well'], take))
            remaining_low -= take
        if remaining_low > 0:
            for k in range(12):
                if remaining_low <= 0:
                    break
                avail = pool_res0[k]['remaining']
                if avail <= 0:
                    continue
                take = min(avail, remaining_low)
                pool_res0[k]['remaining'] -= take
                low_allocs.append((pool_res0[k]['well'], take))
                remaining_low -= take
        if remaining_low > 0:
            raise RuntimeError('Not enough low buffer for Reservoir 4 preparation')
        for src_well, vol in low_allocs:
            per_ch_chunk = vol / 8.0
            p300m.transfer(per_ch_chunk, src_well, dest, new_tip='never')
        remaining_high = per_ch_high * 8.0
        high_allocs = []
        for k in range(6, 12):
            if remaining_high <= 0:
                break
            avail = pool_res2[k]['remaining']
            if avail <= 0:
                continue
            take = min(avail, remaining_high)
            pool_res2[k]['remaining'] -= take
            high_allocs.append((pool_res2[k]['well'], take))
            remaining_high -= take
        if remaining_high > 0:
            for k in range(7, 12):
                if remaining_high <= 0:
                    break
                avail = pool_res1[k]['remaining']
                if avail <= 0:
                    continue
                take = min(avail, remaining_high)
                pool_res1[k]['remaining'] -= take
                high_allocs.append((pool_res1[k]['well'], take))
                remaining_high -= take
        if remaining_high > 0:
            raise RuntimeError('Not enough high buffer for Reservoir 4 preparation')
        for src_well, vol in high_allocs:
            per_ch_chunk = vol / 8.0
            p300m.transfer(per_ch_chunk, src_well, dest, new_tip='never')
        mix_vol = min(p300m.max_volume, max(per_ch_low, per_ch_high))
        if mix_vol > 0:
            p300m.mix(5, mix_vol, dest)
    p300m.drop_tip()

    protocol.comment('STEP 4: Preparing ligand dilutions in Mixing Plate')
    per_well_total = (total_volume_ul / 2.0) * replicates * 1.5
    protocol.comment(f'Per-well total volume for ligand dilutions: {per_well_total} uL')

    cols_needed = num_salt
    if cols_needed > 12:
        raise RuntimeError('Too many salt concentrations for mixing plate columns')

    for col_idx in range(cols_needed):
        for row_idx in range(num_ligand):
            well = mixing_plate.rows()[row_idx][col_idx]
            desired = float(ligand_concs[row_idx]) * 2.0
            vol_stock = per_well_total * (desired / ligand_stock) if ligand_stock != 0 else 0.0
            stock_used_pool = pool_res1
            if vol_stock < 20.0:
                low_stock_conc = ligand_stock / 10.0
                vol_stock = per_well_total * (desired / low_stock_conc) if low_stock_conc != 0 else 0.0
                stock_used_well = pool_res1[1]['well']
            vol_buffer = per_well_total - vol_stock
            protocol.comment(f'Preparing mixing well {well} with ligand {desired} -> stock vol {vol_stock:.1f} uL, buffer {vol_buffer:.1f} uL')
            # allocate stock
            stock_allocs = allocate_from_pool(pool_res1, vol_stock)
            # Use single-channel; pick up new tip per destination to be safe
            p300s.pick_up_tip()
            for src_well, vol in stock_allocs:
                p300s.transfer(vol, src_well, well, new_tip='never')
            # buffer allocation
            buffer_needed = vol_buffer
            buffer_allocs = []
            if pool_res1[2]['remaining'] > 0:
                take = min(pool_res1[2]['remaining'], buffer_needed)
                pool_res1[2]['remaining'] -= take
                buffer_allocs.append((pool_res1[2]['well'], take))
                buffer_needed -= take
            if buffer_needed > 0:
                for k in range(12):
                    if buffer_needed <= 0:
                        break
                    avail = pool_res0[k]['remaining']
                    if avail <= 0:
                        continue
                    take = min(avail, buffer_needed)
                    pool_res0[k]['remaining'] -= take
                    buffer_allocs.append((pool_res0[k]['well'], take))
                    buffer_needed -= take
            if buffer_needed > 0:
                p300s.drop_tip()
                raise RuntimeError('Not enough buffer for ligand dilutions')
            for src_well, vol in buffer_allocs:
                p300s.transfer(vol, src_well, well, new_tip='never')
            # mix and drop tip
            mix_vol = min(p300s.max_volume, per_well_total / 2)
            if mix_vol > 0:
                p300s.mix(5, mix_vol, well)
            p300s.drop_tip()

    protocol.comment('Protocol complete. All mixing steps finished.')
