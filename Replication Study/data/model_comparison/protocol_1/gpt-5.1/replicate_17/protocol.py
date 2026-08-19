from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Preparation (Templated)',
    'author': 'User',
    'description': 'Prepare salt buffers in reservoirs and ligand dilutions in a deep-well plate using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUM_SALT_CONCS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIGAND_CONCS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    s2 = str(s).strip()
    return s2.startswith('[' * 2) and s2.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
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
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ------------------------------------------------------------------
    # Resolve placeholders for simulation (defaults are WORST-CASE-ish)
    # ------------------------------------------------------------------
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # uL per final assay well

    salt_concentrations = parse_list(PLACEHOLDER_SALT_CONCS, [0.0, 50.0, 100.0, 150.0])
    ligand_concentrations = parse_list(PLACEHOLDER_LIGAND_CONCS, [0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0])

    num_salt_concs = parse_int(PLACEHOLDER_NUM_SALT_CONCS, len(salt_concentrations))
    num_ligand_concs = parse_int(PLACEHOLDER_NUM_LIGAND_CONCS, len(ligand_concentrations))

    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK, 1000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK, 1000.0)

    # Sanity: trim lists if placeholder counts smaller
    salt_concentrations = salt_concentrations[:num_salt_concs]
    ligand_concentrations = ligand_concentrations[:num_ligand_concs]

    # Volume constants
    reservoir_start_volume_ul = 14000.0  # 14 mL per reservoir well
    target_reservoir_volume_ul = 10000.0  # 10 mL per prepared buffer well

    # Volume for mixing plate wells: TOTAL_VOLUME/2 * REPLICATES * 1.5
    mixing_well_volume_ul = total_volume / 2.0 * replicates * 1.5

    # ------------------------------------------------------------------
    # Labware setup
    # ------------------------------------------------------------------
    # Slot 1: Custom filter plate
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tip racks
    tiprack_multi_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)
    # Extra tiprack (not explicitly assigned to a pipette but available if needed)
    extra_tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 4)

    # Reservoirs
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # low salt buffer
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # ligands & buffers
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # low & high salt
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # to be prepared (salt buffers)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # to be prepared (2x salt buffers)

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ------------------------------------------------------------------
    # Pipettes
    # ------------------------------------------------------------------
    p300_single = protocol.load_instrument(
        'p300_single_gen2', 'right', tip_racks=[tiprack_single_1, extra_tiprack]
    )

    p300_multi = protocol.load_instrument(
        'p300_multi_gen2', 'left', tip_racks=[tiprack_multi_1]
    )

    # ------------------------------------------------------------------
    # Helper: track reservoir volumes for low/high salt and ligand stocks
    # ------------------------------------------------------------------
    # Pools are dicts: key = well, value = remaining volume in uL

    def make_pool(wells):
        return {w: reservoir_start_volume_ul for w in wells}

    # Low salt buffer sources (0 mM): many wells across reservoirs 0, 1, 2
    low_salt_wells = [
        # reservoir2 wells 0-5 are low salt
        *reservoir2.wells()[0:6],
        # reservoir1 wells 2-6 are low salt
        *reservoir1.wells()[2:7],
        # reservoir0 wells 0-11 are low salt
        *reservoir0.wells()[0:12],
    ]
    low_salt_pool = make_pool(low_salt_wells)

    # High salt buffer sources: reservoir2 wells 6-11 and reservoir1 wells 7-11
    high_salt_wells = [
        *reservoir2.wells()[6:12],
        *reservoir1.wells()[7:12],
    ]
    high_salt_pool = make_pool(high_salt_wells)

    # Ligand stock high and low
    ligand_high_pool = {reservoir1.wells()[0]: reservoir_start_volume_ul}
    ligand_low_pool = {reservoir1.wells()[1]: reservoir_start_volume_ul}

    # ------------------------------------------------------------------
    # Helper functions to draw from pooled wells with tip-volume chunking
    # ------------------------------------------------------------------

    def draw_from_pool(pipette, pool, volume_ul, dest, mix_after=None):
        """Draw total volume_ul into dest, splitting across source wells and
        pipette max volume as needed. Assumes dest is a single well.
        """
        remaining = float(volume_ul)
        max_vol = pipette.max_volume

        while remaining > 0:
            # Select next source well with remaining volume
            source_well = None
            for w, v in pool.items():
                if v > 0:
                    source_well = w
                    break
            if source_well is None:
                raise RuntimeError('Not enough volume in pool to complete request of %.1f uL' % volume_ul)

            available = pool[source_well]
            # Well can contribute at most "available"; take the smaller of remaining and available
            take = min(remaining, available, max_vol)

            pipette.aspirate(take, source_well)
            pipette.dispense(take, dest)
            if mix_after is not None:
                times, vol = mix_after
                pipette.mix(times, vol, dest)

            pool[source_well] -= take
            remaining -= take

    # ------------------------------------------------------------------
    # STEP 2: Prepare salt buffers in Reservoir 3 (1x concentrations)
    # ------------------------------------------------------------------

    protocol.comment('Step 2: Preparing salt buffers in Reservoir 3 (1x).')

    # Ensure we do not exceed 12 wells in reservoir3
    if replicates * num_salt_concs > 12:
        raise RuntimeError('replicates * number_of_salt_concentrations exceeds 12 wells in Reservoir 3.')

    # For each salt concentration, allocate REPLICATES wells in ascending order
    # For a target concentration C_target from a stock C_stock, fraction from stock = C_target / C_stock
    # and fraction from 0 buffer = 1 - that. Volume is 10 mL total per well.

    p300_multi.pick_up_tip()

    dest_index = 0
    for c in salt_concentrations[:num_salt_concs]:
        frac_high = c / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        frac_low = 1.0 - frac_high

        vol_high_per_well = target_reservoir_volume_ul * frac_high
        vol_low_per_well = target_reservoir_volume_ul * frac_low

        for _ in range(replicates):
            dest_well = reservoir3.wells()[dest_index]
            dest_index += 1

            # Low salt first
            if vol_low_per_well > 0:
                remaining = vol_low_per_well
                while remaining > 0:
                    take = min(remaining, p300_multi.max_volume)
                    # draw from low salt pool; per-channel volume is take (API handles 8 channels internally)
                    draw_from_pool(p300_multi, low_salt_pool, take, dest_well)
                    remaining -= take

            # High salt
            if vol_high_per_well > 0:
                remaining = vol_high_per_well
                while remaining > 0:
                    take = min(remaining, p300_multi.max_volume)
                    draw_from_pool(p300_multi, high_salt_pool, take, dest_well)
                    remaining -= take

            # Mix the prepared well
            mix_vol = min(p300_multi.max_volume, target_reservoir_volume_ul / 10.0)
            p300_multi.mix(5, mix_vol, dest_well)

    p300_multi.drop_tip()

    # ------------------------------------------------------------------
    # STEP 3: Prepare 2x salt buffers in Reservoir 4
    # ------------------------------------------------------------------

    protocol.comment('Step 3: Preparing 2x salt buffers in Reservoir 4.')

    if num_salt_concs > 12:
        raise RuntimeError('number_of_salt_concentrations exceeds 12 wells in Reservoir 4.')

    p300_multi.pick_up_tip()

    for idx, c in enumerate(salt_concentrations[:num_salt_concs]):
        c2x = 2.0 * c
        frac_high = c2x / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        frac_low = 1.0 - frac_high

        vol_high = target_reservoir_volume_ul * frac_high
        vol_low = target_reservoir_volume_ul * frac_low

        dest_well = reservoir4.wells()[idx]

        # Low salt portion
        if vol_low > 0:
            remaining = vol_low
            while remaining > 0:
                take = min(remaining, p300_multi.max_volume)
                draw_from_pool(p300_multi, low_salt_pool, take, dest_well)
                remaining -= take

        # High salt portion
        if vol_high > 0:
            remaining = vol_high
            while remaining > 0:
                take = min(remaining, p300_multi.max_volume)
                draw_from_pool(p300_multi, high_salt_pool, take, dest_well)
                remaining -= take

        mix_vol = min(p300_multi.max_volume, target_reservoir_volume_ul / 10.0)
        p300_multi.mix(5, mix_vol, dest_well)

    p300_multi.drop_tip()

    # ------------------------------------------------------------------
    # STEP 4: Prepare 2x ligand dilutions in the mixing plate
    # ------------------------------------------------------------------

    protocol.comment('Step 4: Preparing 2x ligand dilutions in mixing plate.')

    # One column per salt concentration, one row per ligand concentration.
    # Ensure we do not exceed plate dimensions (8 rows, 12 columns)
    if num_ligand_concs > 8:
        raise RuntimeError('More than 8 ligand concentrations will not fit in rows A-H.')
    if num_salt_concs > 12:
        raise RuntimeError('More than 12 salt concentrations will not fit in columns 1-12.')

    # For each salt concentration (column), for each ligand concentration (row),
    # prepare volume = mixing_well_volume_ul with 2x ligand concentration.

    for salt_idx in range(num_salt_concs):
        for lig_idx, lig_conc in enumerate(ligand_concentrations[:num_ligand_concs]):
            row_name = 'ABCDEFGH'[lig_idx]  # 0->A,1->B,...
            col_name = str(salt_idx + 1)   # columns 1..num_salt_concs
            dest_well = mixing_plate[f"{row_name}{col_name}"]

            # Desired 2x ligand concentration in this well: 2 * lig_conc
            target_lig_2x = 2.0 * lig_conc

            # Decide whether to use high- or low-concentration ligand stock.
            # Compute required volume from high stock; if <20 uL use low stock.
            vol_from_high = mixing_well_volume_ul * (target_lig_2x / ligand_stock_conc) if ligand_stock_conc > 0 else 0.0

            if vol_from_high >= 20.0:
                stock_pool = ligand_high_pool
                stock_conc = ligand_stock_conc
            else:
                stock_pool = ligand_low_pool
                stock_conc = ligand_stock_conc / 10.0

            # Recalculate volume from chosen stock concentration
            vol_from_stock = mixing_well_volume_ul * (target_lig_2x / stock_conc) if stock_conc > 0 else 0.0
            vol_from_stock = max(0.0, min(mixing_well_volume_ul, vol_from_stock))

            vol_buffer = mixing_well_volume_ul - vol_from_stock
            if vol_buffer < 0:
                vol_buffer = 0.0

            # Use low salt buffer for the remaining volume (from the low_salt_pool)

            # Add buffer first
            if vol_buffer > 0:
                p300_single.pick_up_tip()
                draw_from_pool(p300_single, low_salt_pool, vol_buffer, dest_well)
                p300_single.drop_tip()

            # Then add ligand stock
            if vol_from_stock > 0:
                p300_single.pick_up_tip()
                draw_from_pool(p300_single, stock_pool, vol_from_stock, dest_well)
                # Mix after ligand addition
                mix_vol = min(p300_single.max_volume, mixing_well_volume_ul / 2.0)
                p300_single.mix(5, mix_vol, dest_well)
                p300_single.drop_tip()

    protocol.comment('Protocol complete.')
