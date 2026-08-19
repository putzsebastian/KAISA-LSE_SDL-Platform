from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Setup',
    'author': 'User',
    'description': 'Templated protocol for preparing salt buffers and ligand dilutions with placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (left as literal strings for later substitution)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONC = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONC = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIGAND = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if s still looks like a [[PLACEHOLDER]] token.

    Implemented without writing '[[' or ']]' literally, so the templating system can
    safely substitute those tokens later.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder as float (or cast), with a simulation default.

    On the real robot, placeholders are substituted before execution and must parse
    cleanly; otherwise this will raise. During dry-run/testing (with tokens still
    present), the provided `default` is used so the protocol can simulate.
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
    """Parse a semicolon-separated list placeholder into a list of numbers.

    Example string: "0;50;100;150".
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


# NOTE ON SIMULATION DEFAULTS
# ---------------------------
# The values below are for off-robot simulation only (when the placeholders
# still appear as [[TOKEN]]). On real runs, the wizard should substitute real
# numeric strings into the PLACEHOLDER_* constants before execution.
SIM_DEFAULT_REPLICATES = 1
SIM_DEFAULT_TOTAL_VOLUME = 100.0  # uL per well base (for ligand dilutions)
SIM_DEFAULT_SALT_CONCS = [0.0, 50.0]  # mM, illustrative
SIM_DEFAULT_LIGAND_CONCS = [0.0, 1.0, 2.0, 4.0]
SIM_DEFAULT_SALT_STOCK_CONC = 500.0  # mM
SIM_DEFAULT_LIGAND_STOCK_CONC = 1000.0  # uM
SIM_DEFAULT_NUM_SALT = 2
SIM_DEFAULT_NUM_LIGAND = 4


def run(protocol: protocol_api.ProtocolContext):
    # ---------------------------------------------------------------------
    # 1. Read and interpret placeholders (or simulation defaults)
    # ---------------------------------------------------------------------
    replicates = parse_int(PLACEHOLDER_REPLICATES, SIM_DEFAULT_REPLICATES)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, SIM_DEFAULT_TOTAL_VOLUME, float)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, SIM_DEFAULT_SALT_CONCS, float)
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, SIM_DEFAULT_LIGAND_CONCS, float)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONC, SIM_DEFAULT_SALT_STOCK_CONC, float)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, SIM_DEFAULT_LIGAND_STOCK_CONC, float)
    num_salt = parse_int(PLACEHOLDER_NUM_SALT, SIM_DEFAULT_NUM_SALT)
    num_ligand = parse_int(PLACEHOLDER_NUM_LIGAND, SIM_DEFAULT_NUM_LIGAND)

    # Ensure counts do not exceed list lengths
    if num_salt > len(salt_concs):
        num_salt = len(salt_concs)
    if num_ligand > len(ligand_concs):
        num_ligand = len(ligand_concs)

    # ---------------------------------------------------------------------
    # 2. Labware setup (deck layout as specified)
    # ---------------------------------------------------------------------
    # Slot 1: custom Cytiva filter plate, with simulation fallback labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
            'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.'
        )
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tip racks
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)   # for multi-channel
    tiprack_single_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)  # for single-channel

    # Reservoirs
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2: low/high salt buffers
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1: ligand + buffers
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0: low salt buffer only
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3: working salt buffers (product)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4: 2x salt buffers (product)

    # Mixing plate (NEST 96 deep-well 2 mL)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ---------------------------------------------------------------------
    # 3. Pipettes
    # ---------------------------------------------------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_multi_7, tiprack_slot4]
    )

    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_single_10, tiprack_slot4]
    )

    # ---------------------------------------------------------------------
    # 4. Reagent mapping & volume tracking (all reservoir wells start at 14 mL)
    # ---------------------------------------------------------------------
    INITIAL_VOL = 14000.0  # uL per reservoir well (14 mL)

    # Reservoir 2 (slot 8): low salt in wells 0–5, high salt in 6–11
    res2_low_wells = [reservoir2.wells()[i] for i in range(6)]
    res2_high_wells = [reservoir2.wells()[i] for i in range(6, 12)]
    res2_low_remaining = {well: INITIAL_VOL for well in res2_low_wells}
    res2_high_remaining = {well: INITIAL_VOL for well in res2_high_wells}

    # Reservoir 1 (slot 9): ligand and various low/high salt buffers
    ligand_high_well = reservoir1.wells()[0]  # high ligand stock
    ligand_low_well = reservoir1.wells()[1]   # low ligand stock (stock/10)

    res1_low_salt_wells = [reservoir1.wells()[i] for i in range(2, 7)]   # wells 2–6: low salt
    res1_high_salt_wells = [reservoir1.wells()[i] for i in range(7, 12)]  # wells 7–11: high salt

    # Track remaining volumes in reservoir 1 wells
    res1_remaining = {
        ligand_high_well: INITIAL_VOL,
        ligand_low_well: INITIAL_VOL,
        **{w: INITIAL_VOL for w in res1_low_salt_wells},
        **{w: INITIAL_VOL for w in res1_high_salt_wells},
    }

    # Reservoir 0 (slot 5): low salt buffer in all 12 wells
    res0_wells = [reservoir0.wells()[i] for i in range(12)]
    res0_remaining = {well: INITIAL_VOL for well in res0_wells}

    # ---------------------------------------------------------------------
    # 5. Helper functions for volume pooling and chunked transfers
    # ---------------------------------------------------------------------
    def take_from_pool_multi(volume_per_channel: float, pool_remaining: dict):
        """Return a well from `pool_remaining` that can supply one
        multi-channel aspiration of `volume_per_channel` uL per channel.

        For a single-row reservoir and an 8-channel pipette, each aspiration
        removes 8 * volume_per_channel from the selected well.
        """
        total_needed = volume_per_channel * 8.0
        for well, remaining in pool_remaining.items():
            if remaining >= total_needed:
                pool_remaining[well] -= total_needed
                return well
        raise RuntimeError('Not enough volume remaining in reservoir pool to satisfy request.')

    def chunked_transfer_multi(pipette, total_volume_per_channel: float, src_pool: dict,
                               dest_well, mix_after: bool = False):
        """Move `total_volume_per_channel` uL per channel from a reservoir pool
        into `dest_well` using the multi-channel pipette.

        The move is chunked by the pipette's max volume. `src_pool` is a dict
        mapping reservoir wells to remaining volume in uL (of that well).
        """
        remaining = total_volume_per_channel
        max_vol = pipette.max_volume
        pipette.pick_up_tip()
        while remaining > 0:
            chunk = min(max_vol, remaining)
            src_well = take_from_pool_multi(chunk, src_pool)
            pipette.aspirate(chunk, src_well)
            pipette.dispense(chunk, dest_well)
            remaining -= chunk
        if mix_after and total_volume_per_channel > 0:
            mix_vol = min(0.8 * max_vol, total_volume_per_channel)
            if mix_vol > 0:
                pipette.mix(3, mix_vol, dest_well)
        pipette.drop_tip()

    def get_low_salt_pool_multi():
        """Return the current pool dict to use for low-salt with multi-channel.

        Preference order: Reservoir 2 low salt wells, then Reservoir 1 low salt
        wells, then Reservoir 0.
        """
        if any(v > 0 for v in res2_low_remaining.values()):
            return res2_low_remaining
        if any(res1_remaining[w] > 0 for w in res1_low_salt_wells):
            return {w: res1_remaining[w] for w in res1_low_salt_wells}
        if any(v > 0 for v in res0_remaining.values()):
            return res0_remaining
        raise RuntimeError('Low salt buffer depleted across all reservoirs (multi-channel).')

    def get_high_salt_pool_multi():
        """Return the current pool dict to use for high-salt with multi-channel.

        Preference order: Reservoir 2 high salt wells, then Reservoir 1 high salt wells.
        """
        if any(v > 0 for v in res2_high_remaining.values()):
            return res2_high_remaining
        if any(res1_remaining[w] > 0 for w in res1_high_salt_wells):
            return {w: res1_remaining[w] for w in res1_high_salt_wells}
        raise RuntimeError('High salt buffer depleted across all reservoirs (multi-channel).')

    # Single-channel helper for low salt (for ligand dilutions)
    def take_low_salt_single(volume_ul: float):
        """Return a low-salt well for a single-channel aspiration of `volume_ul`.

        Preference: low-salt wells in Reservoir 1 (2–6), then any well in Reservoir 0.
        """
        # Reservoir 1 low salt wells first
        for w in res1_low_salt_wells:
            remaining = res1_remaining[w]
            if remaining >= volume_ul:
                res1_remaining[w] -= volume_ul
                return w
        # Then Reservoir 0
        for w, remaining in res0_remaining.items():
            if remaining >= volume_ul:
                res0_remaining[w] -= volume_ul
                return w
        raise RuntimeError('Low salt buffer depleted for single-channel operations.')

    # Ligand stock tracking (Reservoir 1 wells 0 and 1)
    ligand_remaining = {
        ligand_high_well: INITIAL_VOL,
        ligand_low_well: INITIAL_VOL,
    }

    def take_ligand(volume_ul: float, source_well):
        """Reserve `volume_ul` of ligand stock from `source_well` and return it."""
        if ligand_remaining[source_well] < volume_ul:
            raise RuntimeError(f'Ligand stock depleted in well: {source_well}')
        ligand_remaining[source_well] -= volume_ul
        return source_well

    # ---------------------------------------------------------------------
    # 6. Step 2: Working salt buffers in Reservoir 3 (10 mL per well)
    # ---------------------------------------------------------------------
    protocol.comment('Preparing working salt buffers in Reservoir 3 (10 mL each)...')

    if replicates * num_salt > 12:
        raise RuntimeError(
            'REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 wells in Reservoir 3.'
        )

    V_total = 10000.0  # uL total per reservoir 3 well

    dest_index = 0
    for i in range(num_salt):
        c_target = salt_concs[i]
        protocol.comment(f'Preparing salt concentration {c_target} in Reservoir 3 (index {i})...')

        if c_target < 0 or c_target > salt_stock_conc:
            raise RuntimeError('Target salt concentration outside 0..stock range.')

        # Simple linear mixing between 0 and stock concentration
        frac_high = c_target / salt_stock_conc if salt_stock_conc > 0 else 0.0
        if frac_high < 0.0:
            frac_high = 0.0
        if frac_high > 1.0:
            frac_high = 1.0
        frac_low = 1.0 - frac_high

        vol_high = V_total * frac_high
        vol_low = V_total * frac_low

        for rep in range(replicates):
            if dest_index >= 12:
                raise RuntimeError('Not enough wells in Reservoir 3 for all replicates.')
            dest = reservoir3.wells()[dest_index]
            dest_index += 1

            # Low salt first
            if vol_low > 0:
                low_pool = get_low_salt_pool_multi()
                chunked_transfer_multi(p300_multi, vol_low, low_pool, dest, mix_after=False)

            # High salt second; mix after addition
            if vol_high > 0:
                high_pool = get_high_salt_pool_multi()
                chunked_transfer_multi(p300_multi, vol_high, high_pool, dest, mix_after=True)

    # ---------------------------------------------------------------------
    # 7. Step 3: 2x salt buffers in Reservoir 4 (10 mL per well)
    # ---------------------------------------------------------------------
    protocol.comment('Preparing 2x salt buffers in Reservoir 4 (10 mL each)...')

    if num_salt > 12:
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 wells in Reservoir 4.')

    for i in range(num_salt):
        c_target_1x = salt_concs[i]
        c_target_2x = c_target_1x * 2.0
        protocol.comment(f'Preparing 2x salt concentration {c_target_2x} in Reservoir 4 (index {i})...')

        if c_target_2x < 0 or c_target_2x > salt_stock_conc:
            raise RuntimeError('2x target salt concentration exceeds stock concentration.')

        frac_high = c_target_2x / salt_stock_conc if salt_stock_conc > 0 else 0.0
        if frac_high < 0.0:
            frac_high = 0.0
        if frac_high > 1.0:
            frac_high = 1.0
        frac_low = 1.0 - frac_high

        vol_high = V_total * frac_high
        vol_low = V_total * frac_low

        dest = reservoir4.wells()[i]

        # Low salt first
        if vol_low > 0:
            low_pool = get_low_salt_pool_multi()
            chunked_transfer_multi(p300_multi, vol_low, low_pool, dest, mix_after=False)

        # High salt second; mix after addition
        if vol_high > 0:
            high_pool = get_high_salt_pool_multi()
            chunked_transfer_multi(p300_multi, vol_high, high_pool, dest, mix_after=True)

    # ---------------------------------------------------------------------
    # 8. Step 4: Ligand dilutions (2x target concentrations) in mixing plate
    # ---------------------------------------------------------------------
    protocol.comment('Preparing 2x ligand dilutions in the mixing plate...')

    if num_ligand > 8:
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS must not exceed 8 rows (A-H).')
    if num_salt > 12:
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 columns in the mixing plate.')

    # Total volume per mixing-plate well
    # as defined: TOTAL_VOLUME/2 * REPLICATES * 1.5
    total_vol_well = (total_volume / 2.0) * replicates * 1.5

    def compute_ligand_volumes(target_conc_2x: float):
        """Given desired 2x ligand concentration, return
        (stock_volume_uL, buffer_volume_uL, stock_source_well).

        Prefer the high-concentration ligand stock (Reservoir 1 well 0). If the
        required stock volume would be < 20 µL, switch to the lower-concentration
        stock (Reservoir 1 well 1, 10x lower) and adjust the stock volume.
        """
        # Using C1 * V1 = C2 * V2, with C2 = target_conc_2x and V2 = total_vol_well
        v_stock_high = (target_conc_2x * total_vol_well) / ligand_stock_conc if ligand_stock_conc > 0 else 0.0

        if v_stock_high >= 20.0:
            buffer_vol = total_vol_well - v_stock_high
            if buffer_vol < 0:
                buffer_vol = 0
            return v_stock_high, buffer_vol, ligand_high_well

        # Fall back to low stock (10x more dilute)
        low_stock_conc = ligand_stock_conc / 10.0
        v_stock_low = (target_conc_2x * total_vol_well) / low_stock_conc if low_stock_conc > 0 else 0.0
        buffer_vol = total_vol_well - v_stock_low
        if buffer_vol < 0:
            buffer_vol = 0
        return v_stock_low, buffer_vol, ligand_low_well

    # Create rows of ligand dilutions (ascending ligand_concs from row A downward)
    for row_idx in range(num_ligand):
        target_1x = ligand_concs[row_idx]
        target_2x = target_1x * 2.0
        row = mixing_plate.rows()[row_idx]
        protocol.comment(
            f'Preparing ligand row {row_idx} at 2x {target_1x} (={target_2x}) '
            f'for {num_salt} salt concentrations.'
        )

        v_stock, v_buffer, lig_source = compute_ligand_volumes(target_2x)

        for col_idx in range(num_salt):
            dest = row[col_idx]

            # Use one tip per destination well (to avoid cross-contamination)
            p300_single.pick_up_tip()

            # 1) Add ligand stock in chunks, respecting pipette capacity
            if v_stock > 0:
                remaining = v_stock
                while remaining > 0:
                    chunk = min(p300_single.max_volume, remaining)
                    src = take_ligand(chunk, lig_source)
                    p300_single.aspirate(chunk, src)
                    p300_single.dispense(chunk, dest)
                    remaining -= chunk

            # 2) Add low salt buffer to reach the total volume
            if v_buffer > 0:
                remaining_buf = v_buffer
                while remaining_buf > 0:
                    chunk = min(p300_single.max_volume, remaining_buf)
                    buf_src = take_low_salt_single(chunk)
                    p300_single.aspirate(chunk, buf_src)
                    p300_single.dispense(chunk, dest)
                    remaining_buf -= chunk

            # 3) Mix the final well
            if total_vol_well > 0:
                mix_vol = min(0.8 * p300_single.max_volume, total_vol_well)
                if mix_vol > 0:
                    p300_single.mix(3, mix_vol, dest)

            p300_single.drop_tip()

    protocol.comment('Protocol complete: salt buffers and ligand dilutions prepared.')
