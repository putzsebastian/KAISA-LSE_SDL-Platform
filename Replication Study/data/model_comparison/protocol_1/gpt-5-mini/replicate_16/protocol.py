from opentrons import protocol_api

metadata = {
    'protocolName': 'Template: Salt and Ligand Prep with Placeholders',
    'author': 'Lab 167',
    'description': 'Prepare salt dilution series in reservoirs and ligand dilutions in deep-well plate using placeholders'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


# Placeholder literals (must remain verbatim in the file)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    # build the bracket pair so the literal string '[[' never appears in this file
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
    import math

    # Load labware with fallback for custom labware
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

    # Reservoirs according to deck mapping in the request
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (empty)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (has low and high salt)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (ligand stocks + buffers)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (low salt buffer)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack10])
    # Provide multiple tip racks for the multi-channel to avoid OutOfTips on many column picks
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack7, tiprack4])

    # Parse placeholders (simulation-friendly)
    REPLICATES = parse_scalar(PLACEHOLDER_REPLICATES, default=2, cast=int)  # fallback 2
    TOTAL_VOLUME = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=1000.0, cast=float)  # uL fallback 1000
    SALT_CONCS = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=[0.05, 0.2], cast=float)
    LIGAND_CONCS = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, default=[0.1, 0.5, 1, 2], cast=float)
    SALT_STOCK = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, default=1.0, cast=float)
    LIGAND_STOCK = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, default=100.0, cast=float)
    NUMBER_OF_SALT_CONCS = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, default=len(SALT_CONCS), cast=int)
    NUMBER_OF_LIGAND_CONCS = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, default=len(LIGAND_CONCS), cast=int)

    # Truncate or expand lists to their specified counts (honour NUMBER placeholders if provided)
    if NUMBER_OF_SALT_CONCS != len(SALT_CONCS):
        SALT_CONCS = SALT_CONCS[:NUMBER_OF_SALT_CONCS]
    if NUMBER_OF_LIGAND_CONCS != len(LIGAND_CONCS):
        LIGAND_CONCS = LIGAND_CONCS[:NUMBER_OF_LIGAND_CONCS]

    protocol.comment(f'Parsed parameters: REPLICATES={REPLICATES}, TOTAL_VOLUME={TOTAL_VOLUME} uL, '
                     f'{len(SALT_CONCS)} salt concentrations, {len(LIGAND_CONCS)} ligand concentrations')

    # Basic validations
    if REPLICATES * len(SALT_CONCS) > 12:
        raise RuntimeError('REPLICATES * NUMBER_OF_SALT_CONCENTRATIONS exceeds 12 wells of Reservoir 3')

    # Initialize tracking of source volumes (all reservoir wells preloaded with 14 mL = 14000 uL as described)
    initial_well_vol = 14000.0
    remaining = {}
    # reservoirs involved as sources: reservoir0 (all low), reservoir1 (w0 ligand stock, w1 ligand low stock, w2-w6 low, w7-w11 high), reservoir2 (w0-w5 low, w6-w11 high)
    for well in reservoir0.wells():
        remaining[well] = initial_well_vol
    for i, well in enumerate(reservoir1.wells()):
        remaining[well] = initial_well_vol
    for well in reservoir2.wells():
        remaining[well] = initial_well_vol

    # Define pools
    # Low salt sources: reservoir0 all wells + reservoir1 wells 2-6 + reservoir2 wells 0-5
    low_salt_sources = []
    low_salt_sources += reservoir0.wells()
    low_salt_sources += [reservoir1.wells()[i] for i in range(2, 7)]
    low_salt_sources += [reservoir2.wells()[i] for i in range(0, 6)]

    # High salt sources: reservoir2 wells 6-11 and reservoir1 wells 7-11
    high_salt_sources = [reservoir2.wells()[i] for i in range(6, 12)] + [reservoir1.wells()[i] for i in range(7, 12)]

    # Ligand stock wells
    ligand_stock_high = reservoir1.wells()[0]
    ligand_stock_low = reservoir1.wells()[1]
    remaining.setdefault(ligand_stock_high, initial_well_vol)
    remaining.setdefault(ligand_stock_low, initial_well_vol)

    # Helper: consume from a pool for multichannel operations (per-channel volume passed to transfer)
    def _consume_multichannel_and_transfer(per_channel_ul, pool_list, dest_well, pip: protocol_api.InstrumentContext, mix_after_times=0):
        """Consume total = per_channel_ul * 8 uL from pool_list and transfer into dest_well using pip (multi-channel).
        Splits across pool wells if needed. This function issues transfer() calls with new_tip='never' expecting
        the caller to manage pick_up/drop_tip if desired. """
        total_needed = per_channel_ul * 8.0
        remaining_needed = total_needed
        idx = 0
        while remaining_needed > 1e-6:
            if idx >= len(pool_list):
                raise RuntimeError('Not enough volume in the pool to satisfy multichannel transfer')
            src = pool_list[idx]
            avail = remaining.get(src, 0.0)
            if avail <= 0.0:
                idx += 1
                continue
            take = min(avail, remaining_needed)
            per_channel_chunk = take / 8.0
            pip.transfer(per_channel_chunk, src, dest_well, new_tip='never')
            remaining[src] = avail - take
            remaining_needed -= take
            if remaining.get(src, 0.0) <= 1e-6:
                idx += 1
        # mix after if requested (mix volumes are per-channel)
        if mix_after_times and per_channel_ul > 0:
            mix_vol = min(pip.max_volume, per_channel_ul)
            pip.mix(mix_after_times, mix_vol, dest_well)

    # Step 2: For each salt concentration, fill REPLICATES wells in Reservoir 3 by mixing low and high salt to 10 mL total
    total_target_ul = 10000.0  # 10 mL per instruction
    dest_wells_res3 = reservoir3.wells()  # will use first REPLICATES * N wells

    dest_idx = 0
    for conc in sorted(SALT_CONCS):
        for rep in range(REPLICATES):
            dest = dest_wells_res3[dest_idx]
            dest_idx += 1
            # Compute required volumes from high salt stock and low salt (stock low is 0)
            v_high = (conc / SALT_STOCK) * total_target_ul if SALT_STOCK > 0 else 0.0
            v_low = total_target_ul - v_high
            # Convert to per-channel volumes for multichannel pipette
            per_channel_high = v_high / 8.0 if v_high > 0 else 0.0
            per_channel_low = v_low / 8.0 if v_low > 0 else 0.0

            protocol.comment(f'Preparing salt {conc} in Reservoir3 well {dest} -> high {v_high} uL, low {v_low} uL')

            # Use one tip column per destination: pick up, transfer high and low, mix, drop
            p300m.pick_up_tip()
            if per_channel_high > 0:
                _consume_multichannel_and_transfer(per_channel_high, high_salt_sources, dest, p300m, mix_after_times=0)
            if per_channel_low > 0:
                _consume_multichannel_and_transfer(per_channel_low, low_salt_sources, dest, p300m, mix_after_times=0)
            # Mix once target is filled
            mix_vol = min(p300m.max_volume, max(per_channel_high, per_channel_low))
            if mix_vol > 0:
                p300m.mix(3, mix_vol, dest)
            p300m.drop_tip()

    # Step 3: For each salt concentration, fill up 1 well in Reservoir 4 with total 10 mL at 2x required salt concentration
    dest_wells_res4 = reservoir4.wells()
    dest_idx = 0
    for conc in sorted(SALT_CONCS):
        dest = dest_wells_res4[dest_idx]
        dest_idx += 1
        target_conc = conc * 2.0
        v_high = (target_conc / SALT_STOCK) * total_target_ul if SALT_STOCK > 0 else 0.0
        v_low = total_target_ul - v_high
        per_channel_high = v_high / 8.0 if v_high > 0 else 0.0
        per_channel_low = v_low / 8.0 if v_low > 0 else 0.0

        protocol.comment(f'Preparing 2x salt {target_conc} in Reservoir4 well {dest} -> high {v_high} uL, low {v_low} uL')
        p300m.pick_up_tip()
        if per_channel_high > 0:
            _consume_multichannel_and_transfer(per_channel_high, high_salt_sources, dest, p300m, mix_after_times=0)
        if per_channel_low > 0:
            _consume_multichannel_and_transfer(per_channel_low, low_salt_sources, dest, p300m, mix_after_times=0)
        mix_vol = min(p300m.max_volume, max(per_channel_high, per_channel_low))
        if mix_vol > 0:
            p300m.mix(3, mix_vol, dest)
        p300m.drop_tip()

    # Step 4: Create ligand dilutions in mixing_plate (deep well)
    # For each salt concentration create one column in the mixing plate. Rows A-H correspond to ligand concentrations (ascending row-wise from A->H lowest in A)
    max_rows = 8  # A-H
    ligand_values = LIGAND_CONCS[:max_rows]
    n_salt = len(SALT_CONCS)
    # compute per-well total volume: TOTAL_VOLUME/2*REPLICATES*1.5
    per_well_total = (TOTAL_VOLUME / 2.0) * REPLICATES * 1.5

    protocol.comment(f'Preparing ligand dilutions: per well total {per_well_total} uL')

    # iterate columns for each salt concentration
    for col_idx in range(n_salt):
        for row_idx in range(max_rows):
            try:
                conc_ligand = ligand_values[row_idx]
            except IndexError:
                # if fewer ligand concentrations provided than 8, stop filling further rows
                break
            dest = mixing_plate.rows()[row_idx][col_idx]
            desired_conc = conc_ligand * 2.0  # 2x required ligand concentration

            # Decide which stock to use
            # compute required stock volume: V_stock = (C_desired * V_total) / stock_conc
            V_stock_high = (desired_conc * per_well_total) / LIGAND_STOCK if LIGAND_STOCK > 0 else 0.0
            use_low_stock = False
            stock_used = ligand_stock_high
            stock_conc_used = LIGAND_STOCK
            if V_stock_high < 20.0:
                # switch to low stock with concentration stock/10
                stock_used = ligand_stock_low
                stock_conc_used = LIGAND_STOCK / 10.0 if LIGAND_STOCK != 0 else 0.0
                use_low_stock = True
            V_stock = (desired_conc * per_well_total) / stock_conc_used if stock_conc_used > 0 else 0.0
            V_buffer = per_well_total - V_stock

            protocol.comment(f'Preparing ligand at 2x {conc_ligand} in {dest}: stock_vol {V_stock} uL (using {"low" if use_low_stock else "high"} stock), buffer {V_buffer} uL')

            # Perform transfers using single-channel pipette. pick up a fresh tip per well
            p300s.pick_up_tip()
            # Transfer buffer (low salt) from low_salt_sources (we will pick the first available)
            buffer_needed = V_buffer
            # simple loop to take buffer from low_salt_sources sequentially
            for src in low_salt_sources:
                if buffer_needed <= 1e-6:
                    break
                avail = remaining.get(src, 0.0)
                if avail <= 0:
                    continue
                take = min(avail, buffer_needed)
                p300s.transfer(take, src, dest, new_tip='never')
                remaining[src] = avail - take
                buffer_needed -= take
            if buffer_needed > 1e-3:
                raise RuntimeError('Not enough buffer to prepare ligand dilutions')

            # Transfer stock
            stock_needed = V_stock
            avail = remaining.get(stock_used, 0.0)
            if avail < stock_needed - 1e-6:
                raise RuntimeError('Not enough ligand stock in chosen well')
            p300s.transfer(stock_needed, stock_used, dest, new_tip='never', mix_after=(3, min(p300s.max_volume, stock_needed)))
            remaining[stock_used] = avail - stock_needed

            # Do a final mix in the destination well
            mix_vol = min(p300s.max_volume, per_well_total / 2.0)
            if mix_vol > 0:
                p300s.mix(3, mix_vol, dest)

            p300s.drop_tip()

    protocol.comment('Protocol complete')
