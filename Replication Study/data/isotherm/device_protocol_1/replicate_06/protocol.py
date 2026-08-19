from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Titration Setup',
    'author': 'User',
    'description': 'Templated protocol for preparing salt buffers and ligand dilutions using reservoirs and deep-well plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (will be replaced by the wizard)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONC = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONC = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_N_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_N_LIGAND = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a numeric scalar placeholder.

    During simulation, when the placeholder is unreplaced (e.g. '[[TOTAL_VOLUME]]'),
    a worst-case default is used so the protocol can be simulated.
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
    """Parse a semicolon-separated numeric list placeholder."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with simulation fallbacks (chosen to exercise near worst-case usage)
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=2)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=200.0)

    # Up to 6 salt concentrations (since 12 wells / 2 reps = 6)
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 50, 100, 150, 200, 250]
    )
    # Up to 8 ligand concentrations (A–H)
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        default=[0, 1, 2, 3, 4, 5, 6, 7]
    )

    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONC, default=1000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, default=1000.0)

    n_salt = parse_int(PLACEHOLDER_N_SALT, default=len(salt_concs))
    n_ligand = parse_int(PLACEHOLDER_N_LIGAND, default=len(ligand_concs))

    # Respect NUMBER_OF_* if explicitly set (may be <= provided list length)
    salt_concs = salt_concs[:n_salt]
    ligand_concs = ligand_concs[:n_ligand]

    # Basic capacity check for Reservoir 3 (12 wells total)
    if replicates * len(salt_concs) > 12:
        protocol.comment(
            'WARNING: REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS exceeds 12; ' \
            'Reservoir 3 capacity will be exceeded.'
        )

    # Constants
    reservoir_total_volume_ul = 10000.0  # 10 mL final per prepared well
    reservoir_start_volume_ul = 14000.0  # 14 mL initial per source well (per channel pool)

    # --- Labware setup ---

    # Slot 1: custom 96-well filter plate
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; ' \
            'using nest_96_wellplate_200ul_flat as SIMULATION fallback.'
        )
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tip racks
    tiprack_single_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)   # single-channel
    tiprack_multi_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)    # multi-channel
    tiprack_single_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)  # single-channel

    # Reservoirs
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (empty)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (preloaded low/high salt)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (ligand stocks + buffers)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (low salt buffer)

    # Mixing plate (deep-well)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # --- Pipettes ---

    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_single_1, tiprack_single_2]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_multi_1]
    )

    # --- Volume tracking for finite reservoirs ---

    # Reservoir 2: wells 0–5 low salt, 6–11 high salt
    res2_low_vol = {i: reservoir_start_volume_ul for i in range(0, 6)}
    res2_high_vol = {i: reservoir_start_volume_ul for i in range(6, 12)}

    # Reservoir 1: mapping as given in the prompt
    # 0: high ligand stock; 1: low ligand stock; 2–6: low salt; 7–11: high salt
    res1_ligand_high_vol = {0: reservoir_start_volume_ul}
    res1_ligand_low_vol = {1: reservoir_start_volume_ul}
    res1_low_salt_vol = {i: reservoir_start_volume_ul for i in range(2, 7)}
    res1_high_salt_vol = {i: reservoir_start_volume_ul for i in range(7, 12)}

    # Reservoir 0: all low salt
    res0_low_salt_vol = {i: reservoir_start_volume_ul for i in range(12)}

    def select_from_pool(pool, volume_per_channel_ul, pipette):
        """Select a reservoir well index from a volume pool that can supply given volume.

        volume_per_channel_ul is per-channel; a multi-channel pipette pulls 8x
        from a single-row reservoir well. This helper reduces the tracked volume
        in the chosen source well and returns its index.

        It will preferentially use a single well if possible, and otherwise
        split across wells while still returning the last-used index as the
        physical source location for the current aspiration.
        """
        total_required_ul = volume_per_channel_ul * (8 if pipette.channels == 8 else 1)

        # First, try any single well that can supply the entire request
        for idx in sorted(pool.keys()):
            if pool[idx] >= total_required_ul:
                pool[idx] -= total_required_ul
                return idx

        # Otherwise, split across wells: take as much as possible from each,
        # and return the index of the last contributing well.
        remaining = total_required_ul
        last_idx = None
        for idx in sorted(pool.keys()):
            if pool[idx] <= 0:
                continue
            take = min(pool[idx], remaining)
            pool[idx] -= take
            remaining -= take
            last_idx = idx
            if remaining <= 0:
                break
        if remaining > 0 or last_idx is None:
            raise RuntimeError(
                'Reagent pool exhausted for requested volume %.1f uL' % total_required_ul
            )
        return last_idx

    def mix_in_well(pipette, well, volume_ul, repetitions=5):
        """Mix in the given well with the specified total volume (per channel)."""
        vol = min(volume_ul, pipette.max_volume)
        if vol <= 0:
            return
        pipette.mix(repetitions, vol, well)

    # ---------------------------------------------------------------------
    # STEP 2: Prepare salt buffers in Reservoir 3 (10 mL/well, with replicates)
    # ---------------------------------------------------------------------

    protocol.comment('Step 2: Preparing salt buffers in Reservoir 3')

    for i, c_target in enumerate(salt_concs):
        c_target = float(c_target)
        V_total_ul = reservoir_total_volume_ul

        # 2-component mixing: V_high = (C_target / C_stock) * V_total
        V_high_ul = V_total_ul * (c_target / max(salt_stock_conc, 1e-6))
        V_high_ul = max(0.0, min(V_total_ul, V_high_ul))
        V_low_ul = V_total_ul - V_high_ul

        for r in range(replicates):
            dest_well_index = i * replicates + r
            if dest_well_index >= 12:
                protocol.comment(
                    f'Skipping salt conc index {i}, replicate {r}: no more wells in Reservoir 3.'
                )
                continue

            dest = reservoir3.wells()[dest_well_index]

            # Convert to per-channel volumes for multi-channel pipette
            factor = 8 if p300_multi.channels == 8 else 1
            V_high_per_channel = V_high_ul / factor
            V_low_per_channel = V_low_ul / factor

            if V_high_per_channel <= 0 and V_low_per_channel <= 0:
                continue

            if not p300_multi.has_tip:
                p300_multi.pick_up_tip()

            # Low salt contribution: prefer Reservoir 2 (low salt), then 0, then 1
            if V_low_per_channel > 0:
                vol_per_channel = V_low_per_channel
                src_pool_order = [
                    ('res2_low', res2_low_vol, reservoir2),
                    ('res0_low', res0_low_salt_vol, reservoir0),
                    ('res1_low', res1_low_salt_vol, reservoir1)
                ]
                remaining = vol_per_channel
                while remaining > 0:
                    chunk = min(remaining, p300_multi.max_volume)
                    supplied = False
                    for name, pool, lab in src_pool_order:
                        try:
                            idx = select_from_pool(pool, chunk, p300_multi)
                            src = lab.wells()[idx]
                            p300_multi.transfer(chunk, src, dest, new_tip='never')
                            supplied = True
                            break
                        except RuntimeError:
                            continue
                    if not supplied:
                        raise RuntimeError(
                            'Low salt buffer exhausted while preparing Reservoir 3'
                        )
                    remaining -= chunk

            # High salt contribution: prefer Reservoir 2 (high salt), then Reservoir 1 (high salt)
            if V_high_per_channel > 0:
                vol_per_channel = V_high_per_channel
                src_pool_order = [
                    ('res2_high', res2_high_vol, reservoir2),
                    ('res1_high', res1_high_salt_vol, reservoir1)
                ]
                remaining = vol_per_channel
                while remaining > 0:
                    chunk = min(remaining, p300_multi.max_volume)
                    supplied = False
                    for name, pool, lab in src_pool_order:
                        try:
                            idx = select_from_pool(pool, chunk, p300_multi)
                            src = lab.wells()[idx]
                            p300_multi.transfer(chunk, src, dest, new_tip='never')
                            supplied = True
                            break
                        except RuntimeError:
                            continue
                    if not supplied:
                        raise RuntimeError(
                            'High salt buffer exhausted while preparing Reservoir 3'
                        )
                    remaining -= chunk

            # Mix prepared well in Reservoir 3 (per-channel volume used for mixing)
            mix_in_well(p300_multi, dest, V_total_ul / factor)

    # Drop tip if still carried after step 2
    if p300_multi.has_tip:
        p300_multi.drop_tip()

    # ---------------------------------------------------------------------
    # STEP 3: Prepare 2x salt buffers in Reservoir 4 (10 mL/well)
    # ---------------------------------------------------------------------

    protocol.comment('Step 3: Preparing 2x salt buffers in Reservoir 4')

    for i, c_target in enumerate(salt_concs):
        c_2x = float(c_target) * 2.0
        V_total_ul = reservoir_total_volume_ul

        V_high_ul = V_total_ul * (c_2x / max(salt_stock_conc, 1e-6))
        V_high_ul = max(0.0, min(V_total_ul, V_high_ul))
        V_low_ul = V_total_ul - V_high_ul

        dest_well_index = i
        if dest_well_index >= 12:
            protocol.comment(
                f'Skipping 2x salt conc index {i}: no more wells in Reservoir 4.'
            )
            continue

        dest = reservoir4.wells()[dest_well_index]

        factor = 8 if p300_multi.channels == 8 else 1
        V_high_per_channel = V_high_ul / factor
        V_low_per_channel = V_low_ul / factor

        if V_high_per_channel <= 0 and V_low_per_channel <= 0:
            continue

        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        # Low salt contribution
        if V_low_per_channel > 0:
            vol_per_channel = V_low_per_channel
            src_pool_order = [
                ('res2_low', res2_low_vol, reservoir2),
                ('res0_low', res0_low_salt_vol, reservoir0),
                ('res1_low', res1_low_salt_vol, reservoir1)
            ]
            remaining = vol_per_channel
            while remaining > 0:
                chunk = min(remaining, p300_multi.max_volume)
                supplied = False
                for name, pool, lab in src_pool_order:
                    try:
                        idx = select_from_pool(pool, chunk, p300_multi)
                        src = lab.wells()[idx]
                        p300_multi.transfer(chunk, src, dest, new_tip='never')
                        supplied = True
                        break
                    except RuntimeError:
                        continue
                if not supplied:
                    raise RuntimeError(
                        'Low salt buffer exhausted while preparing Reservoir 4'
                    )
                remaining -= chunk

        # High salt contribution
        if V_high_per_channel > 0:
            vol_per_channel = V_high_per_channel
            src_pool_order = [
                ('res2_high', res2_high_vol, reservoir2),
                ('res1_high', res1_high_salt_vol, reservoir1)
            ]
            remaining = vol_per_channel
            while remaining > 0:
                chunk = min(remaining, p300_multi.max_volume)
                supplied = False
                for name, pool, lab in src_pool_order:
                    try:
                        idx = select_from_pool(pool, chunk, p300_multi)
                        src = lab.wells()[idx]
                        p300_multi.transfer(chunk, src, dest, new_tip='never')
                        supplied = True
                        break
                    except RuntimeError:
                        continue
                if not supplied:
                    raise RuntimeError(
                        'High salt buffer exhausted while preparing Reservoir 4'
                    )
                remaining -= chunk

        mix_in_well(p300_multi, dest, V_total_ul / factor)

    if p300_multi.has_tip:
        p300_multi.drop_tip()

    # ---------------------------------------------------------------------
    # STEP 4: Prepare 2x ligand dilutions in mixing plate
    # ---------------------------------------------------------------------

    protocol.comment('Step 4: Preparing ligand dilutions in mixing plate')

    # Each deep-well: TOTAL_VOLUME/2 * REPLICATES * 1.5
    per_well_total_volume_ul = (total_volume / 2.0) * replicates * 1.5

    ligand_high_conc = ligand_stock_conc
    ligand_low_conc = ligand_stock_conc / 10.0

    ligand_high_pool = res1_ligand_high_vol
    ligand_low_pool = res1_ligand_low_vol

    # Layout: for each salt concentration (column), fill rows A–H with ascending ligand concs
    max_rows = min(8, len(ligand_concs))  # up to A–H
    max_cols = min(12, len(salt_concs))   # up to 12 columns

    if max_rows * max_cols == 0:
        protocol.comment('No ligand or salt concentrations provided; skipping step 4.')
        return

    for col_idx in range(max_cols):
        for row_idx in range(max_rows):
            c_ligand_target = float(ligand_concs[row_idx]) * 2.0  # 2x concentrations
            well = mixing_plate.rows()[row_idx][col_idx]

            # Compute ligand volume from high and low stocks
            V_ligand_from_high_ul = per_well_total_volume_ul * (
                c_ligand_target / max(ligand_high_conc, 1e-6)
            )
            V_ligand_from_low_ul = per_well_total_volume_ul * (
                c_ligand_target / max(ligand_low_conc, 1e-6)
            )

            V_ligand_from_high_ul = max(0.0, min(per_well_total_volume_ul, V_ligand_from_high_ul))
            V_ligand_from_low_ul = max(0.0, min(per_well_total_volume_ul, V_ligand_from_low_ul))

            # Decide stock: if high-stock usage would be < 20 uL, use the 10x more dilute stock
            use_low_stock = V_ligand_from_high_ul < 20.0
            if use_low_stock:
                V_ligand_ul = V_ligand_from_low_ul
                ligand_pool = ligand_low_pool
                ligand_lab = reservoir1
                ligand_idx_fixed = 1
            else:
                V_ligand_ul = V_ligand_from_high_ul
                ligand_pool = ligand_high_pool
                ligand_lab = reservoir1
                ligand_idx_fixed = 0

            V_buffer_ul = per_well_total_volume_ul - V_ligand_ul

            if not p300_single.has_tip:
                p300_single.pick_up_tip()

            # Add ligand from chosen stock well
            remaining_ligand = V_ligand_ul
            while remaining_ligand > 0:
                chunk = min(remaining_ligand, p300_single.max_volume)
                total_required = chunk
                if ligand_pool[ligand_idx_fixed] < total_required:
                    raise RuntimeError(
                        'Ligand stock in well %d exhausted.' % ligand_idx_fixed
                    )
                ligand_pool[ligand_idx_fixed] -= total_required
                src = ligand_lab.wells()[ligand_idx_fixed]
                p300_single.transfer(chunk, src, well, new_tip='never')
                remaining_ligand -= chunk

            # Add low-salt buffer to complete volume: prefer Reservoir 0, then 1, then 2
            remaining_buffer = V_buffer_ul
            src_pool_order = [
                ('res0_low', res0_low_salt_vol, reservoir0),
                ('res1_low', res1_low_salt_vol, reservoir1),
                ('res2_low', res2_low_vol, reservoir2)
            ]
            while remaining_buffer > 0:
                chunk = min(remaining_buffer, p300_single.max_volume)
                supplied = False
                for name, pool, lab in src_pool_order:
                    try:
                        idx = select_from_pool(pool, chunk, p300_single)
                        src = lab.wells()[idx]
                        p300_single.transfer(chunk, src, well, new_tip='never')
                        supplied = True
                        break
                    except RuntimeError:
                        continue
                if not supplied:
                    raise RuntimeError(
                        'Low salt buffer exhausted while preparing ligand dilutions'
                    )
                remaining_buffer -= chunk

            mix_in_well(p300_single, well, per_well_total_volume_ul)

    if p300_single.has_tip:
        p300_single.drop_tip()

    protocol.comment('Protocol complete.')
