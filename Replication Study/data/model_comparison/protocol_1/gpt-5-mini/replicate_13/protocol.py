from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Preparation',
    'author': 'Lab 167',
    'description': 'Prepare salt gradients and ligand dilutions using placeholders for templating.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

import math

# Placeholders (literal strings so an external renderer can substitute them)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    # build the double-bracket check to avoid literal '[[' in the file
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
    # Labware
    # Custom labware in slot 1 with simulation fallback
    try:
        try:
            filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
        except Exception as exc:
            if 'not found' not in str(exc):
                raise
            protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
            filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)
    except Exception:
        # If anything else goes wrong, re-raise to fail early
        raise

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs and mixing plate
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # target: Reservoir 4 (slot 3)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # target: Reservoir 3 (slot 6)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # contains low and high salt
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # contains ligand stocks and buffers
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # low salt buffers

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_slot10])
    # multi-channel will primarily use slot7 but include slot4 as backup for simulation
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_slot7, tiprack_slot4])

    protocol.comment('Parsing placeholders (simulation-friendly fallbacks will be used if placeholders are still present).')

    # Fallback defaults for simulation (worst-case to exercise resources):
    # NUMBER_OF_SALT_CONCENTRATIONS -> 4 (so REPLICATES * 4 <= 12)
    # REPLICATES -> 3 (3 * 4 = 12 wells fill Reservoir 3)
    # NUMBER_OF_LIGAND_CONCENTRATIONS -> 8 (rows A-H)
    # TOTAL_VOLUME -> 200 (uL) as a reasonable working volume for mixing plate

    REPLICATES = parse_scalar(PLACEHOLDER_REPLICATES, 3, int)
    TOTAL_VOLUME = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, float)

    SALT_CONCENTRATIONS = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 50.0, 150.0, 300.0])
    LIGAND_CONCENTRATIONS = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0])

    SALT_STOCK_CONCENTRATION = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    LIGAND_STOCK_CONCENTRATION = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, float)

    NUMBER_OF_SALT_CONCENTRATIONS = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(SALT_CONCENTRATIONS), int)
    NUMBER_OF_LIGAND_CONCENTRATIONS = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(LIGAND_CONCENTRATIONS), int)

    # Validate sizing constraint for Reservoir 3 (12 wells max)
    total_salt_wells_needed = REPLICATES * NUMBER_OF_SALT_CONCENTRATIONS
    if total_salt_wells_needed > 12:
        raise RuntimeError(f'Replicates * NUMBER_OF_SALT_CONCENTRATIONS = {total_salt_wells_needed} exceeds 12 wells in Reservoir 3')

    protocol.comment(f'Planned: {NUMBER_OF_SALT_CONCENTRATIONS} salt concentrations, {REPLICATES} replicates -> {total_salt_wells_needed} wells in Reservoir 3')
    protocol.comment(f'Planned: {NUMBER_OF_LIGAND_CONCENTRATIONS} ligand concentrations, mixing plate columns = {NUMBER_OF_SALT_CONCENTRATIONS}')

    # Track remaining volumes in reservoir1 and reservoir2 wells (in uL). Each well starts with 14 mL = 14000 uL
    def _init_pool(labware):
        return [14000.0 for _ in labware.wells()]

    pool_res2_remaining = _init_pool(reservoir2)  # reservoir2 wells 0-11
    pool_res1_remaining = _init_pool(reservoir1)  # reservoir1 wells 0-11
    pool_res0_remaining = _init_pool(reservoir0)  # reservoir0 wells 0-11 (low salt diluent)

    # Helper to consume volume from a pool of wells. This splits across wells as needed.
    def consume_from_pool(protocol, pipette, per_channel_volume, pool_wells, remaining_list, dest, new_tip='never'):
        """
        per_channel_volume: uL per channel to deliver to a single destination well.
        pool_wells is a LIST of Well objects corresponding to remaining_list indices.
        This will remove 8 * per_channel_volume uL from the source pool wells
        and perform transfer calls as required. 'dest' is a single-well destination.
        """
        needed_total = per_channel_volume * 8.0  # total uL needed from pool wells
        idx = 0
        while needed_total > 1e-6 and idx < len(remaining_list):
            avail = remaining_list[idx]
            if avail <= 0:
                idx += 1
                continue
            take = min(avail, needed_total)
            per_channel_take = take / 8.0
            if per_channel_take > 0:
                # Make the transfer from this single pool well to the destination
                src = pool_wells[idx]
                # Use transfer with new_tip='never' because caller controls tips
                pipette.transfer(per_channel_take, src, dest, new_tip=new_tip)
                remaining_list[idx] -= take
                needed_total -= take
            else:
                idx += 1
        if needed_total > 1e-6:
            raise RuntimeError('Pool does not have enough liquid to satisfy a request of %s uL' % (per_channel_volume * 8))

    # Step 2: For each required salt concentration, fill up REPLICATES wells in Reservoir 3 with 10 mL total
    protocol.comment('Step 2: Preparing Reservoir 3 with specified salt concentrations (10 mL per well).')

    total_target_ul = 10000.0  # 10 mL per well
    per_channel_total = total_target_ul / 8.0

    # define source pools for low (0 salt) and high (stock) in reservoirs 2 and 1
    # For low salt use reservoir2 wells 0-5 first, then reservoir1 wells 2-6 or reservoir0 as fallback
    low_pool_wells = list(reservoir2.wells()[0:6]) + list(reservoir1.wells()[2:7]) + list(reservoir0.wells())
    low_remaining = pool_res2_remaining[0:6] + pool_res1_remaining[2:7] + pool_res0_remaining

    # For high salt use reservoir2 wells 6-11 then reservoir1 wells 7-11
    high_pool_wells = list(reservoir2.wells()[6:12]) + list(reservoir1.wells()[7:12])
    high_remaining = pool_res2_remaining[6:12] + pool_res1_remaining[7:12]

    # Fill Reservoir 3 wells in ascending order with salt concentrations
    dest_wells_r3 = reservoir3.wells()

    idx = 0
    for s_idx in range(NUMBER_OF_SALT_CONCENTRATIONS):
        # choose concentration from provided list (ascending assumed in user input)
        conc = SALT_CONCENTRATIONS[s_idx]
        # compute fraction of high stock needed to reach 'conc' given stock concentration
        frac_high = conc / SALT_STOCK_CONCENTRATION if SALT_STOCK_CONCENTRATION != 0 else 0
        frac_high = min(max(frac_high, 0.0), 1.0)
        volume_high_total = frac_high * total_target_ul
        volume_low_total = total_target_ul - volume_high_total
        per_channel_high = volume_high_total / 8.0
        per_channel_low = volume_low_total / 8.0

        for rep in range(REPLICATES):
            dest = dest_wells_r3[idx]
            protocol.comment(f'Filling Reservoir3 well {dest} with salt {conc} (replicate {rep + 1})')
            p300_multi.pick_up_tip()
            # transfer high
            if per_channel_high > 0:
                consume_from_pool(protocol, p300_multi, per_channel_high, high_pool_wells, high_remaining, dest, new_tip='never')
            # transfer low
            if per_channel_low > 0:
                consume_from_pool(protocol, p300_multi, per_channel_low, low_pool_wells, low_remaining, dest, new_tip='never')
            # mix target well
            mix_volume = min(p300_multi.max_volume, per_channel_total)
            p300_multi.mix(5, mix_volume, dest)
            p300_multi.drop_tip()
            idx += 1

    # Step 3: For each required salt concentration, fill up 1 well in Reservoir 4 with 10 mL at 2x concentration
    protocol.comment('Step 3: Preparing Reservoir 4 with 10 mL at 2x salt concentrations.')
    dest_wells_r4 = reservoir4.wells()

    for s_idx in range(NUMBER_OF_SALT_CONCENTRATIONS):
        conc = SALT_CONCENTRATIONS[s_idx]
        target_conc = 2.0 * conc
        frac_high = target_conc / SALT_STOCK_CONCENTRATION if SALT_STOCK_CONCENTRATION != 0 else 0
        frac_high = min(max(frac_high, 0.0), 1.0)
        volume_high_total = frac_high * total_target_ul
        volume_low_total = total_target_ul - volume_high_total
        per_channel_high = volume_high_total / 8.0
        per_channel_low = volume_low_total / 8.0

        dest = dest_wells_r4[s_idx]
        protocol.comment(f'Filling Reservoir4 well {dest} with 2x salt {target_conc}')
        p300_multi.pick_up_tip()
        if per_channel_high > 0:
            consume_from_pool(protocol, p300_multi, per_channel_high, high_pool_wells, high_remaining, dest, new_tip='never')
        if per_channel_low > 0:
            consume_from_pool(protocol, p300_multi, per_channel_low, low_pool_wells, low_remaining, dest, new_tip='never')
        mix_volume = min(p300_multi.max_volume, per_channel_total)
        p300_multi.mix(5, mix_volume, dest)
        p300_multi.drop_tip()

    # Step 4: Prepare ligand dilutions in mixing plate (deep well) using single-channel pipette
    protocol.comment('Step 4: Creating ligand dilutions in the mixing plate (deep well)')

    # Arrange mixing plate: for each salt concentration -> a column; rows A-H are ligand concentrations ascending
    num_rows = NUMBER_OF_LIGAND_CONCENTRATIONS
    num_cols = NUMBER_OF_SALT_CONCENTRATIONS

    total_per_well = (TOTAL_VOLUME / 2.0) * REPLICATES * 1.5

    # helper to map row (0..7) and col (0..n-1) to mixing_plate wells (row-wise ascending A->H across columns)
    def mixing_well(row, col):
        # nest_96_wellplate_2ml_deep wells() is A1, B1, C1...H1, A2 ...
        idx = col * 8 + row
        return mixing_plate.wells()[idx]

    # Pools for ligand stock (reservoir1 well0 and well1) and diluent low salt (use reservoir0 wells)
    ligand_stock_high = reservoir1.wells()[0]
    ligand_stock_low = reservoir1.wells()[1]

    for col in range(num_cols):
        for row in range(num_rows):
            target_ligand = LIGAND_CONCENTRATIONS[row] * 2.0  # 2x concentration
            V = total_per_well
            # compute volume of stock needed: V_stock = (C_target * V) / C_stock
            vol_from_high_stock = (target_ligand * V) / LIGAND_STOCK_CONCENTRATION if LIGAND_STOCK_CONCENTRATION != 0 else 0
            chosen_stock = ligand_stock_high
            chosen_stock_remaining = pool_res1_remaining[0]
            stock_conc_used = LIGAND_STOCK_CONCENTRATION
            if vol_from_high_stock < 20.0:
                # use low concentration stock (stock/10) in well1
                stock_conc_used = LIGAND_STOCK_CONCENTRATION / 10.0
                chosen_stock = ligand_stock_low
                chosen_stock_remaining = pool_res1_remaining[1]
                vol_from_high_stock = (target_ligand * V) / stock_conc_used if stock_conc_used != 0 else 0

            vol_stock_uL = vol_from_high_stock
            vol_diluent = V - vol_stock_uL
            if vol_stock_uL < 0:
                vol_stock_uL = 0
                vol_diluent = V

            dest = mixing_well(row, col)
            protocol.comment(f'Preparing mixing well {dest} for ligand {LIGAND_CONCENTRATIONS[row]} (2x: {target_ligand}) Column {col + 1}, Row {row + 1}')

            p300_single.pick_up_tip()
            # transfer stock from chosen stock well(s) - may need splitting across wells if > available
            # For simplicity, we assume well0 or well1 has enough for simulation fallback; perform a pool-style consumption
            needed_stock_total = vol_stock_uL
            # consume from reservoir1 well0 and well1 as needed
            stock_idx = 0 if chosen_stock is ligand_stock_high else 1
            while needed_stock_total > 1e-6:
                avail = pool_res1_remaining[stock_idx]
                if avail <= 0:
                    stock_idx += 1
                    if stock_idx >= len(pool_res1_remaining):
                        raise RuntimeError('Not enough ligand stock in reservoir1')
                    continue
                take = min(avail, needed_stock_total)
                src = reservoir1.wells()[stock_idx]
                p300_single.transfer(take, src, dest, new_tip='never')
                pool_res1_remaining[stock_idx] -= take
                needed_stock_total -= take
            # transfer diluent (low salt) from reservoir0 wells
            needed_diluent = vol_diluent
            r0_idx = 0
            while needed_diluent > 1e-6:
                avail = pool_res0_remaining[r0_idx]
                if avail <= 0:
                    r0_idx += 1
                    if r0_idx >= len(pool_res0_remaining):
                        raise RuntimeError('Not enough low salt buffer in reservoir0')
                    continue
                take = min(avail, needed_diluent)
                src = reservoir0.wells()[r0_idx]
                p300_single.transfer(take, src, dest, new_tip='never')
                pool_res0_remaining[r0_idx] -= take
                needed_diluent -= take

            # mix the well to ensure homogeneity
            mix_vol = min(p300_single.max_volume, V / 2.0)
            p300_single.mix(5, mix_vol, dest)
            p300_single.drop_tip()

    protocol.comment('Protocol complete.')
