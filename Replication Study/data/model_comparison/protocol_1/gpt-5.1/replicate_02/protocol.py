from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Preparation',
    'author': 'User',
    'description': 'Templated protocol to prepare salt and ligand gradients using reservoirs and a deep well mixing plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# -------------------- Placeholders --------------------
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder; fall back to default for simulation if unreplaced.

    cast is applied to a float(value), so that both ints and floats are accepted.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder into a list of numbers.

    If the placeholder is unreplaced, return a copy of default for simulation.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # -------------------- Parameter parsing --------------------
    # Use conservative worst-case defaults for simulation only. On the actual robot
    # these values are replaced by the wizard.
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)

    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0.0, 50.0, 100.0, 150.0]
    )
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0]
    )

    n_salt = parse_int(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concs))
    n_ligand = parse_int(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concs))

    # Logical check (do not raise: just warn so simulation can still run)
    if replicates * n_salt > 12:
        protocol.comment(
            'WARNING: REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS exceeds 12; '
            'Reservoir 3 has only 12 wells.'
        )

    # -------------------- Labware setup --------------------
    # Slot 1: Cytiva 96 filter plate (custom). Use a 96-well fallback for simulation only.
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

    # Tip racks 300 uL
    tips_single_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)  # slot 4
    tips_multi = protocol.load_labware('opentrons_96_tiprack_300ul', 7)      # slot 7
    tips_single_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)  # slot 10

    # Reservoirs
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Slot 3, Reservoir 4 (2x salt)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Slot 6, Reservoir 3 (working salt)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Slot 8, Reservoir 2 (low & high salt)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Slot 9, Reservoir 1 (ligand stocks & buffers)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Slot 5, Reservoir 0 (low salt)

    # Mixing plate: NEST 96 Deep-Well Plate 2 mL on slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------- Pipettes --------------------
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tips_single_1, tips_single_2]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tips_multi]
    )

    # -------------------- Reagent pools and volume tracking --------------------
    # All volumes tracked in uL. Each specified reservoir well initially holds 14 mL.
    START_VOLUME_PER_WELL_UL = 14000.0

    # Low salt buffer wells across Reservoir 2 (0-5), Reservoir 1 (2-6), Reservoir 0 (0-11)
    low_salt_wells = []
    # Reservoir 2 wells 0-5: low salt buffer
    for idx in range(6):
        low_salt_wells.append([reservoir2.wells()[idx], START_VOLUME_PER_WELL_UL])
    # Reservoir 1 wells 2-6: low salt buffer
    for idx in range(2, 7):
        low_salt_wells.append([reservoir1.wells()[idx], START_VOLUME_PER_WELL_UL])
    # Reservoir 0 wells 0-11: low salt buffer
    for idx in range(12):
        low_salt_wells.append([reservoir0.wells()[idx], START_VOLUME_PER_WELL_UL])

    # High salt buffer wells across Reservoir 2 (6-11) and Reservoir 1 (7-11)
    high_salt_wells = []
    # Reservoir 2 wells 6-11: high salt buffer
    for idx in range(6, 12):
        high_salt_wells.append([reservoir2.wells()[idx], START_VOLUME_PER_WELL_UL])
    # Reservoir 1 wells 7-11: high salt buffer
    for idx in range(7, 12):
        high_salt_wells.append([reservoir1.wells()[idx], START_VOLUME_PER_WELL_UL])

    # Ligand stocks in Reservoir 1
    ligand_stock_high = [reservoir1.wells()[0], START_VOLUME_PER_WELL_UL]  # high concentration
    ligand_stock_low = [reservoir1.wells()[1], START_VOLUME_PER_WELL_UL]   # 10x lower concentration

    # -------------------- Helper functions --------------------
    def draw_from_pool(pipette, pool, volume_per_channel_ul, dest, mix_after=False, source_name='pool'):
        """Draw volume_per_channel_ul (per channel) from a pooled reagent into dest.

        For an 8-channel pipette on a single-row reservoir, one aspiration of
        V uL per channel removes 8 * V uL from the source well.
        This helper splits across wells and pipette max volume as needed.
        """
        channels = pipette.channels
        total_request = volume_per_channel_ul * channels

        remaining_needed = total_request
        pool_index = 0

        if total_request <= 0:
            # Nothing to do; skip mixing as well to avoid zero-volume mix warnings.
            return

        if not pipette.has_tip:
            pipette.pick_up_tip()

        while remaining_needed > 0 and pool_index < len(pool):
            well, remaining_vol = pool[pool_index]
            if remaining_vol <= 0:
                pool_index += 1
                continue

            take = min(remaining_vol, remaining_needed)
            if take <= 0:
                pool_index += 1
                continue

            # Effective volume per channel for this source well
            vol_per_channel_this_source = take / channels

            # Chunk into pipette.max_volume per channel
            remaining_this_source = vol_per_channel_this_source
            while remaining_this_source > 0:
                chunk = min(remaining_this_source, pipette.max_volume)
                pipette.aspirate(chunk, well)
                pipette.dispense(chunk, dest)
                remaining_this_source -= chunk

            remaining_needed -= take
            pool[pool_index][1] = remaining_vol - take

        if remaining_needed > 0:
            protocol.comment(
                f'WARNING: Not enough volume in {source_name} pool to fulfill request '
                f'(short by {remaining_needed:.1f} uL total).'
            )

        if mix_after and volume_per_channel_ul > 0:
            mix_vol = min(pipette.max_volume, volume_per_channel_ul)
            pipette.mix(3, mix_vol, dest)

    # -------------------- Step 2: Working salt buffers in Reservoir 3 --------------------
    # For each salt concentration in SALT_CONCENTRATIONS, create REPLICATES wells
    # in Reservoir 3, each with 10 mL total (low + high salt).
    total_volume_reservoir_well_ul = 10000.0  # 10 mL

    # Simple mixing model: desired_salt = f * stock_high + (1-f) * low, with low = 0.
    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, 1000.0)

    dest_index = 0
    for conc in salt_concs[:n_salt]:
        frac_high = conc / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        high_vol = total_volume_reservoir_well_ul * frac_high
        low_vol = total_volume_reservoir_well_ul - high_vol

        for _ in range(replicates):
            if dest_index >= 12:
                protocol.comment(
                    'WARNING: Not enough wells in Reservoir 3 for all '
                    'salt concentration replicates.'
                )
                break

            dest_well = reservoir3.wells()[dest_index]
            dest_index += 1

            # Use multi-channel pipette with tips from slot 7
            # Low salt first, then high salt; mix after high salt is added.
            draw_from_pool(
                p300_multi,
                low_salt_wells,
                low_vol / 8.0,
                dest_well,
                mix_after=False,
                source_name='low salt'
            )
            draw_from_pool(
                p300_multi,
                high_salt_wells,
                high_vol / 8.0,
                dest_well,
                mix_after=True,
                source_name='high salt'
            )

        if dest_index >= 12:
            break

    if p300_multi.has_tip:
        p300_multi.drop_tip()

    # -------------------- Step 3: 2x salt buffers in Reservoir 4 --------------------
    # For each salt concentration, prepare a 2x buffer in one well of Reservoir 4.
    dest_index = 0
    for conc in salt_concs[:n_salt]:
        target_conc = 2.0 * conc
        frac_high = target_conc / salt_stock_conc if salt_stock_conc > 0 else 0.0
        frac_high = max(0.0, min(1.0, frac_high))
        high_vol = total_volume_reservoir_well_ul * frac_high
        low_vol = total_volume_reservoir_well_ul - high_vol

        if dest_index >= 12:
            protocol.comment(
                'WARNING: Not enough wells in Reservoir 4 for all 2x salt concentrations.'
            )
            break

        dest_well = reservoir4.wells()[dest_index]
        dest_index += 1

        draw_from_pool(
            p300_multi,
            low_salt_wells,
            low_vol / 8.0,
            dest_well,
            mix_after=False,
            source_name='low salt'
        )
        draw_from_pool(
            p300_multi,
            high_salt_wells,
            high_vol / 8.0,
            dest_well,
            mix_after=True,
            source_name='high salt'
        )

    if p300_multi.has_tip:
        p300_multi.drop_tip()

    # -------------------- Step 4: Ligand dilutions in mixing plate --------------------
    # For each salt concentration -> one column of mixing_plate.
    # For each ligand concentration -> one row (A-H) in that column.
    # Total volume per well: TOTAL_VOLUME/2 * REPLICATES * 1.5
    per_well_total_volume = (total_volume / 2.0) * replicates * 1.5

    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0)

    # Track remaining volumes of ligand stocks (for warnings only)
    ligand_high_remaining = ligand_stock_high[1]
    ligand_low_remaining = ligand_stock_low[1]

    def choose_ligand_stock(required_volume_ul):
        """Choose high vs low ligand stock based on required volume.

        If the volume from high stock would be < 20 uL, use the low
        concentration stock instead and log a comment. The actual volume
        adjustment for 10x lower concentration must be handled by the
        upstream calculation that set required_volume_ul.
        """
        if required_volume_ul < 20.0:
            protocol.comment(
                'Using low concentration ligand stock due to small required volume '
                'from high stock; ensure calculation accounts for 10x lower '
                'concentration.'
            )
            return ligand_stock_low
        else:
            return ligand_stock_high

    def draw_from_ligand_stock(volume_ul, dest):
        """Draw volume_ul from whichever ligand stock is appropriate into dest."""
        nonlocal ligand_high_remaining, ligand_low_remaining

        if volume_ul <= 0:
            return

        source = choose_ligand_stock(volume_ul)
        source_well = source[0]

        # Update remaining volumes for logging / sanity checks
        if source_well == ligand_stock_high[0]:
            if ligand_high_remaining < volume_ul:
                protocol.comment('WARNING: Not enough high-concentration ligand stock.')
            ligand_high_remaining -= volume_ul
        else:
            if ligand_low_remaining < volume_ul:
                protocol.comment('WARNING: Not enough low-concentration ligand stock.')
            ligand_low_remaining -= volume_ul

        if not p300_single.has_tip:
            p300_single.pick_up_tip()

        remaining = volume_ul
        while remaining > 0:
            chunk = min(remaining, p300_single.max_volume)
            p300_single.aspirate(chunk, source_well)
            p300_single.dispense(chunk, dest)
            remaining -= chunk

    # Low salt buffer for ligand dilutions: Reservoir 1, well 2 (low salt buffer)
    low_salt_for_ligand = reservoir1.wells()[2]

    max_columns = min(n_salt, 12)
    max_rows = min(n_ligand, 8)  # rows A-H

    for salt_idx in range(max_columns):
        column = mixing_plate.columns()[salt_idx]

        for row_idx in range(max_rows):
            lig_conc = ligand_concs[row_idx]
            dest_well = column[row_idx]

            # Compute ligand vs buffer volumes to achieve 2x ligand concentration.
            # Simple proportional model relative to stock concentration.
            if ligand_stock_conc > 0:
                ligand_volume = per_well_total_volume * (2.0 * lig_conc / ligand_stock_conc)
            else:
                ligand_volume = 0.0

            ligand_volume = max(0.0, min(per_well_total_volume, ligand_volume))
            buffer_volume = per_well_total_volume - ligand_volume

            # Add buffer first
            if buffer_volume > 0:
                if not p300_single.has_tip:
                    p300_single.pick_up_tip()

                remaining_buffer = buffer_volume
                while remaining_buffer > 0:
                    chunk = min(remaining_buffer, p300_single.max_volume)
                    p300_single.aspirate(chunk, low_salt_for_ligand)
                    p300_single.dispense(chunk, dest_well)
                    remaining_buffer -= chunk

            # Then add ligand stock
            if ligand_volume > 0:
                draw_from_ligand_stock(ligand_volume, dest_well)

            # Mix the final dilution (avoid zero volume mix)
            mix_vol = min(p300_single.max_volume, per_well_total_volume * 0.5)
            if mix_vol > 0 and p300_single.has_tip:
                p300_single.mix(3, mix_vol, dest_well)

            # New tip for each well to avoid cross-contamination
            if p300_single.has_tip:
                p300_single.drop_tip()

    protocol.comment(
        'Templated salt and ligand gradient preparation complete. '
        'Replace [[...]] placeholders with actual values before running on the robot.'
    )
