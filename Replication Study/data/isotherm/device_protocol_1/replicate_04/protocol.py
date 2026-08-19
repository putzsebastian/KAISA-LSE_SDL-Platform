from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Titration Setup',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand titrations in reservoirs and deep-well plate.'
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
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return [cast(v) for v in default]
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ----------------------
    # Parse placeholders
    # ----------------------
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 100.0)

    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0.0, 50.0, 100.0, 150.0],
        cast=float,
    )
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        default=[0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0],
        cast=float,
    )

    num_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    num_ligand = parse_int(PLACEHOLDER_NUM_LIGAND, len(ligand_concs))

    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONC, 1000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, 1000.0)

    # Use only the first num_* entries in the concentration lists
    salt_concs = salt_concs[:num_salt]
    ligand_concs = ligand_concs[:num_ligand]

    if replicates * num_salt > 12:
        raise ValueError(
            f"replicates x number_of_salt_concentrations ({replicates} x {num_salt}) exceeds 12 reservoir wells"
        )

    # ----------------------
    # Labware setup
    # ----------------------
    # Custom filter plate in slot 1 with simulation fallback
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware not found; using a standard NEST 96-well plate as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tip racks
    tiprack_multi_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)
    tiprack_extra_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)

    # Reservoirs
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # all low salt
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # ligand stocks + buffers
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # low + high salt stock buffers
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # empty, to be filled with final salt buffers
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # empty, to be filled with 2x salt buffers

    # Mixing deep-well plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ----------------------
    # Pipettes
    # ----------------------
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        'right',
        tip_racks=[tiprack_single_1, tiprack_extra_1],
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        'left',
        tip_racks=[tiprack_multi_1, tiprack_extra_1],
    )

    # ----------------------
    # Reagent pools and volume tracking (µL)
    # ----------------------
    INITIAL_RESERVOIR_VOL_UL = 14000.0

    # Pools as mutable [well, remaining_volume] entries so we can update in place
    low_salt_sources = []
    high_salt_sources = []

    # Reservoir2 wells 0-5: low salt; 6-11: high salt
    for i in range(6):
        low_salt_sources.append([reservoir2.wells()[i], INITIAL_RESERVOIR_VOL_UL])
    for i in range(6, 12):
        high_salt_sources.append([reservoir2.wells()[i], INITIAL_RESERVOIR_VOL_UL])

    # Reservoir1: low salt in wells 2-6, high salt in 7-11
    for i in range(2, 7):
        low_salt_sources.append([reservoir1.wells()[i], INITIAL_RESERVOIR_VOL_UL])
    for i in range(7, 12):
        high_salt_sources.append([reservoir1.wells()[i], INITIAL_RESERVOIR_VOL_UL])

    # Reservoir0: all low salt
    for i in range(12):
        low_salt_sources.append([reservoir0.wells()[i], INITIAL_RESERVOIR_VOL_UL])

    # Ligand stocks (reservoir1 wells 0 and 1)
    ligand_high_source = [reservoir1.wells()[0], INITIAL_RESERVOIR_VOL_UL]
    ligand_low_source = [reservoir1.wells()[1], INITIAL_RESERVOIR_VOL_UL]

    # ----------------------
    # Helper: pooled volume transfer (single or multi-channel)
    # ----------------------
    def pull_from_pool(pool, volume_per_channel, pipette, dest, mix_after=None):
        """Take volume_per_channel µL per channel from a pooled source into dest.

        pool: list of [well, remaining_vol_ul]
        volume_per_channel: volume in µL per channel (multi) or per tip (single)
        pipette: instrument
        dest: target location
        mix_after: optional (repetitions, volume) tuple passed to transfer()
        """
        total_channels = 8 if pipette.channels == 8 else 1
        total_needed = volume_per_channel * total_channels
        remaining_needed = total_needed

        for entry in pool:
            well, remaining = entry
            if remaining <= 0:
                continue

            take = min(remaining, remaining_needed)
            if take <= 0:
                continue

            vol_per_tip = take / total_channels

            if vol_per_tip > pipette.max_volume:
                # chunk aspiration/dispense
                chunk_total = pipette.max_volume * total_channels
                while take > 0:
                    this_chunk_total = min(chunk_total, take)
                    this_chunk_per_tip = this_chunk_total / total_channels
                    pipette.aspirate(this_chunk_per_tip, well)
                    pipette.dispense(this_chunk_per_tip, dest)
                    take -= this_chunk_total
                    remaining_needed -= this_chunk_total
                    entry[1] -= this_chunk_total
            else:
                pipette.transfer(
                    vol_per_tip,
                    well,
                    dest,
                    new_tip='never',
                    mix_after=mix_after,
                )
                remaining_needed -= take
                entry[1] -= take

            if remaining_needed <= 0:
                break

        if remaining_needed > 0:
            raise RuntimeError('Not enough volume left in pooled sources to complete transfer.')

    # ----------------------
    # Step 2: Prepare salt buffers in Reservoir 3 (1x)
    # ----------------------
    per_well_total_ul = 10000.0  # 10 mL per reservoir well

    dest_wells_res3 = reservoir3.wells()  # 12 wells

    if not p300_multi.has_tip:
        p300_multi.pick_up_tip()

    well_index = 0
    for salt_idx, salt_conc in enumerate(salt_concs[:num_salt]):
        # Compute low/high volumes for 1x final concentration
        high_vol_total = (salt_conc * per_well_total_ul) / salt_stock_conc
        high_vol_total = max(0.0, min(per_well_total_ul, high_vol_total))
        low_vol_total = per_well_total_ul - high_vol_total

        for _ in range(replicates):
            if well_index >= len(dest_wells_res3):
                raise RuntimeError('Not enough wells in Reservoir 3 for all salt buffers.')
            dest = dest_wells_res3[well_index]
            well_index += 1

            high_vol_per_channel = high_vol_total / 8.0
            low_vol_per_channel = low_vol_total / 8.0

            if high_vol_total > 0:
                pull_from_pool(high_salt_sources, high_vol_per_channel, p300_multi, dest)
            if low_vol_total > 0:
                pull_from_pool(low_salt_sources, low_vol_per_channel, p300_multi, dest)

            # Mix the well with multi-channel
            p300_multi.mix(5, min(300.0, per_well_total_ul / 8.0), dest)

    p300_multi.drop_tip()

    # ----------------------
    # Step 3: Prepare 2x salt buffers in Reservoir 4
    # ----------------------
    dest_wells_res4 = reservoir4.wells()

    if not p300_multi.has_tip:
        p300_multi.pick_up_tip()

    for idx, salt_conc in enumerate(salt_concs[:num_salt]):
        if idx >= len(dest_wells_res4):
            raise RuntimeError('Not enough wells in Reservoir 4 for all 2x salt buffers.')
        dest = dest_wells_res4[idx]

        target_conc_2x = 2.0 * salt_conc
        high_vol_total = (target_conc_2x * per_well_total_ul) / salt_stock_conc
        high_vol_total = max(0.0, min(per_well_total_ul, high_vol_total))
        low_vol_total = per_well_total_ul - high_vol_total

        high_vol_per_channel = high_vol_total / 8.0
        low_vol_per_channel = low_vol_total / 8.0

        if high_vol_total > 0:
            pull_from_pool(high_salt_sources, high_vol_per_channel, p300_multi, dest)
        if low_vol_total > 0:
            pull_from_pool(low_salt_sources, low_vol_per_channel, p300_multi, dest)

        p300_multi.mix(5, min(300.0, per_well_total_ul / 8.0), dest)

    p300_multi.drop_tip()

    # ----------------------
    # Step 4: Ligand dilutions in mixing plate (2x ligand)
    # ----------------------
    # Total volume per well: [[TOTAL_VOLUME]]/2 * [[REPLICATES]] * 1.5
    per_well_total_mix_ul = (total_volume / 2.0) * replicates * 1.5

    # Decide which ligand stock to use for each target concentration
    def choose_ligand_stock(target_conc):
        """Return (source_ref, stock_conc) for this target concentration.

        Use high stock unless volume from high stock would be < 20 µL total; then use low.
        """
        vol_from_high = per_well_total_mix_ul * target_conc / (2.0 * ligand_stock_conc)
        if vol_from_high < 20.0:
            # Use low stock, 10x more dilute
            return ligand_low_source, ligand_stock_conc / 10.0
        else:
            return ligand_high_source, ligand_stock_conc

    # Helper to draw from ligand stock, tracking volume
    def pull_ligand(source_entry, volume_ul, dest):
        well, remaining = source_entry
        if volume_ul <= 0:
            return
        if volume_ul > remaining:
            raise RuntimeError('Not enough ligand stock volume.')

        if volume_ul > p300_single.max_volume:
            remaining_needed = volume_ul
            while remaining_needed > 0:
                chunk = min(p300_single.max_volume, remaining_needed)
                p300_single.aspirate(chunk, well)
                p300_single.dispense(chunk, dest)
                remaining_needed -= chunk
        else:
            p300_single.transfer(volume_ul, well, dest, new_tip='never')

        source_entry[1] -= volume_ul

    # Low salt buffer make-up volume with single channel from pooled low-salt sources
    def pull_low_salt_single(volume_ul, dest):
        remaining_needed = volume_ul
        for entry in low_salt_sources:
            well, remaining = entry
            if remaining <= 0:
                continue
            take = min(remaining, remaining_needed)
            if take <= 0:
                continue

            if take > p300_single.max_volume:
                rem = take
                while rem > 0:
                    chunk = min(p300_single.max_volume, rem)
                    p300_single.aspirate(chunk, well)
                    p300_single.dispense(chunk, dest)
                    rem -= chunk
            else:
                p300_single.transfer(take, well, dest, new_tip='never')

            remaining_needed -= take
            entry[1] -= take
            if remaining_needed <= 0:
                break

        if remaining_needed > 0:
            raise RuntimeError('Not enough low salt buffer volume in pool for single-channel.')

    # Perform the ligand dilutions
    if not p300_single.has_tip:
        p300_single.pick_up_tip()

    # For each salt concentration -> one column (0..num_salt-1)
    # Within each column, ascending ligand concentrations A..H (use up to 8)
    for col_idx in range(num_salt):
        for row_idx, ligand_conc in enumerate(ligand_concs[:8]):  # rows A-H
            well = mixing_plate.rows()[row_idx][col_idx]

            # Target concentration in the well is 2x the given ligand concentration
            target_conc_2x = 2.0 * ligand_conc

            # Choose stock (high or low) based on volume requirement
            ligand_source_entry, stock_conc_used = choose_ligand_stock(target_conc_2x)

            # C1 * V1 = C2 * V2 => V1 = C2 * V2 / C1
            ligand_vol_total = (target_conc_2x * per_well_total_mix_ul) / stock_conc_used
            ligand_vol_total = max(0.0, min(per_well_total_mix_ul, ligand_vol_total))
            buffer_vol_total = per_well_total_mix_ul - ligand_vol_total

            # Add ligand, then buffer, then mix
            pull_ligand(ligand_source_entry, ligand_vol_total, well)
            if buffer_vol_total > 0:
                pull_low_salt_single(buffer_vol_total, well)

            p300_single.mix(5, min(300.0, per_well_total_mix_ul), well)

    p300_single.drop_tip()

    protocol.comment('Protocol complete.')
