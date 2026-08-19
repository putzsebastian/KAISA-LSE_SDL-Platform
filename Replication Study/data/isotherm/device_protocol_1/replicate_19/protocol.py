from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt/Ligand Prep',
    'author': 'User',
    'description': 'Templated preparation of salt buffers and ligand dilutions with placeholders'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# --- Placeholders (literal strings, replaced by external template system) ---
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONC = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONC = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Detect unreplaced [[PLACEHOLDER]] tokens without using '[[' literal in code."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder, returning a numeric value.

    For simulation (before template substitution) we fall back to a worst-case
    default that exercises volumes and tip usage. Once placeholders are
    substituted, any invalid value will raise and should be corrected upstream.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_int(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder into a Python list."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --- Parsed parameters with simulation fallbacks ---
    # Use conservative but non-trivial defaults so simulation fully exercises logic.
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # uL per assay well

    # Example default lists (can be overridden by template substitution)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 50, 100, 150])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS,
                              [0, 1, 2, 4, 8, 16, 32, 64])

    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONC, 1000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, 1000.0)

    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_lig = parse_int(PLACEHOLDER_NUM_LIG, len(ligand_concs))

    # Safety note about reservoir-3 capacity
    if replicates * num_salt > 12:
        protocol.comment('WARNING: REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS exceeds 12; '
                         'only the first 12 wells of Reservoir 3 will be used.')

    # --- Labware ---
    # Slot 1: custom Cytiva 96 filter plate (with simulation fallback)
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tip racks
    tiprack_right_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)   # single-channel tips
    tiprack_left_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)    # multi-channel tips
    tiprack_right_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)  # extra single-channel tips

    # Reservoirs (NEST 12-well, 15 mL each well)
    # Slot 5: Reservoir 0 (low salt buffer, all wells)
    res0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    # Slot 9: Reservoir 1 (ligand stocks + low & high salt buffers)
    res1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    # Slot 8: Reservoir 2 (low salt in wells 0-5, high salt in wells 6-11)
    res2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    # Slot 6: Reservoir 3 (product: mixed salt buffers with replicates)
    res3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    # Slot 3: Reservoir 4 (product: 2x salt buffers)
    res4 = protocol.load_labware('nest_12_reservoir_15ml', 3)

    # Slot 11: NEST 96 Deep-Well Plate 2 mL (mixing plate)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # --- Pipettes ---
    # Right: P300 Single-Channel GEN2 (uses tips from slots 4 and 10)
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        'right',
        tip_racks=[tiprack_right_1, tiprack_right_2]
    )

    # Left: P300 Multi-Channel GEN2 (uses tips from slot 7)
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        'left',
        tip_racks=[tiprack_left_1]
    )

    # --- Volume tracking for reservoir wells ---
    # Each reservoir well initially contains 14 mL = 14000 uL
    INITIAL_RES_VOL = 14000.0

    def make_pool(wells):
        return {w: INITIAL_RES_VOL for w in wells}

    # Low salt buffer pool combines all wells described as low salt:
    low_salt_wells = []
    # Reservoir 0: wells 0-11 all low salt
    low_salt_wells.extend(res0.wells())
    # Reservoir 2: wells 0-5 low salt
    low_salt_wells.extend(res2.wells()[:6])
    # Reservoir 1: wells 2-6 low salt
    low_salt_wells.extend(res1.wells()[2:7])
    low_salt_pool = make_pool(low_salt_wells)

    # High salt buffer pool from Reservoir 2 (wells 6-11) and Reservoir 1 (wells 7-11)
    high_salt_wells = []
    high_salt_wells.extend(res2.wells()[6:12])
    high_salt_wells.extend(res1.wells()[7:12])
    high_salt_pool = make_pool(high_salt_wells)

    # Ligand stock wells in Reservoir 1
    # Well 0: high concentration [[LIGAND_STOCK_CONCENTRATION]]
    # Well 1: low concentration [[LIGAND_STOCK_CONCENTRATION]]/10
    ligand_high_well = res1.wells()[0]
    ligand_low_well = res1.wells()[1]
    ligand_high_pool = {ligand_high_well: INITIAL_RES_VOL}
    ligand_low_pool = {ligand_low_well: INITIAL_RES_VOL}

    # --- Helper for drawing from a pooled reagent with volume tracking ---
    def get_from_pool(pool: dict, volume_per_channel: float, pipette, dest,
                      mix_after: bool = False, mix_volume: float = 0, mix_repetitions: int = 3):
        """Dispense volume_per_channel (uL) into dest, drawing from pooled source wells.

        * For an 8-channel pipette, volume_per_channel is per channel. The function
          automatically accounts for 8x draw on a single-row reservoir well.
        * For a single-channel, volume_per_channel is simply the per-tip volume.
        * Handles splitting across multiple wells as they deplete and chunks the
          aspiration by pipette.max_volume.
        """
        total_needed = volume_per_channel
        remaining_needed = total_needed

        while remaining_needed > 0:
            available_sources = [w for w, vol in pool.items() if vol > 0]
            if not available_sources:
                raise RuntimeError('Reagent pool exhausted while attempting to transfer volume.')
            src = available_sources[0]

            # Channels: 8 for multi, 1 for single
            channels = 8 if pipette.channels == 8 else 1
            max_per_channel_by_tip = pipette.max_volume
            src_remaining = pool[src]
            max_from_src_per_channel = src_remaining / channels

            # Chunk size per channel for this aspiration
            chunk_per_channel = min(remaining_needed,
                                    max_per_channel_by_tip,
                                    max_from_src_per_channel)
            if chunk_per_channel <= 0:
                # This well is effectively empty; mark and move on
                pool[src] = 0
                continue

            vol_for_all_channels = chunk_per_channel * channels

            pipette.aspirate(chunk_per_channel, src)
            pipette.dispense(chunk_per_channel, dest)

            # Update bookkeeping
            pool[src] -= vol_for_all_channels
            remaining_needed -= chunk_per_channel

        if mix_after and mix_volume > 0:
            mv = min(mix_volume, pipette.max_volume)
            pipette.mix(mix_repetitions, mv, dest)

    # ------------------------------------------------------------------
    # Step 2: Prepare salt buffers in Reservoir 3 (mixed low/high, replicates)
    # ------------------------------------------------------------------
    protocol.comment('Step 2: Preparing mixed salt buffers with replicates in Reservoir 3.')

    # Use only up to 12 wells in Reservoir 3
    res3_count = replicates * num_salt
    if res3_count > 12:
        res3_count = 12
    res3_wells = res3.wells()[:res3_count]

    TOTAL_VOL_SALT_RESERVOIR_UL = 10000.0  # 10 mL per reservoir well

    if res3_wells:
        # Use a single tip for all low/high buffer moves (same sources) to save tips
        p300_multi.pick_up_tip()
        for idx, c in enumerate(salt_concs[:num_salt]):
            if idx * replicates >= len(res3_wells):
                break

            # Fractions from stock: C_target = f_high * C_stock
            f_high = float(c) / float(salt_stock_conc) if salt_stock_conc > 0 else 0.0
            f_high = max(0.0, min(1.0, f_high))
            f_low = 1.0 - f_high

            vol_high_per_well = TOTAL_VOL_SALT_RESERVOIR_UL * f_high
            vol_low_per_well = TOTAL_VOL_SALT_RESERVOIR_UL * f_low

            for r in range(replicates):
                dest_index = idx * replicates + r
                if dest_index >= len(res3_wells):
                    break
                dest_well = res3_wells[dest_index]

                # Per-channel volumes (do not multiply by 8)
                high_per_channel = vol_high_per_well / 8.0
                low_per_channel = vol_low_per_well / 8.0

                # First add low salt, then high salt with mixing
                if low_per_channel > 0:
                    get_from_pool(low_salt_pool, low_per_channel, p300_multi, dest_well)
                if high_per_channel > 0:
                    get_from_pool(
                        high_salt_pool,
                        high_per_channel,
                        p300_multi,
                        dest_well,
                        mix_after=True,
                        mix_volume=min(1000.0, TOTAL_VOL_SALT_RESERVOIR_UL / 5.0)
                    )
        p300_multi.drop_tip()

    # ------------------------------------------------------------------
    # Step 3: Prepare 2x salt buffers in Reservoir 4
    # ------------------------------------------------------------------
    protocol.comment('Step 3: Preparing 2x salt buffers in Reservoir 4.')

    res4_wells = res4.wells()[:num_salt]

    if res4_wells:
        p300_multi.pick_up_tip()
        for idx, c in enumerate(salt_concs[:num_salt]):
            if idx >= len(res4_wells):
                break

            target_2x = float(c) * 2.0
            f_high = float(target_2x) / float(salt_stock_conc) if salt_stock_conc > 0 else 0.0
            f_high = max(0.0, min(1.0, f_high))
            f_low = 1.0 - f_high

            vol_high = TOTAL_VOL_SALT_RESERVOIR_UL * f_high
            vol_low = TOTAL_VOL_SALT_RESERVOIR_UL * f_low

            dest_well = res4_wells[idx]
            high_per_channel = vol_high / 8.0
            low_per_channel = vol_low / 8.0

            if low_per_channel > 0:
                get_from_pool(low_salt_pool, low_per_channel, p300_multi, dest_well)
            if high_per_channel > 0:
                get_from_pool(
                    high_salt_pool,
                    high_per_channel,
                    p300_multi,
                    dest_well,
                    mix_after=True,
                    mix_volume=min(1000.0, TOTAL_VOL_SALT_RESERVOIR_UL / 5.0)
                )
        p300_multi.drop_tip()

    # ------------------------------------------------------------------
    # Step 4: Prepare ligand dilutions in the 96-well deep mixing plate
    # ------------------------------------------------------------------
    protocol.comment('Step 4: Preparing ligand dilutions in mixing plate.')

    # Total volume per well in mixing plate
    # [[TOTAL_VOLUME]]/2 * [[REPLICATES]] * 1.5
    total_well_volume = (total_volume / 2.0) * replicates * 1.5  # uL

    def prepare_ligand_well(dest_well, target_conc):
        """Create a 2x ligand solution at target_conc in dest_well.

        Uses high-concentration stock in Reservoir 1, well 0 by default. If
        required stock volume would drop below 20 uL, switches to low-concentration
        stock in well 1 (10x lower concentration) and adjusts volume accordingly.

        All dilutions are made up with low salt buffer from the combined pool.
        """
        desired_conc_2x = float(target_conc) * 2.0

        if desired_conc_2x <= 0:
            ligand_vol = 0.0
            buffer_vol = total_well_volume
            use_low = False
        else:
            # First compute volume from high-concentration stock
            vol_from_high = total_well_volume * desired_conc_2x / float(ligand_stock_conc)
            if vol_from_high < 20.0:
                # Use low-concentration stock [[LIGAND_STOCK_CONCENTRATION]]/10 instead
                effective_low_conc = float(ligand_stock_conc) / 10.0
                ligand_vol = total_well_volume * desired_conc_2x / effective_low_conc
                use_low = True
            else:
                ligand_vol = vol_from_high
                use_low = False

            # Cap ligand volume at total volume (in case of very high concentrations)
            ligand_vol = min(ligand_vol, total_well_volume)
            buffer_vol = total_well_volume - ligand_vol

        # Single-channel: per-tip volume is simply the total volume for that well
        ligand_per_channel = ligand_vol
        buffer_per_channel = buffer_vol

        # Add buffer first, then ligand, finally mix
        if buffer_per_channel > 0:
            get_from_pool(low_salt_pool, buffer_per_channel, p300_single, dest_well)

        if ligand_per_channel > 0:
            if use_low:
                get_from_pool(
                    ligand_low_pool,
                    ligand_per_channel,
                    p300_single,
                    dest_well,
                    mix_after=True,
                    mix_volume=min(200.0, total_well_volume / 2.0)
                )
            else:
                get_from_pool(
                    ligand_high_pool,
                    ligand_per_channel,
                    p300_single,
                    dest_well,
                    mix_after=True,
                    mix_volume=min(200.0, total_well_volume / 2.0)
                )

    # Layout on mixing plate:
    # * For each salt concentration in [[SALT_CONCENTRATIONS]], create one column.
    # * For each ligand concentration in [[LIGAND_CONCENTRATIONS]], create one row entry,
    #   ascending from A-H.
    # Example: 8 ligand concs x 4 salt concs -> A1-H4 filled.
    num_rows = min(8, num_lig)
    num_cols = min(12, num_salt)

    for col_idx in range(num_cols):
        for row_idx in range(num_rows):
            target_conc = ligand_concs[row_idx]
            dest_well = mixing_plate.rows()[row_idx][col_idx]
            p300_single.pick_up_tip()
            prepare_ligand_well(dest_well, target_conc)
            p300_single.drop_tip()

    protocol.comment('Protocol complete. This script is templated and expects placeholders '
                     '([[...]] tokens) to be substituted with concrete values before execution.')
