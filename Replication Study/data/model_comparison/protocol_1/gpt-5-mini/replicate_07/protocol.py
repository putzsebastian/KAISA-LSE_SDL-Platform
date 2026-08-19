from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Preparation',
    'author': 'Lab 167',
    'description': "Prepare salt gradients and ligand dilutions using placeholders",
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Placeholders (declared as literal strings so a wizard can substitute them)
    REPLICATES = '[[REPLICATES]]'
    TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
    SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
    LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
    SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
    NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

    # Helpers to detect unreplaced tokens (build brackets by repetition)
    def _unreplaced(s: str) -> bool:
        s = str(s).strip()
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
        return [cast(x) for x in s.split(';') if x.strip()]

    # Simulation-safe fallbacks (worst-case sensible defaults)
    DEFAULT_SALT_CONCS = [0.0, 50.0, 150.0, 300.0]
    DEFAULT_LIGAND_CONCS = [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0]
    DEFAULT_REPLICATES = 3
    DEFAULT_TOTAL_VOLUME = 100.0  # uL - per well in mixing plate fallback

    # Parse placeholders (or use fallbacks for simulation)
    replicates = int(parse_scalar(REPLICATES, DEFAULT_REPLICATES, cast=int))
    total_volume = float(parse_scalar(TOTAL_VOLUME, DEFAULT_TOTAL_VOLUME, cast=float))
    salt_concs = parse_list(SALT_CONCENTRATIONS, DEFAULT_SALT_CONCS, cast=float)
    ligand_concs = parse_list(LIGAND_CONCENTRATIONS, DEFAULT_LIGAND_CONCS, cast=float)
    salt_stock = float(parse_scalar(SALT_STOCK_CONCENTRATION, 1000.0, cast=float))
    ligand_stock = float(parse_scalar(LIGAND_STOCK_CONCENTRATION, 1000.0, cast=float))
    num_salt_concs = int(parse_scalar(NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concs), cast=int))
    num_ligand_concs = int(parse_scalar(NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concs), cast=int))

    # Normalize counts
    if num_salt_concs != len(salt_concs):
        num_salt_concs = len(salt_concs)
    if num_ligand_concs != len(ligand_concs):
        num_ligand_concs = len(ligand_concs)

    if replicates * num_salt_concs > 12:
        raise RuntimeError('Requested replicates x number of salt concentrations exceeds 12 reservoir wells')

    # Labware loading
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs per user mapping
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Slot 3: Reservoir 4 (target for 2x salt)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Slot 6: Reservoir 3 (target for salt replicates)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Slot 8: Reservoir 2 (contains low & high salt)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Slot 9: Reservoir 1 (ligand stocks + buffers)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Slot 5: Reservoir 0 (low salt buffers)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes - provide multiple tipracks to multi-channel to avoid OutOfTips
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack4, tiprack7, tiprack10])
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack10])

    # Pools with remaining volumes (uL), initial 14 mL per specified well
    def init_pool(labware, indices):
        return [{'labware': labware, 'index': i, 'remaining': 14000.0} for i in indices]

    # low salt pool: reservoir2 wells 0-5, reservoir1 wells 2-6, reservoir0 wells 0-11
    low_pool = []
    low_pool += init_pool(reservoir2, list(range(0, 6)))
    low_pool += init_pool(reservoir1, list(range(2, 7)))
    low_pool += init_pool(reservoir0, list(range(0, 12)))

    # high salt pool: reservoir2 wells 6-11, reservoir1 wells 7-11
    high_pool = []
    high_pool += init_pool(reservoir2, list(range(6, 12)))
    high_pool += init_pool(reservoir1, list(range(7, 12)))

    # ligand stock wells
    ligand_stock_high = {'labware': reservoir1, 'index': 0, 'remaining': 14000.0}
    ligand_stock_low = {'labware': reservoir1, 'index': 1, 'remaining': 14000.0}

    protocol.comment(f'Parameters (simulation if unreplaced): replicates={replicates}, total_volume={total_volume} uL, salt_concs={salt_concs[:num_salt_concs]}, ligand_concs={ligand_concs[:num_ligand_concs]}')

    # Helper to transfer from a pool to a single destination using multi-channel pipette
    def transfer_from_pool_multi(pipette, pool, total_uL, dest):
        remaining = total_uL
        for entry in pool:
            if remaining <= 0:
                break
            avail = entry['remaining']
            if avail <= 0:
                continue
            take = min(avail, remaining)
            # perform transfer: per-channel volume
            per_channel = take / 8.0
            if per_channel > 0:
                pipette.transfer(per_channel, entry['labware'].wells()[entry['index']], dest, new_tip='never')
            entry['remaining'] -= take
            remaining -= take
        if remaining > 1e-6:
            raise RuntimeError(f'Pool exhausted: short {remaining} uL')

    # STEP 2: For each salt concentration, fill up `replicates` wells in Reservoir 3 with total 10 mL each
    total_uL_step2 = 10000.0
    dest_wells_r3 = reservoir3.wells()
    targets_r3 = []
    idx = 0
    for c in salt_concs[:num_salt_concs]:
        for r in range(replicates):
            targets_r3.append((c, dest_wells_r3[idx]))
            idx += 1

    for conc, dest in targets_r3:
        v_high = total_uL_step2 * (conc / salt_stock)
        v_low = total_uL_step2 - v_high
        per_channel_high = v_high / 8.0
        per_channel_low = v_low / 8.0
        protocol.comment(f'R3: preparing {dest} conc={conc} total={total_uL_step2}uL high={v_high}uL low={v_low}uL')
        p300m.pick_up_tip()
        if v_high > 0:
            transfer_from_pool_multi(p300m, high_pool, v_high, dest)
        if v_low > 0:
            transfer_from_pool_multi(p300m, low_pool, v_low, dest)
        # mix
        mix_vol = min(p300m.max_volume, max(per_channel_high + per_channel_low, 50))
        p300m.mix(3, mix_vol, dest)
        p300m.drop_tip()

    # STEP 3: For each salt concentration, fill up 1 well in Reservoir 4 with 2x required concentration, total 10 mL
    total_uL_step3 = 10000.0
    dest_wells_r4 = reservoir4.wells()
    targets_r4 = []
    # ascending concentrations in reservoir4
    for i, c in enumerate(salt_concs[:num_salt_concs]):
        targets_r4.append((2 * c, dest_wells_r4[i]))

    for target_conc, dest in targets_r4:
        v_high = total_uL_step3 * (target_conc / salt_stock)
        v_low = total_uL_step3 - v_high
        protocol.comment(f'R4: preparing {dest} target_conc={target_conc}u (2x) total={total_uL_step3}uL high={v_high}uL low={v_low}uL')
        p300m.pick_up_tip()
        if v_high > 0:
            transfer_from_pool_multi(p300m, high_pool, v_high, dest)
        if v_low > 0:
            transfer_from_pool_multi(p300m, low_pool, v_low, dest)
        mix_vol = min(p300m.max_volume, max((v_high + v_low) / 8.0, 50))
        p300m.mix(3, mix_vol, dest)
        p300m.drop_tip()

    # STEP 4: Prepare ligand dilutions in mixing plate (nest 96 deep) using single-channel pipette
    # Layout: for each salt concentration -> one column; rows A-H are ligand concentrations ascending
    cols_needed = num_salt_concs
    rows_needed = min(8, num_ligand_concs)
    if num_ligand_concs > 8:
        protocol.comment('Warning: more than 8 ligand concentrations given; only first 8 will be used (rows A-H)')

    # total volume per well = (TOTAL_VOLUME / 2) * REPLICATES * 1.5
    total_per_well = (total_volume / 2.0) * replicates * 1.5

    # helper to get mixing plate well by row index (0-7) and col index (0..)
    def mixing_well(row_idx, col_idx):
        return mixing_plate.rows()[row_idx][col_idx]

    for col_idx in range(cols_needed):
        for row_idx in range(rows_needed):
            ligand_conc = ligand_concs[row_idx]
            target_conc = 2.0 * ligand_conc
            # compute volume of stock required
            v_stock_high = total_per_well * (target_conc / ligand_stock) if ligand_stock > 0 else 0
            use_low_stock = False
            if v_stock_high < 20.0:
                # switch to low-concentration stock (stock/10)
                ligand_stock_low_conc = ligand_stock / 10.0 if ligand_stock > 0 else 0
                v_stock = total_per_well * (target_conc / ligand_stock_low_conc) if ligand_stock_low_conc > 0 else 0
                stock_source = ligand_stock_low
                stock_key = 'low'
                use_low_stock = True
            else:
                v_stock = v_stock_high
                stock_source = ligand_stock_high
                stock_key = 'high'

            v_buffer = total_per_well - v_stock
            if v_buffer < 0:
                raise RuntimeError('Requested ligand volume exceeds total per well; adjust concentrations or total_volume')

            dest = mixing_well(row_idx, col_idx)
            protocol.comment(f'Mixing plate {dest}: target_ligand={target_conc} total={total_per_well}uL stock={v_stock}uL buffer={v_buffer}uL using stock {stock_key}')

            # perform transfers with single-channel pipette
            p300s.pick_up_tip()
            # transfer stock
            if v_stock > 0:
                stock_source['remaining'] -= v_stock
                p300s.transfer(v_stock, stock_source['labware'].wells()[stock_source['index']], dest, new_tip='never')
            # transfer buffer (use low_pool sources)
            remaining_buf = v_buffer
            for entry in low_pool:
                if remaining_buf <= 0:
                    break
                avail = entry['remaining']
                if avail <= 0:
                    continue
                take = min(avail, remaining_buf)
                entry['remaining'] -= take
                p300s.transfer(take, entry['labware'].wells()[entry['index']], dest, new_tip='never')
                remaining_buf -= take
            if remaining_buf > 1e-6:
                raise RuntimeError(f'Low buffer pool exhausted when preparing {dest}; short {remaining_buf} uL')

            # mix
            mix_vol = min(p300s.max_volume, max(total_per_well * 0.5, 50))
            p300s.mix(3, mix_vol, dest)
            p300s.drop_tip()

    protocol.comment('Protocol complete')
