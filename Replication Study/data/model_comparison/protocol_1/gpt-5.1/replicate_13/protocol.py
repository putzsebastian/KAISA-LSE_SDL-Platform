from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Dilution Setup',
    'author': 'User',
    'description': 'Templated protocol to prepare salt buffers and ligand dilutions using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders as plain literals so the template system can substitute values
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if s still looks like an unreplaced [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder; use default for simulation if unreplaced.

    Parsed via float first so that strings like '3.0' work for int casts.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder; use default list if unreplaced."""
    s = str(value).strip()
    if _unreplaced(s):
        return [cast(x) for x in default]
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ===============================================================
    # 1) Read & interpret templated parameters (with simulation fallbacks)
    # ===============================================================
    # Use a conservative default replicate count that respects 12 wells in Reservoir 3
    replicates = int(parse_scalar(PLACEHOLDER_REPLICATES, 3, int))

    # SALT_CONCENTRATIONS & LIGAND_CONCENTRATIONS are semicolon-separated lists
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 50.0, 100.0, 150.0])
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0]
    )

    # For simulation, enforce the 12-well constraint (replicates * n_salt <= 12)
    max_salt_for_reps = 12 // max(1, replicates)
    n_salt_default = min(len(salt_concs), max_salt_for_reps)
    n_salt = int(parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, n_salt_default, int))

    # Mixing plate has 8 rows (A–H); do not simulate more ligand concentrations than rows
    n_ligand_default = min(len(ligand_concs), 8)
    n_ligand = int(
        parse_scalar(
            PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS,
            n_ligand_default,
            int,
        )
    )

    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0)

    # Trim lists to the declared lengths
    salt_concs = salt_concs[:n_salt]
    ligand_concs = ligand_concs[:n_ligand]

    # ===============================================================
    # 2) Deck setup
    # ===============================================================
    # Slot 1: custom 96-well filter plate
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:  # custom labware missing in simulation
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
            'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.'
        )
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tipracks
    # Slot 7: tips for multi-channel
    tiprack_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    # Slot 10: tips for single-channel
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10)
    # Slot 4: extra tips available to either pipette if needed
    tiprack_extra = protocol.load_labware('opentrons_96_tiprack_300ul', 4)

    # Reservoirs
    # Slot 8: Reservoir 2 (low/high salt buffers)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    # Slot 9: Reservoir 1 (ligand stocks + buffers)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    # Slot 5: Reservoir 0 (low salt buffer)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    # Slot 6: Reservoir 3 (to be prepared: salt buffers)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    # Slot 3: Reservoir 4 (to be prepared: 2× salt buffers)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)

    # Slot 11: mixing plate (NEST 96 Deep-Well 2 mL)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ===============================================================
    # 3) Pipettes
    # ===============================================================
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_single, tiprack_extra],
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_multi, tiprack_extra],
    )

    # ===============================================================
    # 4) Reagent volume tracking (all volumes in µL)
    # ===============================================================
    INITIAL_VOL = 14000.0  # each reservoir well initially has 14 mL

    # Low salt buffer pool:
    # - Reservoir 2: wells 0–5 are low salt
    # - Reservoir 1: wells 2–6 are low salt
    # - Reservoir 0: wells 0–11 are low salt
    low_salt_pool = {
        'res2_0': {'well': reservoir2.wells()[0], 'remaining': INITIAL_VOL},
        'res2_1': {'well': reservoir2.wells()[1], 'remaining': INITIAL_VOL},
        'res2_2': {'well': reservoir2.wells()[2], 'remaining': INITIAL_VOL},
        'res2_3': {'well': reservoir2.wells()[3], 'remaining': INITIAL_VOL},
        'res2_4': {'well': reservoir2.wells()[4], 'remaining': INITIAL_VOL},
        'res2_5': {'well': reservoir2.wells()[5], 'remaining': INITIAL_VOL},
        'res1_2': {'well': reservoir1.wells()[2], 'remaining': INITIAL_VOL},
        'res1_3': {'well': reservoir1.wells()[3], 'remaining': INITIAL_VOL},
        'res1_4': {'well': reservoir1.wells()[4], 'remaining': INITIAL_VOL},
        'res1_5': {'well': reservoir1.wells()[5], 'remaining': INITIAL_VOL},
        'res1_6': {'well': reservoir1.wells()[6], 'remaining': INITIAL_VOL},
    }
    # add reservoir0 wells 0–11
    for i in range(12):
        low_salt_pool[f'res0_{i}'] = {
            'well': reservoir0.wells()[i],
            'remaining': INITIAL_VOL,
        }

    # High salt buffer pool:
    # - Reservoir 2: wells 6–11 are high salt
    # - Reservoir 1: wells 7–11 are high salt
    high_salt_pool = {
        'res2_6': {'well': reservoir2.wells()[6], 'remaining': INITIAL_VOL},
        'res2_7': {'well': reservoir2.wells()[7], 'remaining': INITIAL_VOL},
        'res2_8': {'well': reservoir2.wells()[8], 'remaining': INITIAL_VOL},
        'res2_9': {'well': reservoir2.wells()[9], 'remaining': INITIAL_VOL},
        'res2_10': {'well': reservoir2.wells()[10], 'remaining': INITIAL_VOL},
        'res2_11': {'well': reservoir2.wells()[11], 'remaining': INITIAL_VOL},
        'res1_7': {'well': reservoir1.wells()[7], 'remaining': INITIAL_VOL},
        'res1_8': {'well': reservoir1.wells()[8], 'remaining': INITIAL_VOL},
        'res1_9': {'well': reservoir1.wells()[9], 'remaining': INITIAL_VOL},
        'res1_10': {'well': reservoir1.wells()[10], 'remaining': INITIAL_VOL},
        'res1_11': {'well': reservoir1.wells()[11], 'remaining': INITIAL_VOL},
    }

    # Ligand stocks in Reservoir 1
    ligand_high = {
        'well': reservoir1.wells()[0],  # high concentration [[LIGAND_STOCK_CONCENTRATION]]
        'remaining': INITIAL_VOL,
    }
    ligand_low = {
        'well': reservoir1.wells()[1],  # low concentration [[LIGAND_STOCK_CONCENTRATION]] / 10
        'remaining': INITIAL_VOL,
    }

    # ===============================================================
    # 5) Helper functions
    # ===============================================================
    def take_from_pool_multi(pipette, pool, volume_per_channel_ul, dest, mix_after=False):
        """Multi-channel draw from a pooled reagent.

        - volume_per_channel_ul is PER-CHANNEL.
        - For a single-row reservoir, 8 channels share one well: one move of V µL
          per channel removes 8 × V from the source well and delivers 8 × V to dest.
        - Respects pipette.max_volume and actual remaining volume in each well.
        - Uses and then drops a single tip per destination well.
        """
        if volume_per_channel_ul <= 0:
            return

        total_remaining = volume_per_channel_ul
        pipette.pick_up_tip()

        while total_remaining > 0:
            # Choose a source well with remaining volume
            source_key = None
            for key, entry in pool.items():
                if entry['remaining'] > 0:
                    source_key = key
                    break
            if source_key is None:
                raise RuntimeError('Not enough volume in reagent pool to complete multi-channel transfer.')

            entry = pool[source_key]
            available_total = entry['remaining']           # total across 8 channels
            max_per_channel_from_well = available_total / 8.0
            move_per_channel = min(
                pipette.max_volume,
                total_remaining,
                max_per_channel_from_well,
            )
            if move_per_channel <= 0:
                entry['remaining'] = 0.0
                continue

            # Single transfer call; do not multiply by 8 here
            pipette.transfer(
                move_per_channel,
                entry['well'],
                dest,
                new_tip='never',
                mix_after=(3, min(200.0, move_per_channel)) if mix_after else None,
            )

            used_total = move_per_channel * 8.0
            entry['remaining'] -= used_total
            total_remaining -= move_per_channel

        pipette.drop_tip()

    def take_from_pool_single(pipette, pool, volume_ul, dest):
        """Single-channel draw from a pooled low-salt buffer for ligand dilutions."""
        if volume_ul <= 0:
            return
        remaining_to_move = volume_ul
        pipette.pick_up_tip()
        while remaining_to_move > 0:
            # choose a source with remaining volume
            source_key = None
            for key, entry in pool.items():
                if entry['remaining'] > 0:
                    source_key = key
                    break
            if source_key is None:
                pipette.drop_tip()
                raise RuntimeError('Not enough low salt buffer volume for ligand dilutions.')
            entry = pool[source_key]
            available = entry['remaining']
            move = min(pipette.max_volume, remaining_to_move, available)
            if move <= 0:
                entry['remaining'] = 0.0
                continue
            pipette.transfer(move, entry['well'], dest, new_tip='never')
            entry['remaining'] -= move
            remaining_to_move -= move
        pipette.drop_tip()

    def take_from_ligand_single(pipette, stock_entry, volume_ul, dest):
        """Take volume from a ligand stock well with single-channel pipette."""
        if volume_ul <= 0:
            return
        if stock_entry['remaining'] < volume_ul:
            raise RuntimeError('Not enough ligand stock volume for requested transfer.')
        remaining_to_move = volume_ul
        pipette.pick_up_tip()
        while remaining_to_move > 0:
            move = min(pipette.max_volume, remaining_to_move)
            pipette.transfer(move, stock_entry['well'], dest, new_tip='never')
            stock_entry['remaining'] -= move
            remaining_to_move -= move
        pipette.drop_tip()

    # ===============================================================
    # 6) Step 2 – Prepare salt buffers in Reservoir 3 (10 mL per well, with replicates)
    # ===============================================================
    protocol.comment('Step 2: Preparing salt buffers in Reservoir 3 (10 mL per well, with replicates).')

    vol_total_well_ul = 10000.0  # 10 mL per well

    current_well_index = 0
    for salt_idx, salt_c in enumerate(salt_concs[:n_salt]):
        # high/low salt fractions based on stock concentration
        frac_high = salt_c / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        frac_low = 1.0 - frac_high

        vol_high_per_channel = vol_total_well_ul * frac_high / 8.0
        vol_low_per_channel = vol_total_well_ul * frac_low / 8.0

        for rep in range(replicates):
            dest_well = reservoir3.wells()[current_well_index]
            protocol.comment(
                f'Reservoir 3 well index {current_well_index}: salt={salt_c}, replicate={rep + 1}. '
                f'vol_low/ch={vol_low_per_channel:.1f} µL, vol_high/ch={vol_high_per_channel:.1f} µL.'
            )

            if vol_low_per_channel > 0:
                take_from_pool_multi(p300_multi, low_salt_pool, vol_low_per_channel, dest_well, mix_after=False)
            if vol_high_per_channel > 0:
                take_from_pool_multi(p300_multi, high_salt_pool, vol_high_per_channel, dest_well, mix_after=True)

            current_well_index += 1

    # ===============================================================
    # 7) Step 3 – Prepare 2× salt buffers in Reservoir 4 (10 mL per well, no replicates)
    # ===============================================================
    protocol.comment('Step 3: Preparing 2× salt buffers in Reservoir 4 (10 mL per well).')

    for salt_idx, salt_c in enumerate(salt_concs[:n_salt]):
        target_c = 2.0 * salt_c
        frac_high = target_c / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        frac_low = 1.0 - frac_high

        vol_high_per_channel = vol_total_well_ul * frac_high / 8.0
        vol_low_per_channel = vol_total_well_ul * frac_low / 8.0

        dest_well = reservoir4.wells()[salt_idx]
        protocol.comment(
            f'Reservoir 4 well index {salt_idx}: 2× salt={target_c}. '
            f'vol_low/ch={vol_low_per_channel:.1f} µL, vol_high/ch={vol_high_per_channel:.1f} µL.'
        )

        if vol_low_per_channel > 0:
            take_from_pool_multi(p300_multi, low_salt_pool, vol_low_per_channel, dest_well, mix_after=False)
        if vol_high_per_channel > 0:
            take_from_pool_multi(p300_multi, high_salt_pool, vol_high_per_channel, dest_well, mix_after=True)

    # ===============================================================
    # 8) Step 4 – Prepare ligand dilutions in mixing plate
    # ===============================================================
    protocol.comment('Step 4: Preparing ligand dilutions in the mixing plate (2× ligand).')

    # Total volume per well: [[TOTAL_VOLUME]] / 2 * [[REPLICATES]] * 1.5
    vol_per_well = (total_volume / 2.0) * replicates * 1.5

    # For each salt concentration -> one column in mixing plate
    # Ligand concentrations ascend row-wise A–H
    columns_to_use = min(n_salt, 12)

    for col_idx in range(columns_to_use):
        for row_idx, lig_c in enumerate(ligand_concs[:8]):  # at most 8 rows (A–H)
            well = mixing_plate.rows()[row_idx][col_idx]
            target_lig_c = 2.0 * lig_c

            # Theoretical ligand volume if using high stock
            vol_from_high = (
                (target_lig_c * vol_per_well) / ligand_stock_conc
                if ligand_stock_conc > 0
                else 0.0
            )
            use_low = vol_from_high < 20.0 and ligand_stock_conc > 0

            effective_stock_conc = ligand_stock_conc / 10.0 if use_low else ligand_stock_conc
            ligand_vol = (
                (target_lig_c * vol_per_well) / effective_stock_conc
                if effective_stock_conc > 0
                else 0.0
            )
            ligand_vol = max(0.0, ligand_vol)
            buffer_vol = max(0.0, vol_per_well - ligand_vol)

            protocol.comment(
                f'Mixing plate well {well}: 2× ligand={target_lig_c}, '
                f'ligand_vol={ligand_vol:.1f} µL, buffer_vol={buffer_vol:.1f} µL '
                f'(using {"low" if use_low else "high"} stock).'
            )

            # Add low-salt buffer first
            if buffer_vol > 0:
                take_from_pool_single(p300_single, low_salt_pool, buffer_vol, well)

            # Add ligand stock (high or low)
            if ligand_vol > 0:
                if use_low:
                    take_from_ligand_single(p300_single, ligand_low, ligand_vol, well)
                else:
                    take_from_ligand_single(p300_single, ligand_high, ligand_vol, well)

    protocol.comment('Protocol complete.')
