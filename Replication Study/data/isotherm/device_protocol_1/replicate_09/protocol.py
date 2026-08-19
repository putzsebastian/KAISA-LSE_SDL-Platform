from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Preparation',
    'author': 'User',
    'description': 'Templated protocol for preparing salt buffers and ligand dilutions using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (templating tokens)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if s still contains an unreplaced [[TOKEN]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder to float (or other cast).

    During simulation, when the token is still unreplaced, returns `default`.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated placeholder into a list of numbers.

    Example: '0;50;100' -> [0.0, 50.0, 100.0]
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --- 1. Parse placeholders (with simulation fallbacks) ---
    # Use upper-bound defaults so the simulation stresses tips and volumes.
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=4)  # up to 4 replicates
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=200.0)  # uL per reaction (example upper bound)
    num_salt = parse_int(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, default=3)
    num_lig = parse_int(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, default=8)

    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=[0.0, 50.0, 100.0])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, default=[1.0] * 8)

    # Ensure consistency between specified counts and list lengths (simulation only)
    if len(salt_concs) < num_salt:
        num_salt = len(salt_concs)
    if len(ligand_concs) < num_lig:
        num_lig = len(ligand_concs)

    # Basic constraint: replicates x number_of_salt_concentrations must not exceed 12 wells in reservoir3
    if replicates * num_salt > 12:
        raise ValueError('replicates x number_of_salt_concentrations must not exceed 12 (wells of Reservoir 3).')

    # Volumes in uL
    reservoir_target_volume_ul = 10000.0  # 10 mL per target reservoir well

    # --- 2. Load labware ---
    # Slot 1: custom Cytiva 96 filter plate
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tips
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (NEST 12-well 15 mL)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (low salt buffer)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (ligand + buffers)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (low & high salt stocks)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (mixed salt buffers, replicates)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (2x salt buffers)

    # Mixing plate (NEST 96 deep-well 2 mL) in slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # --- 3. Load pipettes ---
    # Right mount: P300 single-channel GEN2, tips from slot 10 + backup from slot 4
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_slot10, tiprack_slot4]
    )

    # Left mount: P300 multi-channel GEN2, tips from slot 7 + backup from slot 4
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_slot7, tiprack_slot4]
    )

    # --- 4. Reservoir volume tracking helpers ---
    # Each reservoir well initially contains 14 mL = 14000 uL
    INITIAL_RESERVOIR_VOL = 14000.0

    def init_reservoir_pool(labware, wells):
        """Create a pool dict {well_name: remaining_volume_ul} for given well indices."""
        pool = {}
        for i in wells:
            w = labware.wells()[i]
            pool[w.well_name] = INITIAL_RESERVOIR_VOL
        return pool

    # Low salt buffer pool: includes
    # - all wells 0-11 of reservoir0 (slot 5)
    # - wells 0-5 of reservoir2 (slot 8)
    # - wells 2-6 of reservoir1 (slot 9)
    low_salt_wells_res0 = list(range(12))
    low_salt_wells_res2 = list(range(0, 6))
    low_salt_wells_res1 = [2, 3, 4, 5, 6]

    low_salt_pool = {}
    low_salt_pool.update(init_reservoir_pool(reservoir0, low_salt_wells_res0))
    low_salt_pool.update(init_reservoir_pool(reservoir2, low_salt_wells_res2))
    low_salt_pool.update(init_reservoir_pool(reservoir1, low_salt_wells_res1))

    # High salt buffer pool: includes
    # - wells 6-11 of reservoir2 (slot 8)
    # - wells 7-11 of reservoir1 (slot 9)
    high_salt_wells_res2 = list(range(6, 12))
    high_salt_wells_res1 = list(range(7, 12))

    high_salt_pool = {}
    high_salt_pool.update(init_reservoir_pool(reservoir2, high_salt_wells_res2))
    high_salt_pool.update(init_reservoir_pool(reservoir1, high_salt_wells_res1))

    # Ligand stocks in reservoir1
    ligand_high_stock_well = reservoir1.wells()[0]  # high concentration [[LIGAND_STOCK_CONCENTRATION]]
    ligand_low_stock_well = reservoir1.wells()[1]   # 10x lower concentration
    ligand_high_stock_remaining = INITIAL_RESERVOIR_VOL
    ligand_low_stock_remaining = INITIAL_RESERVOIR_VOL

    def take_from_pool(pool: dict, volume_ul: float):
        """Yield (well_name, draw_volume) pairs that together supply volume_ul.

        Splits the request across wells as needed. Raises RuntimeError only if the
        ENTIRE pool cannot satisfy the request.
        """
        remaining = volume_ul
        while remaining > 0:
            total_remaining = sum(pool.values())
            if total_remaining < remaining - 1e-6:
                raise RuntimeError('Not enough volume remaining in reservoir pool to satisfy request.')
            # Choose the well with the largest remaining volume
            well_name = max(pool.keys(), key=lambda n: pool[n])
            available = pool[well_name]
            draw = min(available, remaining)
            pool[well_name] -= draw
            remaining -= draw
            yield well_name, draw

    def get_well_and_labware_by_name(well_name: str):
        """Return (well, labware) for a given well_name among reservoir0/1/2."""
        for lw in [reservoir0, reservoir1, reservoir2]:
            if well_name in lw.wells_by_name():
                return lw.wells_by_name()[well_name], lw
        raise RuntimeError(f'Well {well_name} not found in known reservoirs.')

    # --- 5. Step 2: Prepare mixed salt buffers in Reservoir 3 (replicates) ---
    # [[SALT_CONCENTRATIONS]]: list of target salt concentrations (e.g., mM).
    # For each concentration, create `replicates` wells in reservoir3, each with 10 mL
    # total, by mixing low and high salt stock from reservoir0/1/2 as needed.

    res3_wells = reservoir3.wells()  # 12 wells
    current_res3_index = 0

    for salt_idx in range(num_salt):
        target_salt = float(salt_concs[salt_idx])
        stock_salt = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, default=1000.0)
        if stock_salt <= 0:
            raise ValueError('Salt stock concentration must be positive.')

        # Fraction of high-salt stock and low-salt buffer
        fraction_high = max(0.0, min(1.0, target_salt / stock_salt))
        fraction_low = 1.0 - fraction_high

        vol_high_total = reservoir_target_volume_ul * fraction_high      # uL per WELL (total)
        vol_low_total = reservoir_target_volume_ul * fraction_low        # uL per WELL (total)

        # Per-channel volumes for multi-channel (one command acts on 8 channels)
        vol_high_per_channel = vol_high_total / 8.0
        vol_low_per_channel = vol_low_total / 8.0

        for _rep in range(replicates):
            if current_res3_index >= 12:
                raise RuntimeError('Not enough wells in Reservoir 3 for all salt concentration replicates.')
            dest = res3_wells[current_res3_index]
            current_res3_index += 1

            # 5a. Add low salt buffer
            remaining_low = vol_low_per_channel
            while remaining_low > 0:
                chunk = min(p300_multi.max_volume, remaining_low)  # uL per channel
                # Need chunk * 8 uL total from the pool
                for src_name, draw in take_from_pool(low_salt_pool, chunk * 8.0):
                    src_well, _src_labware = get_well_and_labware_by_name(src_name)
                    if not p300_multi.has_tip:
                        p300_multi.pick_up_tip()
                    p300_multi.aspirate(chunk, src_well)
                    p300_multi.dispense(chunk, dest)
                remaining_low -= chunk

            # 5b. Add high salt buffer
            remaining_high = vol_high_per_channel
            while remaining_high > 0:
                chunk = min(p300_multi.max_volume, remaining_high)
                for src_name, draw in take_from_pool(high_salt_pool, chunk * 8.0):
                    src_well, _src_labware = get_well_and_labware_by_name(src_name)
                    if not p300_multi.has_tip:
                        p300_multi.pick_up_tip()
                    p300_multi.aspirate(chunk, src_well)
                    p300_multi.dispense(chunk, dest)
                remaining_high -= chunk

            # 5c. Mix the final buffer in the target well
            if not p300_multi.has_tip:
                p300_multi.pick_up_tip()
            mix_vol = min(p300_multi.max_volume, reservoir_target_volume_ul / 16.0)  # modest mix volume
            p300_multi.mix(3, mix_vol, dest)
            p300_multi.drop_tip()

    # --- 6. Step 3: Prepare 2x salt buffers in Reservoir 4 ---
    # For each required salt concentration, prepare one well in reservoir4 with
    # 10 mL of buffer at 2x the target concentration (ascending well order).

    res4_wells = reservoir4.wells()
    current_res4_index = 0

    for salt_idx in range(num_salt):
        if current_res4_index >= 12:
            raise RuntimeError('Not enough wells in Reservoir 4 for 2x salt buffers.')
        dest = res4_wells[current_res4_index]
        current_res4_index += 1

        target_salt_2x = float(salt_concs[salt_idx]) * 2.0
        stock_salt = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, default=1000.0)
        if stock_salt <= 0:
            raise ValueError('Salt stock concentration must be positive.')

        fraction_high = max(0.0, min(1.0, target_salt_2x / stock_salt))
        fraction_low = 1.0 - fraction_high

        vol_high_total = reservoir_target_volume_ul * fraction_high
        vol_low_total = reservoir_target_volume_ul * fraction_low

        vol_high_per_channel = vol_high_total / 8.0
        vol_low_per_channel = vol_low_total / 8.0

        # 6a. Add low salt buffer
        remaining_low = vol_low_per_channel
        while remaining_low > 0:
            chunk = min(p300_multi.max_volume, remaining_low)
            for src_name, draw in take_from_pool(low_salt_pool, chunk * 8.0):
                src_well, _src_labware = get_well_and_labware_by_name(src_name)
                if not p300_multi.has_tip:
                    p300_multi.pick_up_tip()
                p300_multi.aspirate(chunk, src_well)
                p300_multi.dispense(chunk, dest)
            remaining_low -= chunk

        # 6b. Add high salt buffer
        remaining_high = vol_high_per_channel
        while remaining_high > 0:
            chunk = min(p300_multi.max_volume, remaining_high)
            for src_name, draw in take_from_pool(high_salt_pool, chunk * 8.0):
                src_well, _src_labware = get_well_and_labware_by_name(src_name)
                if not p300_multi.has_tip:
                    p300_multi.pick_up_tip()
                p300_multi.aspirate(chunk, src_well)
                p300_multi.dispense(chunk, dest)
            remaining_high -= chunk

        # 6c. Mix the 2x buffer
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()
        mix_vol = min(p300_multi.max_volume, reservoir_target_volume_ul / 16.0)
        p300_multi.mix(3, mix_vol, dest)
        p300_multi.drop_tip()

    # --- 7. Step 4: Prepare ligand dilutions (2x) in the mixing plate ---
    # For each salt concentration (column), create a column of wells spanning the
    # ligand concentration series (rows A–H). Concentrations ascend from row A to H.
    # Total volume per well = [[TOTAL_VOLUME]]/2 * [[REPLICATES]] * 1.5

    total_well_volume = (total_volume / 2.0) * replicates * 1.5

    if num_salt > 12:
        raise RuntimeError('Number of salt concentrations cannot exceed 12 (columns of mixing plate).')
    if num_lig > 8:
        raise RuntimeError('Number of ligand concentrations cannot exceed 8 (rows A–H of mixing plate).')

    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, default=1000.0)
    if ligand_stock_conc <= 0:
        raise ValueError('Ligand stock concentration must be positive.')

    # All dilutions prepared with low salt buffer from reservoir1 well 2 (as primary buffer source).
    buffer_source_for_ligand = reservoir1.wells()[2]

    for salt_idx in range(num_salt):
        col = mixing_plate.columns()[salt_idx]  # column for this salt concentration
        for lig_idx in range(num_lig):
            dest = col[lig_idx]  # one row in this column (A–H)
            target_lig_conc_2x = float(ligand_concs[lig_idx]) * 2.0

            # Assume ligand_concs are relative to stock concentration.
            fraction_from_high = target_lig_conc_2x / ligand_stock_conc
            use_low_stock = False

            # If using high stock would require <20 uL from that stock, use 10x lower stock instead.
            if fraction_from_high * total_well_volume < 20.0:
                use_low_stock = True
                effective_stock = ligand_stock_conc / 10.0
                fraction_from_high = target_lig_conc_2x / effective_stock

            fraction_from_high = max(0.0, min(1.0, fraction_from_high))
            lig_volume = total_well_volume * fraction_from_high
            buffer_volume = total_well_volume - lig_volume

            # 7a. Add ligand stock (high or low), chunked to pipette capacity
            remaining_lig = lig_volume
            while remaining_lig > 0:
                chunk = min(p300_single.max_volume, remaining_lig)
                if use_low_stock:
                    if ligand_low_stock_remaining < chunk:
                        raise RuntimeError('Not enough low-concentration ligand stock remaining.')
                    ligand_low_stock_remaining -= chunk
                    src = ligand_low_stock_well
                else:
                    if ligand_high_stock_remaining < chunk:
                        raise RuntimeError('Not enough high-concentration ligand stock remaining.')
                    ligand_high_stock_remaining -= chunk
                    src = ligand_high_stock_well

                if not p300_single.has_tip:
                    p300_single.pick_up_tip()
                p300_single.aspirate(chunk, src)
                p300_single.dispense(chunk, dest)
                remaining_lig -= chunk

            # 7b. Add low salt buffer to reach total volume (from Reservoir 1 well 2)
            remaining_buffer = buffer_volume
            while remaining_buffer > 0:
                chunk = min(p300_single.max_volume, remaining_buffer)
                if not p300_single.has_tip:
                    p300_single.pick_up_tip()
                p300_single.aspirate(chunk, buffer_source_for_ligand)
                p300_single.dispense(chunk, dest)
                remaining_buffer -= chunk

            # 7c. Mix final ligand dilution in the well
            if not p300_single.has_tip:
                p300_single.pick_up_tip()
            mix_vol = min(p300_single.max_volume, total_well_volume / 4.0)
            p300_single.mix(3, mix_vol, dest)
            p300_single.drop_tip()

    protocol.comment('Templated protocol completed. Replace [[...]] placeholders with real values before running on the robot.')
