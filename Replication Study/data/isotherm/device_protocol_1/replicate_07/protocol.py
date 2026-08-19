from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Preparation (Templated)',
    'author': 'User',
    'description': 'Templated protocol to prepare salt and ligand gradients using reservoirs and a deep-well mixing plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONC = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONC = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_N_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_N_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


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
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # -------------------------------------------------------------------------
    # Parse placeholders with conservative defaults for simulation
    # -------------------------------------------------------------------------
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # uL per assay well
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0.0, 50.0, 100.0, 150.0])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [0.0, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0])
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONC, 1000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, 1000.0)
    n_salt = parse_int(PLACEHOLDER_N_SALT, len(salt_concs))
    n_lig = parse_int(PLACEHOLDER_N_LIG, len(ligand_concs))

    # Ensure counts do not exceed what lists provide in simulation
    salt_concs = salt_concs[:n_salt]
    ligand_concs = ligand_concs[:n_lig]
    n_salt = len(salt_concs)
    n_lig = len(ligand_concs)

    if replicates * n_salt > 12:
        raise RuntimeError('replicates x number of salt concentrations must not exceed 12 (reservoir wells).')

    # -------------------------------------------------------------------------
    # Load labware
    # -------------------------------------------------------------------------
    # Slot 1: custom Cytiva 96 filter plate with fallback for simulation only
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tip racks
    tips_300_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tips_300_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tips_300_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # Reservoir 0
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # Reservoir 1
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # Reservoir 2
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # Reservoir 3 (to be prepared)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # Reservoir 4 (to be prepared)

    # Mixing plate (NEST 96 Deep-Well Plate 2 mL)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------------------------------------------------------
    # Load pipettes
    # -------------------------------------------------------------------------
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tips_300_slot10]
    )

    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tips_300_slot7]
    )

    # -------------------------------------------------------------------------
    # Helper functions for volume tracking and multi-channel moves
    # -------------------------------------------------------------------------
    INITIAL_RES_VOL = 14000.0  # 14 mL in uL

    # Track remaining volumes in high-salt wells (reservoir1 & reservoir2)
    res1_remaining = {i: INITIAL_RES_VOL for i in range(12)}  # reservoir1 in slot 9
    res2_remaining = {i: INITIAL_RES_VOL for i in range(12)}  # reservoir2 in slot 8

    def multi_channel_move(pipette, total_volume_ul, source_pools, dest_well):
        """Move total_volume_ul (per destination well) from a pool of reservoir wells
        into a single destination reservoir well using a multi-channel pipette.

        - total_volume_ul: final volume in DESTINATION well (uL), per channel.
        - source_pools: list of (labware, remaining_dict, indices) with indices
          specifying well order to draw from.

        This function respects both pipette capacity and remaining stock volume.
        One aspirate of `chunk` uL per channel removes `8 * chunk` uL from source.
        """
        remaining_to_move = total_volume_ul

        # Flatten all source wells into a simple ordered list
        flat_sources = []  # list of (labware, remaining_dict, idx)
        for lab, remaining_dict, indices in source_pools:
            for idx in indices:
                flat_sources.append((lab, remaining_dict, idx))

        src_i = 0
        while remaining_to_move > 0:
            # Find next source with remaining volume
            while src_i < len(flat_sources) and flat_sources[src_i][1][flat_sources[src_i][2]] <= 0:
                src_i += 1
            if src_i >= len(flat_sources):
                raise RuntimeError('Not enough buffer volume available to complete the requested transfer.')

            lab, remaining_dict, idx = flat_sources[src_i]
            src_well = lab.wells()[idx]
            available_total = remaining_dict[idx]        # uL total in well
            available_per_channel = available_total / 8.0

            # Chunk size is limited by pipette, request, and source availability
            chunk = min(pipette.max_volume, remaining_to_move, available_per_channel)
            if chunk <= 0:
                src_i += 1
                continue

            if not pipette.has_tip:
                pipette.pick_up_tip()

            pipette.aspirate(chunk, src_well)
            remaining_dict[idx] -= chunk * 8.0
            pipette.dispense(chunk, dest_well)

            remaining_to_move -= chunk

        # Caller decides if tip is dropped or kept; this helper does not drop.

    # -------------------------------------------------------------------------
    # Step 1 & 2: Prepare salt buffers in Reservoir 3 (replicates for each conc)
    # -------------------------------------------------------------------------
    protocol.comment('Preparing salt buffers with replicates in Reservoir 3.')

    # Low-salt pool: reservoir0 (all 12 wells, effectively unlimited for simulation)
    low_salt_pools = [
        (reservoir0, {i: float('inf') for i in range(12)}, list(range(12)))
    ]

    # High-salt pools: wells 6–11 in reservoir2 and 7–11 in reservoir1
    high_salt_pools = [
        (reservoir2, res2_remaining, list(range(6, 12))),
        (reservoir1, res1_remaining, list(range(7, 12)))
    ]

    total_well_volume_ul = 10000.0  # 10 mL per reservoir well
    dest_index = 0

    for salt_c in salt_concs:
        # Required fraction of high-salt stock to reach salt_c
        frac_high = salt_c / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        vol_high_ul = total_well_volume_ul * frac_high
        vol_low_ul = total_well_volume_ul - vol_high_ul

        for _ in range(replicates):
            if dest_index >= 12:
                raise RuntimeError('Not enough wells in Reservoir 3 for all salt buffers.')

            dest = reservoir3.wells()[dest_index]

            # Add low-salt (no depletion tracking)
            if vol_low_ul > 0:
                remaining = vol_low_ul
                while remaining > 0:
                    chunk = min(p300_multi.max_volume, remaining)
                    if not p300_multi.has_tip:
                        p300_multi.pick_up_tip()
                    # Always take low-salt from well 0 of reservoir0 for reproducibility
                    p300_multi.aspirate(chunk, reservoir0.wells()[0])
                    p300_multi.dispense(chunk, dest)
                    remaining -= chunk

            # Add high-salt from tracked pools
            if vol_high_ul > 0:
                multi_channel_move(p300_multi, vol_high_ul, high_salt_pools, dest)

            # Mix the destination well
            if not p300_multi.has_tip:
                p300_multi.pick_up_tip()
            mix_vol = min(p300_multi.max_volume, total_well_volume_ul / 10.0)
            p300_multi.mix(5, mix_vol, dest)
            p300_multi.drop_tip()

            dest_index += 1

    # -------------------------------------------------------------------------
    # Step 3: Prepare 2x salt buffers in Reservoir 4 (one well per salt conc)
    # -------------------------------------------------------------------------
    protocol.comment('Preparing 2x salt buffers in Reservoir 4.')

    total_well_volume_2x_ul = 10000.0
    dest_index_2x = 0

    for salt_c in salt_concs:
        if dest_index_2x >= 12:
            raise RuntimeError('Not enough wells in Reservoir 4 for all 2x salt buffers.')

        dest = reservoir4.wells()[dest_index_2x]

        # Target is 2x salt concentration
        target_conc_2x = 2.0 * salt_c
        frac_high = target_conc_2x / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        vol_high_ul = total_well_volume_2x_ul * frac_high
        vol_low_ul = total_well_volume_2x_ul - vol_high_ul

        # Add low-salt
        if vol_low_ul > 0:
            remaining = vol_low_ul
            while remaining > 0:
                chunk = min(p300_multi.max_volume, remaining)
                if not p300_multi.has_tip:
                    p300_multi.pick_up_tip()
                p300_multi.aspirate(chunk, reservoir0.wells()[0])
                p300_multi.dispense(chunk, dest)
                remaining -= chunk

        # Add high-salt from tracked pools
        if vol_high_ul > 0:
            multi_channel_move(p300_multi, vol_high_ul, high_salt_pools, dest)

        # Mix the destination well
        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()
        mix_vol = min(p300_multi.max_volume, total_well_volume_2x_ul / 10.0)
        p300_multi.mix(5, mix_vol, dest)
        p300_multi.drop_tip()

        dest_index_2x += 1

    # -------------------------------------------------------------------------
    # Step 4: Prepare ligand dilutions (2x ligand concentrations) in mixing plate
    # -------------------------------------------------------------------------
    protocol.comment('Preparing ligand dilutions in mixing plate.')

    # Total volume per mixing-plate well
    well_total_vol_ul = (total_volume / 2.0) * replicates * 1.5

    high_stock_well = reservoir1.wells()[0]  # high-concentration ligand stock
    low_stock_well = reservoir1.wells()[1]   # 10x lower ligand stock

    for salt_idx in range(n_salt):
        if salt_idx >= 12:
            raise RuntimeError('Not enough columns in mixing plate for all salt concentrations.')

        col = mixing_plate.columns()[salt_idx]

        for lig_idx, target_conc in enumerate(ligand_concs):
            if lig_idx >= 8:
                break  # only rows A–H

            dest_well = col[lig_idx]

            # Desired 2x ligand concentration in this well
            target_conc_2x = 2.0 * target_conc

            # First attempt with high stock concentration
            if ligand_stock_conc > 0:
                vol_lig_high = well_total_vol_ul * (target_conc_2x / ligand_stock_conc)
            else:
                vol_lig_high = 0.0

            # Decide stock source based on minimum 20 uL criterion
            if vol_lig_high < 20.0:
                eff_low_conc = ligand_stock_conc / 10.0 if ligand_stock_conc > 0 else 0.0
                if eff_low_conc > 0:
                    lig_volume_ul = well_total_vol_ul * (target_conc_2x / eff_low_conc)
                else:
                    lig_volume_ul = 0.0
                stock_well = low_stock_well
            else:
                lig_volume_ul = vol_lig_high
                stock_well = high_stock_well

            lig_volume_ul = max(0.0, min(lig_volume_ul, well_total_vol_ul))
            buffer_volume_ul = well_total_vol_ul - lig_volume_ul

            # Add ligand stock
            if lig_volume_ul > 0:
                p300_single.pick_up_tip()
                remaining = lig_volume_ul
                while remaining > 0:
                    chunk = min(p300_single.max_volume, remaining)
                    p300_single.aspirate(chunk, stock_well)
                    p300_single.dispense(chunk, dest_well)
                    remaining -= chunk
                p300_single.drop_tip()

            # Add low-salt buffer for dilution
            if buffer_volume_ul > 0:
                p300_single.pick_up_tip()
                remaining = buffer_volume_ul
                low_salt_well_for_ligand = reservoir0.wells()[0]
                while remaining > 0:
                    chunk = min(p300_single.max_volume, remaining)
                    p300_single.aspirate(chunk, low_salt_well_for_ligand)
                    p300_single.dispense(chunk, dest_well)
                    remaining -= chunk
                # Mix the final dilution
                mix_vol = min(p300_single.max_volume, well_total_vol_ul / 3.0)
                p300_single.mix(3, mix_vol, dest_well)
                p300_single.drop_tip()

    protocol.comment('Templated salt and ligand gradient preparation complete.')
