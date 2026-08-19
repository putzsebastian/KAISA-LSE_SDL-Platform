from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Preparation',
    'author': 'User',
    'description': 'Templated protocol to prepare salt gradients in reservoirs and ligand dilutions in a deepwell plate using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONC = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONC = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUM_SALT_CONC = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIGAND_CONC = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --- Parameter parsing (simulation uses worst-case fallbacks) ---
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=4)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=200.0)  # uL per assay well
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=[0, 50, 100, 150])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, default=[0, 1, 2, 3, 4, 5, 6, 7])
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONC, default=1000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, default=1000.0)
    num_salt_conc = parse_int(PLACEHOLDER_NUM_SALT_CONC, default=len(salt_concs))
    num_ligand_conc = parse_int(PLACEHOLDER_NUM_LIGAND_CONC, default=len(ligand_concs))

    # Safety checks (simulation only comments)
    if replicates * num_salt_conc > 12:
        protocol.comment('WARNING: replicates * number_of_salt_concentrations exceeds 12; adjust parameters.')

    # --- Load labware ---
    # Slot 1: custom Cytiva 96 filter plate with a simulation fallback
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Slot 4,7,10: tipracks 300 uL
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (all low salt)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (ligand + buffers)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (low/high salt stocks)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (to be filled salt buffers)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (2x salt buffers)

    # Mixing deepwell plate in slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # --- Load pipettes ---
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_slot10])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_slot7])

    # --- Reservoir content bookkeeping (uL) ---
    # Each reservoir well starts with 14 mL = 14000 uL where specified
    initial_vol = 14000.0

    # Reservoir 2: wells 0-5 low salt, 6-11 high salt
    res2_remaining = {i: initial_vol for i in range(12)}

    # Reservoir 1: ligand and mixed buffers
    res1_remaining = {i: initial_vol for i in range(12)}

    # Reservoir 0: all low salt buffers
    res0_remaining = {i: initial_vol for i in range(12)}

    # Reservoir 3 & 4: start empty, track as they are filled (for completeness)
    res3_remaining = {i: 0.0 for i in range(12)}
    res4_remaining = {i: 0.0 for i in range(12)}

    # Helper to get reservoir well object by (reservoir, index)
    def get_reservoir_well(reservoir_labware, index: int):
        return reservoir_labware.wells()[index]

    # Helper: pooled volume management across low/high salt pools
    def withdraw_from_pools(volume_per_channel: float, high_salt: bool, description: str):
        """Withdraw volume_per_channel (uL per channel) from pooled wells.

        For multi-channel use with single-row reservoirs: one aspiration removes
        8 * volume_per_channel from the source well.
        This helper will split across wells if needed.
        """
        remaining = volume_per_channel * 8.0
        moves = []

        if high_salt:
            pools = [
                (res2_remaining, reservoir2, list(range(6, 12))),  # Reservoir 2 high salt
                (res1_remaining, reservoir1, list(range(7, 12)))   # Reservoir 1 high salt
            ]
        else:
            pools = [
                (res2_remaining, reservoir2, list(range(0, 6))),   # Reservoir 2 low salt
                (res1_remaining, reservoir1, list(range(2, 7))),   # Reservoir 1 low salt
                (res0_remaining, reservoir0, list(range(0, 12)))   # Reservoir 0 low salt
            ]

        for pool_dict, labware, idx_list in pools:
            if remaining <= 0:
                break
            for idx in idx_list:
                if remaining <= 0:
                    break
                available = pool_dict.get(idx, 0.0)
                if available <= 0:
                    continue
                take = min(available, remaining)
                vol_per_channel_chunk = take / 8.0
                if vol_per_channel_chunk <= 0:
                    continue
                pool_dict[idx] -= take
                remaining -= take
                moves.append((get_reservoir_well(labware, idx), vol_per_channel_chunk))

        if remaining > 0.1:
            raise RuntimeError(f'Not enough volume in {description} pools to withdraw {volume_per_channel * 8.0} uL (short by {remaining} uL).')

        return moves

    # --- Salt mixing helpers ---
    total_volume_salt_well_ul = 10000.0  # 10 mL total per reservoir well

    def calc_salt_mix_volumes(target_conc):
        # Simple linear mixing using low=0, high=salt_stock_conc
        # C_target = (V_high * C_stock) / (V_low + V_high)
        # with V_total = V_low + V_high = total_volume_salt_well_ul
        if salt_stock_conc <= 0:
            raise RuntimeError('Salt stock concentration must be > 0.')
        frac_high = float(target_conc) / salt_stock_conc
        frac_high = max(0.0, min(1.0, frac_high))
        v_high = total_volume_salt_well_ul * frac_high
        v_low = total_volume_salt_well_ul - v_high
        # convert to per-channel
        return v_low / 8.0, v_high / 8.0

    def calc_salt_mix_volumes_2x(target_conc):
        # Target is 2x concentration
        if salt_stock_conc <= 0:
            raise RuntimeError('Salt stock concentration must be > 0.')
        c_target = 2.0 * float(target_conc)
        frac_high = c_target / salt_stock_conc
        frac_high = max(0.0, min(1.0, frac_high))
        v_high = total_volume_salt_well_ul * frac_high
        v_low = total_volume_salt_well_ul - v_high
        return v_low / 8.0, v_high / 8.0

    # Multi-channel max per-channel volume
    mc_max = p300_multi.max_volume

    def chunk_and_transfer_multi(vol_per_channel_total, src_moves, dest_well):
        """Chunk a per-channel total volume into tip-sized moves for multi-channel pipette.

        src_moves: list of (src_well, vol_per_channel_available) that collectively sum
                   to vol_per_channel_total (uL per channel).
        """
        remaining = vol_per_channel_total
        for src_well, vol_per_channel_available in src_moves:
            vol_from_this_source = vol_per_channel_available
            while vol_from_this_source > 0.1:
                chunk = min(mc_max, vol_from_this_source)
                p300_multi.aspirate(chunk, src_well)
                p300_multi.dispense(chunk, dest_well)
                vol_from_this_source -= chunk
                remaining -= chunk
        return

    # --- Step 2: Prepare salt buffers in Reservoir 3 (10 mL per well, replicates for each concentration) ---
    target_well_index3 = 0

    for salt_c in salt_concs[:num_salt_conc]:
        for _ in range(replicates):
            if target_well_index3 >= 12:
                protocol.comment('Reached maximum wells in Reservoir 3; skipping remaining salt concentrations/replicates.')
                break
            dest = reservoir3.wells()[target_well_index3]
            protocol.comment(f'Preparing salt buffer {salt_c} in Reservoir 3 well {target_well_index3}.')

            v_low_pc, v_high_pc = calc_salt_mix_volumes(salt_c)

            low_moves = withdraw_from_pools(v_low_pc, high_salt=False, description='low salt')
            high_moves = withdraw_from_pools(v_high_pc, high_salt=True, description='high salt')

            if not p300_multi.has_tip:
                p300_multi.pick_up_tip()

            chunk_and_transfer_multi(v_low_pc, low_moves, dest)
            chunk_and_transfer_multi(v_high_pc, high_moves, dest)

            p300_multi.mix(5, min(200.0, mc_max), dest)
            p300_multi.blow_out(dest.top())

            res3_remaining[target_well_index3] += total_volume_salt_well_ul
            target_well_index3 += 1
        if target_well_index3 >= 12:
            break

    if p300_multi.has_tip:
        p300_multi.drop_tip()

    # --- Step 3: Prepare 2x salt buffers in Reservoir 4 (10 mL per well, one well per salt conc) ---
    target_well_index4 = 0

    for salt_c in salt_concs[:num_salt_conc]:
        if target_well_index4 >= 12:
            protocol.comment('Reached maximum wells in Reservoir 4; skipping remaining salt concentrations.')
            break
        dest = reservoir4.wells()[target_well_index4]
        protocol.comment(f'Preparing 2x salt buffer for concentration {salt_c} in Reservoir 4 well {target_well_index4}.')

        v_low_pc, v_high_pc = calc_salt_mix_volumes_2x(salt_c)

        low_moves = withdraw_from_pools(v_low_pc, high_salt=False, description='low salt (2x prep)')
        high_moves = withdraw_from_pools(v_high_pc, high_salt=True, description='high salt (2x prep)')

        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        chunk_and_transfer_multi(v_low_pc, low_moves, dest)
        chunk_and_transfer_multi(v_high_pc, high_moves, dest)

        p300_multi.mix(5, min(200.0, mc_max), dest)
        p300_multi.blow_out(dest.top())

        res4_remaining[target_well_index4] += total_volume_salt_well_ul
        target_well_index4 += 1

    if p300_multi.has_tip:
        p300_multi.drop_tip()

    # --- Step 4: Prepare ligand dilutions in mixing plate (deepwell) ---
    # Each well total volume: (TOTAL_VOLUME / 2 * REPLICATES * 1.5)
    well_total_volume = (total_volume / 2.0) * replicates * 1.5

    # Ligand stocks: high conc in reservoir1 well 0, low conc (stock/10) in reservoir1 well 1
    ligand_high_well = reservoir1.wells()[0]
    ligand_low_well = reservoir1.wells()[1]

    def calc_ligand_and_buffer_volumes(target_conc):
        """Return (ligand_volume, buffer_volume, use_low_stock) in uL needed to make
        well_total_volume at 2x target ligand concentration.
        Decide whether to use high or low stock based on minimum stock volume threshold (20 uL)."""
        c_target_2x = 2.0 * float(target_conc)
        if ligand_stock_conc <= 0:
            raise RuntimeError('Ligand stock concentration must be > 0.')
        # default: use high concentration stock
        v_ligand = (c_target_2x * well_total_volume) / ligand_stock_conc

        use_low_stock = False
        if v_ligand < 20.0:
            # use 10x more volume at 1/10 concentration
            v_ligand = v_ligand * 10.0
            use_low_stock = True
        v_buffer = max(0.0, well_total_volume - v_ligand)
        return v_ligand, v_buffer, use_low_stock

    # For simplicity, use low-salt buffer from reservoir0 well 0 for ligand dilutions
    buffer_well = reservoir0.wells()[0]

    # Iterate over salt concentrations as columns, ligand concentrations as rows (A-H)
    cols = mixing_plate.columns()[:num_salt_conc]
    rows_per_col = min(8, num_ligand_conc)  # up to 8 rows (A-H)

    sc_max = p300_single.max_volume

    for col_idx, col in enumerate(cols):
        for row_idx in range(rows_per_col):
            dest = col[row_idx]
            ligand_c = ligand_concs[row_idx]
            protocol.comment(f'Preparing ligand dilution {ligand_c} (2x) in mixing plate well {dest}.')

            v_ligand, v_buffer, use_low_stock = calc_ligand_and_buffer_volumes(ligand_c)

            # Add buffer first using a single tip per well
            remaining_buffer = v_buffer
            if remaining_buffer > 0.1:
                p300_single.pick_up_tip()
                while remaining_buffer > 0.1:
                    chunk = min(sc_max, remaining_buffer)
                    p300_single.aspirate(chunk, buffer_well)
                    p300_single.dispense(chunk, dest)
                    remaining_buffer -= chunk
                p300_single.blow_out(dest.top())
                p300_single.drop_tip()

            # Add ligand stock, tracking reservoir1 usage
            remaining_ligand = v_ligand
            stock_well = ligand_low_well if use_low_stock else ligand_high_well
            stock_index = 1 if use_low_stock else 0

            while remaining_ligand > 0.1:
                if res1_remaining[stock_index] <= 0:
                    raise RuntimeError('Not enough ligand stock remaining for dilutions.')
                chunk = min(sc_max, remaining_ligand, res1_remaining[stock_index])
                p300_single.pick_up_tip()
                p300_single.aspirate(chunk, stock_well)
                p300_single.dispense(chunk, dest)
                p300_single.blow_out(dest.top())
                p300_single.drop_tip()
                remaining_ligand -= chunk
                res1_remaining[stock_index] -= chunk

            # Mix the well after addition
            mix_vol = min(sc_max, well_total_volume / 2.0)
            p300_single.pick_up_tip()
            p300_single.mix(5, mix_vol, dest)
            p300_single.blow_out(dest.top())
            p300_single.drop_tip()

    protocol.comment('Templated salt and ligand gradient preparation complete.')
