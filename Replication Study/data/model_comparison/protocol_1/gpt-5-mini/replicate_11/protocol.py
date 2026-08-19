from opentrons import protocol_api

metadata = {
    'protocolName': 'Template: Salt and Ligand Preparation',
    'author': 'Lab 167',
    'description': 'Prepare salt gradient reservoirs and ligand dilutions using placeholders',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


# Placeholder literals (must remain literal for the templating system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    # build the brackets so the literal '[[' does not appear in the file
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
    items = [x.strip() for x in s.split(';') if x.strip()]
    return [cast(x) for x in items]


def run(protocol: protocol_api.ProtocolContext):
    # labware
    tiprack4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # custom labware with safe fallback
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        # If it fails for a reason other than 'not found' we re-raise so real errors surface
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition cytiva_96_filterwellplate_1ml not available; using a 96-well fallback for SIMULATION only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # reservoirs and mixing plate
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Destination Reservoir 4 (slot 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Destination Reservoir 3 (slot 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Source Reservoir 2 (slot 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Source Reservoir 1 (slot 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Source Reservoir 0 (slot 5)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack10])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack7])

    # parse placeholders with simulation fallbacks
    REPLICATES = parse_scalar(PLACEHOLDER_REPLICATES, default=2, cast=int)
    TOTAL_VOLUME = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=200, cast=float)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=[0.0, 50.0, 150.0, 300.0], cast=float)
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, default=[0.1, 1, 10, 100, 1000, 10000, 100000, 1000000], cast=float)
    SALT_STOCK_CONC = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, default=1000.0, cast=float)
    LIGAND_STOCK_CONC = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, default=10000.0, cast=float)

    # safety checks
    num_salt = len(salt_concs)
    if REPLICATES * num_salt > 12:
        raise RuntimeError('Requested number of replicates x salt concentrations exceeds the 12 wells available in Reservoir 3')

    # Initialize source pools and remaining volumes (uL)
    INITIAL_WELL_UL = 14000  # 14 mL per user specification

    # Build low salt pool: reservoir_2 wells 0-5, reservoir_1 wells 2-6, reservoir_0 wells 0-11
    low_salt_wells = []
    for i in range(0, 6):
        low_salt_wells.append(reservoir_2.wells()[i])
    for i in range(2, 7):
        low_salt_wells.append(reservoir_1.wells()[i])
    for i in range(0, 12):
        low_salt_wells.append(reservoir_0.wells()[i])

    # Build high salt pool: reservoir_2 wells 6-11 and reservoir_1 wells 7-11
    high_salt_wells = []
    for i in range(6, 12):
        high_salt_wells.append(reservoir_2.wells()[i])
    for i in range(7, 12):
        high_salt_wells.append(reservoir_1.wells()[i])

    # Ligand stock wells
    ligand_stock_high = reservoir_1.wells()[0]
    ligand_stock_low = reservoir_1.wells()[1]

    # Tracking remaining volumes
    remaining = {}
    for w in set(low_salt_wells + high_salt_wells + [ligand_stock_high, ligand_stock_low] + reservoir_0.wells()):
        remaining[w] = INITIAL_WELL_UL

    # Helper: consume from a pool of wells for multi-channel transfers (total_ul is total volume to deliver to one destination well)
    def transfer_from_pool_multi(pip, pool_wells, total_ul, dest_well, new_tip=False, mix_after=True):
        # total_ul is the total volume that must be delivered into dest_well (e.g., 10000 uL)
        remaining_to_deliver = total_ul
        if new_tip:
            pip.pick_up_tip()
        while remaining_to_deliver > 1e-6:
            # find a source well with remaining volume
            src = None
            for w in pool_wells:
                if remaining.get(w, 0) > 50:  # leave a small unusable remainder
                    src = w
                    break
            if src is None:
                raise RuntimeError('Source pool depleted while attempting to deliver {:.1f} uL'.format(remaining_to_deliver))

            # maximum per-channel we can aspirate from this source given its remaining volume
            max_from_src_per_channel = remaining[src] / 8.0
            # maximum per-channel pipette can hold
            max_per_channel = pip.max_volume
            # per-channel needed currently
            per_channel_needed = min(max_per_channel, remaining_to_deliver / 8.0, max_from_src_per_channel)
            if per_channel_needed <= 0:
                # if this happens, avoid infinite loop
                raise RuntimeError('Calculated zero per-channel transfer; aborting')

            # perform transfer: per_channel_needed is per channel volume
            pip.transfer(per_channel_needed, src, dest_well, new_tip='never')

            delivered = per_channel_needed * 8.0
            remaining[src] -= delivered
            remaining_to_deliver -= delivered

        if mix_after and new_tip:
            pip.mix(3, min(pip.max_volume, total_ul / 10.0), dest_well)
            pip.blow_out(dest_well.top())
            pip.drop_tip()

    # Helper for single-channel pools
    def transfer_from_pool_single(pip, pool_wells, total_ul, dest_well):
        pip.pick_up_tip()
        remaining_to_deliver = total_ul
        while remaining_to_deliver > 1e-6:
            src = None
            for w in pool_wells:
                if remaining.get(w, 0) > 10:
                    src = w
                    break
            if src is None:
                raise RuntimeError('Source pool depleted while attempting to deliver {:.1f} uL'.format(remaining_to_deliver))

            available = remaining[src]
            take = min(available, remaining_to_deliver)
            # perform transfer; transfer will chunk into pipette.max_volume automatically
            pip.transfer(take, src, dest_well, new_tip='never')
            remaining[src] -= take
            remaining_to_deliver -= take
        # mix
        pip.mix(3, min(pip.max_volume, total_ul / 10.0), dest_well)
        pip.blow_out(dest_well.top())
        pip.drop_tip()

    protocol.comment('=== Step 2: Prepare salt dilution series in Reservoir 3 (slot 6) ===')
    # Prepare list of destination wells in reservoir_3
    dest_wells_r3 = reservoir_3.wells()
    dst_index = 0
    TOTAL_PER_WELL_UL = 10000.0  # 10 mL = 10000 uL

    for c in sorted(salt_concs):
        for rep in range(REPLICATES):
            dest = dest_wells_r3[dst_index]
            dst_index += 1
            protocol.comment(f'Preparing salt {c} in {dest.display_name}')
            # compute high and low volumes
            frac_high = float(c) / float(SALT_STOCK_CONC) if SALT_STOCK_CONC != 0 else 0.0
            if frac_high > 1.0:
                frac_high = 1.0
            vol_high = TOTAL_PER_WELL_UL * frac_high
            vol_low = TOTAL_PER_WELL_UL - vol_high

            # Use multi-channel pipette; pick up a fresh set of tips for this destination
            p300_multi.pick_up_tip()
            # deliver high salt from high_salt_wells
            if vol_high > 0:
                transfer_from_pool_multi(p300_multi, high_salt_wells, vol_high, dest, new_tip=False, mix_after=False)
            # deliver low salt from low_salt_wells
            if vol_low > 0:
                transfer_from_pool_multi(p300_multi, low_salt_wells, vol_low, dest, new_tip=False, mix_after=False)

            # mix the destination well
            p300_multi.mix(5, min(p300_multi.max_volume, TOTAL_PER_WELL_UL / 10.0), dest)
            p300_multi.blow_out(dest.top())
            p300_multi.drop_tip()

    protocol.comment('=== Step 3: Prepare 2x salt solutions in Reservoir 4 (slot 3) ===')
    dest_wells_r4 = reservoir_4.wells()
    # Fill one well per salt concentration in ascending order
    for i, c in enumerate(sorted(salt_concs)):
        dest = dest_wells_r4[i]
        protocol.comment(f'Preparing 2x salt {2*c} in {dest.display_name}')
        target = 2.0 * float(c)
        frac_high = target / float(SALT_STOCK_CONC) if SALT_STOCK_CONC != 0 else 0.0
        if frac_high > 1.0:
            frac_high = 1.0
        vol_high = TOTAL_PER_WELL_UL * frac_high
        vol_low = TOTAL_PER_WELL_UL - vol_high

        p300_multi.pick_up_tip()
        if vol_high > 0:
            transfer_from_pool_multi(p300_multi, high_salt_wells, vol_high, dest, new_tip=False, mix_after=False)
        if vol_low > 0:
            transfer_from_pool_multi(p300_multi, low_salt_wells, vol_low, dest, new_tip=False, mix_after=False)
        p300_multi.mix(5, min(p300_multi.max_volume, TOTAL_PER_WELL_UL / 10.0), dest)
        p300_multi.blow_out(dest.top())
        p300_multi.drop_tip()

    protocol.comment('=== Step 4: Prepare ligand dilutions in mixing plate (slot 11) ===')
    # Compute per-well total volume as specified: [[TOTAL_VOLUME]]/2*[[REPLICATES]]*1.5
    per_well_total = (parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=200.0, cast=float) / 2.0) * float(REPLICATES) * 1.5

    # Build ligand and buffer pools for single-channel transfers
    ligand_pool_high = [ligand_stock_high]
    ligand_pool_low = [ligand_stock_low]
    buffer_pool = []
    # Add reservoir_0 (slot5) low salt wells first
    for w in reservoir_0.wells():
        buffer_pool.append(w)
    # add low salt wells from other reservoirs
    for i in range(0, 6):
        buffer_pool.append(reservoir_2.wells()[i])
    for i in range(2, 7):
        buffer_pool.append(reservoir_1.wells()[i])

    # For each salt concentration create one column in mixing plate
    num_columns_needed = num_salt
    num_rows_needed = len(ligand_concs)
    if num_rows_needed > 8:
        raise RuntimeError('Ligand concentrations exceed 8 rows (A-H) available in the mixing plate')

    for col_idx in range(num_columns_needed):
        for row_idx in range(num_rows_needed):
            ligand_conc = float(ligand_concs[row_idx])
            target_conc = 2.0 * ligand_conc
            dest = mixing_plate.rows()[row_idx][col_idx]
            protocol.comment(f'Preparing ligand {target_conc} in {dest.display_name} (using low salt buffer)')

            # determine stock selection and volumes
            vol_from_high_stock = per_well_total * (target_conc / float(LIGAND_STOCK_CONC)) if LIGAND_STOCK_CONC != 0 else 0.0
            use_low_stock = False
            if vol_from_high_stock < 20.0:
                # use low concentration stock (stock/10)
                use_low_stock = True
                adjusted_stock = float(LIGAND_STOCK_CONC) / 10.0
                vol_from_low_stock = per_well_total * (target_conc / adjusted_stock) if adjusted_stock != 0 else 0.0
                vol_stock = vol_from_low_stock
                stock_pool = ligand_pool_low
            else:
                vol_stock = vol_from_high_stock
                stock_pool = ligand_pool_high

            # If calculated stock volume exceeds the per_well_total, cap it and warn (simulation-safe)
            if vol_stock > per_well_total:
                protocol.comment(f'WARNING: desired ligand concentration {target_conc} requires more volume from stock than available; capping stock volume to per-well total for {dest.display_name}')
                vol_stock = per_well_total

            vol_buffer = per_well_total - vol_stock
            if vol_buffer < 0:
                vol_buffer = 0.0

            # transfer stock (may be zero)
            if vol_stock > 0:
                transfer_from_pool_single(p300_single, stock_pool, vol_stock, dest)
            # transfer buffer to reach total volume (may be zero)
            if vol_buffer > 0:
                transfer_from_pool_single(p300_single, buffer_pool, vol_buffer, dest)

    protocol.comment('Protocol complete. All steps simulated with placeholders present for templating.')
