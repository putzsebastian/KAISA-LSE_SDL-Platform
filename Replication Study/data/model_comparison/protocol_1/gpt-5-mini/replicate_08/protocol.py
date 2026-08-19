from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Prep Template',
    'author': 'User',
    'description': "Template protocol using placeholders for salt and ligand preparations"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    """
    Template protocol that prepares salt dilution reservoirs and ligand dilutions.
    All user-configurable values are placeholders that should be substituted before running on a robot.
    """

    # -----------------
    # Labware
    # -----------------
    # Custom labware (with simulation fallback)
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_300_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty -> will receive 2x salt)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (destination for salt replicates)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (contains low and high salt, as specified)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (contains ligand stocks and buffers)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (low salt buffers)

    # Mixing plate (deep well)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -----------------
    # Pipettes
    # -----------------
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300_7, tiprack_300_4])
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300_10])

    # -----------------
    # Placeholders (literal strings for the wizard to substitute)
    # -----------------
    SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
    LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
    SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    REPLICATES = '[[REPLICATES]]'
    TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
    NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
    NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

    # -----------------
    # Helpers to parse placeholders safely in simulation
    # -----------------
    def _unreplaced(s: str) -> bool:
        return s.startswith('[' * 2) and s.endswith(']' * 2)

    def parse_list(value: str, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return list(default)
        if s == '':
            return list(default)
        return [cast(x) for x in s.split(';') if x.strip()]

    def parse_scalar(value: str, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return default
        return cast(s)

    # -----------------
    # Parse placeholders into Python values (simulation-friendly defaults chosen to exercise the protocol)
    # -----------------
    salt_concs = parse_list(SALT_CONCENTRATIONS, default=[0, 50, 150, 300])
    ligand_concs = parse_list(LIGAND_CONCENTRATIONS, default=[0.1, 1, 10, 100, 250, 500, 750, 1000])

    num_salt = int(parse_scalar(NUMBER_OF_SALT_CONCENTRATIONS, default=len(salt_concs), cast=int)) if not _unreplaced(NUMBER_OF_SALT_CONCENTRATIONS) else len(salt_concs)
    if num_salt != len(salt_concs):
        # be robust: prefer the explicit list length if provided
        num_salt = len(salt_concs)

    num_ligand = int(parse_scalar(NUMBER_OF_LIGAND_CONCENTRATIONS, default=len(ligand_concs), cast=int)) if not _unreplaced(NUMBER_OF_LIGAND_CONCENTRATIONS) else len(ligand_concs)
    if num_ligand != len(ligand_concs):
        num_ligand = len(ligand_concs)

    replicates = int(parse_scalar(REPLICATES, default=3, cast=int))
    total_volume = float(parse_scalar(TOTAL_VOLUME, default=50.0, cast=float))  # uL per final assay well (user placeholder)

    salt_stock_conc = float(parse_scalar(SALT_STOCK_CONCENTRATION, default=1000.0, cast=float))
    ligand_stock_conc = float(parse_scalar(LIGAND_STOCK_CONCENTRATION, default=10000.0, cast=float))

    protocol.comment('Parsed {} salt concentrations and {} ligand concentrations. Replicates: {}'.format(num_salt, num_ligand, replicates))

    # Validate deck capacity for Reservoir 3: replicates * number_of_salt_concentrations <= 12
    if replicates * num_salt > 12:
        raise RuntimeError('Requested number of salt wells (replicates * number_of_salt_concentrations) exceeds the 12 wells available in Reservoir 3')

    # -----------------
    # Volume tracking (in uL) for all reservoir wells that contain consumable liquids
    # Each well initially contains 14 mL = 14000 uL, as specified
    # Keys are tuples: (labware_object, well_index)
    # -----------------
    initial_well_volume = 14000
    pool_remaining = {}

    # helper to register wells of a reservoir in pool_remaining
    def register_reservoir_wells(res_labware):
        for idx, w in enumerate(res_labware.wells()):
            pool_remaining[(res_labware, idx)] = initial_well_volume

    register_reservoir_wells(reservoir_2)
    register_reservoir_wells(reservoir_1)
    register_reservoir_wells(reservoir_0)

    # Define pools by (labware, well_index) lists for convenience
    # Low-salt sources: reservoir_2 wells 0-5, reservoir_1 wells 2-6, reservoir_0 wells 0-11
    low_pool = [(reservoir_2, i) for i in range(0, 6)] + [(reservoir_1, i) for i in range(2, 7)] + [(reservoir_0, i) for i in range(0, 12)]
    # High-salt sources: reservoir_2 wells 6-11 and reservoir_1 wells 7-11
    high_pool = [(reservoir_2, i) for i in range(6, 12)] + [(reservoir_1, i) for i in range(7, 12)]
    # Ligand stock: reservoir_1 well 0 (high stock), alternative reservoir_1 well 1 (low stock)
    ligand_stock_high = (reservoir_1, 0)
    ligand_stock_low = (reservoir_1, 1)

    # -----------------
    # Helper to consume volume from a pool; returns list of (labware, well_idx, vol_uL) allocations
    # This splits the total requested amount across wells in the pool in order until the request is satisfied.
    # It will raise if the entire pool cannot satisfy the request.
    # -----------------
    def allocate_from_pool(pool_list, total_uL):
        remaining = total_uL
        allocations = []
        for (lab, idx) in pool_list:
            avail = pool_remaining.get((lab, idx), 0)
            if avail <= 0:
                continue
            take = min(avail, remaining)
            allocations.append((lab, idx, take))
            remaining -= take
            if remaining <= 1e-6:
                break
        if remaining > 1e-6:
            raise RuntimeError('Pool shortfall: cannot allocate {} uL; {} uL missing'.format(total_uL, remaining))
        # decrement pool_remaining
        for (lab, idx, vol) in allocations:
            pool_remaining[(lab, idx)] -= vol
        return allocations

    # -----------------
    # Step 2: For each salt concentration, fill up REPLICATES wells in Reservoir 3 with total 10 mL by mixing low and high salt buffer
    # Reservoir 3 wells should be ascending with increasing well number. Use p300_multi (tips from slot 7).
    # -----------------
    target_total_per_well_ul = 10000  # 10 mL -> uL

    protocol.comment('Step 2: Preparing salt dilution series in Reservoir 3')

    # We'll use reservoir_3.columns() to identify destination wells for multi-channel operations
    dest_cols_res3 = reservoir_3.columns()

    # iterate concentrations in ascending order
    for sc_index, sc in enumerate(sorted(salt_concs)[:num_salt]):
        for rep in range(replicates):
            dest_idx = sc_index * replicates + rep
            dest_col = dest_cols_res3[dest_idx]
            # Compute volumes of high and low salt (total volumes)
            v_high_total = target_total_per_well_ul * (sc / salt_stock_conc)
            v_low_total = target_total_per_well_ul - v_high_total
            # Per-channel volumes to pass to multi-channel pipette
            per_channel_high = v_high_total / 8.0
            per_channel_low = v_low_total / 8.0

            protocol.comment('Preparing Reservoir3 well index {}: target {} (high {} uL, low {} uL)'.format(dest_idx, sc, v_high_total, v_low_total))

            # pick up a fresh column of tips
            p300_multi.pick_up_tip()

            # dispense high salt from high_pool
            if v_high_total > 0:
                # allocate from high_pool in TOTAL terms (not per-channel)
                allocations = allocate_from_pool(high_pool, v_high_total)
                for (lab, idx, vol) in allocations:
                    src_well = lab.wells()[idx]
                    # compute per-channel chunk for this allocation
                    per_channel_chunk = vol / 8.0
                    # use transfer; new_tip='never' to keep the same tip for additional allocations
                    p300_multi.transfer(per_channel_chunk, src_well, dest_col, new_tip='never')

            # dispense low salt from low_pool
            if v_low_total > 0:
                allocations = allocate_from_pool(low_pool, v_low_total)
                for (lab, idx, vol) in allocations:
                    src_well = lab.wells()[idx]
                    per_channel_chunk = vol / 8.0
                    p300_multi.transfer(per_channel_chunk, src_well, dest_col, new_tip='never')

            # mix in destination (mixing per-channel volume reasonable)
            mix_vol = min(p300_multi.max_volume, max(50, per_channel_high + per_channel_low))
            p300_multi.mix(5, mix_vol, dest_col[0])

            p300_multi.drop_tip()

    # -----------------
    # Step 3: For each salt concentration, fill up 1 well with total 10 mL in Reservoir 4 with 2x the required salt concentration
    # -----------------
    protocol.comment('Step 3: Preparing 2x salt solutions in Reservoir 4')
    dest_cols_res4 = reservoir_4.columns()
    for sc_index, sc in enumerate(sorted(salt_concs)[:num_salt]):
        dest_idx = sc_index
        dest_col = dest_cols_res4[dest_idx]
        # target concentration is 2x the required salt concentration
        target_sc = sc * 2.0
        v_high_total = target_total_per_well_ul * (target_sc / salt_stock_conc)
        v_low_total = target_total_per_well_ul - v_high_total
        per_channel_high = v_high_total / 8.0
        per_channel_low = v_low_total / 8.0

        protocol.comment('Preparing Reservoir4 well index {}: target {} (high {} uL, low {} uL)'.format(dest_idx, target_sc, v_high_total, v_low_total))

        p300_multi.pick_up_tip()
        if v_high_total > 0:
            allocations = allocate_from_pool(high_pool, v_high_total)
            for (lab, idx, vol) in allocations:
                src_well = lab.wells()[idx]
                p300_multi.transfer(vol / 8.0, src_well, dest_col, new_tip='never')
        if v_low_total > 0:
            allocations = allocate_from_pool(low_pool, v_low_total)
            for (lab, idx, vol) in allocations:
                src_well = lab.wells()[idx]
                p300_multi.transfer(vol / 8.0, src_well, dest_col, new_tip='never')
        # mix
        mix_vol = min(p300_multi.max_volume, max(50, per_channel_high + per_channel_low))
        p300_multi.mix(5, mix_vol, dest_col[0])
        p300_multi.drop_tip()

    # -----------------
    # Step 4: Prepare ligand dilutions in mixing plate (deep well)
    # Concentrations are 2x ligand concentrations provided; ascending row-wise from A-H
    # For each salt concentration, create one column of the mixing plate
    # Total volume for each well = [[TOTAL_VOLUME]]/2*[[REPLICATES]]*1.5
    # -----------------
    protocol.comment('Step 4: Preparing ligand dilutions in mixing plate')

    # compute total volume per mixing well as specified
    mixing_total_per_well = float(total_volume) / 2.0 * float(replicates) * 1.5

    # mapping rows A-H to indexes 0-7
    rows = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']

    # For columns we will use the first num_salt columns of mixing_plate (1-based indexing in user terms)
    for salt_idx in range(num_salt):
        for lig_idx in range(num_ligand):
            # destination well: row lig_idx, column salt_idx
            row_name = rows[lig_idx]
            col_number = salt_idx + 1
            dest_well_name = f'{row_name}{col_number}'
            dest = mixing_plate.wells_by_name()[dest_well_name]

            # target ligand concentration is 2x given ligand concentration
            target_lig_conc = 2.0 * ligand_concs[lig_idx]
            # compute volume of stock needed (C1V1 = C2V2)
            v_stock = (target_lig_conc * mixing_total_per_well) / ligand_stock_conc

            used_stock = ligand_stock_high
            used_stock_conc = ligand_stock_conc
            # if stock volume below 20 uL, use the low stock (stock/10)
            if v_stock < 20.0:
                # calculate what v_stock would be with the low stock concentration
                v_stock_low = (target_lig_conc * mixing_total_per_well) / (ligand_stock_conc / 10.0)
                # only use low stock if the recalculated volume still fits within the total mixing volume
                if v_stock_low <= mixing_total_per_well:
                    used_stock = ligand_stock_low
                    used_stock_conc = ligand_stock_conc / 10.0
                    v_stock = v_stock_low
                else:
                    # otherwise keep using the high stock and warn (simulation-only fallback)
                    protocol.comment('WARNING: low stock would require more volume than available; using high stock instead for this well')

            v_diluent = mixing_total_per_well - v_stock
            if v_diluent < 0:
                raise RuntimeError('Calculated stock volume exceeds total mixing volume; check concentrations and totals')

            protocol.comment('Preparing mixing plate well {}: target {} (stock {:.2f} uL, diluent {:.2f} uL)'.format(dest_well_name, target_lig_conc, v_stock, v_diluent))

            # allocate stock from ligand stock pool
            stock_pool = [used_stock]
            allocations_stock = allocate_from_pool(stock_pool, v_stock)
            # allocate diluent from low_pool
            allocations_diluent = allocate_from_pool(low_pool, v_diluent)

            # perform transfers with single channel pipette; keep same tip for both operations per well
            p300_single.pick_up_tip()
            for (lab, idx, vol) in allocations_stock:
                src = lab.wells()[idx]
                p300_single.transfer(vol, src, dest, new_tip='never')
            for (lab, idx, vol) in allocations_diluent:
                src = lab.wells()[idx]
                p300_single.transfer(vol, src, dest, new_tip='never')

            # mix the well
            mix_vol = min(p300_single.max_volume, max(50, v_stock + v_diluent / 10.0))
            p300_single.mix(5, mix_vol, dest)
            p300_single.drop_tip()

    protocol.comment('Protocol complete.')