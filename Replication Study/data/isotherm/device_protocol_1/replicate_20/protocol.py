from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Dilution Templated Protocol',
    'author': 'User',
    'description': 'Templated protocol for preparing salt buffers and ligand dilutions with placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONC = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONC = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIGAND = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    s2 = str(s).strip()
    return s2.startswith('[' * 2) and s2.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_int(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    parts = [p for p in s.split(';') if p.strip()]
    return [cast(float(p)) for p in parts]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholder parameters with conservative worst-case defaults for simulation
    # For simulation we use upper-bound defaults to stress-test tips and reagent volumes.
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # uL per well before factors
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 50.0, 100.0, 150.0])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS,
                              [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0])
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONC, 1000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, 1000.0)
    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_ligand = parse_int(PLACEHOLDER_NUM_LIGAND, len(ligand_concs))

    salt_concs = salt_concs[:num_salt]
    ligand_concs = ligand_concs[:num_ligand]

    if replicates * num_salt > 12:
        raise RuntimeError('replicates x number of salt concentrations must not exceed 12 for Reservoir 3')

    # Labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware not found; using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_single_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (target salt buffers)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (2x salt buffers)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300_single = protocol.load_instrument(
        'p300_single_gen2', mount='right', tip_racks=[tiprack_single_1, tiprack_single_2]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2', mount='left', tip_racks=[tiprack_multi_1]
    )

    # Reagent pools and volume tracking (volumes per well in uL)
    INITIAL_RESERVOIR_VOL_UL = 14000.0

    # Low salt buffer pool: all wells in reservoir0 plus designated low-salt wells in reservoir1 and reservoir2
    low_salt_sources = []
    low_salt_remaining = {}

    for w in reservoir0.wells():
        low_salt_sources.append(w)
        low_salt_remaining[w] = INITIAL_RESERVOIR_VOL_UL

    # Reservoir1: wells 2-6 are low salt
    for idx in range(2, 7):
        w = reservoir1.wells()[idx]
        low_salt_sources.append(w)
        low_salt_remaining[w] = INITIAL_RESERVOIR_VOL_UL

    # Reservoir2: wells 0-5 are low salt
    for idx in range(0, 6):
        w = reservoir2.wells()[idx]
        low_salt_sources.append(w)
        low_salt_remaining[w] = INITIAL_RESERVOIR_VOL_UL

    # High salt buffer pool: reservoir2 wells 6-11 and reservoir1 wells 7-11
    high_salt_sources = []
    high_salt_remaining = {}

    for idx in range(6, 12):
        w = reservoir2.wells()[idx]
        high_salt_sources.append(w)
        high_salt_remaining[w] = INITIAL_RESERVOIR_VOL_UL

    for idx in range(7, 12):
        w = reservoir1.wells()[idx]
        high_salt_sources.append(w)
        high_salt_remaining[w] = INITIAL_RESERVOIR_VOL_UL

    # Ligand stock wells in reservoir1
    ligand_stock_high = reservoir1.wells()[0]
    ligand_stock_low = reservoir1.wells()[1]
    ligand_stock_high_remaining = INITIAL_RESERVOIR_VOL_UL
    ligand_stock_low_remaining = INITIAL_RESERVOIR_VOL_UL

    def get_volume_from_pool(pipette, pool_wells, remaining_dict, volume_per_channel_ul, dest):
        """Dispense volume_per_channel_ul (per channel) into dest using multi-channel pipette.
        Handles splitting across multiple source wells and multiple aspirations if needed.
        """
        total_needed = volume_per_channel_ul
        if total_needed <= 0:
            return
        # single pickup per destination: caller is responsible for pick_up_tip() / drop_tip()
        while total_needed > 0:
            # find next well with remaining volume
            src = None
            for w in pool_wells:
                if remaining_dict[w] > 0:
                    src = w
                    break
            if src is None:
                raise RuntimeError('Not enough volume remaining in reagent pool to complete transfer.')

            # each aspiration removes volume_per_channel_ul per channel from well; for 8 channels that is x8
            max_give = remaining_dict[src] / 8.0
            give = min(total_needed, max_give, pipette.max_volume)
            if give <= 0:
                remaining_dict[src] = 0.0
                continue

            pipette.aspirate(give, src)
            pipette.dispense(give, dest)
            remaining_dict[src] -= give * 8.0
            total_needed -= give

    def get_volume_from_single(pipette, src, remaining_ref, volume_ul, dest):
        total_needed = volume_ul
        if total_needed <= 0:
            return
        while total_needed > 0:
            if remaining_ref[0] <= 0:
                raise RuntimeError('Not enough volume remaining in ligand stock to complete transfer.')
            max_give = min(pipette.max_volume, remaining_ref[0])
            give = min(total_needed, max_give)
            if give <= 0:
                break
            pipette.aspirate(give, src)
            pipette.dispense(give, dest)
            remaining_ref[0] -= give
            total_needed -= give

    # Step 2: Prepare salt buffers in Reservoir 3 (10 mL per well) with target salt_concs
    protocol.comment('Step 2: Preparing salt buffers in Reservoir 3')

    total_vol_reservoir_well_ul = 10000.0  # 10 mL per well

    # For each salt concentration, allocate replicates wells in Reservoir3 in ascending order
    dest_wells_res3 = reservoir3.wells()[:replicates * num_salt]

    for idx_salt, target_salt in enumerate(salt_concs):
        # volumetric ratio low/high based on simple linear mixing model
        frac_high = target_salt / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        frac_low = 1.0 - frac_high

        vol_high_ul = total_vol_reservoir_well_ul * frac_high
        vol_low_ul = total_vol_reservoir_well_ul * frac_low

        # multi-channel per-channel volumes
        per_channel_high_ul = vol_high_ul / 8.0
        per_channel_low_ul = vol_low_ul / 8.0

        for r in range(replicates):
            dest_index = idx_salt * replicates + r
            dest = dest_wells_res3[dest_index]

            # low salt first, then high salt
            p300_multi.pick_up_tip()
            if per_channel_low_ul > 0:
                get_volume_from_pool(p300_multi, low_salt_sources, low_salt_remaining,
                                     per_channel_low_ul, dest)
            if per_channel_high_ul > 0:
                get_volume_from_pool(p300_multi, high_salt_sources, high_salt_remaining,
                                     per_channel_high_ul, dest)

            # mix in destination well
            mix_vol = min(p300_multi.max_volume, total_vol_reservoir_well_ul / 8.0)
            if mix_vol > 0:
                p300_multi.mix(5, mix_vol, dest)
            p300_multi.drop_tip()

    # Step 3: Prepare 2x salt buffers in Reservoir 4 (10 mL per salt concentration)
    protocol.comment('Step 3: Preparing 2x salt buffers in Reservoir 4')

    dest_wells_res4 = reservoir4.wells()[:num_salt]

    for idx_salt, target_salt in enumerate(salt_concs):
        target_2x = 2.0 * target_salt
        frac_high = target_2x / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        frac_low = 1.0 - frac_high

        vol_high_ul = total_vol_reservoir_well_ul * frac_high
        vol_low_ul = total_vol_reservoir_well_ul * frac_low

        per_channel_high_ul = vol_high_ul / 8.0
        per_channel_low_ul = vol_low_ul / 8.0

        dest = dest_wells_res4[idx_salt]

        p300_multi.pick_up_tip()
        if per_channel_low_ul > 0:
            get_volume_from_pool(p300_multi, low_salt_sources, low_salt_remaining,
                                 per_channel_low_ul, dest)
        if per_channel_high_ul > 0:
            get_volume_from_pool(p300_multi, high_salt_sources, high_salt_remaining,
                                 per_channel_high_ul, dest)
        mix_vol = min(p300_multi.max_volume, total_vol_reservoir_well_ul / 8.0)
        if mix_vol > 0:
            p300_multi.mix(5, mix_vol, dest)
        p300_multi.drop_tip()

    # Step 4: Prepare ligand dilutions in mixing plate (2x ligand concentrations)
    protocol.comment('Step 4: Preparing ligand dilutions in mixing plate')

    # total volume per well: TOTAL_VOLUME/2 * REPLICATES * 1.5
    total_well_vol_ul = (total_volume / 2.0) * replicates * 1.5

    ligand_high_remaining_ref = [ligand_stock_high_remaining]
    ligand_low_remaining_ref = [ligand_stock_low_remaining]

    # For each salt concentration, create one column of ligand dilutions (ascending A-H)
    for col_idx in range(num_salt):
        col = mixing_plate.columns()[col_idx]
        for row_idx, target_ligand in enumerate(ligand_concs):
            if row_idx >= len(col):
                break
            dest = col[row_idx]

            target_2x_ligand = 2.0 * target_ligand

            # Compute stock volume from high-conc ligand; if <20 uL, fall back to low stock (10x more dilute)
            vol_stock_high = 0.0
            if ligand_stock_conc > 0:
                vol_stock_high = target_2x_ligand * total_well_vol_ul / ligand_stock_conc

            use_low = vol_stock_high < 20.0

            if use_low:
                effective_stock_conc = ligand_stock_conc / 10.0
            else:
                effective_stock_conc = ligand_stock_conc

            vol_stock_ul = 0.0
            if effective_stock_conc > 0:
                vol_stock_ul = target_2x_ligand * total_well_vol_ul / effective_stock_conc
            vol_buffer_ul = max(0.0, total_well_vol_ul - vol_stock_ul)

            # Perform transfer with single-channel pipette
            p300_single.pick_up_tip()

            # Ligand stock addition
            if vol_stock_ul > 0:
                if use_low:
                    get_volume_from_single(p300_single, ligand_stock_low, ligand_low_remaining_ref,
                                            vol_stock_ul, dest)
                else:
                    get_volume_from_single(p300_single, ligand_stock_high, ligand_high_remaining_ref,
                                            vol_stock_ul, dest)

            # Low salt buffer for dilutions from low_salt_sources pool, but single-channel now.
            remaining_total_low = sum(low_salt_remaining[w] for w in low_salt_sources)
            if vol_buffer_ul > remaining_total_low:
                raise RuntimeError('Not enough low salt buffer remaining for ligand dilutions.')

            remaining_needed = vol_buffer_ul
            while remaining_needed > 0:
                src = None
                for w in low_salt_sources:
                    if low_salt_remaining[w] > 0:
                        src = w
                        break
                if src is None:
                    raise RuntimeError('Not enough low salt buffer remaining for ligand dilutions.')

                chunk = min(p300_single.max_volume, remaining_needed, low_salt_remaining[src])
                if chunk <= 0:
                    break
                p300_single.aspirate(chunk, src)
                p300_single.dispense(chunk, dest)
                low_salt_remaining[src] -= chunk
                remaining_needed -= chunk

            # Mix final well
            mix_vol_single = min(p300_single.max_volume, total_well_vol_ul)
            if mix_vol_single > 0:
                p300_single.mix(5, mix_vol_single, dest)
            p300_single.drop_tip()

    protocol.comment('Protocol complete.')
