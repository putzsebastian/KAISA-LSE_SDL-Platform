from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Preparation',
    'author': 'User',
    'description': 'Templated protocol to prepare salt buffers and ligand dilutions using placeholders.'
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
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    parts = [p.strip() for p in s.split(';') if p.strip()]
    return [cast(float(p)) for p in parts]


def run(protocol: protocol_api.ProtocolContext):
    # -----------------------------
    # Parse placeholders with safe simulation defaults (worst-case style)
    # -----------------------------
    # For simulation we choose relatively large but valid defaults that satisfy deck limits
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)  # must satisfy replicates * num_salt <= 12
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 50.0, 100.0, 150.0])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0])
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONC, 1000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, 1000.0)
    num_salt = parse_int(PLACEHOLDER_NUM_SALT_CONC, len(salt_concs))
    num_lig = parse_int(PLACEHOLDER_NUM_LIGAND_CONC, len(ligand_concs))

    # Limit lists to specified numbers to avoid mismatch
    salt_concs = salt_concs[:num_salt]
    ligand_concs = ligand_concs[:num_lig]

    # -----------------------------
    # Load labware
    # -----------------------------
    # Slot 1: custom Cytiva 96 filter plate
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Slot 4,7,10: tipracks 300 ul
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    # Slot 3: Reservoir 4 (empty, to be filled with 2x salt buffers)
    res4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    # Slot 6: Reservoir 3 (empty, to be filled with working salt buffers)
    res3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    # Slot 8: Reservoir 2 (pre-filled low/high salt)
    res2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    # Slot 9: Reservoir 1 (ligand stocks and buffers)
    res1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    # Slot 5: Reservoir 0 (low salt buffer)
    res0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    # Slot 11: NEST 96 deep well plate 2ml (mixing plate)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -----------------------------
    # Load pipettes
    # -----------------------------
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_10]
    )

    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_7]
    )

    # -----------------------------
    # Helper for volume tracking in reservoirs
    # -----------------------------
    # Each reservoir well initially 14 mL = 14000 uL as per description
    INITIAL_VOL_UL = 14000.0

    # Reservoir 2 (slot 8): low salt in wells 0-5, high salt in wells 6-11
    res2_low_wells = [res2.wells()[i] for i in range(0, 6)]
    res2_high_wells = [res2.wells()[i] for i in range(6, 12)]

    # Reservoir 1 (slot 9): ligand and buffers/high salt; treat low/high salt pools as separate
    # Low salt buffer wells: 2-6
    res1_low_wells = [res1.wells()[i] for i in range(2, 7)]
    # High salt buffer wells: 7-11
    res1_high_wells = [res1.wells()[i] for i in range(7, 12)]

    # Reservoir 0 (slot 5): all low salt
    res0_low_wells = [w for w in res0.wells()]

    # Build pools: map well -> remaining volume
    pool_low = {w: INITIAL_VOL_UL for w in res2_low_wells + res1_low_wells + res0_low_wells}
    pool_high = {w: INITIAL_VOL_UL for w in res2_high_wells + res1_high_wells}

    def _take_from_pool(pool: dict, volume_ul: float) -> list:
        """Return list of (well, vol) chunks that sum to volume_ul from given pool.

        Depletes wells in the order they appear in the pool dict. Raises if pool exhausted.
        """
        remaining = volume_ul
        chunks = []
        for well in list(pool.keys()):
            if remaining <= 0:
                break
            available = pool[well]
            if available <= 0:
                continue
            take = min(available, remaining)
            if take > 0:
                chunks.append((well, take))
                pool[well] -= take
                remaining -= take
        if remaining > 0:
            raise RuntimeError('Not enough volume left in pool to satisfy request of ' + str(volume_ul) + ' uL')
        return chunks

    def _multi_move_total(pipette, source_well, dest_well, total_volume_ul: float):
        """Move total_volume_ul from source to dest with multi-channel pipette.

        The volume passed to pipette is per channel. For a single-row reservoir, an
        aspiration/dispense of v_per_channel removes 8*v_per_channel from the well.
        This helper chunks the total across tip capacity and calls transfer().
        """
        per_channel_total = total_volume_ul / 8.0
        per_channel_max = pipette.max_volume
        remaining = per_channel_total
        while remaining > 0:
            this_per_channel = min(per_channel_max, remaining)
            pipette.transfer(
                this_per_channel,
                source_well,
                dest_well,
                new_tip='never',
                mix_after=(3, this_per_channel * 0.6)
            )
            remaining -= this_per_channel

    # -----------------------------
    # Step 2: Prepare working salt buffers in Reservoir 3 (slot 6)
    # -----------------------------
    # 10 mL per working buffer well
    WORKING_VOL_TOTAL_UL = 10000.0

    total_required_wells_res3 = replicates * num_salt
    if total_required_wells_res3 > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS exceeds 12 for Reservoir 3.')

    # Use first total_required_wells_res3 wells of res3 as targets, ordered ascending
    target_wells_res3 = res3.wells()[:total_required_wells_res3]

    p300_multi.pick_up_tip()

    for salt_index, salt_c in enumerate(salt_concs[:num_salt]):
        # Using simple linear mixing between 0 and stock concentration
        frac_high = salt_c / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        frac_low = 1.0 - frac_high

        vol_high_ul = WORKING_VOL_TOTAL_UL * frac_high
        vol_low_ul = WORKING_VOL_TOTAL_UL * frac_low

        # For each replicate, fill a separate well in res3
        for rep in range(replicates):
            idx = salt_index * replicates + rep
            dest = target_wells_res3[idx]

            # Take from low pool
            low_chunks = _take_from_pool(pool_low, vol_low_ul)
            for src_well, vol in low_chunks:
                _multi_move_total(p300_multi, src_well, dest, vol)

            # Take from high pool
            high_chunks = _take_from_pool(pool_high, vol_high_ul)
            for src_well, vol in high_chunks:
                _multi_move_total(p300_multi, src_well, dest, vol)

            # Mix after both components are in place
            p300_multi.mix(5, min(250.0, p300_multi.max_volume), dest)

    p300_multi.drop_tip()

    # -----------------------------
    # Step 3: Prepare 2x salt buffers in Reservoir 4 (slot 3)
    # -----------------------------
    # 10 mL per 2x buffer well
    TWOFOLD_VOL_TOTAL_UL = 10000.0

    if num_salt > 12:
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS exceeds 12 for Reservoir 4.')

    target_wells_res4 = res4.wells()[:num_salt]

    p300_multi.pick_up_tip()

    for salt_index, salt_c in enumerate(salt_concs[:num_salt]):
        target_c = 2.0 * salt_c
        frac_high = target_c / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        frac_low = 1.0 - frac_high

        vol_high_ul = TWOFOLD_VOL_TOTAL_UL * frac_high
        vol_low_ul = TWOFOLD_VOL_TOTAL_UL * frac_low

        dest = target_wells_res4[salt_index]

        # Low pool
        low_chunks = _take_from_pool(pool_low, vol_low_ul)
        for src_well, vol in low_chunks:
            _multi_move_total(p300_multi, src_well, dest, vol)

        # High pool
        high_chunks = _take_from_pool(pool_high, vol_high_ul)
        for src_well, vol in high_chunks:
            _multi_move_total(p300_multi, src_well, dest, vol)

        p300_multi.mix(5, min(250.0, p300_multi.max_volume), dest)

    p300_multi.drop_tip()

    # -----------------------------
    # Step 4: Prepare ligand dilutions (2x concentrations) in mixing plate (slot 11)
    # -----------------------------
    # Total volume per well: (TOTAL_VOLUME / 2 * REPLICATES * 1.5)
    per_well_total_ul = (total_volume / 2.0) * replicates * 1.5

    # Ligand source wells
    ligand_high_well = res1.wells()[0]  # high concentration [[LIGAND_STOCK_CONCENTRATION]]
    ligand_low_well = res1.wells()[1]   # low concentration [[LIGAND_STOCK_CONCENTRATION]]/10

    def _compute_stock_volume(target_conc, stock_conc):
        if stock_conc <= 0:
            return 0.0
        return (target_conc / stock_conc) * per_well_total_ul

    # Prepare columns: one column per salt concentration, rows A-H per ligand concentration
    max_cols = 12
    max_rows = 8  # A-H

    if num_salt > max_cols:
        raise RuntimeError('Too many salt concentrations for mixing plate columns (max 12).')
    if num_lig > max_rows:
        raise RuntimeError('Too many ligand concentrations for mixing plate rows (max 8).')

    # All dilutions use low salt buffer from pool_low

    # Track ligand pool volumes separately (assuming 14 mL each)
    ligand_pool_high = {ligand_high_well: INITIAL_VOL_UL}
    ligand_pool_low = {ligand_low_well: INITIAL_VOL_UL}

    def _take_from_ligand_pool(use_low_stock: bool, volume_ul: float) -> list:
        if use_low_stock:
            return _take_from_pool(ligand_pool_low, volume_ul)
        else:
            return _take_from_pool(ligand_pool_high, volume_ul)

    # Single-channel operations for mixing plate
    for salt_idx in range(num_salt):
        col = mixing_plate.columns()[salt_idx]
        for lig_idx in range(num_lig):
            row_well = col[lig_idx]  # row-wise A-H
            target_lig_conc = 2.0 * ligand_concs[lig_idx]

            # Compute required volume from high stock
            v_stock_high = _compute_stock_volume(target_lig_conc, ligand_stock_conc)
            use_low_stock = v_stock_high < 20.0  # if below 20 uL from high stock, use low stock instead

            if use_low_stock:
                effective_stock_conc = ligand_stock_conc / 10.0
                v_stock = _compute_stock_volume(target_lig_conc, effective_stock_conc)
            else:
                v_stock = v_stock_high

            v_stock = max(0.0, min(per_well_total_ul, v_stock))
            v_buffer = per_well_total_ul - v_stock

            # First add buffer from low-salt pool
            buffer_chunks = _take_from_pool(pool_low, v_buffer)

            p300_single.pick_up_tip()
            for src_well, vol in buffer_chunks:
                remaining = vol
                while remaining > 0:
                    vol_this = min(p300_single.max_volume, remaining)
                    p300_single.transfer(
                        vol_this,
                        src_well,
                        row_well,
                        new_tip='never'
                    )
                    remaining -= vol_this

            # Then add ligand stock
            ligand_chunks = _take_from_ligand_pool(use_low_stock, v_stock)
            for src_well, vol in ligand_chunks:
                remaining = vol
                while remaining > 0:
                    vol_this = min(p300_single.max_volume, remaining)
                    p300_single.transfer(
                        vol_this,
                        src_well,
                        row_well,
                        new_tip='never'
                    )
                    remaining -= vol_this

            # Mix final well
            mix_vol = min(0.8 * per_well_total_ul, p300_single.max_volume)
            if mix_vol > 0:
                p300_single.mix(5, mix_vol, row_well)

            p300_single.drop_tip()

    protocol.comment('Templated salt and ligand gradient preparation complete.')
