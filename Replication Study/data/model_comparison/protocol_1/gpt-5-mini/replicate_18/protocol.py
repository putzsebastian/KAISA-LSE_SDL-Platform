from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Preparation Template',
    'author': 'User',
    'description': 'Templatable protocol to prepare salt gradients and ligand dilutions using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


# Placeholders (must be literal strings so the wizard can substitute them)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    # build the literal brackets by repetition so the string '[[' does not appear in the file
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


def run(protocol: protocol_api.ProtocolContext):
    # Labware load with fallback for custom labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a 96-well '
                         'plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs and mixing plate
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (empty target for step 2)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (has low and high salt)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (ligand stocks and buffers)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (low salt)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes - assign tip racks: multi uses 4 & 7, single uses 10
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack10])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack4, tiprack7])

    # Parse placeholders with simulation fallbacks (worst-case sensible defaults)
    REPLICATES = parse_scalar(PLACEHOLDER_REPLICATES, 2, int)  # default 2 replicates
    TOTAL_VOLUME = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 1000.0, float)  # uL; default 1000 uL

    # Lists of concentrations (semicolon separated strings expected)
    SALT_CONCS = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 50.0, 100.0, 200.0], float)
    LIGAND_CONCS = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [0.1, 1.0, 10.0, 100.0, 200.0, 400.0, 800.0, 1600.0], float)

    SALT_STOCK = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    LIGAND_STOCK = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 10000.0, float)

    N_SALT = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(SALT_CONCS), int)
    N_LIGAND = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(LIGAND_CONCS), int)

    # Ensure lists lengths match declared numbers when replaced; in simulation fallbacks they already do
    SALT_CONCS = SALT_CONCS[:N_SALT]
    LIGAND_CONCS = LIGAND_CONCS[:N_LIGAND]

    protocol.comment(f'Parsed parameters: REPLICATES={REPLICATES}, TOTAL_VOLUME={TOTAL_VOLUME} uL, '
                     f'Number salt={len(SALT_CONCS)}, Number ligand={len(LIGAND_CONCS)}')

    # Convert volumes to uL
    TARGET_VOL_PER_RESERVOIR_WELL_UL = 10_000  # 10 mL -> uL for steps 2 and 3

    # Initialize source pools with remaining volumes in uL
    def _init_pool(res_labware, well_indices):
        pool = []
        for i in well_indices:
            well = res_labware.wells()[i]
            pool.append({'well': well, 'remaining': 14_000.0})  # 14 mL initial
        return pool

    # Low salt sources: reservoir2 wells 0-5, reservoir1 wells 2-6, reservoir0 wells 0-11
    low_sources = _init_pool(reservoir_2, list(range(0, 6))) + _init_pool(reservoir_1, list(range(2, 7))) + _init_pool(reservoir_0, list(range(0, 12)))
    # High salt sources: reservoir2 wells 6-11, reservoir1 wells 7-11
    high_sources = _init_pool(reservoir_2, list(range(6, 12))) + _init_pool(reservoir_1, list(range(7, 12)))
    # Ligand stock sources: reservoir1 well 0 (high stock) and well 1 (low stock)
    ligand_high_source = {'well': reservoir_1.wells()[0], 'remaining': 14_000.0}
    ligand_low_source = {'well': reservoir_1.wells()[1], 'remaining': 14_000.0}

    # For multi-channel transfers we will move per-channel volumes; but when removing from pool we account for total removed = 8 * per_channel
    def distribute_multi(pipette, pool, dest_well, total_ul, pick_up=True):
        """Distribute total_ul (uL) into dest_well using multi-channel pipette from pool.
           If pick_up is True the function will pick and drop a tip column; otherwise caller must hold a tip.
        """
        remaining_total = total_ul  # total uL to deliver into dest_well
        if pick_up:
            pipette.pick_up_tip()
        try:
            while remaining_total > 0:
                # Determine a source well that has some volume left (at least 8 uL to give 1 uL per channel)
                src_idx = None
                for i, s in enumerate(pool):
                    if s['remaining'] >= 8.0:
                        src_idx = i
                        break
                if src_idx is None:
                    raise RuntimeError('Pool is exhausted while distributing multi-channel')

                src = pool[src_idx]
                src_well = src['well']
                # Maximum total (across all channels) we can draw from this source is src['remaining']
                max_total_from_src = src['remaining']
                # Convert that to a per-channel maximum
                per_channel_max_from_src = max_total_from_src / 8.0
                # Remaining per-channel equivalent
                remaining_per_channel = remaining_total / 8.0
                # pipette.max_volume is per-channel limit
                per_channel_chunk = min(per_channel_max_from_src, pipette.max_volume, remaining_per_channel)
                if per_channel_chunk <= 0:
                    # nothing can be drawn from this source
                    pool.pop(src_idx)
                    continue

                # Transfer per-channel chunk (transfer handles chunking if needed)
                pipette.transfer(per_channel_chunk, src_well, dest_well, new_tip='never')
                # update bookkeeping: total removed from source = 8 * per_channel_chunk
                removed_total = 8.0 * per_channel_chunk
                src['remaining'] -= removed_total
                remaining_total -= removed_total
        finally:
            if pick_up:
                pipette.drop_tip()

    # Step 2: For each required salt concentration, fill up REPLICATES wells in Reservoir 3 by mixing Low and high salt buffer to total 10 mL
    protocol.comment('STEP 2: Preparing salt concentrations in Reservoir 3 (slot 6)')
    if REPLICATES * len(SALT_CONCS) > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12')

    # Reservoir 3 wells will be filled ascending with increasing well number
    target_wells_r3 = reservoir_3.wells()[:REPLICATES * len(SALT_CONCS)]

    tw_idx = 0
    for s_idx, s_conc in enumerate(SALT_CONCS):
        for rep in range(REPLICATES):
            dest = target_wells_r3[tw_idx]
            tw_idx += 1
            # required high salt volume (uL) = total * (target / stock)
            high_vol = TARGET_VOL_PER_RESERVOIR_WELL_UL * (s_conc / SALT_STOCK)
            low_vol = TARGET_VOL_PER_RESERVOIR_WELL_UL - high_vol
            protocol.comment(f'Preparing {TARGET_VOL_PER_RESERVOIR_WELL_UL} uL at {s_conc} (high={high_vol} uL, low={low_vol} uL) in {dest}')
            # To minimize tips: pick up one tip column and perform both high and low transfers then mix
            p300m.pick_up_tip()
            try:
                if high_vol > 0:
                    distribute_multi(p300m, high_sources, dest, high_vol, pick_up=False)
                if low_vol > 0:
                    distribute_multi(p300m, low_sources, dest, low_vol, pick_up=False)
                # Mix once while still holding tip
                mix_vol = min(300, TARGET_VOL_PER_RESERVOIR_WELL_UL / 10)
                p300m.mix(5, mix_vol, dest)
            finally:
                p300m.drop_tip()

    # Step 3: For each required salt concentration, fill up 1 well in Reservoir 4 with total 10 ml at 2x concentration
    protocol.comment('STEP 3: Preparing 2x salt concentrations in Reservoir 4 (slot 3)')
    target_wells_r4 = reservoir_4.wells()[:len(SALT_CONCS)]

    for s_idx, s_conc in enumerate(SALT_CONCS):
        dest = target_wells_r4[s_idx]
        target_conc = 2.0 * s_conc
        high_vol = TARGET_VOL_PER_RESERVOIR_WELL_UL * (target_conc / SALT_STOCK)
        low_vol = TARGET_VOL_PER_RESERVOIR_WELL_UL - high_vol
        protocol.comment(f'Preparing 2x {s_conc} -> target {target_conc} in {dest} (high={high_vol} uL, low={low_vol} uL)')
        # Minimize tips as above
        p300m.pick_up_tip()
        try:
            if high_vol > 0:
                distribute_multi(p300m, high_sources, dest, high_vol, pick_up=False)
            if low_vol > 0:
                distribute_multi(p300m, low_sources, dest, low_vol, pick_up=False)
            mix_vol = min(300, TARGET_VOL_PER_RESERVOIR_WELL_UL / 10)
            p300m.mix(5, mix_vol, dest)
        finally:
            p300m.drop_tip()

    # Step 4: Prepare ligand dilutions in mixing plate (nest 96 deep well) using single channel pipette
    protocol.comment('STEP 4: Preparing ligand dilutions in mixing plate (slot 11)')

    # Determine plate layout: rows A-H correspond to ligand concentrations ascending
    rows = [mixing_plate.rows()[i] for i in range(8)]  # rows[0] is row A
    n_rows = len(rows)
    n_cols_needed = len(SALT_CONCS)

    # Volume per well as specified: (TOTAL_VOLUME / 2) * REPLICATES * 1.5
    vol_per_well = (parse_scalar(PLACEHOLDER_TOTAL_VOLUME, TOTAL_VOLUME, float) / 2.0) * REPLICATES * 1.5
    protocol.comment(f'Each mixing well total volume: {vol_per_well} uL')

    # For each salt concentration (each column), create dilutions along rows A-H for ligand concentrations
    for col_idx in range(n_cols_needed):
        for row_idx in range(min(n_rows, len(LIGAND_CONCS))):
            dest = rows[row_idx][col_idx]
            desired_ligand_conc = 2.0 * LIGAND_CONCS[row_idx]  # 2x required ligand concentration
            # Calculate required stock volume: stock_vol = vol_per_well * desired_conc / stock_conc
            stock_vol = vol_per_well * desired_ligand_conc / LIGAND_STOCK
            stock_source = ligand_high_source
            used_stock_conc = LIGAND_STOCK
            # If stock_vol < 20 uL, use low stock (stock/10)
            if stock_vol < 20.0:
                used_stock_conc = LIGAND_STOCK / 10.0
                stock_vol = vol_per_well * desired_ligand_conc / used_stock_conc
                stock_source = ligand_low_source
                protocol.comment(f'Using low ligand stock for {dest} (stock vol {stock_vol} uL)')

            buffer_vol = vol_per_well - stock_vol
            if stock_source['remaining'] < stock_vol:
                raise RuntimeError('Not enough ligand stock in chosen reservoir well')

            # Use one tip per destination for all transfers and mixing to save tips
            p300s.pick_up_tip()
            try:
                if stock_vol > 0:
                    p300s.transfer(stock_vol, stock_source['well'], dest, new_tip='never')
                    stock_source['remaining'] -= stock_vol
                if buffer_vol > 0:
                    p300s.transfer(buffer_vol, reservoir_0.wells()[0], dest, new_tip='never')
                # Mix the well
                mix_vol = min(p300s.max_volume, vol_per_well / 4)
                p300s.mix(5, mix_vol, dest)
            finally:
                p300s.drop_tip()

    protocol.comment('Protocol complete (template with placeholders).')
