from opentrons import protocol_api

metadata = {
    'protocolName': 'Template: Salt and Ligand Prep with Placeholders',
    'author': 'User',
    'description': 'Prepare salt gradients and ligand dilutions using placeholders for templating',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (must remain literal strings for external templating)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    # build brackets rather than write them literally so the simulator can run with unreplaced tokens
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(value)


def parse_list(value, default_list, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return list(default_list)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with sensible simulation fallbacks (worst-case within prompt constraints)
    REPLICATES = parse_scalar(PLACEHOLDER_REPLICATES, 3, int)  # fallback 3
    TOTAL_VOLUME = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)  # uL fallback 200
    SALT_CONCS = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 50.0, 100.0], float)
    LIGAND_CONCS = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0], float)
    SALT_STOCK = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 100.0, float)
    LIGAND_STOCK = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)
    NUM_SALT = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(SALT_CONCS), int)
    NUM_LIGAND = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(LIGAND_CONCS), int)

    # Limit lists to the declared counts (when substituted these should match)
    SALT_CONCS = SALT_CONCS[:NUM_SALT]
    LIGAND_CONCS = LIGAND_CONCS[:NUM_LIGAND]

    protocol.comment(f"Simulation: REPLICATES={REPLICATES}, TOTAL_VOLUME={TOTAL_VOLUME} uL, "
                     f"#SALT={len(SALT_CONCS)}, #LIGAND={len(LIGAND_CONCS)}")

    # Labware loading
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using fallback plate for simulation.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs per user-specified mapping
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (to be filled)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (slot 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (slot 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (slot 5)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300m = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack7])
    p300s = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack10])

    # Track remaining volumes in each reservoir well (uL). Each well starts with 14 mL = 14000 uL
    def make_res_pool(res_labware):
        return {i: 14000.0 for i in range(12)}

    pool_r2 = make_res_pool(reservoir_2)  # slot 8
    pool_r1 = make_res_pool(reservoir_1)  # slot 9
    pool_r0 = make_res_pool(reservoir_0)  # slot 5

    # Helper to consume volume from a pool of wells for a multi-channel pipette.
    # amount_per_channel is the per-channel volume to be transferred to the destination (uL).
    def consume_from_pool_multi(pool_dict, labware, amount_per_channel):
        # total needed across all channels (since single-row reservoir spans 8 channels)
        remaining_needed = amount_per_channel * 8.0
        used_sources = []  # list of tuples (well_index, per_channel_volume_from_this)
        for idx in range(12):
            if remaining_needed <= 1e-6:
                break
            avail = pool_dict.get(idx, 0.0)
            if avail <= 1e-6:
                continue
            take = min(avail, remaining_needed)
            # translate taken total into per-channel volume
            per_channel_take = take / 8.0
            pool_dict[idx] = pool_dict.get(idx, 0.0) - take
            remaining_needed -= take
            used_sources.append((idx, per_channel_take))
        if remaining_needed > 1e-6:
            raise RuntimeError(f"Not enough volume in the provided pool to supply {amount_per_channel} uL per channel")
        return used_sources  # caller will issue transfers from these specific wells

    # Step 2: For each salt concentration, fill up REPLICATES wells in Reservoir 3 by mixing Low and High salt buffers to total 10 mL
    TOTAL_VOL_R3 = 10000.0  # uL per destination well
    protocol.comment('Step 2: Preparing Reservoir 3 gradient wells')
    # Build list of destination wells in reservoir_3 (12 wells max) in ascending order
    dest_wells_r3 = reservoir_3.wells()[:12]
    # Sort salt concentrations ascending
    salt_sorted = sorted(SALT_CONCS)

    # Pools: define which wells in reservoir_2 and reservoir_1 are low and high salt
    # According to user mapping: reservoir_2 wells 0-5 low salt; 6-11 high salt
    low_pool_r2 = list(range(0, 6))
    high_pool_r2 = list(range(6, 12))
    # reservoir_1 also has ligand and some high-salt wells (per user description): wells 6-11 high salt, 0 ligand stock, 1 low ligand stock
    low_pool_r1 = [2, 3, 4, 5, 6]  # wells 2-6 low salt in reservoir_1 (user listed several low salt wells)
    high_pool_r1 = [7, 8, 9, 10, 11]

    # Create convenient combined pools for low and high salt (priority: reservoir_2 then reservoir_1)
    low_salt_pool = [(reservoir_2, pool_r2, i) for i in low_pool_r2] + [(reservoir_1, pool_r1, i) for i in low_pool_r1] + [(reservoir_0, pool_r0, i) for i in range(12)]
    high_salt_pool = [(reservoir_2, pool_r2, i) for i in high_pool_r2] + [(reservoir_1, pool_r1, i) for i in high_pool_r1]

    # Helper to fetch and consume from a combined pool (tries reservoirs in order)
    def consume_combined_pool(combined_pool, amount_per_channel):
        # returns list of tuples (labware, well_index, per_channel_vol)
        remaining_needed_total = amount_per_channel * 8.0
        used = []
        for lab, pooldict, idx in combined_pool:
            if remaining_needed_total <= 1e-6:
                break
            avail = pooldict.get(idx, 0.0)
            if avail <= 1e-6:
                continue
            take = min(avail, remaining_needed_total)
            per_channel_take = take / 8.0
            pooldict[idx] = pooldict.get(idx, 0.0) - take
            remaining_needed_total -= take
            used.append((lab, idx, per_channel_take))
        if remaining_needed_total > 1e-6:
            raise RuntimeError(f"Not enough volume in combined pool to provide {amount_per_channel} uL per channel")
        return used

    dest_index = 0
    for s_idx, salt in enumerate(salt_sorted):
        for rep in range(REPLICATES):
            if dest_index >= 12:
                raise RuntimeError('Destination count for Reservoir 3 would exceed 12 wells')
            dest = dest_wells_r3[dest_index]
            dest_index += 1
            # Compute fraction of high salt needed
            frac_high = float(salt) / float(SALT_STOCK) if SALT_STOCK != 0 else 0.0
            frac_high = max(0.0, min(1.0, frac_high))
            vol_high_total = TOTAL_VOL_R3 * frac_high
            vol_low_total = TOTAL_VOL_R3 - vol_high_total
            # per-channel volumes
            perchan_high = vol_high_total / 8.0
            perchan_low = vol_low_total / 8.0

            protocol.comment(f'Filling Reservoir3 well {dest.well_name} with salt {salt} -> high {vol_high_total} uL, low {vol_low_total} uL')

            # Use one multi-channel tip for this destination (pick up once, do all source transfers, then mix and drop)
            p300m.pick_up_tip()

            # Consume from high salt pool
            if vol_high_total > 1e-6:
                used_high = consume_combined_pool(high_salt_pool, perchan_high)
                for lab, well_idx, perchan in used_high:
                    src = lab.wells()[well_idx]
                    # transfer perchan (per-channel) from src to dest; transfer will chunk as needed
                    p300m.transfer(perchan, src, dest, new_tip='never')

            # Consume from low salt pool
            if vol_low_total > 1e-6:
                used_low = consume_combined_pool(low_salt_pool, perchan_low)
                for lab, well_idx, perchan in used_low:
                    src = lab.wells()[well_idx]
                    p300m.transfer(perchan, src, dest, new_tip='never')

            # Mix destination well once (mix volume must not exceed pipette max)
            mix_vol = min(p300m.max_volume, TOTAL_VOL_R3 / 10.0)
            p300m.mix(5, mix_vol, dest)
            p300m.drop_tip()

    # Step 3: For each salt concentration create one well in Reservoir 4 with total 10 mL at 2x concentration
    protocol.comment('Step 3: Preparing Reservoir 4 wells at 2x salt concentration')
    dest_wells_r4 = reservoir_4.wells()[:12]
    for i, salt in enumerate(salt_sorted):
        if i >= 12:
            break
        dest = dest_wells_r4[i]
        target = float(salt) * 2.0
        frac_high = target / float(SALT_STOCK) if SALT_STOCK != 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        vol_high_total = TOTAL_VOL_R3 * frac_high
        vol_low_total = TOTAL_VOL_R3 - vol_high_total
        perchan_high = vol_high_total / 8.0
        perchan_low = vol_low_total / 8.0

        protocol.comment(f'Filling Reservoir4 well {dest.well_name} with 2x salt {target} -> high {vol_high_total} uL, low {vol_low_total} uL')

        p300m.pick_up_tip()
        if vol_high_total > 1e-6:
            used_high = consume_combined_pool(high_salt_pool, perchan_high)
            for lab, well_idx, perchan in used_high:
                src = lab.wells()[well_idx]
                p300m.transfer(perchan, src, dest, new_tip='never')
        if vol_low_total > 1e-6:
            used_low = consume_combined_pool(low_salt_pool, perchan_low)
            for lab, well_idx, perchan in used_low:
                src = lab.wells()[well_idx]
                p300m.transfer(perchan, src, dest, new_tip='never')
        mix_vol = min(p300m.max_volume, TOTAL_VOL_R3 / 10.0)
        p300m.mix(5, mix_vol, dest)
        p300m.drop_tip()

    # Step 4: Create ligand dilutions in the mixing plate (deep well) using single-channel pipette
    protocol.comment('Step 4: Preparing ligand dilutions in mixing plate')
    rows = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    # per well total volume as specified: TOTAL_VOLUME/2 * REPLICATES * 1.5
    per_well_total = float(TOTAL_VOLUME) / 2.0 * float(REPLICATES) * 1.5
    protocol.comment(f'Per-well total volume for ligand dilutions: {per_well_total} uL')

    # Determine available ligand stock wells in reservoir_1: well 0 high stock, well1 low stock
    stock_high_idx = 0
    stock_low_idx = 1

    # Helper to select which stock to use (ensure at least 20 uL available in chosen stock well)
    def pick_ligand_stock(vol_needed, pool_dict):
        # prefer high stock (well 0)
        if pool_dict.get(stock_high_idx, 0.0) >= vol_needed:
            return reservoir_1.wells()[stock_high_idx], LIGAND_STOCK, stock_high_idx
        # else if low stock has enough use it
        if pool_dict.get(stock_low_idx, 0.0) >= vol_needed:
            return reservoir_1.wells()[stock_low_idx], LIGAND_STOCK / 10.0, stock_low_idx
        # else choose the one with more volume
        if pool_dict.get(stock_high_idx, 0.0) >= pool_dict.get(stock_low_idx, 0.0):
            return reservoir_1.wells()[stock_high_idx], LIGAND_STOCK, stock_high_idx
        return reservoir_1.wells()[stock_low_idx], LIGAND_STOCK / 10.0, stock_low_idx

    # For each salt concentration, create a column in mixing plate; columns are 1-indexed for human but API uses 0-indexed access
    for c_idx, salt in enumerate(salt_sorted):
        dest_col = c_idx  # zero-based column index
        if dest_col >= 12:
            break
        for r_idx, ligand_conc in enumerate(sorted(LIGAND_CONCS)):
            if r_idx >= 8:
                break
            row = rows[r_idx]
            # determine well address in deep well: e.g., 'A1' corresponds to mixing_plate.wells_by_name()['A1']
            well_name = f"{row}{dest_col+1}"
            dest = mixing_plate.wells_by_name()[well_name]

            # Desired concentration in the well is 2x the ligand concentration (as per step)
            target_conc = float(ligand_conc) * 2.0
            # volume of ligand stock required (using chosen stock concentration)
            # vol_stock = target_conc / stock_conc * per_well_total
            stock_well, stock_conc, stock_idx = pick_ligand_stock(20.0, pool_r1)
            vol_stock = per_well_total * (target_conc / float(stock_conc)) if stock_conc != 0 else 0.0
            vol_stock = max(0.0, min(per_well_total, vol_stock))
            vol_diluent = per_well_total - vol_stock

            protocol.comment(f'Preparing mixing plate well {well_name}: target {target_conc}, stock_vol {vol_stock} uL, diluent {vol_diluent} uL')

            # Transfer diluent (low salt buffer) from reservoir_0 well 0 (safe default) then stock
            # Use single-channel tip per destination
            p300s.pick_up_tip()
            # take diluent from reservoir_0 well 0
            diluent_src = reservoir_0.wells()[0]
            # ensure pool tracking
            if pool_r0[0] < vol_diluent:
                raise RuntimeError('Not enough low-salt diluent in reservoir_0 well 0 for ligand dilutions')
            pool_r0[0] -= vol_diluent
            p300s.transfer(vol_diluent, diluent_src, dest, new_tip='never')

            # transfer stock
            if vol_stock > 0:
                # check pool_r1 for stock availability
                if pool_r1[stock_idx] < vol_stock:
                    raise RuntimeError('Not enough ligand stock in selected well')
                pool_r1[stock_idx] -= vol_stock
                p300s.transfer(vol_stock, stock_well, dest, new_tip='never')

            # mix and drop tip (mix volume must not exceed single-channel max)
            mix_vol_s = min(p300s.max_volume, per_well_total / 10.0)
            p300s.mix(5, mix_vol_s, dest)
            p300s.drop_tip()

    protocol.comment('Protocol complete')
