from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Preparation Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand gradients in reservoirs and deep-well plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (kept as literal strings for the template engine)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if s still contains an unsubstituted [[TOKEN]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse scalar placeholder as float-like, falling back for simulation.

    The default should be the *largest plausible* value so that simulation
    exercises the worst case for volumes and tip usage.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    """Parse placeholder as int, with worst-case default for simulation."""
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse semicolon-separated list placeholder into list[cast]."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


# Each reservoir well initially contains 14 mL
INITIAL_RESERVOIR_VOLUME_UL = 14000.0


def run(protocol: protocol_api.ProtocolContext):
    import math

    # -------------------------------------------------------------------------
    # 1) Resolve placeholders into numeric parameters (with simulation defaults)
    # -------------------------------------------------------------------------
    # Use upper-bound defaults to stress-test volumes and tips in simulation.
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=3)
    total_volume_scalar = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=200.0)

    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0.0, 50.0, 100.0, 150.0]
    )
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        default=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    )

    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, default=1000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, default=1000.0)

    n_salt = parse_int(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, default=len(salt_concs))
    n_ligand = parse_int(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, default=len(ligand_concs))

    # Ensure consistency if count placeholders disagree with the lists
    if n_salt != len(salt_concs):
        n_salt = len(salt_concs)
    if n_ligand != len(ligand_concs):
        n_ligand = len(ligand_concs)

    # Reservoir 3 has 12 wells total for step 2
    if replicates * n_salt > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must be <= 12 (Reservoir 3 wells).')

    # -------------------------------------------------------------------------
    # 2) Deck layout: load labware
    # -------------------------------------------------------------------------
    # Slot 1: Custom Cytiva 96 filter plate, with simulation fallback
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

    # Tipracks
    tiprack_single_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_single_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # Reservoir 4 (empty)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # Reservoir 0 (low salt)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # Reservoir 3 (empty)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # Reservoir 2 (low & high salt)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # Reservoir 1 (ligand + buffers)

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------------------------------------------------------
    # 3) Pipettes
    # -------------------------------------------------------------------------
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_single_slot4, tiprack_single_slot10]
    )

    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_multi_slot7]
    )

    # -------------------------------------------------------------------------
    # 4) Define reagent pools and track remaining volumes
    # -------------------------------------------------------------------------
    # Low salt buffer pool (14 mL per well) from:
    # - Reservoir 0: wells 0–11 (all low salt)
    # - Reservoir 2: wells 0–5 (low salt)
    # - Reservoir 1: wells 2–6 (low salt)
    low_salt_sources = (
        [reservoir0.wells()[i] for i in range(12)] +
        [reservoir2.wells()[i] for i in range(6)] +
        [reservoir1.wells()[i] for i in range(2, 7)]
    )

    # High salt buffer pool (14 mL per well) from:
    # - Reservoir 2: wells 6–11 (high salt)
    # - Reservoir 1: wells 7–11 (high salt)
    high_salt_sources = (
        [reservoir2.wells()[i] for i in range(6, 12)] +
        [reservoir1.wells()[i] for i in range(7, 12)]
    )

    low_salt_remaining = {well: INITIAL_RESERVOIR_VOLUME_UL for well in low_salt_sources}
    high_salt_remaining = {well: INITIAL_RESERVOIR_VOLUME_UL for well in high_salt_sources}

    # Ligand stock wells (Reservoir 1)
    ligand_high_stock_well = reservoir1.wells()[0]  # high concentration
    ligand_low_stock_well = reservoir1.wells()[1]   # 10x lower concentration
    ligand_high_remaining = INITIAL_RESERVOIR_VOLUME_UL
    ligand_low_remaining = INITIAL_RESERVOIR_VOLUME_UL

    # -------------------------------------------------------------------------
    # 5) Helper functions for volume budgeting and dispensing
    # -------------------------------------------------------------------------
    def allocate_from_pool(pool_remaining: dict, volume_per_channel_ul: float, total_channels: int = 8):
        """Allocate a per-channel volume from a pool of reservoir wells.

        volume_per_channel_ul is the volume requested per pipette channel.
        For an 8-channel pipette, the total volume is volume_per_channel_ul * 8.

        Returns a list of (well, total_volume_taken) tuples.
        Raises if the pool cannot satisfy the total request.
        """
        total_volume_ul = volume_per_channel_ul * total_channels
        assigned = []
        remaining_to_allocate = total_volume_ul
        wells_in_order = list(pool_remaining.keys())

        for well in wells_in_order:
            if remaining_to_allocate <= 0:
                break
            avail = pool_remaining[well]
            if avail <= 0:
                continue
            take = min(avail, remaining_to_allocate)
            pool_remaining[well] -= take
            assigned.append((well, take))
            remaining_to_allocate -= take

        if remaining_to_allocate > 0:
            raise RuntimeError(
                'Not enough volume in pool to allocate requested volume %.1f uL '
                '(short by %.1f uL)' % (total_volume_ul, remaining_to_allocate)
            )
        return assigned

    def multi_channel_dispense_from_pool(pipette, pool_remaining: dict, per_channel_volume_ul: float, dest_well):
        """Deliver per_channel_volume_ul into dest_well using an 8-channel pipette.

        This respects both the reservoir pool limit and the pipette tip capacity.
        It will pick up a *single* tip for the entire destination well, using
        multiple source wells or multiple aspirate/dispense cycles as needed.
        """
        moves = allocate_from_pool(pool_remaining, per_channel_volume_ul, total_channels=8)

        # Total volume per destination well
        total_needed = per_channel_volume_ul * 8.0
        # Ensure we have one tip for the whole fill of this destination
        if not pipette.has_tip:
            pipette.pick_up_tip()

        for source_well, taken_total_ul in moves:
            remaining_for_this = taken_total_ul
            while remaining_for_this > 0:
                # Chunk by pipette capacity per channel
                chunk_total = min(pipette.max_volume * 8, remaining_for_this)
                chunk_per_channel = chunk_total / 8.0
                pipette.aspirate(chunk_per_channel, source_well)
                pipette.dispense(chunk_per_channel, dest_well)
                remaining_for_this -= chunk_total

        # Do not drop tip here; caller decides when to drop for potential reuse.

    def compute_low_high_volumes(target_conc, stock_conc, total_volume_ul):
        """Given target concentration and stock, return (low_vol, high_vol).

        Simple C1V1 = C2V2 mixing where high stock is at stock_conc and
        low buffer has 0 of the solute.
        """
        if stock_conc <= 0:
            return total_volume_ul, 0.0
        high_vol = (target_conc / stock_conc) * total_volume_ul
        if high_vol < 0:
            high_vol = 0.0
        if high_vol > total_volume_ul:
            high_vol = total_volume_ul
        low_vol = total_volume_ul - high_vol
        return low_vol, high_vol

    # -------------------------------------------------------------------------
    # 6) Step 2: Prepare salt buffers in Reservoir 3 (replicates)
    # -------------------------------------------------------------------------
    protocol.comment('Step 2: Preparing salt buffers in Reservoir 3 (replicates).')

    total_volume_reservoir_well_ul = 10000.0  # 10 mL per reservoir well
    per_channel_total_ul = total_volume_reservoir_well_ul / 8.0

    # Reservoir 3 wells (ascending salt concentration with well index)
    res3_wells = reservoir3.wells()[: replicates * n_salt]

    for i_salt in range(n_salt):
        target_conc = salt_concs[i_salt]
        low_vol_total_ul, high_vol_total_ul = compute_low_high_volumes(
            target_conc, salt_stock_conc, total_volume_reservoir_well_ul
        )
        low_per_channel_ul = low_vol_total_ul / 8.0
        high_per_channel_ul = high_vol_total_ul / 8.0

        for r in range(replicates):
            dest_index = i_salt * replicates + r
            dest_well = res3_wells[dest_index]

            # Use one tip per destination well, reusing across low/high additions
            if not p300_multi.has_tip:
                p300_multi.pick_up_tip()

            # Add low salt buffer
            if low_per_channel_ul > 0:
                protocol.comment(
                    f'Adding low-salt buffer for salt conc {target_conc} to Reservoir 3 well {dest_well}.'
                )
                multi_channel_dispense_from_pool(
                    p300_multi, low_salt_remaining, low_per_channel_ul, dest_well
                )

            # Add high salt buffer
            if high_per_channel_ul > 0:
                protocol.comment(
                    f'Adding high-salt buffer for salt conc {target_conc} to Reservoir 3 well {dest_well}.'
                )
                multi_channel_dispense_from_pool(
                    p300_multi, high_salt_remaining, high_per_channel_ul, dest_well
                )

            # Mix the well
            mix_vol = min(300, per_channel_total_ul / 2.0)
            if mix_vol > 0:
                p300_multi.mix(5, mix_vol, dest_well)

            p300_multi.drop_tip()

    # -------------------------------------------------------------------------
    # 7) Step 3: Prepare 2x salt buffers in Reservoir 4
    # -------------------------------------------------------------------------
    protocol.comment('Step 3: Preparing 2x salt buffers in Reservoir 4.')

    res4_wells = reservoir4.wells()[:n_salt]

    for i_salt in range(n_salt):
        target_conc_2x = salt_concs[i_salt] * 2.0
        low_vol_total_ul, high_vol_total_ul = compute_low_high_volumes(
            target_conc_2x, salt_stock_conc, total_volume_reservoir_well_ul
        )
        low_per_channel_ul = low_vol_total_ul / 8.0
        high_per_channel_ul = high_vol_total_ul / 8.0
        dest_well = res4_wells[i_salt]

        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        if low_per_channel_ul > 0:
            protocol.comment(
                f'Adding low-salt buffer for 2x salt conc {target_conc_2x} to Reservoir 4 well {dest_well}.'
            )
            multi_channel_dispense_from_pool(
                p300_multi, low_salt_remaining, low_per_channel_ul, dest_well
            )

        if high_per_channel_ul > 0:
            protocol.comment(
                f'Adding high-salt buffer for 2x salt conc {target_conc_2x} to Reservoir 4 well {dest_well}.'
            )
            multi_channel_dispense_from_pool(
                p300_multi, high_salt_remaining, high_per_channel_ul, dest_well
            )

        mix_vol = min(300, per_channel_total_ul / 2.0)
        if mix_vol > 0:
            p300_multi.mix(5, mix_vol, dest_well)

        p300_multi.drop_tip()

    # -------------------------------------------------------------------------
    # 8) Step 4: Prepare 2x ligand dilutions in the mixing plate
    # -------------------------------------------------------------------------
    protocol.comment('Step 4: Preparing 2x ligand dilutions in mixing plate.')

    # Total volume for each well: TOTAL_VOLUME/2 * REPLICATES * 1.5
    total_per_well_ul = (total_volume_scalar / 2.0) * replicates * 1.5

    # Ensure plate has at least 8 rows (A–H)
    if len(mixing_plate.rows()) < 8:
        raise RuntimeError('Mixing plate must have at least 8 rows (A-H).')

    max_columns = math.ceil(n_salt)
    if max_columns > 12:
        raise RuntimeError('Number of salt concentrations exceeds plate column capacity (12).')

    # Minimum useful volume from high ligand stock to prefer high over low
    ligand_high_min_stock_ul = 20.0

    for i_salt in range(n_salt):
        if i_salt >= 12:
            break
        col = mixing_plate.columns()[i_salt]

        for i_ligand in range(n_ligand):
            # Only rows A–H (0–7) are used
            if i_ligand >= 8:
                break

            target_conc_2x = ligand_concs[i_ligand] * 2.0
            dest_well = col[i_ligand]

            # Compute volumes for high ligand stock at ligand_stock_conc
            low_buf_vol_ul, ligand_stock_vol_ul = compute_low_high_volumes(
                target_conc_2x, ligand_stock_conc, total_per_well_ul
            )

            # Decide whether to use high or 10x lower stock
            use_high_stock = ligand_stock_vol_ul >= ligand_high_min_stock_ul
            if not use_high_stock:
                # Scale volume appropriately for 10x lower concentration
                ligand_stock_vol_ul = ligand_stock_vol_ul * 10.0

            # Adjust buffer to maintain total volume
            low_buf_vol_ul = total_per_well_ul - ligand_stock_vol_ul

            protocol.comment(
                f'Preparing ligand conc {target_conc_2x} in well {dest_well} '
                f'(salt condition index {i_salt}).'
            )

            # 8a) Add low salt buffer (all dilutions with low salt buffer)
            remaining_low_buf = low_buf_vol_ul
            while remaining_low_buf > 0:
                chunk = min(p300_single.max_volume, remaining_low_buf)
                if not p300_single.has_tip:
                    p300_single.pick_up_tip()
                low_salt_well_for_ligand = reservoir1.wells()[2]  # low-salt buffer in Reservoir 1
                p300_single.aspirate(chunk, low_salt_well_for_ligand)
                p300_single.dispense(chunk, dest_well)
                remaining_low_buf -= chunk

            if p300_single.has_tip:
                p300_single.drop_tip()

            # 8b) Add ligand stock solution (high or low)
            remaining_ligand = ligand_stock_vol_ul
            while remaining_ligand > 0:
                chunk = min(p300_single.max_volume, remaining_ligand)
                if not p300_single.has_tip:
                    p300_single.pick_up_tip()
                if use_high_stock:
                    p300_single.aspirate(chunk, ligand_high_stock_well)
                else:
                    p300_single.aspirate(chunk, ligand_low_stock_well)
                p300_single.dispense(chunk, dest_well)
                remaining_ligand -= chunk

            if p300_single.has_tip:
                p300_single.drop_tip()

            # 8c) Mix final solution in the destination well
            p300_single.pick_up_tip()
            mix_vol = min(300, total_per_well_ul / 2.0)
            if mix_vol > 0:
                p300_single.mix(5, mix_vol, dest_well)
            p300_single.drop_tip()

    protocol.comment('Protocol complete.')
