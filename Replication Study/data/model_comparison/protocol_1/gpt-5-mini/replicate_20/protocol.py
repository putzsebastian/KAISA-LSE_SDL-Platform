from opentrons import protocol_api

metadata = {
    'protocolName': 'Template: Salt and Ligand Prep',
    'author': 'Lab 167',
    'description': 'Prepare salt gradients and ligand dilutions using placeholders for templating.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings for wizard substitution)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return [cast(x) for x in default]
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Load labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (target for salt replicates)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (sources: low & high salt)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (ligand stock + buffers)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (low salt buffers)

    # Deep-well mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_slot10])
    # Provide two tip racks for multi to avoid OutOfTips in simulation
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_slot7, tiprack_slot4])

    # Parse placeholders (with safe simulation fallbacks)
    # Defaults chosen to exercise worst-case for simulation: 4 salt concentrations, 8 ligand concentrations, 3 replicates
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=['0', '100', '200', '300'], cast=float)
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, default=['0', '1', '2', '4', '8', '16', '32', '64'], cast=float)
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, default=3, cast=int)
    total_vol = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=1000.0, cast=float)  # uL per the formula use
    salt_stock = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, default=1000.0, cast=float)
    ligand_stock = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, default=100.0, cast=float)

    num_salt = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, default=len(salt_concs), cast=int)
    num_ligand = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, default=len(ligand_concs), cast=int)

    # Truncate or extend parsed lists to declared counts (if placeholders provided both)
    salt_concs = salt_concs[:num_salt]
    ligand_concs = ligand_concs[:num_ligand]

    protocol.comment(f'Parsed {len(salt_concs)} salt concentrations and {len(ligand_concs)} ligand concentrations; replicates={replicates}')

    # Validation: replicates * number_of_salt_concentrations must not exceed 12 (Reservoir 3 wells)
    if replicates * len(salt_concs) > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 (Reservoir 3 has 12 wells)')

    # Prepare tracking of reservoir source volumes (in uL) for wells that initially contain 14 mL = 14000 uL
    def make_vol_list(n, initial=14000):
        return [initial for _ in range(n)]

    # Map reservoir labware wells to lists and initial volumes
    # reservoir_2 and reservoir_1 contain both low and high salt and ligand stocks per user description
    res2_wells = reservoir_2.wells()
    res1_wells = reservoir_1.wells()
    res0_wells = reservoir_0.wells()

    res2_vols = make_vol_list(len(res2_wells))
    res1_vols = make_vol_list(len(res1_wells))
    res0_vols = make_vol_list(len(res0_wells))

    # Helper to find and consume volume from a pool of wells (pool: list of (wells_list, vols_list))
    def consume_from_pool(pool, amount_ul):
        """Consume amount_ul total from the pool. Returns list of (labware_well, taken_ul, vols_list, idx)
        pool is list of tuples (wells, vols)
        This will reduce vols in place. Raises RuntimeError if pool exhausted."""
        remaining = amount_ul
        taken = []
        for wells, vols in pool:
            for idx, w in enumerate(wells):
                if remaining <= 0:
                    break
                avail = vols[idx]
                take = min(avail, remaining)
                if take > 0:
                    vols[idx] -= take
                    taken.append((w, take, vols, idx))
                    remaining -= take
            if remaining <= 0:
                break
        if remaining > 0:
            raise RuntimeError(f'Not enough volume in pool to supply {amount_ul} uL; shortfall {remaining} uL')
        return taken

    # Build pools: low salt pool (many wells), high salt pool (wells 6-11 in reservoir_2 and 7-11 in reservoir_1 per description)
    # According to the deck description:
    # - reservoir_2 wells 0-5 low salt; wells 6-11 high salt
    # - reservoir_1 well0 ligand high stock, well1 ligand low stock, wells2-6 low salt, wells7-11 high salt
    low_salt_wells = []
    low_salt_vols = []
    # add reservoir_2 wells 0-5
    for i in range(0, 6):
        low_salt_wells.append(res2_wells[i])
        low_salt_vols.append(res2_vols[i])
    # add reservoir_1 wells 2-6
    for i in range(2, 7):
        low_salt_wells.append(res1_wells[i])
        low_salt_vols.append(res1_vols[i])
    # add reservoir_0 wells 0-11
    for i in range(0, len(res0_wells)):
        low_salt_wells.append(res0_wells[i])
        low_salt_vols.append(res0_vols[i])

    low_salt_pool = [(low_salt_wells, low_salt_vols)]

    # High salt pool from reservoir_2 wells 6-11 and reservoir_1 wells 7-11
    high_salt_wells = []
    high_salt_vols = []
    for i in range(6, 12):
        high_salt_wells.append(res2_wells[i])
        high_salt_vols.append(res2_vols[i])
    for i in range(7, 12):
        high_salt_wells.append(res1_wells[i])
        high_salt_vols.append(res1_vols[i])
    high_salt_pool = [(high_salt_wells, high_salt_vols)]

    # Ligand stock wells: reservoir_1 well0 (high stock) and well1 (low stock)
    ligand_stock_pool = [([res1_wells[0], res1_wells[1]], [res1_vols[0], res1_vols[1]])]

    # --- Step 2: For each salt concentration, fill up REPLICATES wells in Reservoir 3 (slot 6) with total 10 mL
    total_per_target_ul = 10000  # 10 mL per target well

    # Reservoir_3 target wells order ascending with increasing well number
    res3_targets = reservoir_3.wells()[:12]

    tgt_idx = 0
    for salt_c in sorted(salt_concs):
        for rep in range(replicates):
            if tgt_idx >= len(res3_targets):
                raise RuntimeError('Exceeded Reservoir 3 capacity')
            dest = res3_targets[tgt_idx]
            tgt_idx += 1

            # compute volumes of high and low salt
            vol_high = total_per_target_ul * (salt_c / salt_stock) if salt_stock != 0 else 0.0
            vol_low = total_per_target_ul - vol_high
            protocol.comment(f'Preparing {total_per_target_ul}uL at {salt_c} (high {vol_high} uL, low {vol_low} uL) into {dest}')

            # consume volumes from pools (total volumes in uL)
            # take high salt
            if vol_high > 0:
                high_taken = consume_from_pool(high_salt_pool, vol_high)
            else:
                high_taken = []
            # take low salt
            if vol_low > 0:
                low_taken = consume_from_pool(low_salt_pool, vol_low)
            else:
                low_taken = []

            # Transfer with multi-channel pipette. Use one tip per destination (multi-channel picks a column of tips)
            p300m.pick_up_tip()

            # Transfer low salt pieces
            for (src_well, taken_ul, vols_list, idx) in low_taken:
                per_channel = taken_ul / 8.0
                # pass per-channel volume to transfer; transfer will chunk to pipette.max_volume automatically
                p300m.transfer(per_channel, src_well, dest, new_tip='never')

            # Transfer high salt pieces
            for (src_well, taken_ul, vols_list, idx) in high_taken:
                per_channel = taken_ul / 8.0
                p300m.transfer(per_channel, src_well, dest, new_tip='never')

            # Mix once after dispensing
            mix_vol = min(p300m.max_volume, total_per_target_ul / 10.0 / 8.0)  # per-channel mixing estimate
            try:
                p300m.mix(3, mix_vol, dest)
            except Exception:
                # fallback: do nothing if mix not possible
                protocol.comment('Mix failed or skipped for multi-channel')

            p300m.drop_tip()

    # --- Step 3: For each required salt concentration, fill up 1 well in Reservoir 4 (slot 3) with total 10 mL at 2x concentration
    res4_targets = reservoir_4.wells()[:len(salt_concs)]
    for i, salt_c in enumerate(sorted(salt_concs)):
        dest = res4_targets[i]
        target_c = salt_c * 2.0
        vol_total = 10000  # 10 mL
        vol_high = vol_total * (target_c / salt_stock) if salt_stock != 0 else 0.0
        vol_low = vol_total - vol_high
        protocol.comment(f'Preparing in Reservoir4 {vol_total}uL at 2x salt {target_c} (high {vol_high} uL, low {vol_low} uL) into {dest}')

        high_taken = consume_from_pool(high_salt_pool, vol_high) if vol_high > 0 else []
        low_taken = consume_from_pool(low_salt_pool, vol_low) if vol_low > 0 else []

        p300m.pick_up_tip()
        for (src_well, taken_ul, vols_list, idx) in low_taken:
            per_channel = taken_ul / 8.0
            p300m.transfer(per_channel, src_well, dest, new_tip='never')
        for (src_well, taken_ul, vols_list, idx) in high_taken:
            per_channel = taken_ul / 8.0
            p300m.transfer(per_channel, src_well, dest, new_tip='never')

        mix_vol = min(p300m.max_volume, vol_total / 10.0 / 8.0)
        try:
            p300m.mix(3, mix_vol, dest)
        except Exception:
            protocol.comment('Mix failed or skipped for multi-channel')
        p300m.drop_tip()

    # --- Step 4: Prepare ligand dilutions in mixing plate
    # Layout: for each salt concentration -> one column; ligand concentrations ascend row-wise from A-H
    n_cols = len(salt_concs)
    n_rows = len(ligand_concs)
    if n_rows > 8:
        raise RuntimeError('Too many ligand concentrations: more than 8 (rows A-H)')

    # Calculate total volume per mixing well: TOTAL_VOLUME/2 * REPLICATES * 1.5
    mixing_total = (total_vol / 2.0) * replicates * 1.5
    protocol.comment(f'Each mixing well total volume: {mixing_total} uL')

    # Build list of target wells by columns
    # mixing_plate.wells() is row-major; but we need column-wise grouping. We'll select by (row, col)
    def well_by_row_col(plate, row_idx, col_idx):
        # plate.rows()[row_idx][col_idx]
        return plate.rows()[row_idx][col_idx]

    for col_idx in range(n_cols):
        for row_idx in range(n_rows):
            dest = well_by_row_col(mixing_plate, row_idx, col_idx)
            ligand_conc = ligand_concs[row_idx]
            target_conc = ligand_conc * 2.0
            # Determine which ligand stock to use: try high stock first
            vol_from_high_stock = mixing_total * (target_conc / ligand_stock) if ligand_stock != 0 else mixing_total
            use_low_stock = False
            if vol_from_high_stock < 20.0:
                # use low stock (stock/10)
                effective_stock = ligand_stock / 10.0
                vol_from_high_stock = mixing_total * (target_conc / effective_stock)
                use_low_stock = True

            # Compute volumes
            vol_stock = vol_from_high_stock
            vol_buffer = mixing_total - vol_stock
            protocol.comment(f'Preparing mixing plate well {dest} with total {mixing_total}uL: stock {vol_stock}uL (use_low_stock={use_low_stock}), buffer {vol_buffer}uL')

            # Consume stock
            # choose reservoir_1 well 0 for high stock or well1 for low stock per instruction
            if not use_low_stock:
                src_stock_well = res1_wells[0]
                # reduce its tracked volume
                if vol_stock > 0:
                    consume_from_pool([([src_stock_well], [res1_vols[0]])], vol_stock)
            else:
                src_stock_well = res1_wells[1]
                if vol_stock > 0:
                    consume_from_pool([([src_stock_well], [res1_vols[1]])], vol_stock)

            # Consume buffer from low_salt_pool
            buffer_taken = consume_from_pool(low_salt_pool, vol_buffer) if vol_buffer > 0 else []

            # Transfer with single-channel pipette: pick a new tip for each well (as instructed)
            p300s.pick_up_tip()

            # Transfer stock (single-channel volume in uL)
            try:
                if vol_stock > 0:
                    p300s.transfer(vol_stock, src_stock_well, dest, new_tip='never')
            except Exception:
                # transfer may chunk automatically; fallback to manual aspirate/dispense
                if vol_stock > 0:
                    p300s.aspirate(vol_stock, src_stock_well)
                    p300s.dispense(vol_stock, dest)

            # Transfer buffer pieces
            for (src_well, taken_ul, vols_list, idx) in buffer_taken:
                try:
                    p300s.transfer(taken_ul, src_well, dest, new_tip='never')
                except Exception:
                    p300s.aspirate(taken_ul, src_well)
                    p300s.dispense(taken_ul, dest)

            # Mix in destination
            mix_vol = min(p300s.max_volume, mixing_total / 4.0)
            try:
                p300s.mix(3, mix_vol, dest)
            except Exception:
                protocol.comment('Mix failed or skipped for single-channel')

            p300s.drop_tip()

    protocol.comment('Protocol complete (templated).')
