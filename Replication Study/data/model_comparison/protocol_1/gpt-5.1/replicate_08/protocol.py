from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Preparation (Templated)',
    'author': 'User',
    'description': 'Prepare salt gradients in reservoirs 3 & 4 and ligand dilutions in a mixing deepwell plate using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONC = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONC = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    # go via float so '3.0' is valid for int as well
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default=default, cast=float))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    parts = [p for p in s.split(';') if p.strip()]
    return [cast(float(p)) for p in parts]


def run(protocol: protocol_api.ProtocolContext):
    # ---------------------------------------------------------------------
    # Parameter parsing with conservative simulation defaults (upper bounds)
    # ---------------------------------------------------------------------
    # Use worst-case but still valid defaults for simulation.
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=50.0, cast=float)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=[0, 150, 300], cast=float)
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, default=[0, 1, 2, 3], cast=float)
    n_salt = parse_int(PLACEHOLDER_NUM_SALT, default=len(salt_concs))
    n_lig = parse_int(PLACEHOLDER_NUM_LIG, default=len(ligand_concs))

    # ensure we only use as many entries as declared by NUMBER_OF_* placeholders
    salt_concs = salt_concs[:n_salt]
    ligand_concs = ligand_concs[:n_lig]

    # enforce capacity of reservoir 3: 12 wells total
    if replicates * n_salt > 12:
        raise RuntimeError('replicates x number_of_salt_concentrations exceeds 12 reservoir wells')

    # -----------------
    # Labware and tips
    # -----------------
    # Slot 1: custom filter plate
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tip racks
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # Reservoir 0 (low salt)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # Reservoir 1 (ligand + buffers)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # Reservoir 2 (low + high salt)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # Reservoir 3 (product salt gradients)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # Reservoir 4 (2x salt gradients)

    # Mixing deepwell plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_slot10]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_slot7]
    )

    # ----------------------------------------
    # Helper: track volumes in reservoir wells
    # ----------------------------------------
    # Each reservoir well initially 14 mL = 14000 uL where filled
    INITIAL_VOL_UL = 14000.0

    # Low salt buffer pool: all wells that hold low salt buffer (0 salt)
    low_salt_sources = [
        # Reservoir 2 (slot 8): wells 0-5 are low salt 0
        reservoir2.wells()[0],
        reservoir2.wells()[1],
        reservoir2.wells()[2],
        reservoir2.wells()[3],
        reservoir2.wells()[4],
        reservoir2.wells()[5],
        # Reservoir 1 (slot 9): wells 2-6 are low salt 0
        reservoir1.wells()[2],
        reservoir1.wells()[3],
        reservoir1.wells()[4],
        reservoir1.wells()[5],
        reservoir1.wells()[6],
        # Reservoir 0 (slot 5): wells 0-11 are low salt 0
        *reservoir0.wells()
    ]

    # High salt buffer pool: all wells that hold stock high salt
    high_salt_sources = [
        # Reservoir 2 (slot 8): wells 6-11 are high salt
        reservoir2.wells()[6],
        reservoir2.wells()[7],
        reservoir2.wells()[8],
        reservoir2.wells()[9],
        reservoir2.wells()[10],
        reservoir2.wells()[11],
        # Reservoir 1 (slot 9): wells 7-11 are high salt
        reservoir1.wells()[7],
        reservoir1.wells()[8],
        reservoir1.wells()[9],
        reservoir1.wells()[10],
        reservoir1.wells()[11],
    ]

    # Volume tracking dictionaries
    low_salt_remaining = {w: INITIAL_VOL_UL for w in low_salt_sources}
    high_salt_remaining = {w: INITIAL_VOL_UL for w in high_salt_sources}

    def _aspirate_from_pool(pipette, volume_ul: float, pool_wells, remaining_dict):
        """Aspirate volume_ul (per channel) from a pool of single-row reservoir wells.

        For multi-channel pipettes, volume_ul is per channel; each aspirate removes
        volume_ul * 8 from the source well's remaining volume.
        """
        per_channel = volume_ul
        total_from_well = per_channel * 8.0
        vol_needed = total_from_well

        for src in pool_wells:
            if remaining_dict[src] >= vol_needed:
                # take entire request from this well
                pipette.aspirate(per_channel, src)
                remaining_dict[src] -= vol_needed
                return
        # If we reach here, sum of pool wells is insufficient for a single move
        total_available = sum(remaining_dict.values())
        raise RuntimeError(
            f'Not enough volume in pool (required {vol_needed} uL total, '
            f'available {total_available} uL).')

    def _dispense_and_mix(pipette, volume_ul: float, dest, mix_reps: int = 5, mix_vol: float = 200.0):
        pipette.dispense(volume_ul, dest)
        if mix_reps > 0:
            pipette.mix(mix_reps, min(mix_vol, pipette.max_volume), dest)

    # --------------------------------------------------
    # Step 2: Prepare salt buffers in Reservoir 3 (10 mL)
    # --------------------------------------------------
    # Reservoir 3: 12 wells total, ascending salt concentration with well index
    target_wells_res3 = reservoir3.wells()[:replicates * n_salt]

    # Compute low/high volumes per 10 mL (10000 uL) for each target salt concentration
    # Using place-holder stock concentration but assuming linear mixing of 0 and stock.
    stock_salt_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONC, default=1000.0, cast=float)  # units arbitrary but consistent
    total_vol_reservoir_ul = 10000.0

    def compute_mix_volumes(target_conc):
        # target_conc and stock_salt_conc in same arbitrary units
        if stock_salt_conc <= 0:
            raise RuntimeError('Stock salt concentration must be > 0')
        frac_stock = target_conc / stock_salt_conc
        frac_stock = max(0.0, min(1.0, frac_stock))
        stock_vol = total_vol_reservoir_ul * frac_stock
        low_vol = total_vol_reservoir_ul - stock_vol
        return low_vol, stock_vol

    # Use multi-channel pipette with tips from slot 7
    p300_multi.flow_rate.aspirate = 50
    p300_multi.flow_rate.dispense = 100

    idx = 0
    for conc in salt_concs[:n_salt]:
        low_vol, high_vol = compute_mix_volumes(conc)
        for _rep in range(replicates):
            dest = target_wells_res3[idx]
            idx += 1
            # Convert to per-channel volume for multi-channel (same per channel; total is x8)
            low_per_channel = low_vol / 8.0
            high_per_channel = high_vol / 8.0

            # Chunk aspiration/dispense into pipette.max_volume if needed
            # First low salt, then high salt, then final mix
            p300_multi.pick_up_tip()

            remaining = low_per_channel
            while remaining > 0:
                chunk = min(p300_multi.max_volume, remaining)
                _aspirate_from_pool(p300_multi, chunk, low_salt_sources, low_salt_remaining)
                _dispense_and_mix(p300_multi, chunk, dest, mix_reps=0)  # no mix yet
                remaining -= chunk

            remaining = high_per_channel
            while remaining > 0:
                chunk = min(p300_multi.max_volume, remaining)
                _aspirate_from_pool(p300_multi, chunk, high_salt_sources, high_salt_remaining)
                _dispense_and_mix(p300_multi, chunk, dest, mix_reps=0)
                remaining -= chunk

            # Final mix in destination
            p300_multi.mix(5, min(300, p300_multi.max_volume), dest)
            p300_multi.drop_tip()

    # --------------------------------------------------------------
    # Step 3: Prepare 2x salt buffers in Reservoir 4 (10 mL per well)
    # --------------------------------------------------------------
    target_wells_res4 = reservoir4.wells()[:n_salt]

    for conc, dest in zip(salt_concs[:n_salt], target_wells_res4):
        two_x_conc = conc * 2.0
        low_vol, high_vol = compute_mix_volumes(two_x_conc)

        low_per_channel = low_vol / 8.0
        high_per_channel = high_vol / 8.0

        p300_multi.pick_up_tip()

        remaining = low_per_channel
        while remaining > 0:
            chunk = min(p300_multi.max_volume, remaining)
            _aspirate_from_pool(p300_multi, chunk, low_salt_sources, low_salt_remaining)
            _dispense_and_mix(p300_multi, chunk, dest, mix_reps=0)
            remaining -= chunk

        remaining = high_per_channel
        while remaining > 0:
            chunk = min(p300_multi.max_volume, remaining)
            _aspirate_from_pool(p300_multi, chunk, high_salt_sources, high_salt_remaining)
            _dispense_and_mix(p300_multi, chunk, dest, mix_reps=0)
            remaining -= chunk

        p300_multi.mix(5, min(300, p300_multi.max_volume), dest)
        p300_multi.drop_tip()

    # --------------------------------------------------------------------
    # Step 4: Prepare 2x ligand dilutions in mixing deep-well plate (slot 11)
    # --------------------------------------------------------------------
    # Total volume for each well: [[TOTAL_VOLUME]]/2 * [[REPLICATES]] * 1.5
    vol_per_well = (total_volume / 2.0) * replicates * 1.5

    # Ligand stock wells in Reservoir 1
    ligand_stock_high = reservoir1.wells()[0]  # high concentration [[LIGAND_STOCK_CONCENTRATION]]
    ligand_stock_low = reservoir1.wells()[1]   # low concentration [[LIGAND_STOCK_CONCENTRATION]]/10

    ligand_high_remaining = INITIAL_VOL_UL
    ligand_low_remaining = INITIAL_VOL_UL

    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, default=1000.0, cast=float)

    def compute_ligand_volumes(target_conc_2x):
        """Return (ligand_vol_ul, buffer_vol_ul, use_low_stock: bool).

        If ligand volume from high stock would be < 20 uL, use low stock instead
        and scale volume by 10x.
        """
        if ligand_stock_conc <= 0:
            raise RuntimeError('Ligand stock concentration must be > 0')
        frac_stock = target_conc_2x / ligand_stock_conc
        frac_stock = max(0.0, min(1.0, frac_stock))
        lig_vol_high = vol_per_well * frac_stock
        if lig_vol_high < 20.0:
            # use 10x lower stock concentration: need 10x volume
            use_low = True
            lig_vol = lig_vol_high * 10.0
        else:
            use_low = False
            lig_vol = lig_vol_high
        buffer_vol = vol_per_well - lig_vol
        return lig_vol, buffer_vol, use_low

    # For each salt concentration -> one column in mixing plate
    # Within each column, ligand concentrations ascending from row A to H
    max_rows = 8  # A-H
    if n_lig > max_rows:
        raise RuntimeError('More ligand concentrations than available rows (max 8).')

    # Single-channel pipette settings
    p300_single.flow_rate.aspirate = 25
    p300_single.flow_rate.dispense = 50

    for salt_idx in range(n_salt):
        col = mixing_plate.columns()[salt_idx]  # list of 8 wells (A-H of this column)
        for lig_idx in range(n_lig):
            target_conc = ligand_concs[lig_idx]
            target_conc_2x = target_conc * 2.0
            dest = col[lig_idx]  # row-wise A-H

            lig_vol, buffer_vol, use_low = compute_ligand_volumes(target_conc_2x)

            # Use one tip per COLUMN instead of per well to save tips
            if lig_idx == 0:
                p300_single.pick_up_tip()

            # Buffer first - may require multiple chunks
            remaining = buffer_vol
            while remaining > 0:
                chunk = min(p300_single.max_volume, remaining)
                # re-use low_salt_sources & remaining dict with single-channel logic
                # For single-channel, volume_ul is exactly volume removed from one well
                taken = False
                for src in low_salt_sources:
                    if low_salt_remaining[src] >= chunk:
                        p300_single.aspirate(chunk, src)
                        low_salt_remaining[src] -= chunk
                        p300_single.dispense(chunk, dest)
                        taken = True
                        break
                if not taken:
                    raise RuntimeError('Not enough low salt buffer remaining for ligand dilutions.')
                remaining -= chunk

            # Ligand stock
            remaining = lig_vol
            while remaining > 0:
                chunk = min(p300_single.max_volume, remaining)
                if use_low:
                    if ligand_low_remaining < chunk:
                        raise RuntimeError('Not enough low concentration ligand stock remaining.')
                    p300_single.aspirate(chunk, ligand_stock_low)
                    ligand_low_remaining -= chunk
                else:
                    if ligand_high_remaining < chunk:
                        raise RuntimeError('Not enough high concentration ligand stock remaining.')
                    p300_single.aspirate(chunk, ligand_stock_high)
                    ligand_high_remaining -= chunk
                p300_single.dispense(chunk, dest)
                remaining -= chunk

            # Final mix of the well
            mix_vol = min(0.5 * vol_per_well, p300_single.max_volume)
            p300_single.mix(3, mix_vol, dest)

            # Drop tip only after finishing the column
            if lig_idx == n_lig - 1:
                p300_single.drop_tip()

    protocol.comment('Salt gradients and ligand dilutions prepared successfully.')
