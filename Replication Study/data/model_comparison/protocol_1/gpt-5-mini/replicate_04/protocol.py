from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Preparation',
    'author': 'Lab 167',
    'description': 'Prepare salt gradients in reservoirs and ligand dilutions in a deep-well plate using placeholders',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

import math

# Placeholders (literal strings for wizard substitution)
REPLICATES = '[[REPLICATES]]'
TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

# Helpers to detect unreplaced tokens in simulation
def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)

def parse_scalar(value: str, default: float, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)

def parse_list(value: str, default: list, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return [cast(x) for x in default]
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # labware
    # Slot 1: custom filter plate
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a 96-well fallback for simulation only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # reservoirs
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (target for step 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (target for step 2)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (source)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (source, ligand stocks present)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (extra low-salt source)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_10])
    # Put tiprack_7 first so multi-channel uses Slot 7 first (as requested) but include others to avoid OutOfTips in simulation
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_7, tiprack_4, tiprack_10])

    # parse placeholders with simulation fallbacks
    replicates = int(parse_scalar(REPLICATES, 3, int))
    total_volume = parse_scalar(TOTAL_VOLUME, 1000.0, float)  # uL

    salt_concs = parse_list(SALT_CONCENTRATIONS, ['50', '150', '300', '600'], float)
    ligand_concs = parse_list(LIGAND_CONCENTRATIONS, ['0.1','1','10','100','200','400','800','1600'], float)

    salt_stock = parse_scalar(SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock = parse_scalar(LIGAND_STOCK_CONCENTRATION, 1000.0, float)

    number_of_salt = int(parse_scalar(NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concs), int))
    number_of_ligand = int(parse_scalar(NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concs), int))

    # Ensure lists lengths match provided counts (simulation only fallback behavior)
    if number_of_salt != len(salt_concs):
        # trim or extend salt_concs to match the count
        if number_of_salt < len(salt_concs):
            salt_concs = salt_concs[:number_of_salt]
        else:
            # extend with last value
            salt_concs = salt_concs + [salt_concs[-1]] * (number_of_salt - len(salt_concs))

    if number_of_ligand != len(ligand_concs):
        if number_of_ligand < len(ligand_concs):
            ligand_concs = ligand_concs[:number_of_ligand]
        else:
            ligand_concs = ligand_concs + [ligand_concs[-1]] * (number_of_ligand - len(ligand_concs))

    protocol.comment(f'Using {number_of_salt} salt concentrations: {salt_concs}')
    protocol.comment(f'Using {number_of_ligand} ligand concentrations: {ligand_concs}')
    protocol.comment(f'Replicates: {replicates}, total_volume per ligand well: {total_volume} uL')

    # Basic checks
    if replicates * number_of_salt > 12:
        raise RuntimeError('Requested replicates x number_of_salt_concentrations exceeds 12 wells available in Reservoir 3')

    # Build source pools (track remaining volumes in uL)
    # Reservoir 2 wells: indices 0-11 as described in the layout
    # Per prompt, Reservoir 2 wells 0-5 low salt, 6-11 high salt
    res2_low_wells = [reservoir_2.wells()[i] for i in range(0,6)]
    res2_high_wells = [reservoir_2.wells()[i] for i in range(6,12)]

    # Reservoir 1: well0 ligand high stock, well1 ligand low stock, wells2-6 low salt, wells7-11 high salt
    ligand_high_well = reservoir_1.wells()[0]
    ligand_low_well = reservoir_1.wells()[1]
    res1_low_salt_wells = [reservoir_1.wells()[i] for i in range(2,7)]
    res1_high_salt_wells = [reservoir_1.wells()[i] for i in range(7,12)]

    # Reservoir 0: all low salt
    res0_low_wells = [reservoir_0.wells()[i] for i in range(0,12)]

    # global low salt pool includes res2 low, res1 low (2-6), res0
    low_salt_pool = res2_low_wells + res1_low_salt_wells + res0_low_wells
    low_salt_remaining = {w: 14000.0 for w in low_salt_pool}  # uL per well (as specified)

    # global high salt pool includes res2 high and res1 high
    high_salt_pool = res2_high_wells + res1_high_salt_wells
    high_salt_remaining = {w: 14000.0 for w in high_salt_pool}

    # ligand stocks remaining (uL) - simulation uses a larger fallback to allow run completion
    ligand_remaining = {ligand_high_well: 140000.0, ligand_low_well: 140000.0}

    # Helper to find next well in a pool with enough remaining (>= min_vol)
    def _find_pool_well(pool_remaining, min_total_uL=1.0):
        for w, rem in pool_remaining.items():
            if rem >= min_total_uL:
                return w
        return None

    # Helper to withdraw total_volume_uL from a pool for multi-channel operations
    def withdraw_from_pool_multi(target_well, pool_remaining, total_uL, pip):
        """Withdraw total_uL (absolute) from pool_remaining using multi-channel pipette 'pip' and dispense into target_well.
           This function handles chunking per-channel and source-well switching.
        """
        remaining = total_uL
        pip.pick_up_tip()
        while remaining > 1e-6:
            src = _find_pool_well(pool_remaining, min_total_uL=1.0)
            if src is None:
                pip.drop_tip()
                raise RuntimeError('Pool is exhausted while trying to prepare reagents')
            # available in this source (total uL)
            avail = pool_remaining[src]
            # for multi-channel, one aspiration of v_per_chan uL removes 8*v_per_chan from source
            # compute max per-channel we can take from this source
            max_per_channel_from_src = avail / 8.0
            # remaining per-channel need
            need_per_channel = remaining / 8.0
            # chunk per channel limited by pipette max
            chunk_per_channel = min(pip.max_volume, max_per_channel_from_src, need_per_channel)
            if chunk_per_channel <= 0:
                # this source cannot provide per-channel chunk; mark as empty and continue
                pool_remaining[src] = 0.0
                continue
            # perform aspirate/dispense cycle
            pip.aspirate(chunk_per_channel, src)
            pip.dispense(chunk_per_channel, target_well)
            # update pool
            consumed = chunk_per_channel * 8.0
            pool_remaining[src] -= consumed
            remaining -= consumed
        # mix once
        mix_vol = min(pip.max_volume, 1000)
        pip.mix(3, mix_vol, target_well)
        pip.drop_tip()

    # Step 2: For each salt concentration, fill replicates wells in Reservoir 3 by mixing low and high salt buffers to total 10 mL
    TOTAL_TARGET_UL = 10000.0  # 10 mL per target well
    protocol.comment('STEP 2: Preparing salt concentrations in Reservoir 3')
    dest_indices = []
    # Reservoir 3 wells should be ascending with increasing well number
    for ci, conc in enumerate(salt_concs[:number_of_salt]):
        for r in range(replicates):
            idx = ci * replicates + r
            dest = reservoir_3.wells()[idx]
            target_conc = float(conc)
            # compute volumes
            vol_high = (target_conc / salt_stock) * TOTAL_TARGET_UL
            vol_low = TOTAL_TARGET_UL - vol_high
            protocol.comment(f'Preparing {TOTAL_TARGET_UL}uL at {target_conc} (high {vol_high}uL, low {vol_low}uL) in Reservoir3 well {dest}')
            # withdraw high then low using multi-channel helper (volumes are absolute totals)
            if vol_high > 0:
                withdraw_from_pool_multi(dest, high_salt_remaining, vol_high, p300m)
            if vol_low > 0:
                withdraw_from_pool_multi(dest, low_salt_remaining, vol_low, p300m)

    # Step 3: For each required salt concentration, fill up 1 well in Reservoir 4 with total 10 ml at 2x concentration
    protocol.comment('STEP 3: Preparing 2x salt concentrations in Reservoir 4')
    for ci, conc in enumerate(salt_concs[:number_of_salt]):
        dest = reservoir_4.wells()[ci]
        target_conc = float(conc) * 2.0
        vol_high = (target_conc / salt_stock) * TOTAL_TARGET_UL
        vol_low = TOTAL_TARGET_UL - vol_high
        protocol.comment(f'Preparing {TOTAL_TARGET_UL}uL at 2x {conc} -> target {target_conc} (high {vol_high}uL, low {vol_low}uL) in Reservoir4 well {dest}')
        if vol_high > 0:
            withdraw_from_pool_multi(dest, high_salt_remaining, vol_high, p300m)
        if vol_low > 0:
            withdraw_from_pool_multi(dest, low_salt_remaining, vol_low, p300m)

    # Step 4: Prepare ligand dilutions in the deep-well mixing plate
    protocol.comment('STEP 4: Preparing ligand dilutions in mixing plate')
    # concentration to prepare is 2x ligand concentrations
    ligand_2x = [c * 2.0 for c in ligand_concs[:number_of_ligand]]

    # total volume per well as specified: [[TOTAL_VOLUME]]/2*[[REPLICATES]]*1.5
    total_per_well = (total_volume / 2.0) * replicates * 1.5
    protocol.comment(f'Total per mixing well: {total_per_well} uL')

    # For each salt concentration create one column. Rows A-H ascending row-wise low->high
    for col_index in range(number_of_salt):
        for row_index in range(8):
            # compute mixing plate well for row A-H and column col_index
            well = mixing_plate.rows()[row_index][col_index]
            desired_conc = ligand_2x[row_index]  # ascending row-wise: row A lowest
            # compute stock volume needed (from high stock)
            vol_stock_high = (desired_conc / ligand_stock) * total_per_well
            vol_stock = vol_stock_high
            used_low_stock = False
            stock_well = ligand_high_well
            # if high stock volume is below minimum usable, switch to low stock (10x lower concentration)
            if vol_stock_high < 20.0:
                stock_conc = ligand_stock / 10.0
                vol_stock_low = (desired_conc / stock_conc) * total_per_well
                if vol_stock_low > total_per_well:
                    # stock cannot provide needed concentration even without buffer; cap to total volume
                    protocol.comment('WARNING: even low stock cannot provide desired concentration; using stock only (no buffer)')
                    vol_stock = total_per_well
                    stock_well = ligand_low_well
                    used_low_stock = True
                else:
                    vol_stock = vol_stock_low
                    stock_well = ligand_low_well
                    used_low_stock = True
            else:
                # if vol_stock_high exceeds total_per_well, cap it
                if vol_stock_high > total_per_well:
                    protocol.comment('WARNING: high stock required volume exceeds total well volume; capping to total volume')
                    vol_stock = total_per_well
                    stock_well = ligand_high_well
                    used_low_stock = False
            # compute buffer volume (low salt buffer) to fill to total
            vol_buffer = total_per_well - vol_stock
            if vol_buffer < 0:
                vol_buffer = 0.0

            protocol.comment(f'Preparing well {well} with desired conc {desired_conc}: stock {vol_stock} uL from {"low" if used_low_stock else "high"} stock, buffer {vol_buffer} uL')
            # perform transfers using single channel pipette
            p300s.pick_up_tip()
            # transfer stock (may be > pipette.max_volume; chunk manually)
            remaining_stock = vol_stock
            while remaining_stock > 1e-6:
                # choose stock well with remaining
                src = ligand_high_well if ligand_remaining[ligand_high_well] >= 1.0 else ligand_low_well
                if ligand_remaining[src] <= 0:
                    # try the other
                    src = ligand_low_well if src is ligand_high_well else ligand_high_well
                if ligand_remaining[src] <= 0:
                    p300s.drop_tip()
                    raise RuntimeError('Ligand stocks exhausted')
                # available in this src
                avail = ligand_remaining[src]
                take = min(avail, remaining_stock, p300s.max_volume)
                p300s.aspirate(take, src)
                p300s.dispense(take, well)
                ligand_remaining[src] -= take
                remaining_stock -= take
            # transfer buffer from low salt pool
            remaining_buf = vol_buffer
            # find a low salt well with volume
            while remaining_buf > 1e-6:
                src = _find_pool_well(low_salt_remaining, min_total_uL=1.0)
                if src is None:
                    p300s.drop_tip()
                    raise RuntimeError('Low salt buffer pool exhausted')
                avail = low_salt_remaining[src]
                take = min(avail, remaining_buf, p300s.max_volume)
                p300s.aspirate(take, src)
                p300s.dispense(take, well)
                low_salt_remaining[src] -= take
                remaining_buf -= take
            # mix the well
            mix_vol = min(p300s.max_volume, total_per_well/2)
            p300s.mix(3, mix_vol, well)
            p300s.drop_tip()

    protocol.comment('Protocol complete')
