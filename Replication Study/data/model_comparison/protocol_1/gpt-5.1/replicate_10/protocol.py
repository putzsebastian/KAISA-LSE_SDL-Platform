from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Preparation',
    'author': 'User',
    'description': 'Templated protocol to prepare salt buffers and ligand dilutions using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONC = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONC = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUM_SALT_CONC = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIGAND_CONC = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


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


class VolumeTracker:
    """Track remaining volume in a pool of wells and allocate volumes across them.

    The pool is treated as a single reagent distributed across multiple wells.
    """

    def __init__(self, wells, initial_volume_ul):
        # maintain deterministic order for allocation
        self.wells = list(wells)
        self.remaining = {well: float(initial_volume_ul) for well in wells}

    def request(self, volume_ul):
        """Return list of (well, vol) that together sum to volume_ul, or raise if short.

        This will draw down from each well in order until the requested volume is met.
        """
        needed = float(volume_ul)
        allocations = []

        for well in self.wells:
            if needed <= 0:
                break
            rem = self.remaining.get(well, 0.0)
            if rem <= 0:
                continue
            take = min(rem, needed)
            if take > 0:
                allocations.append((well, take))
                self.remaining[well] = rem - take
                needed -= take

        if needed > 0.0001:
            raise RuntimeError(
                f"Not enough volume remaining in pool to supply {volume_ul} uL; short by {needed} uL"
            )
        return allocations


