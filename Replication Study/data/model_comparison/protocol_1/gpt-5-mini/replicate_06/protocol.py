from opentrons import protocol_api

metadata = {
    'protocolName': 'Template: Salt and Ligand Prep',
    'author': 'User',
    'description': 'Prepare gradient salt buffers and ligand dilutions using placeholders for templating.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Placeholders (declared literally so an external templating engine can replace them)
    REPLICATES = '[[REPLICATES]]'
    TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
    SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
    LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
    SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
    NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

    # Helper to detect unreplaced tokens in simulation (build brackets so the literal never appears)
    def _unreplaced(s: str) -> bool:
        return str(s).startswith('[' * 2) and str(s).endswith(']' * 2)

    def parse_scalar(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return cast(default)
        return cast(s)

    def parse_list(value, default_list, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return list(default_list)
        # split on semicolons, ignore empty
        parts = [p.strip() for p in s.split(';') if p.strip()]
        return [cast(p) for p in parts]

    # Simulation fallbacks (worst-case sensible defaults so the simulator is exercised)
    FALLBACK_REPLICATES = 3
    FALLBACK_SALT_CONCS = [0.0, 50.0, 100.0, 150.0]
    FALLBACK_LIGAND_CONCS = [0.1 * i for i in range(1, 9)]  # 0.1, 0.2, ..., 0.8
    FALLBACK_SALT_STOCK = 1000.0
    FALLBACK_LIGAND_STOCK = 1000.0
    FALLBACK_NUM_SALT = len(FALLBACK_SALT_CONCS)
    FALLBACK_NUM_LIGAND = len(FALLBACK_LIGAND_CONCS)
    FALLBACK_TOTAL_VOLUME = 200.0

    # Parse placeholders (or fall back)
    replicates = parse_scalar(REPLICATES, FALLBACK_REPLICATES, int)
    salt_concs = parse_list(SALT_CONCENTRATIONS, FALLBACK_SALT_CONCS, float)
    ligand_concs = parse_list(LIGAND_CONCENTRATIONS, FALLBACK_LIGAND_CONCS, float)
    salt_stock = parse_scalar(SALT_STOCK_CONCENTRATION, FALLBACK_SALT_STOCK, float)
    ligand_stock = parse_scalar(LIGAND_STOCK_CONCENTRATION, FALLBACK_LIGAND_STOCK, float)
    num_salt = parse_scalar(NUMBER_OF_SALT_CONCENTRATIONS, FALLBACK_NUM_SALT, int)
    num_ligand = parse_scalar(NUMBER_OF_LIGAND_CONCENTRATIONS, FALLBACK_NUM_LIGAND, int)
    total_volume = parse_scalar(TOTAL_VOLUME, FALLBACK_TOTAL_VOLUME, float)

    # Sanity checks in simulation only (do not raise for unreplaced values)
    if not _unreplaced(NUMBER_OF_SALT_CONCENTRATIONS) and len(salt_concs) != num_salt:
        # if explicit list provided, prefer its length
        num_salt = len(salt_concs)
    if not _unreplaced(NUMBER_OF_LIGAND_CONCENTRATIONS) and len(ligand_concs) != num_ligand:
        num_ligand = len(ligand_concs)

    # Ensure replicates x num_salt does not exceed 12 (Reservoir 3 capacity)
    if replicates * num_salt > 12:
        protocol.comment('WARNING: replicates * number_of_salt_concentrations > 12; '
                         'adjusting replicates to fit in 12 wells for simulation')
        replicates = max(1, 12 // max(1, num_salt))

    # Deck setup
    # Custom labware in slot 1 with fallback
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a 96-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs and mixing plate
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # target for step 3 (Reservoir 4)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # target for step 2 (Reservoir 3)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # contains low/high buffers (Reservoir 2)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # contains ligand stock and buffers (Reservoir 1)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # low salt buffers for dilutions (Reservoir 0)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack10])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack7, tiprack4])

    # Track remaining volumes in source wells (uL)
    # Initialize all wells that initially contain 14 mL (as specified) - reservoirs 1 and 2
    initial_source_wells = []
    for w in reservoir2.wells():
        initial_source_wells.append(w)
    for w in reservoir1.wells():
        initial_source_wells.append(w)

    source_remaining = {w: 14000.0 for w in initial_source_wells}  # 14 mL each

    # Utility to get next available source well from a pool (wells list) with at least min_vol uL remaining
    def find_source_with_volume(pool, min_vol):
        for w in pool:
            if source_remaining.get(w, 0) >= min_vol:
                return w
        return None

    # Utility to consume volume from a source well (uL), raising if pool dry
    def consume_from_pool(pool, amount):
        # amount is total uL that will be removed from a single reservoir well (not per-channel)
        # This function will choose wells in pool and decrement their remaining volumes;
        # returns list of (well, vol_taken) pairs summing to amount. If insufficient total in pool, raise.
        remaining = amount
        actions = []
        total_pool = sum(source_remaining.get(w, 0) for w in pool)
        if total_pool + 1e-6 < remaining:
            raise RuntimeError(f'Not enough liquid in pool to supply {amount} uL (available {total_pool} uL)')
        for w in pool:
            avail = source_remaining.get(w, 0)
            if avail <= 0:
                continue
            take = min(avail, remaining)
            source_remaining[w] = avail - take
            actions.append((w, take))
            remaining -= take
            if remaining <= 1e-6:
                break
        return actions

    # Pools (lists of well objects) for low and high salt
    # According to the user description: Reservoir 2 wells 0-5 = low salt, 6-11 = high salt
    low_salt_pool = [reservoir2.wells()[i] for i in range(0, 6)] + [reservoir1.wells()[i] for i in range(2, 7)] + [reservoir0.wells()[i] for i in range(0, 12)]
    # High salt pool uses wells that were described as high salt in reservoir2 (6-11) and reservoir1 (7-11)
    high_salt_pool = [reservoir2.wells()[i] for i in range(6, 12)] + [reservoir1.wells()[i] for i in range(7, 12)]

    # The protocol steps
    protocol.comment('STEP 1: Calculations and setup completed.\n')

    # Step 2: For each concentration in SALT_CONCENTRATIONS, fill up [[REPLICATES]] wells in Reservoir 3
    protocol.comment('STEP 2: Preparing salt gradient in Reservoir 3 (slot 6)')
    total_volume_each = 10000.0  # 10 mL per target well in uL

    # sort concentrations ascending
    salt_concs_sorted = sorted(salt_concs)[:num_salt]

    dest_wells_r3 = reservoir3.wells()
    dest_index = 0

    # Pick up one set of tips for multi-channel and reuse to save tips in simulation
    p300m.pick_up_tip()
    for conc in salt_concs_sorted:
        for rep in range(replicates):
            if dest_index >= 12:
                raise RuntimeError('Not enough wells in Reservoir 3 to place all replicates')
            dst = dest_wells_r3[dest_index]
            # compute required high volume and low volume
            frac_high = conc / salt_stock if salt_stock != 0 else 0
            vol_high = total_volume_each * frac_high
            vol_low = total_volume_each - vol_high

            # For per-channel volumes passed to p300m, divide by 8
            perch_high = vol_high / 8.0
            perch_low = vol_low / 8.0

            protocol.comment(f'Preparing salt {conc} (replicate {rep+1}) in {dst}. '
                             f'High: {vol_high:.1f} uL, Low: {vol_low:.1f} uL')

            # Transfer high salt portion first
            if vol_high > 0:
                total_to_remove = vol_high
                actions = consume_from_pool(high_salt_pool, total_to_remove)
                for (src_well, taken) in actions:
                    per_channel_for_call = taken / 8.0
                    p300m.transfer(per_channel_for_call, src_well, dst, new_tip='never')

            # Transfer low salt portion
            if vol_low > 0:
                total_to_remove = vol_low
                actions = consume_from_pool(low_salt_pool, total_to_remove)
                for (src_well, taken) in actions:
                    per_channel_for_call = taken / 8.0
                    p300m.transfer(per_channel_for_call, src_well, dst, new_tip='never')

            # Mix destination once using multi-channel (tip still attached)
            p300m.mix(5, min(200, total_volume_each * 0.1 / 8.0), dst)

            dest_index += 1

    # Step 3: For each salt concentration, fill up 1 well in Reservoir 4 with total 10 mL at 2x concentration
    protocol.comment('STEP 3: Preparing 2x salt solutions in Reservoir 4 (slot 3)')
    dest_wells_r4 = reservoir4.wells()
    r4_index = 0
    for conc in salt_concs_sorted:
        if r4_index >= 12:
            raise RuntimeError('Not enough wells in Reservoir 4 to place all requested 2x salt solutions')
        dst = dest_wells_r4[r4_index]
        target_conc = conc * 2.0
        frac_high = target_conc / salt_stock if salt_stock != 0 else 0
        vol_high = total_volume_each * frac_high
        vol_low = total_volume_each - vol_high

        protocol.comment(f'Preparing 2x salt {target_conc} in {dst}. High: {vol_high:.1f} uL, Low: {vol_low:.1f} uL')

        if vol_high > 0:
            actions = consume_from_pool(high_salt_pool, vol_high)
            for (src, taken) in actions:
                p300m.transfer(taken / 8.0, src, dst, new_tip='never')
        if vol_low > 0:
            actions = consume_from_pool(low_salt_pool, vol_low)
            for (src, taken) in actions:
                p300m.transfer(taken / 8.0, src, dst, new_tip='never')

        p300m.mix(5, min(200, total_volume_each * 0.1 / 8.0), dst)
        r4_index += 1

    # Drop the multi-channel tip after finishing reservoir prep
    p300m.drop_tip()

    protocol.comment('STEP 2 and 3 completed.\n')

    # Step 4: Prepare ligand dilutions (2x ligand concentrations) in mixing plate (nest 2ml deep)
    protocol.comment('STEP 4: Preparing ligand dilutions in mixing plate (slot 11)')

    # The layout: for each salt concentration (columns 1..num_salt), create one column of dilutions
    # Rows A-H correspond to ligand concentrations ascending row-wise from A (lowest) to H (highest)
    # compute number of ligand concentrations (rows) and number of salt concentrations (columns)
    rows = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    n_rows = min(8, len(ligand_concs))
    n_cols = num_salt

    # Total volume for each mixing well (user specified formula): TOTAL_VOLUME/2*REPLICATES*1.5
    mix_well_vol = (total_volume / 2.0) * replicates * 1.5

    protocol.comment(f'Each mixing well volume: {mix_well_vol:.1f} uL')

    # Determine ligand stock well to use: reservoir1.wells()[0] is high stock, [1] is low stock (stock/10)
    ligand_stock_high_well = reservoir1.wells()[0]
    ligand_stock_low_well = reservoir1.wells()[1]

    # For each column (salt concentration), fill rows A..H with 2x ligand dilutions
    for col_index in range(n_cols):
        for row_index in range(n_rows):
            conc = ligand_concs[row_index]
            target_conc = conc * 2.0
            # Compute stock volume needed: V1 = C2 * V2 / Cstock
            V2 = mix_well_vol
            V1 = (target_conc * V2) / ligand_stock if ligand_stock != 0 else 0
            stock_well = ligand_stock_high_well
            effective_stock = ligand_stock
            if V1 < 20.0:
                # use low stock concentration (1/10)
                effective_stock = ligand_stock / 10.0
                stock_well = ligand_stock_low_well
                V1 = (target_conc * V2) / effective_stock if effective_stock != 0 else 0

            if V1 < 0:
                V1 = 0
            if V1 > V2:
                # clamp
                V1 = V2

            vol_buffer = V2 - V1
            # Destination well in mixing plate: column index +1 (1-based columns), row letter
            dest_well_name = f"{rows[row_index]}{col_index+1}"
            dst = mixing_plate.wells_by_name().get(dest_well_name)
            if dst is None:
                # fallback: compute by wells list index (row-major)
                dst = mixing_plate.wells()[row_index * 12 + col_index]

            protocol.comment(f'Preparing ligand {target_conc} in {dst}: stock {V1:.1f} uL, buffer {vol_buffer:.1f} uL')

            # Use one tip per destination well for single-channel operations
            p300s.pick_up_tip()

            # Transfer stock
            if V1 > 0:
                p300s.transfer(V1, stock_well, dst, new_tip='never')
            # Transfer buffer from reservoir0 wells (low salt)
            if vol_buffer > 0:
                if reservoir0.wells()[0] not in source_remaining:
                    for w in reservoir0.wells():
                        source_remaining[w] = 14000.0
                actions = consume_from_pool(reservoir0.wells(), vol_buffer)
                for (src, taken) in actions:
                    p300s.transfer(taken, src, dst, new_tip='never')

            # Mix the well
            p300s.mix(3, min(200, V2 * 0.5), dst)
            p300s.drop_tip()

    protocol.comment('STEP 4 completed.\n')

    protocol.comment('Protocol completed successfully.')
