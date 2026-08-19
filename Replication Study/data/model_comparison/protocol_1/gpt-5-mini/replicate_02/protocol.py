from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Preparation Template',
    'author': 'Lab 167',
    'description': 'Templated protocol to prepare salt series and ligand dilutions using placeholders'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


# Placeholders (literal strings - do not modify)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Detect an unreplaced [[TOKEN]] in the file without writing '[[' literally.
    """
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
    # labware
    # Slot 1: custom labware - fallback if missing
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; using a standard 96-well plate as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    res_slot3 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    res_slot6 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (empty)
    res_slot8 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    res_slot9 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    res_slot5 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # pipettes
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_slot7])
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_slot10])

    # Parse placeholders (with safe fallbacks for simulation)
    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 3, cast=int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0, cast=float)  # uL
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [50.0, 150.0, 300.0, 600.0], cast=float)
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0], cast=float)
    salt_stock = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0, cast=float)
    ligand_stock = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0, cast=float)

    num_salt = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concs), cast=int)
    num_ligand = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concs), cast=int)

    # enforce list lengths according to provided number placeholders when substituted
    salt_concs = salt_concs[:num_salt]
    ligand_concs = ligand_concs[:num_ligand]

    protocol.comment(f'Parsed parameters: replicates={replicates}, total_volume={total_volume} uL, num_salt={num_salt}, num_ligand={num_ligand}')

    # Reservoir mappings according to the user specification
    # Reservoir 0 -> slot 5 wells
    reservoir0 = res_slot5.wells()
    # Reservoir 1 -> slot 9 wells
    reservoir1 = res_slot9.wells()
    # Reservoir 2 -> slot 8 wells
    reservoir2 = res_slot8.wells()
    # Reservoir 3 -> slot 6 wells (empty initially, will be filled as targets)
    reservoir3 = res_slot6.wells()
    # Reservoir 4 -> slot 3 wells (empty initially, will be filled as targets)
    reservoir4 = res_slot3.wells()

    # Initialize pool volumes (uL) for source reservoirs (0,1 and 2) - each well starts with 14 mL
    initial_well_ul = 14000
    pool_volumes = {}
    for w in reservoir1:
        pool_volumes[w] = initial_well_ul
    for w in reservoir2:
        pool_volumes[w] = initial_well_ul
    for w in reservoir0:
        pool_volumes[w] = initial_well_ul

    # Helper to consume from a pool of wells (pool_wells is a list), updating pool_volumes dict.
    def consume_from_pool(pool_wells, amount_ul):
        """Return a list of (well, ul) allocations summing to amount_ul by taking from pool_wells in order."""
        remaining = amount_ul
        allocations = []
        for w in pool_wells:
            avail = pool_volumes.get(w, 0)
            if avail <= 1:  # tiny remainder treated as empty
                continue
            take = min(avail, remaining)
            if take > 0:
                pool_volumes[w] = avail - take
                allocations.append((w, take))
                remaining -= take
            if remaining <= 0:
                break
        if remaining > 0:
            raise RuntimeError(f'Pool ran dry while trying to allocate {amount_ul} uL; {remaining} uL short')
        return allocations

    # Step 2: For each salt concentration, fill up [[REPLICATES]] wells in Reservoir 3 with total 10 mL each
    protocol.comment('STEP 2: Preparing salt dilutions in Reservoir 3 (slot 6)')
    total_target_ul = 10000  # 10 mL per target well

    # Validate capacity: replicates * num_salt must not exceed 12
    if replicates * num_salt > 12:
        raise RuntimeError('Requested number of target wells (replicates * number of salt concentrations) exceeds 12 in Reservoir 3')

    # Prepare ascending concentrations in reservoir3 wells (use first N wells)
    target_res3_wells = list(reservoir3)[: replicates * num_salt]

    # Build list of target concentrations repeated per replicates and ascending with well number
    targets = []
    for conc in salt_concs:
        for r in range(replicates):
            targets.append(conc)

    # Now iterate and create each target by mixing low (0) and high (salt_stock) from reservoirs 2 and 1
    # Low salt buffer sources: assume zero-salt are in wells 0..5 of reservoir2 and many in reservoir1/0; we'll use reservoir2 wells with zeros and reservoir1 wells as needed
    # High salt buffer sources: wells 6-11 in reservoir2 and 7-11 in reservoir1 as specified

    # Define low and high source pools (search across reservoir2 and reservoir1 as available)
    # low pool: reservoir2 wells 0-5 then reservoir1 wells 2-6 then reservoir0 wells (all zeros)
    low_pool = [reservoir2[i] for i in range(6)] + [reservoir1[i] for i in range(2, 7)] + [reservoir0[i] for i in range(12)]
    # high pool: reservoir2 wells 6-11 then reservoir1 wells 7-11
    high_pool = [reservoir2[i] for i in range(6, 12)] + [reservoir1[i] for i in range(7, 12)]

    protocol.comment(f'Using {len(low_pool)} wells as low_pool and {len(high_pool)} wells as high_pool sources')

    # pick up a tip on multi-channel pipette (from slot 7)
    p300_multi.pick_up_tip()

    for idx, (conc, dest_well) in enumerate(zip(targets, target_res3_wells)):
        protocol.comment(f'Preparing Reservoir3 well {idx} at target salt conc {conc}')
        # fraction from high stock
        fraction = conc / salt_stock if salt_stock != 0 else 0
        if fraction < 0:
            fraction = 0
        if fraction > 1:
            fraction = 1
        high_vol = int(round(total_target_ul * fraction))
        low_vol = int(round(total_target_ul - high_vol))

        protocol.comment(f'Will add {high_vol} uL high-salt and {low_vol} uL low-salt into {dest_well} (total {total_target_ul} uL)')

        # consume from pools and perform transfers (multi-channel: per-channel volumes passed)
        # High salt first
        if high_vol > 0:
            allocations = consume_from_pool(high_pool, high_vol)
            for (src, vol) in allocations:
                per_channel = vol / 8.0
                # perform transfer from src to dest_well
                p300_multi.transfer(per_channel, src, dest_well, new_tip='never')
        # Low salt
        if low_vol > 0:
            allocations = consume_from_pool(low_pool, low_vol)
            for (src, vol) in allocations:
                per_channel = vol / 8.0
                p300_multi.transfer(per_channel, src, dest_well, new_tip='never')

        # Mix the destination well once fully dispensed
        mix_vol = min(p300_multi.max_volume, 300)
        p300_multi.mix(5, mix_vol, dest_well)

    p300_multi.drop_tip()

    # Step 3: For each salt concentration, fill up 1 well in Reservoir 4 with total 10 mL at 2x required salt concentration
    protocol.comment('STEP 3: Preparing 2x salt solutions in Reservoir 4 (slot 3)')
    total_target_ul_2x = 10000
    if len(salt_concs) > 12:
        raise RuntimeError('Too many salt concentrations to place into Reservoir 4 (max 12)')
    target_res4_wells = list(reservoir4)[: len(salt_concs)]

    # Re-use same pools; pick up tip
    p300_multi.pick_up_tip()

    for idx, (conc, dest_well) in enumerate(zip(salt_concs, target_res4_wells)):
        target_conc = conc * 2.0
        fraction = target_conc / salt_stock if salt_stock != 0 else 0
        if fraction < 0:
            fraction = 0
        if fraction > 1:
            fraction = 1
        high_vol = int(round(total_target_ul_2x * fraction))
        low_vol = int(round(total_target_ul_2x - high_vol))
        protocol.comment(f'Preparing Reservoir4 well {idx} at 2x salt conc {target_conc}: high {high_vol} uL, low {low_vol} uL')

        if high_vol > 0:
            allocations = consume_from_pool(high_pool, high_vol)
            for (src, vol) in allocations:
                per_channel = vol / 8.0
                p300_multi.transfer(per_channel, src, dest_well, new_tip='never')
        if low_vol > 0:
            allocations = consume_from_pool(low_pool, low_vol)
            for (src, vol) in allocations:
                per_channel = vol / 8.0
                p300_multi.transfer(per_channel, src, dest_well, new_tip='never')
        p300_multi.mix(5, min(p300_multi.max_volume, 300), dest_well)

    p300_multi.drop_tip()

    # Step 4: Create ligand dilutions in mixing plate (nest 96 deep well) using single-channel pipette
    protocol.comment('STEP 4: Preparing ligand dilutions in mixing plate (slot 11)')

    # For each salt concentration create one column; rows A-H are ligand concentrations ascending
    cols_needed = len(salt_concs)
    rows = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    if len(ligand_concs) > 8:
        protocol.comment('Warning: more than 8 ligand concentrations provided; only first 8 (rows A-H) will be used for mixing plate')
    ligand_list = ligand_concs[:8]

    # total volume per well as specified: [[TOTAL_VOLUME]]/2*[[REPLICATES]]*1.5
    volume_per_well = (total_volume / 2.0) * replicates * 1.5
    volume_per_well = float(volume_per_well)
    protocol.comment(f'Volume per mixing well: {volume_per_well} uL')

    # Single-channel tip pick up is done per transfer to avoid tip reuse across different wells

    # Determine ligand stock well (res_slot9 well 0) default
    ligand_stock_well_high = reservoir1[0]
    ligand_stock_well_low = reservoir1[1]

    # For each column (salt), for each row (ligand concentration), prepare 2x ligand concentration dilution
    for col_idx in range(cols_needed):
        for row_idx, ligand_c in enumerate(ligand_list):
            dest = mixing_plate.wells()[row_idx + col_idx * 8]  # row-wise ascending (A-H) per column
            target_ligand_conc = ligand_c * 2.0
            # compute volume from stock: Vstock = target_conc / stock_conc * total_volume
            vol_from_high_stock = (target_ligand_conc / ligand_stock) * volume_per_well if ligand_stock != 0 else 0
            chosen_stock = ligand_stock_well_high
            stock_conc_used = ligand_stock
            # if below 20 uL use low-conc stock (stock/10) and recalc
            if vol_from_high_stock < 20:
                stock_conc_used = ligand_stock / 10.0
                chosen_stock = ligand_stock_well_low
                vol_from_stock = (target_ligand_conc / stock_conc_used) * volume_per_well
            else:
                vol_from_stock = vol_from_high_stock

            if vol_from_stock > 0:
                # ensure source has enough volume
                allocations = []
                # try consuming from chosen_stock well first
                if pool_volumes.get(chosen_stock, 0) >= vol_from_stock:
                    allocations = [(chosen_stock, vol_from_stock)]
                    pool_volumes[chosen_stock] = pool_volumes.get(chosen_stock, 0) - vol_from_stock
                else:
                    # take what is available and then try other wells from reservoir1
                    remaining_needed = vol_from_stock
                    # try chosen_stock
                    avail = pool_volumes.get(chosen_stock, 0)
                    if avail > 0:
                        allocations.append((chosen_stock, avail))
                        remaining_needed -= avail
                        pool_volumes[chosen_stock] = 0
                    # then take from other reservoir1 wells (2..6 are low-salt but may contain ligand none-the-less in simulation; fallback to reservoir0 if needed)
                    for w in reservoir1[2:7] + list(reservoir0):
                        if remaining_needed <= 0:
                            break
                        avail = pool_volumes.get(w, 0)
                        take = min(avail, remaining_needed)
                        if take > 0:
                            allocations.append((w, take))
                            pool_volumes[w] = avail - take
                            remaining_needed -= take
                    if remaining_needed > 0:
                        raise RuntimeError('Not enough ligand stock available to prepare mixing well')

                # perform transfers from allocations using single-channel pipette
                for (src, vol) in allocations:
                    p300_single.pick_up_tip()
                    p300_single.transfer(vol, src, dest, new_tip='never')
                    p300_single.drop_tip()

            # Fill the rest with low salt buffer (use reservoir0 wells)
            vol_low_fill = volume_per_well - vol_from_stock
            if vol_low_fill > 0:
                # consume from reservoir0 pool
                allocations = []
                remaining_needed = vol_low_fill
                for w in reservoir0:
                    avail = pool_volumes.get(w, 0)
                    take = min(avail, remaining_needed)
                    if take > 0:
                        allocations.append((w, take))
                        pool_volumes[w] = avail - take
                        remaining_needed -= take
                    if remaining_needed <= 0:
                        break
                if remaining_needed > 0:
                    raise RuntimeError('Not enough low-salt buffer to fill mixing well')
                for (src, vol) in allocations:
                    p300_single.pick_up_tip()
                    p300_single.transfer(vol, src, dest, new_tip='never')
                    p300_single.drop_tip()

            # Mix plate well
            mix_vol = min(p300_single.max_volume, volume_per_well / 2)
            p300_single.pick_up_tip()
            p300_single.mix(5, mix_vol, dest)
            p300_single.drop_tip()

    protocol.comment('Protocol complete. Pools remaining volumes (uL) printed below:')
    for w, v in pool_volumes.items():
        protocol.comment(f'Well {w} remaining: {v} uL')

    protocol.comment('All steps finished.')
