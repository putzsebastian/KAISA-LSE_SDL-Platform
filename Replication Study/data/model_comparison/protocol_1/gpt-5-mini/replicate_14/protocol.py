from opentrons import protocol_api

metadata = {
    'protocolName': 'Template: Salt and Ligand Preparation',
    'author': 'Lab 167',
    'description': "Prepare salt gradients in reservoirs and ligand dilutions in a deep-well mixing plate. All numeric values are templated with placeholders.",
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


# Placeholders (literal strings for external substitution)
SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
REPLICATES = '[[REPLICATES]]'
TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    # build brackets by repetition so the token is not literally present here
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default=0.0, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def parse_list(value, default=None, cast=float):
    if default is None:
        default = []
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Labware
    try:
        cytiva_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard plate as a SIMULATION fallback only.')
        cytiva_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (slot 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (slot 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (slot 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (slot 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (slot 5)

    # Mixing plate (deep well)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    # Primary tip source for multi-channel is slot 7; allow slot 4 as fallback if slot7 runs out
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_7, tiprack_4])
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_10])

    # Parse placeholders (simulation-friendly defaults chosen to exercise limits)
    salt_list = parse_list(SALT_CONCENTRATIONS, default=[0, 50, 150, 300])
    ligand_list = parse_list(LIGAND_CONCENTRATIONS, default=[0.1, 1, 10, 100, 250, 500, 750, 1000])
    replicates = int(parse_scalar(REPLICATES, default=3, cast=int))
    total_volume = parse_scalar(TOTAL_VOLUME, default=200.0)  # uL
    salt_stock = parse_scalar(SALT_STOCK_CONCENTRATION, default=1000.0)
    ligand_stock = parse_scalar(LIGAND_STOCK_CONCENTRATION, default=1000.0)

    n_salt = len(salt_list)
    n_ligand = len(ligand_list)

    # Sanity checks
    if replicates * n_salt > 12:
        raise RuntimeError('Requested number of wells (replicates x number_of_salt_concentrations) exceeds 12 for Reservoir 3.')

    # Initialize reservoir remaining volumes (in uL)
    # Each reservoir well initially contains 14 mL = 14000 uL
    pool_remaining = {}
    for i in range(12):
        pool_remaining[('res2', i)] = 14000.0
        pool_remaining[('res1', i)] = 14000.0
        pool_remaining[('res0', i)] = 14000.0

    # Helper: get labware object and well object by pool key
    def pool_key_to_well(key):
        loc, idx = key
        if loc == 'res2':
            return reservoir_2.wells()[idx]
        if loc == 'res1':
            return reservoir_1.wells()[idx]
        if loc == 'res0':
            return reservoir_0.wells()[idx]
        raise RuntimeError('Unknown pool location')

    # Pools (ordered lists) - low salt pool and high salt pool
    low_pool = [('res2', i) for i in range(0, 6)] + [('res1', i) for i in range(2, 7)] + [('res0', i) for i in range(12)]
    high_pool = [('res2', i) for i in range(6, 12)] + [('res1', i) for i in range(7, 12)]

    # Generic transfer from a pooled set of wells using multi-channel pipette
    def transfer_from_pool_multi(pip, pool, remaining, total_needed_ul, dest_well):
        needed = float(total_needed_ul)
        while needed > 1e-6:
            src = None
            for key in pool:
                if remaining.get(key, 0) > 1e-6:
                    src = key
                    break
            if src is None:
                raise RuntimeError('Pool is exhausted while trying to supply %s uL' % total_needed_ul)
            avail = remaining[src]
            take = min(avail, needed)
            per_channel = take / 8.0
            if per_channel <= 0:
                raise RuntimeError('Calculated per-channel volume is zero or negative')
            pip.transfer(per_channel, pool_key_to_well(src), dest_well, new_tip='never')
            remaining[src] -= take
            needed -= take

    # Generic transfer from a pooled set of wells using single-channel pipette
    def transfer_from_pool_single(pip, pool, remaining, total_needed_ul, dest_well):
        needed = float(total_needed_ul)
        while needed > 1e-6:
            src = None
            for key in pool:
                if remaining.get(key, 0) > 1e-6:
                    src = key
                    break
            if src is None:
                raise RuntimeError('Pool is exhausted while trying to supply %s uL' % total_needed_ul)
            avail = remaining[src]
            take = min(avail, needed)
            pip.transfer(take, pool_key_to_well(src), dest_well, new_tip='never')
            remaining[src] -= take
            needed -= take

    # Step 2: For each salt concentration, fill replicates wells in Reservoir 3 (slot 6) with total 10 mL
    protocol.comment('Step 2: Preparing salt dilutions in Reservoir 3 (slot 6)')
    total_target_ul = 10000.0  # 10 mL per well
    dest_index = 0
    for c in salt_list:
        for rep in range(replicates):
            desired = float(c)
            vol_high = total_target_ul * (desired / salt_stock) if salt_stock != 0 else 0.0
            vol_high = max(0.0, min(vol_high, total_target_ul))
            vol_low = total_target_ul - vol_high

            dest = reservoir_3.wells()[dest_index]
            protocol.comment(f'Preparing Reservoir 3 well {dest_index} for concentration {desired} (low {vol_low} uL, high {vol_high} uL)')

            p300_multi.pick_up_tip()
            if vol_low > 0:
                transfer_from_pool_multi(p300_multi, low_pool, pool_remaining, vol_low, dest)
            if vol_high > 0:
                transfer_from_pool_multi(p300_multi, high_pool, pool_remaining, vol_high, dest)

            mix_vol = min(p300_multi.max_volume, (total_target_ul / 8.0) / 2.0)
            mix_vol = max(20, mix_vol)
            p300_multi.mix(5, mix_vol, dest)
            p300_multi.drop_tip()

            dest_index += 1
            if dest_index >= 12:
                break
        if dest_index >= 12:
            break

    # Step 3: For each salt concentration, fill 1 well in Reservoir 4 (slot 3) with total 10 mL at 2x concentration
    protocol.comment('Step 3: Preparing 2x salt solutions in Reservoir 4 (slot 3)')
    dest_index = 0
    for c in salt_list:
        desired_2x = float(c) * 2.0
        vol_high = total_target_ul * (desired_2x / salt_stock) if salt_stock != 0 else 0.0
        vol_high = max(0.0, min(vol_high, total_target_ul))
        vol_low = total_target_ul - vol_high

        dest = reservoir_4.wells()[dest_index]
        protocol.comment(f'Preparing Reservoir 4 well {dest_index} for concentration {desired_2x} (low {vol_low} uL, high {vol_high} uL)')

        p300_multi.pick_up_tip()
        if vol_low > 0:
            transfer_from_pool_multi(p300_multi, low_pool, pool_remaining, vol_low, dest)
        if vol_high > 0:
            transfer_from_pool_multi(p300_multi, high_pool, pool_remaining, vol_high, dest)
        mix_vol = min(p300_multi.max_volume, (total_target_ul / 8.0) / 2.0)
        mix_vol = max(20, mix_vol)
        p300_multi.mix(5, mix_vol, dest)
        p300_multi.drop_tip()

        dest_index += 1
        if dest_index >= 12:
            break

    # Step 4: Prepare ligand dilutions in mixing plate (deep well), using single-channel pipette
    protocol.comment('Step 4: Preparing ligand dilutions in mixing plate (slot 11)')
    total_vol_well = parse_scalar(TOTAL_VOLUME, default=200.0) / 2.0 * replicates * 1.5
    total_vol_well = float(total_vol_well)

    # Define diluent pool (low salt sources across reservoirs)
    diluent_pool = [('res2', i) for i in range(0, 6)] + [('res1', i) for i in range(2, 7)] + [('res0', i) for i in range(12)]

    for col_idx in range(n_salt):
        for row_idx in range(8):
            if row_idx >= n_ligand:
                continue
            ligand_conc = float(ligand_list[row_idx]) * 2.0
            vol_from_high = total_vol_well * (ligand_conc / ligand_stock) if ligand_stock != 0 else 0.0
            if vol_from_high < 20.0:
                low_stock_conc = ligand_stock / 10.0
                if low_stock_conc <= 0:
                    raise RuntimeError('Low stock concentration invalid')
                vol_from_low = total_vol_well * (ligand_conc / low_stock_conc)
                stock_keys = [('res1', 1)]  # low stock well
                vol_stock_to_use = vol_from_low
            else:
                stock_keys = [('res1', 0)]  # high stock well
                vol_stock_to_use = vol_from_high

            # Clamp stock volume to total volume to avoid negative diluent
            if vol_stock_to_use > total_vol_well:
                vol_stock_to_use = total_vol_well

            vol_diluent = total_vol_well - vol_stock_to_use
            if vol_diluent < 0:
                vol_diluent = 0.0

            dest_well = mixing_plate.wells()[row_idx * 12 + col_idx]
            protocol.comment(f'Preparing mixing plate well {dest_well} for ligand {ligand_conc} (stock {vol_stock_to_use} uL, diluent {vol_diluent} uL)')

            p300_single.pick_up_tip()
            # transfer stock from the designated stock well(s)
            if vol_stock_to_use > 0:
                transfer_from_pool_single(p300_single, stock_keys, pool_remaining, vol_stock_to_use, dest_well)
            # transfer diluent from diluent_pool
            if vol_diluent > 0:
                transfer_from_pool_single(p300_single, diluent_pool, pool_remaining, vol_diluent, dest_well)

            mix_v = min(p300_single.max_volume, total_vol_well / 4.0)
            mix_v = max(20, mix_v)
            p300_single.mix(5, mix_v, dest_well)
            p300_single.drop_tip()

    protocol.comment('Protocol complete.')