def run(protocol: protocol_api.ProtocolContext):
    # Parse parameters (template placeholders fall back to safe simulation defaults)
    # NOTE: On the real robot, these placeholders are replaced before execution.

    # Choose defaults that satisfy REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS <= 12
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)

    # Default example gradients (units are arbitrary here and interpreted as fraction of stock 0.0–1.0)
    salt_concentrations = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        [0.0, 0.25, 0.5, 0.75]
    )
    ligand_concentrations = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        [0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0]
    )

    num_salt_conc = parse_int(PLACEHOLDER_NUM_SALT_CONC, min(4, len(salt_concentrations)))
    num_ligand_conc = parse_int(PLACEHOLDER_NUM_LIGAND_CONC, min(8, len(ligand_concentrations)))

    # Labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware not found; using a standard plate as a SIMULATION fallback only.'
        )
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tipracks
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (empty)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0

    # Mixing plate (NEST 96 deep-well 2 mL)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_slot10]
    )

    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_slot7]
    )

    # -------------------------------------------------------------------------
    # Volume tracking: low-salt and high-salt pools
    # Each reservoir well initially contains 14 mL = 14000 uL.
    # -------------------------------------------------------------------------

    # Reservoir 2 (slot 8):
    # wells 0–5: low salt, wells 6–11: high salt
    low_salt_wells_res2 = [reservoir2.wells()[i] for i in range(0, 6)]
    high_salt_wells_res2 = [reservoir2.wells()[i] for i in range(6, 12)]

    # Reservoir 1 (slot 9):
    # well 0: ligand high; well 1: ligand low; wells 2–6: low salt; wells 7–11: high salt
    low_salt_wells_res1 = [reservoir1.wells()[i] for i in range(2, 7)]
    high_salt_wells_res1 = [reservoir1.wells()[i] for i in range(7, 12)]

    # Reservoir 0 (slot 5): all 12 wells low salt
    low_salt_wells_res0 = list(reservoir0.wells())

    # Build global pools across all reservoirs for low- and high-salt buffers
    low_salt_pool_wells = low_salt_wells_res0 + low_salt_wells_res1 + low_salt_wells_res2
    high_salt_pool_wells = high_salt_wells_res1 + high_salt_wells_res2

    low_salt_pool = VolumeTracker(low_salt_pool_wells, 14000.0)
    high_salt_pool = VolumeTracker(high_salt_pool_wells, 14000.0)

    # Ligand stock wells (not volume-tracked in this template)
    high_ligand_stock = reservoir1.wells()[0]
    low_ligand_stock = reservoir1.wells()[1]

    # -------------------------------------------------------------------------
    # Step 2: Prepare salt buffers in Reservoir 3
    # -------------------------------------------------------------------------

    protocol.comment('Step 2: Preparing salt buffers in Reservoir 3.')

    # Reservoir 3 has 12 wells total
    if replicates * num_salt_conc > 12:
        raise RuntimeError(
            'REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS exceeds 12 wells in Reservoir 3.'
        )

    total_vol_per_well_ul = 10000.0  # 10 mL per destination well

    # Reasonable flow rates for large-volume buffer moves
    p300_multi.flow_rate.aspirate = 50
    p300_multi.flow_rate.dispense = 100

    dest_wells_res3 = reservoir3.wells()  # 12 wells, increasing index = increasing salt concentration

    current_dest_index = 0

    for conc_index in range(num_salt_conc):
        target_conc = salt_concentrations[conc_index]
        protocol.comment(
            f'Preparing salt concentration {target_conc} in Reservoir 3, {replicates} replicate(s).'
        )

        # Fraction of high-salt stock to achieve target concentration.
        # Assumes target_conc is expressed relative to the high-salt stock (0.0–1.0). Values
        # outside this range are clamped for safety.
        high_fraction = min(max(float(target_conc), 0.0), 1.0)
        low_fraction = 1.0 - high_fraction

        high_vol_per_well_ul = total_vol_per_well_ul * high_fraction
        low_vol_per_well_ul = total_vol_per_well_ul * low_fraction

        for _ in range(replicates):
            if current_dest_index >= len(dest_wells_res3):
                raise RuntimeError(
                    'Not enough wells in Reservoir 3 to allocate all replicates.'
                )

            dest = dest_wells_res3[current_dest_index]
            current_dest_index += 1

            # Allocate volumes from low- and high-salt pools for this destination well
            low_allocations = low_salt_pool.request(low_vol_per_well_ul)
            high_allocations = high_salt_pool.request(high_vol_per_well_ul)

            # Dispense low-salt components
            for src_well, vol_ul in low_allocations:
                remaining = vol_ul
                while remaining > 0:
                    chunk = min(p300_multi.max_volume, remaining)
                    p300_multi.pick_up_tip()
                    p300_multi.aspirate(chunk, src_well)
                    p300_multi.dispense(chunk, dest)
                    p300_multi.drop_tip()
                    remaining -= chunk

            # Dispense high-salt components
            for src_well, vol_ul in high_allocations:
                remaining = vol_ul
                while remaining > 0:
                    chunk = min(p300_multi.max_volume, remaining)
                    p300_multi.pick_up_tip()
                    p300_multi.aspirate(chunk, src_well)
                    p300_multi.dispense(chunk, dest)
                    p300_multi.drop_tip()
                    remaining -= chunk

            # Mix this reservoir well once both components are in
            mix_vol = min(1000.0, total_vol_per_well_ul / 2.0)
            p300_multi.pick_up_tip()
            for _ in range(5):
                p300_multi.aspirate(mix_vol, dest.bottom(2))
                p300_multi.dispense(mix_vol, dest.bottom(5))
            p300_multi.drop_tip()

    # -------------------------------------------------------------------------
    # Step 3: Prepare 2x salt buffers in Reservoir 4 (one 10 mL well per salt concentration)
    # -------------------------------------------------------------------------

    protocol.comment('Step 3: Preparing 2x salt buffers in Reservoir 4.')

    dest_wells_res4 = reservoir4.wells()

    if num_salt_conc > len(dest_wells_res4):
        raise RuntimeError('NUMBER_OF_SALT_CONCENTRATIONS exceeds wells in Reservoir 4.')

    current_dest_index = 0

    for conc_index in range(num_salt_conc):
        target_conc = salt_concentrations[conc_index]
        protocol.comment(f'Preparing 2x salt concentration {target_conc} in Reservoir 4.')

        # 2x target concentration from 0 and stock (again assumed 0.0–1.0 range)
        two_x_conc = 2.0 * float(target_conc)
        high_fraction = min(max(two_x_conc, 0.0), 1.0)
        low_fraction = 1.0 - high_fraction

        high_vol_per_well_ul = total_vol_per_well_ul * high_fraction
        low_vol_per_well_ul = total_vol_per_well_ul * low_fraction

        dest = dest_wells_res4[current_dest_index]
        current_dest_index += 1

        low_allocations = low_salt_pool.request(low_vol_per_well_ul)
        high_allocations = high_salt_pool.request(high_vol_per_well_ul)

        # Dispense low-salt components
        for src_well, vol_ul in low_allocations:
            remaining = vol_ul
            while remaining > 0:
                chunk = min(p300_multi.max_volume, remaining)
                p300_multi.pick_up_tip()
                p300_multi.aspirate(chunk, src_well)
                p300_multi.dispense(chunk, dest)
                p300_multi.drop_tip()
                remaining -= chunk

        # Dispense high-salt components
        for src_well, vol_ul in high_allocations:
            remaining = vol_ul
            while remaining > 0:
                chunk = min(p300_multi.max_volume, remaining)
                p300_multi.pick_up_tip()
                p300_multi.aspirate(chunk, src_well)
                p300_multi.dispense(chunk, dest)
                p300_multi.drop_tip()
                remaining -= chunk

        # Mix the 2x buffer well
        mix_vol = min(1000.0, total_vol_per_well_ul / 2.0)
        p300_multi.pick_up_tip()
        for _ in range(5):
            p300_multi.aspirate(mix_vol, dest.bottom(2))
            p300_multi.dispense(mix_vol, dest.bottom(5))
        p300_multi.drop_tip()

    # -------------------------------------------------------------------------
    # Step 4: Prepare ligand dilutions in the mixing plate (2x target ligand concentrations)
    # -------------------------------------------------------------------------

    protocol.comment('Step 4: Preparing ligand dilutions in mixing plate.')

    # Total volume for each well in the mixing plate
    vol_per_well_ul = (total_volume / 2.0) * replicates * 1.5

    rows = mixing_plate.rows()  # rows[0] = A, ..., rows[7] = H

    if num_ligand_conc > 8:
        raise RuntimeError(
            'NUMBER_OF_LIGAND_CONCENTRATIONS must not exceed 8 (rows A-H).'
        )

    if num_salt_conc > 12:
        raise RuntimeError(
            'NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 (columns 1-12).'
        )

    # For each salt concentration, create a column of ligand dilutions.
    # Row index encodes ligand concentration; column index encodes salt concentration.

    for salt_index in range(num_salt_conc):
        for lig_index in range(num_ligand_conc):
            target_lig_conc = ligand_concentrations[lig_index]

            row = rows[lig_index]  # 0..7 => A..H
            dest_well = row[salt_index]  # column index = salt_index

            protocol.comment(
                f'Preparing ligand dilution at salt index {salt_index}, '
                f'ligand index {lig_index}, 2x based on {target_lig_conc}.'
            )

            # For templating, use a generic 10% ligand stock / 90% buffer split by volume.
            stock_fraction = 0.1
            ligand_stock_volume_ul = vol_per_well_ul * stock_fraction
            buffer_volume_ul = vol_per_well_ul - ligand_stock_volume_ul

            # Choose high vs low ligand stock based on 20 uL threshold per well
            use_low_stock = ligand_stock_volume_ul < 20.0
            ligand_source = low_ligand_stock if use_low_stock else high_ligand_stock

            # 1) Add low-salt buffer from the pooled low-salt buffer wells
            low_buffer_allocations = low_salt_pool.request(buffer_volume_ul)

            for src_well, vol_ul in low_buffer_allocations:
                remaining = vol_ul
                while remaining > 0:
                    chunk = min(p300_single.max_volume, remaining)
                    p300_single.pick_up_tip()
                    p300_single.aspirate(chunk, src_well)
                    p300_single.dispense(chunk, dest_well)
                    p300_single.drop_tip()
                    remaining -= chunk

            # 2) Add ligand stock from the selected stock well
            remaining = ligand_stock_volume_ul
            while remaining > 0:
                chunk = min(p300_single.max_volume, remaining)
                p300_single.pick_up_tip()
                p300_single.aspirate(chunk, ligand_source)
                p300_single.dispense(chunk, dest_well)
                p300_single.drop_tip()
                remaining -= chunk

            # 3) Mix each well after ligand addition
            mix_vol = min(200.0, vol_per_well_ul / 2.0)
            p300_single.pick_up_tip()
            for _ in range(5):
                p300_single.aspirate(mix_vol, dest_well.bottom(1))
                p300_single.dispense(mix_vol, dest_well.bottom(3))
            p300_single.drop_tip()

    protocol.comment('Protocol complete.')
