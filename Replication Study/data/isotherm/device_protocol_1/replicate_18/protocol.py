from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Preparation',
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
PLACEHOLDER_NUM_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(v) for v in s.split(';') if v.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # --- Parse placeholders with defaults (simulation fallbacks) ---
    # These defaults are for simulation only; on the real robot, the placeholders must be
    # substituted with values that obey the physical constraints described in the prompt.

    # Replicates and concentration lists
    num_replicates = parse_int(PLACEHOLDER_REPLICATES, 3)

    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS,
                            [0.0, 50.0, 100.0, 200.0])  # default example
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS,
                              [0.0, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0])

    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_lig = parse_int(PLACEHOLDER_NUM_LIG, len(ligand_concs))

    # Truncate lists to requested counts
    salt_concs = salt_concs[:num_salt]
    ligand_concs = ligand_concs[:num_lig]

    # NOTE: On the real robot, REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12.
    # This script does not enforce it at runtime (to allow simulation with defaults),
    # but the template engine / user must choose values that satisfy this.

    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # used for ligand dilutions
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONC, 3000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, 1000.0)

    # --- Labware ---
    # Slot 1: custom filter plate with simulation fallback
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware not found; using standard 96-well plate as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tipracks
    tiprack_multi_main = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_multi_extra = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_single_main = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs and mixing plate
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (low salt only)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (ligand + buffers)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (low/high salt stocks)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (salt working buffers)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (2x salt buffers)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # --- Pipettes ---
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_single_main]
    )

    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_multi_main, tiprack_multi_extra]
    )

    # --- Volume tracking for reservoirs (step 2 & 3) ---
    START_VOL_RESERVOIR = 14000.0  # 14 mL per reservoir well, in µL

    # Pools for low- and high-salt buffers
    # reservoir2 wells 0-5: low salt, 6-11: high salt
    # reservoir1 wells 2-6: low salt, 7-11: high salt

    low_salt_sources = []   # list of [well, remaining_volume_ul]
    high_salt_sources = []

    # Low salt pool: reservoir2 A1-A6, reservoir1 A3-A7, reservoir0 A1-A12
    for idx in range(6):
        low_salt_sources.append([reservoir2.wells()[idx], START_VOL_RESERVOIR])
    for idx in range(2, 7):
        low_salt_sources.append([reservoir1.wells()[idx], START_VOL_RESERVOIR])
    for idx in range(12):
        low_salt_sources.append([reservoir0.wells()[idx], START_VOL_RESERVOIR])

    # High salt pool: reservoir2 A7-A12, reservoir1 A8-A12
    for idx in range(6, 12):
        high_salt_sources.append([reservoir2.wells()[idx], START_VOL_RESERVOIR])
    for idx in range(7, 12):
        high_salt_sources.append([reservoir1.wells()[idx], START_VOL_RESERVOIR])

    def take_from_pool(pipette, volume_per_channel_ul, pool, description: str):
        """Aspirate volume_per_channel_ul from pooled wells, splitting across wells if needed.

        volume_per_channel_ul is PER-CHANNEL. For an 8-channel pipette on a single-row reservoir,
        each aspiration removes 8 x volume_per_channel_ul from the source well.
        This helper only performs aspiration; caller must handle dispense.
        """
        remaining_to_take = volume_per_channel_ul
        per_move_channels = 8.0 if pipette.channels == 8 else 1.0

        while remaining_to_take > 0:
            if not pool:
                raise RuntimeError(f'Not enough {description} remaining in reservoir pool.')

            src_well, remaining_vol = pool[0]
            # Maximum PER-CHANNEL volume this well can still provide
            max_per_channel_from_well = remaining_vol / per_move_channels

            # Chunk by both well capacity and pipette capacity
            take_now = min(remaining_to_take,
                           max_per_channel_from_well,
                           pipette.max_volume)

            if take_now <= 0:
                # This well is exhausted; move to the next
                pool.pop(0)
                continue

            pipette.aspirate(take_now, src_well)
            used_total = take_now * per_move_channels
            pool[0][1] -= used_total
            remaining_to_take -= take_now

    def mix_in_well(pipette, well, repetitions: int = 5, volume: float = None):
        if volume is None:
            volume = min(200.0, pipette.max_volume)
        pipette.mix(repetitions, volume, well)

    # -----------------
    # Step 2: Prepare salt buffers in Reservoir 3
    # -----------------
    total_volume_reservoir_ul = 10000.0  # 10 mL total per well

    def prep_salt_buffers_in_reservoir3():
        protocol.comment('Step 2: Preparing salt buffers in Reservoir 3 (working buffers).')

        sorted_salts = sorted(list(salt_concs))
        dest_wells = reservoir3.wells()  # 12 wells total
        dest_index = 0

        for salt in sorted_salts:
            # Fraction of high-salt stock to reach the required concentration
            frac_high = salt / salt_stock_conc if salt_stock_conc > 0 else 0.0
            if frac_high < 0:
                frac_high = 0.0
            if frac_high > 1:
                frac_high = 1.0

            # Per-channel volumes (multi-channel, 8 channels over a single-row reservoir well)
            vol_high_per_channel = total_volume_reservoir_ul * frac_high / 8.0
            vol_low_per_channel = total_volume_reservoir_ul / 8.0 - vol_high_per_channel
            if vol_low_per_channel < 0:
                vol_low_per_channel = 0.0

            for _ in range(num_replicates):
                if dest_index >= 12:
                    protocol.comment('WARNING: Not enough wells in Reservoir 3 for all requested salt buffers; remaining combinations are skipped.')
                    return

                dest = dest_wells[dest_index]
                dest_index += 1

                p300_multi.pick_up_tip()

                # Low-salt addition
                if vol_low_per_channel > 0:
                    take_from_pool(p300_multi, vol_low_per_channel, low_salt_sources, 'low salt buffer')
                    p300_multi.dispense(vol_low_per_channel, dest)

                # High-salt addition
                if vol_high_per_channel > 0:
                    take_from_pool(p300_multi, vol_high_per_channel, high_salt_sources, 'high salt buffer')
                    p300_multi.dispense(vol_high_per_channel, dest)

                # Mix the resulting buffer in the destination well
                mix_in_well(p300_multi, dest, repetitions=5, volume=min(200.0, p300_multi.max_volume))
                p300_multi.drop_tip()

    # -----------------
    # Step 3: Prepare 2x salt buffers in Reservoir 4
    # -----------------
    def prep_2x_salt_buffers_in_reservoir4():
        protocol.comment('Step 3: Preparing 2x salt buffers in Reservoir 4.')

        dest_wells = reservoir4.wells()
        sorted_salts = sorted(list(salt_concs))

        for i, salt in enumerate(sorted_salts):
            if i >= 12:
                protocol.comment('WARNING: Not enough wells in Reservoir 4 for all 2x salt buffers; extra concentrations are skipped.')
                break

            target_salt_2x = 2.0 * salt
            frac_high = target_salt_2x / salt_stock_conc if salt_stock_conc > 0 else 0.0
            if frac_high < 0:
                frac_high = 0.0
            if frac_high > 1:
                frac_high = 1.0

            vol_high_per_channel = total_volume_reservoir_ul * frac_high / 8.0
            vol_low_per_channel = total_volume_reservoir_ul / 8.0 - vol_high_per_channel
            if vol_low_per_channel < 0:
                vol_low_per_channel = 0.0

            dest = dest_wells[i]
            p300_multi.pick_up_tip()

            if vol_low_per_channel > 0:
                take_from_pool(p300_multi, vol_low_per_channel, low_salt_sources, 'low salt buffer')
                p300_multi.dispense(vol_low_per_channel, dest)

            if vol_high_per_channel > 0:
                take_from_pool(p300_multi, vol_high_per_channel, high_salt_sources, 'high salt buffer')
                p300_multi.dispense(vol_high_per_channel, dest)

            mix_in_well(p300_multi, dest, repetitions=5, volume=min(200.0, p300_multi.max_volume))
            p300_multi.drop_tip()

    # -----------------
    # Step 4: Ligand dilutions in mixing plate
    # -----------------
    def prep_ligand_dilutions_in_mixing_plate():
        protocol.comment('Step 4: Preparing ligand dilutions in the 96 deep-well mixing plate.')

        # Total volume per WELL: [[TOTAL_VOLUME]]/2 * [[REPLICATES]] * 1.5
        per_well_total_ul = float(total_volume / 2.0 * num_replicates * 1.5)

        # Ligand sources and effective stock concentration
        ligand_high_well = reservoir1.wells()[0]  # high concentration stock
        ligand_low_well = reservoir1.wells()[1]   # 10x lower concentration stock

        nonzero_ligs = [c for c in ligand_concs if c > 0]
        min_lig = min(nonzero_ligs) if nonzero_ligs else 0.0

        using_low_stock = False
        if min_lig > 0 and ligand_stock_conc > 0:
            # Approximate required stock volume for the smallest concentration
            vol_stock_for_min = per_well_total_ul * (min_lig / (2.0 * ligand_stock_conc))
            if vol_stock_for_min < 20.0:
                using_low_stock = True

        if using_low_stock:
            protocol.comment('Using low concentration ligand stock in Reservoir 1 well 1 (10x lower).')
            ligand_source = ligand_low_well
            effective_stock_conc = ligand_stock_conc / 10.0
        else:
            protocol.comment('Using high concentration ligand stock in Reservoir 1 well 0.')
            ligand_source = ligand_high_well
            effective_stock_conc = ligand_stock_conc

        low_salt_for_ligand = reservoir1.wells()[2]  # low salt buffer for ligand dilutions

        rows = mixing_plate.rows()  # A-H

        # We place ligand concentrations ascending row-wise (A-H) for each salt concentration column.
        if num_lig > 8:
            protocol.comment('WARNING: More than 8 ligand concentrations requested; only the first 8 are used (rows A-H).')
        used_lig_concs = ligand_concs[:min(num_lig, 8)]

        for col_index in range(num_salt):
            if col_index >= 12:
                protocol.comment('WARNING: Not enough columns in mixing plate for all salt concentrations; extra columns are skipped.')
                break

            for row_index, lig_c in enumerate(used_lig_concs):
                dest = rows[row_index][col_index]

                # Required final concentration = 2x the given target ligand concentration
                target_conc = 2.0 * lig_c

                if effective_stock_conc > 0 and target_conc > 0:
                    frac_stock = target_conc / effective_stock_conc
                else:
                    frac_stock = 0.0

                if frac_stock < 0:
                    frac_stock = 0.0
                if frac_stock > 1:
                    frac_stock = 1.0

                vol_stock = per_well_total_ul * frac_stock
                vol_buffer = per_well_total_ul - vol_stock

                p300_single.pick_up_tip()

                # Add low salt buffer first
                if vol_buffer > 0:
                    remaining = vol_buffer
                    while remaining > 0:
                        chunk = min(remaining, p300_single.max_volume)
                        if chunk <= 0:
                            break
                        p300_single.aspirate(chunk, low_salt_for_ligand)
                        p300_single.dispense(chunk, dest)
                        remaining -= chunk

                # Add ligand stock
                if vol_stock > 0:
                    remaining = vol_stock
                    while remaining > 0:
                        chunk = min(remaining, p300_single.max_volume)
                        if chunk <= 0:
                            break
                        p300_single.aspirate(chunk, ligand_source)
                        p300_single.dispense(chunk, dest)
                        remaining -= chunk

                # Mix the final dilution in the destination well
                mix_in_well(p300_single, dest, repetitions=5, volume=min(100.0, p300_single.max_volume))
                p300_single.drop_tip()

    # Execute protocol steps in order
    prep_salt_buffers_in_reservoir3()
    prep_2x_salt_buffers_in_reservoir4()
    prep_ligand_dilutions_in_mixing_plate()
