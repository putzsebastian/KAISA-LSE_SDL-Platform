from opentrons import protocol_api
import math

metadata = {
    'protocolName': 'Salt and Ligand Preparation Template',
    'author': 'User',
    'description': 'Template protocol to prepare salt gradients and ligand dilutions using placeholders',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings so the wizard can substitute them)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    # build brackets rather than write literal '[[' to be safe for replacement tooling
    return str(s).startswith('[' * 2) and str(s).endswith(']' * 2)


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
    # parse placeholders with conservative fallbacks (worst-case where sensible)
    REPLICATES = parse_scalar(PLACEHOLDER_REPLICATES, 3, int)  # fallback 3
    TOTAL_VOLUME = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 2000, float)  # fallback 2000 uL
    SALT_CONCENTRATIONS = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [50.0, 150.0, 300.0])
    # Use conservative but realistic fallback ligand concentrations to avoid exhausting 14 mL stock wells in simulation
    LIGAND_CONCENTRATIONS = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0])
    SALT_STOCK_CONCENTRATION = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, float)
    LIGAND_STOCK_CONCENTRATION = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 10000.0, float)
    NUMBER_OF_SALT_CONCENTRATIONS = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(SALT_CONCENTRATIONS), int)
    NUMBER_OF_LIGAND_CONCENTRATIONS = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(LIGAND_CONCENTRATIONS), int)

    # Basic parameters
    SALT_TOTAL_VOL_UL = 10_000  # 10 mL for each salt destination well
    SALT_RES3_MAX_WELLS = 12

    # Labware load (with fallback for custom labware)
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # reservoirs
    res4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    res3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (target for salt replicates)
    res2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (contains low and high salt)
    res1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (ligand stocks + buffers)
    res0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (low salt buffers)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack10])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack7])

    # Initialize tracking of remaining volumes in source wells (uL)
    # Every listed well initially contains 14 mL = 14000 uL
    def init_pool(wells):
        return {w: 14000.0 for w in wells}

    # Pools for high and low salt across reservoirs (ordered to use reservoir2 first then reservoir1 then reservoir0)
    high_salt_wells = [res2.wells()[i] for i in range(6, 12)] + [res1.wells()[i] for i in range(7, 12)]
    low_salt_wells = [res2.wells()[i] for i in range(0, 6)] + [res1.wells()[i] for i in range(2, 7)] + res0.wells()

    high_pool_remaining = init_pool(high_salt_wells)
    low_pool_remaining = init_pool(low_salt_wells)

    # Ligand stock wells
    ligand_stock_high = res1.wells()[0]
    ligand_stock_low = res1.wells()[1]
    ligand_pool_remaining = {ligand_stock_high: 14000.0, ligand_stock_low: 14000.0}

    # Helper: find next available well in a pool with any remaining volume
    def _next_source(pool_remaining):
        for well, rem in pool_remaining.items():
            if rem > 10.0:  # leave tiny unusable remainder
                return well
        return None

    # Helper: consume total_ul from pool, performing transfers with p300_multi
    def consume_from_pool_multichannel(pip, pool_remaining, dest, total_ul):
        """Consume total_ul (uL) from pool_remaining dict and transfer to dest well using pip (multi-channel).
        total_ul is the TOTAL volume to deliver to the destination well (not per-channel). Caller must hold a tip."""
        remaining_needed = float(total_ul)
        while remaining_needed > 1e-6:
            src = _next_source(pool_remaining)
            if src is None:
                raise RuntimeError('Source pool is exhausted while trying to supply {} uL'.format(total_ul))
            available = pool_remaining[src]
            take = min(available, remaining_needed)
            # For multi-channel pass PER-CHANNEL volume
            per_channel = take / 8.0
            pip.transfer(per_channel, src, dest, new_tip='never')
            pool_remaining[src] -= take
            remaining_needed -= take

    # Helper: consume from single well stock (for single-channel operations)
    def consume_from_single_source(pip, src_pool_remaining, dest, total_ul):
        """Consume total_ul (uL) from src_pool_remaining and transfer to dest using single-channel pipette. Caller must hold a tip."""
        remaining_needed = float(total_ul)
        while remaining_needed > 1e-6:
            src = None
            for w, rem in src_pool_remaining.items():
                if rem > 1e-6:
                    src = w
                    break
            if src is None:
                raise RuntimeError('Ligand stock pool exhausted')
            available = src_pool_remaining[src]
            take = min(available, remaining_needed)
            pip.transfer(take, src, dest, new_tip='never')
            src_pool_remaining[src] -= take
            remaining_needed -= take

    protocol.comment('Starting Step 2: create salt dilutions in Reservoir 3 (slot 6)')

    # Step 2: For each required salt concentration, fill up REPLICATES wells in Reservoir 3 (slot 6)
    salt_values = sorted([float(x) for x in SALT_CONCENTRATIONS])[:NUMBER_OF_SALT_CONCENTRATIONS]
    total_slots_needed = REPLICATES * len(salt_values)
    if total_slots_needed > SALT_RES3_MAX_WELLS:
        raise RuntimeError('Requested number of salt wells ({}) exceeds Reservoir 3 capacity (12)'.format(total_slots_needed))

    dest_index = 0
    for conc in salt_values:
        for rep in range(REPLICATES):
            dest_well = res3.wells()[dest_index]
            dest_index += 1
            # calculate volumes (uL)
            vol_high = SALT_TOTAL_VOL_UL * (conc / SALT_STOCK_CONCENTRATION)
            vol_low = SALT_TOTAL_VOL_UL - vol_high
            protocol.comment(f'Preparing salt conc {conc} in {dest_well} -> high {vol_high} uL; low {vol_low} uL')
            # pick up a multi-channel tip and transfer low then high (allow reuse of tip when aspirating from same source)
            p300_multi.pick_up_tip()
            # transfer low salt from low_pool
            if vol_low > 0:
                consume_from_pool_multichannel(p300_multi, low_pool_remaining, dest_well, vol_low)
            # transfer high salt
            if vol_high > 0:
                consume_from_pool_multichannel(p300_multi, high_pool_remaining, dest_well, vol_high)
            # mix once: use multi-channel mix on the destination well
            mix_per_channel = min(p300_multi.max_volume, 200)
            p300_multi.transfer(mix_per_channel, dest_well, dest_well, mix_after=(3, mix_per_channel), new_tip='never')
            p300_multi.drop_tip()

    protocol.comment('Step 2 complete')

    protocol.comment('Starting Step 3: prepare 2x salt solutions in Reservoir 4 (slot 3)')
    # Step 3: for each required salt concentration, fill up 1 well in Reservoir4 with 2x required salt concentration
    res4_index = 0
    for conc in salt_values:
        dest_well = res4.wells()[res4_index]
        res4_index += 1
        desired_conc = conc * 2.0
        vol_high = SALT_TOTAL_VOL_UL * (desired_conc / SALT_STOCK_CONCENTRATION)
        vol_low = SALT_TOTAL_VOL_UL - vol_high
        protocol.comment(f'Preparing 2x salt {desired_conc} in {dest_well} -> high {vol_high} uL; low {vol_low} uL')
        p300_multi.pick_up_tip()
        if vol_low > 0:
            consume_from_pool_multichannel(p300_multi, low_pool_remaining, dest_well, vol_low)
        if vol_high > 0:
            consume_from_pool_multichannel(p300_multi, high_pool_remaining, dest_well, vol_high)
        # mix
        mix_per_channel = min(p300_multi.max_volume, 200)
        p300_multi.transfer(mix_per_channel, dest_well, dest_well, mix_after=(3, mix_per_channel), new_tip='never')
        p300_multi.drop_tip()

    protocol.comment('Step 3 complete')

    protocol.comment('Starting Step 4: prepare ligand dilutions in mixing plate (slot 11)')
    # Step 4: prepare ligand dilutions (2x concentrations) in mixing plate
    ligand_values = LIGAND_CONCENTRATIONS[:NUMBER_OF_LIGAND_CONCENTRATIONS]
    salt_count = len(salt_values)
    ligand_count = len(ligand_values)

    # determine per-well total volume in uL
    well_total = (TOTAL_VOLUME / 2.0) * REPLICATES * 1.5
    protocol.comment(f'Each mixing well total volume: {well_total} uL')

    # iterate rows A-H (0-7) and columns 0..salt_count-1
    for row_idx in range(min(8, ligand_count)):
        ligand_conc = float(ligand_values[row_idx])
        desired_conc = ligand_conc * 2.0
        for col_idx in range(salt_count):
            dest = mixing_plate.rows()[row_idx][col_idx]
            # determine which stock to use based on required stock volume
            required_stock_vol = well_total * (desired_conc / LIGAND_STOCK_CONCENTRATION)
            stock = ligand_stock_high
            stock_conc_used = LIGAND_STOCK_CONCENTRATION
            if required_stock_vol < 20.0:
                # use low-concentration stock instead (S/10)
                stock = ligand_stock_low
                stock_conc_used = LIGAND_STOCK_CONCENTRATION / 10.0
                required_stock_vol = well_total * (desired_conc / stock_conc_used)
            if required_stock_vol > 0:
                if ligand_pool_remaining.get(stock, 0) < required_stock_vol:
                    raise RuntimeError('Not enough ligand stock in chosen well to prepare dilutions')
            # volumes to transfer
            vol_stock = required_stock_vol
            vol_buffer = well_total - vol_stock
            protocol.comment(f'Preparing ligand {desired_conc} in {dest}: stock {vol_stock} uL from {stock}; buffer {vol_buffer} uL')
            # transfer stock and buffer using single-channel pipette
            p300_single.pick_up_tip()
            # transfer stock (may need to split across stock wells)
            if vol_stock > 0:
                consume_from_single_source(p300_single, ligand_pool_remaining, dest, vol_stock)
            # transfer buffer from low-salt sources
            remaining_buf = vol_buffer
            while remaining_buf > 1e-6:
                src = _next_source(low_pool_remaining)
                if src is None:
                    raise RuntimeError('Low salt buffer pool exhausted while preparing ligand dilutions')
                avail = low_pool_remaining[src]
                take = min(avail, remaining_buf)
                p300_single.transfer(take, src, dest, new_tip='never')
                low_pool_remaining[src] -= take
                remaining_buf -= take
            # mix the destination
            p300_single.transfer(min(200, p300_single.max_volume), dest, dest, mix_after=(3, min(200, p300_single.max_volume)), new_tip='never')
            p300_single.drop_tip()

    protocol.comment('Step 4 complete: ligand dilutions prepared in mixing plate')

    protocol.comment('Protocol complete')
