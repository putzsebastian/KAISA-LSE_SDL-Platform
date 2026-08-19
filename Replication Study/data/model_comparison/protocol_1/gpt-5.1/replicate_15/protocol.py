from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Dilution Setup',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand dilutions'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (must appear as literal strings for the template engine)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a value is still an unreplaced [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder to float (then cast), with a simulation fallback.

    The fallback MUST represent a realistic upper-bound so that simulation
    stresses volumes and tip usage.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder.

    Example: "0;100;200;300" -> [0.0, 100.0, 200.0, 300.0]
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):

    # ------------------------------------------------------------------
    # 1) Read and interpret placeholders (with simulation fallbacks)
    # ------------------------------------------------------------------
    # Use worst-case style defaults so simulation stresses capacity.
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)

    # Example default lists represent realistic upper bounds (8 ligand concs, 4 salt concs)
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 100, 200, 300])
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0.1, 1, 10, 100, 500, 1000, 2000, 5000]
    )

    n_salt = parse_int(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concs))
    n_ligand = parse_int(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concs))

    # Truncate to the requested counts
    salt_concs = salt_concs[:n_salt]
    ligand_concs = ligand_concs[:n_ligand]

    mixing_plate_cols_needed = n_salt
    mixing_plate_rows_needed = min(8, n_ligand)  # A–H

    if replicates * n_salt > 12:
        raise RuntimeError('replicates x number_of_salt_concentrations must be <= 12 for Reservoir 3.')

    protocol.comment(
        f'Simulation parameters: replicates={replicates}, total_volume={total_volume}, '
        f'n_salt={n_salt}, n_ligand={n_ligand}'
    )

    # ------------------------------------------------------------------
    # 2) Deck layout (labware)
    # ------------------------------------------------------------------
    # Slot 1: custom Cytiva filter plate with simulation fallback
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            # Real structural/layout errors must surface
            raise
        protocol.comment(
            'WARNING: custom labware not found; using a standard plate as a '
            'SIMULATION fallback only.'
        )
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tip racks
    # Slot 7: multi-channel tips
    tiprack_multi_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    # Slot 10: single-channel tips
    tiprack_single_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)
    # Slot 4: additional 300 µL tips (used as spare for multi)
    tiprack_spare = protocol.load_labware('opentrons_96_tiprack_300ul', 4)

    # Reservoirs
    # Slot 5: Reservoir 0 (all low salt)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    # Slot 9: Reservoir 1 (ligand stocks + low/high salt)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    # Slot 8: Reservoir 2 (low & high salt stocks)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    # Slot 6: Reservoir 3 (target salt buffers, to be prepared here)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    # Slot 3: Reservoir 4 (2x salt buffers, to be prepared here)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)

    # Slot 11: NEST 96 Deep-Well Plate 2 ml (Mixing Plate)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ------------------------------------------------------------------
    # 3) Pipettes
    # ------------------------------------------------------------------
    p300m = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_multi_1, tiprack_spare]
    )
    p300s = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_single_1]
    )

    # ------------------------------------------------------------------
    # 4) Buffer source pools and volume tracking
    # ------------------------------------------------------------------
    # Each reservoir well starts with 14 mL (14000 µL), and multi-channel
    # moves remove 8x the per-channel volume from a single-row reservoir.

    # Reservoir 2 (slot 8)
    LOW_SALT_WELLS_RES2 = list(range(0, 6))   # wells 0–5
    HIGH_SALT_WELLS_RES2 = list(range(6, 12)) # wells 6–11

    # Reservoir 1 (slot 9)
    LOW_SALT_WELLS_RES1 = [2, 3, 4, 5, 6]
    HIGH_SALT_WELLS_RES1 = [7, 8, 9, 10, 11]

    # Build low- and high-salt source pools (all relevant wells grouped)
    low_salt_sources = [reservoir2.wells()[i] for i in LOW_SALT_WELLS_RES2] + \
                       [reservoir1.wells()[i] for i in LOW_SALT_WELLS_RES1] + \
                       [reservoir0.wells()[i] for i in range(12)]

    high_salt_sources = [reservoir2.wells()[i] for i in HIGH_SALT_WELLS_RES2] + \
                        [reservoir1.wells()[i] for i in HIGH_SALT_WELLS_RES1]

    # Volume trackers (µL)
    low_salt_vol_remaining = {well: 14000.0 for well in low_salt_sources}
    high_salt_vol_remaining = {well: 14000.0 for well in high_salt_sources}

    # Destination wells in Reservoir 3 (for salt buffers with replicates)
    dest_res3_wells = reservoir3.wells()[:replicates * n_salt]

    # ------------------------------------------------------------------
    # Helper: allocate volume from a pooled set of reservoir wells
    # ------------------------------------------------------------------
    def allocate_from_pool(pipette, volume_per_channel_ul, pool, remaining_dict, dest_well, mix_after=False):
        """Allocate a given PER-CHANNEL volume from a pool into one destination well.

        One aspiration of V µL per channel removes 8*V from a single-row
        reservoir well. This helper walks the pool and splits across wells
        and tip-sized chunks as needed.
        """
        total_needed = volume_per_channel_ul * 8.0  # total µL in reservoir well
        remaining_to_fill = total_needed

        if pipette.has_tip is False:
            pipette.pick_up_tip()

        for src_well in pool:
            if remaining_to_fill <= 0:
                break
            available = remaining_dict.get(src_well, 0.0)
            if available <= 0:
                continue

            move_volume = min(available, remaining_to_fill)  # total from this source (all channels)
            per_channel = move_volume / 8.0

            # Tip-volume-aware chunking
            while per_channel > pipette.max_volume:
                chunk = pipette.max_volume
                pipette.aspirate(chunk, src_well)
                pipette.dispense(chunk, dest_well)
                remaining_dict[src_well] -= chunk * 8.0
                remaining_to_fill -= chunk * 8.0
                move_volume -= chunk * 8.0
                per_channel = move_volume / 8.0 if move_volume > 0 else 0

            if move_volume > 0:
                pipette.aspirate(per_channel, src_well)
                pipette.dispense(per_channel, dest_well)
                remaining_dict[src_well] -= move_volume
                remaining_to_fill -= move_volume

        if remaining_to_fill > 0:
            raise RuntimeError('Not enough volume in pool to satisfy request')

        if mix_after:
            mix_vol = min(pipette.max_volume, volume_per_channel_ul)
            pipette.mix(5, mix_vol, dest_well)

    # Total volume per reservoir well we want to end up with (10 mL)
    total_reservoir_volume_ul = 10000.0

    # ------------------------------------------------------------------
    # Step 2: Prepare salt buffers in Reservoir 3 (with replicates)
    # ------------------------------------------------------------------
    # For each salt concentration, prepare `replicates` wells of 10 mL each,
    # ascending by well index in Reservoir 3.

    for i, salt in enumerate(salt_concs[:n_salt]):
        if n_salt == 1:
            # Avoid division by zero; treat single salt conc as all low-salt by default
            high_fraction = 0.0
        else:
            # Fraction of high-salt stock to use; exact mapping of [SALT_CONC]
            # to [high/low fractions] depends on user-supplied series and
            # stock concentration; this protocol just scales linearly vs max
            high_fraction = salt / max(salt_concs)

        high_vol_total = total_reservoir_volume_ul * high_fraction
        low_vol_total = total_reservoir_volume_ul - high_vol_total

        high_per_channel = high_vol_total / 8.0
        low_per_channel = low_vol_total / 8.0

        for rep in range(replicates):
            dest_index = i * replicates + rep
            dest_well = dest_res3_wells[dest_index]
            protocol.comment(f'Preparing salt conc {salt} in Reservoir 3 well {dest_well}')

            p300m.pick_up_tip()
            if low_per_channel > 0:
                allocate_from_pool(
                    p300m, low_per_channel,
                    low_salt_sources, low_salt_vol_remaining,
                    dest_well
                )
            if high_per_channel > 0:
                allocate_from_pool(
                    p300m, high_per_channel,
                    high_salt_sources, high_salt_vol_remaining,
                    dest_well
                )

            # Mix the well after both components are in
            mix_vol = min(p300m.max_volume, total_reservoir_volume_ul / 8.0)
            p300m.mix(10, mix_vol, dest_well)
            p300m.drop_tip()

    # ------------------------------------------------------------------
    # Step 3: Prepare 2x salt buffers in Reservoir 4 (one well per conc)
    # ------------------------------------------------------------------
    dest_res4_wells = reservoir4.wells()[:n_salt]

    for i, salt in enumerate(salt_concs[:n_salt]):
        if n_salt == 1:
            high_fraction = 0.0
        else:
            # Approximate scaling: 2x target concentration vs max in list
            high_fraction = min(1.0, (2 * salt) / max(salt_concs))

        high_vol_total = total_reservoir_volume_ul * high_fraction
        low_vol_total = total_reservoir_volume_ul - high_vol_total

        high_per_channel = high_vol_total / 8.0
        low_per_channel = low_vol_total / 8.0

        dest_well = dest_res4_wells[i]
        protocol.comment(f'Preparing 2x salt conc {2 * salt} in Reservoir 4 well {dest_well}')

        p300m.pick_up_tip()
        if low_per_channel > 0:
            allocate_from_pool(
                p300m, low_per_channel,
                low_salt_sources, low_salt_vol_remaining,
                dest_well
            )
        if high_per_channel > 0:
            allocate_from_pool(
                p300m, high_per_channel,
                high_salt_sources, high_salt_vol_remaining,
                dest_well
            )

        mix_vol = min(p300m.max_volume, total_reservoir_volume_ul / 8.0)
        p300m.mix(10, mix_vol, dest_well)
        p300m.drop_tip()

    # ------------------------------------------------------------------
    # Step 4: Prepare 2x ligand dilutions in mixing plate (single-channel)
    # ------------------------------------------------------------------
    # Ligand stock sources (Reservoir 1)
    ligand_stock_high = reservoir1.wells()[0]  # high concentration [[LIGAND_STOCK_CONCENTRATION]]
    ligand_stock_low = reservoir1.wells()[1]   # low concentration [[LIGAND_STOCK_CONCENTRATION]] / 10
    low_salt_for_ligand = reservoir1.wells()[2]  # low-salt buffer for ligand dilutions

    ligand_high_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, 1000.0)

    # In mixing plate, columns correspond to salt concentrations,
    # rows A–H to increasing ligand concentration for each salt.
    mixing_wells = [
        well
        for col in mixing_plate.columns()[:mixing_plate_cols_needed]
        for well in col[:mixing_plate_rows_needed]
    ]

    # Per-well total volume: TOTAL_VOLUME/2 * REPLICATES * 1.5
    per_well_total = (total_volume / 2.0) * replicates * 1.5

    def prepare_ligand_dilution(dest_well, target_conc):
        """Prepare a 2x ligand dilution for one well in the mixing plate.

        - Use high-concentration stock unless the required stock volume
          would drop below 20 µL, in which case use the 10x-diluted stock
          in well 1 and adjust the volume accordingly.
        """
        effective_stock_conc = ligand_high_stock_conc
        stock_source = ligand_stock_high

        # For a 2x target concentration, required stock volume is:
        # V_stock = V_total * (C_target / (2 * C_stock))
        stock_volume = per_well_total * (target_conc / (2.0 * effective_stock_conc))

        # Switch to 10x-diluted stock if volume would be too small
        if stock_volume < 20.0:
            effective_stock_conc = ligand_high_stock_conc / 10.0
            stock_source = ligand_stock_low
            stock_volume = per_well_total * (target_conc / (2.0 * effective_stock_conc))

        # Enforce pipette volume range – cap at max, do not exceed
        if stock_volume > p300s.max_volume:
            stock_volume = p300s.max_volume

        buffer_volume = per_well_total - stock_volume
        if buffer_volume < 0:
            buffer_volume = 0

        p300s.pick_up_tip()

        # First add low-salt buffer
        if buffer_volume > 0:
            p300s.transfer(
                buffer_volume,
                low_salt_for_ligand,
                dest_well,
                new_tip='never'
            )

        # Then add ligand stock and mix
        if stock_volume > 0:
            p300s.transfer(
                stock_volume,
                stock_source,
                dest_well,
                new_tip='never',
                mix_after=(5, min(p300s.max_volume, stock_volume))
            )

        p300s.drop_tip()

    # Fill mixing plate row-wise (A–H) for each salt column
    idx = 0
    for lig_idx, lig_conc in enumerate(ligand_concs[:mixing_plate_rows_needed]):
        for salt_idx in range(mixing_plate_cols_needed):
            if idx >= len(mixing_wells):
                break
            dest = mixing_wells[idx]
            protocol.comment(f'Preparing ligand 2x conc {lig_conc} in well {dest}')
            prepare_ligand_dilution(dest, lig_conc)
            idx += 1
