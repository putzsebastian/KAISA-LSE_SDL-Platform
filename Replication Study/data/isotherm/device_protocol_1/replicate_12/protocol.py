from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Preparation',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand gradients in reservoirs and deepwell plate.'
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
        return default
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
    return [cast(v) for v in s.split(';') if v.strip()]


class ReagentPool:
    def __init__(self, wells, initial_volume_ul):
        # wells: list of Well objects
        self.wells = wells
        # map well -> remaining volume in uL
        self.remaining = {w: float(initial_volume_ul) for w in wells}

    def total_remaining(self):
        return sum(self.remaining.values())

    def dispense(self, pipette, total_per_channel_ul, dest_well, mix_after=False, mix_volume_ul=0, mix_reps=0):
        """Dispense total_per_channel_ul per channel into dest_well, splitting across source wells as needed.
        For an 8-channel pipette, this removes 8x volume from source reservoir wells.
        """
        channels = 8
        volume_needed_total = total_per_channel_ul * channels
        vol_left = volume_needed_total

        for src_well in self.wells:
            if vol_left <= 0:
                break
            available = self.remaining[src_well]
            if available <= 0:
                continue
            move = min(available, vol_left)
            # per-channel for this move
            per_channel = move / channels
            while per_channel > 0:
                chunk = min(per_channel, pipette.max_volume)
                actual_move = chunk * channels
                if self.remaining[src_well] < actual_move:
                    break
                pipette.aspirate(chunk, src_well)
                pipette.dispense(chunk, dest_well)
                self.remaining[src_well] -= actual_move
                per_channel -= chunk
                vol_left -= actual_move

        if vol_left > 0.1:
            raise RuntimeError(f"Not enough volume in reagent pool to dispense {volume_needed_total} uL total.")

        if mix_after and mix_reps > 0 and mix_volume_ul > 0:
            mix_vol = min(mix_volume_ul, pipette.max_volume)
            pipette.mix(mix_reps, mix_vol, dest_well)


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with conservative defaults that still exercise the protocol
    # Choose defaults that satisfy constraint replicates * n_salt <= 12
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)

    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, [0, 50, 100, 150])
    n_salt = parse_int(PLACEHOLDER_NUM_SALT, len(salt_concs))
    n_salt = min(n_salt, 4)
    salt_concs = salt_concs[:n_salt]

    # Use a moderate default total volume so that stock volumes remain <= total
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 100.0)  # per reaction total volume in uL

    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, [0, 1, 2, 4, 8, 16, 32, 64])
    n_ligand = parse_int(PLACEHOLDER_NUM_LIGAND, len(ligand_concs))
    n_ligand = min(n_ligand, 8)
    ligand_concs = ligand_concs[:n_ligand]

    salt_stock_conc = parse_scalar(PLACEHOLDER_SALT_STOCK_CONC, 1000.0)
    ligand_stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, 1000.0)

    # --- Load labware ---
    # Slot 1: custom filter plate with simulation fallback
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware not found; using NEST 96 flat plate as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tipracks
    tiprack_right = protocol.load_labware('opentrons_96_tiprack_300ul', 10)
    tiprack_multi_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_multi_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Reservoirs and mixing plate
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (empty)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # --- Pipettes ---
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_right])
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_multi_1, tiprack_multi_2])

    # --- Define reagent pools for low and high salt buffers ---
    # Each reservoir well has 14 mL = 14000 uL
    initial_reservoir_volume_ul = 14000.0

    # Low salt buffer wells: Reservoir2 A1-A6; Reservoir1 A2-A6; Reservoir0 A1-A12
    low_salt_wells = [
        reservoir2.wells()[i] for i in range(6)
    ] + [
        reservoir1.wells()[i] for i in range(2, 7)
    ] + list(reservoir0.wells())

    # High salt buffer wells: Reservoir2 A7-A12; Reservoir1 A8-A12
    high_salt_wells = [
        reservoir2.wells()[i] for i in range(6, 12)
    ] + [
        reservoir1.wells()[i] for i in range(7, 12)
    ]

    low_salt_pool = ReagentPool(low_salt_wells, initial_reservoir_volume_ul)
    high_salt_pool = ReagentPool(high_salt_wells, initial_reservoir_volume_ul)

    # Ligand stock wells in Reservoir1: high conc A1, low conc A2
    ligand_high_well = reservoir1.wells()[0]
    ligand_low_well = reservoir1.wells()[1]

    # --- Step 1: calculations and checks ---
    # For Reservoir3: replicates * n_salt <= 12
    if replicates * n_salt > 12:
        raise RuntimeError('replicates x number of salt concentrations must not exceed 12 for Reservoir 3.')

    # Total volume in each reservoir well target is 10 mL = 10000 uL
    target_reservoir_volume_ul = 10000.0

    # --- Step 2: Prepare salt buffers in Reservoir 3 using multi-channel pipette ---
    protocol.comment('Preparing salt buffers in Reservoir 3.')

    p300_multi.pick_up_tip()

    for i, salt_c in enumerate(salt_concs[:n_salt]):
        for rep in range(replicates):
            dest_index = i * replicates + rep
            dest_well = reservoir3.wells()[dest_index]

            # Calculate low and high salt volumes based on simple linear mixing
            # total volume per channel in uL
            if salt_stock_conc <= 0:
                raise RuntimeError('Salt stock concentration must be > 0.')
            frac_high = salt_c / salt_stock_conc
            frac_high = max(0.0, min(1.0, frac_high))
            frac_low = 1.0 - frac_high

            low_vol_total = target_reservoir_volume_ul * frac_low
            high_vol_total = target_reservoir_volume_ul * frac_high

            low_per_channel = low_vol_total / 8.0
            high_per_channel = high_vol_total / 8.0

            if low_per_channel > 0:
                low_salt_pool.dispense(p300_multi, low_per_channel, dest_well)
            if high_per_channel > 0:
                high_salt_pool.dispense(p300_multi, high_per_channel, dest_well)

            # Mix destination
            mix_vol = min(p300_multi.max_volume, target_reservoir_volume_ul / 10.0 / 8.0)
            p300_multi.mix(5, mix_vol, dest_well)

    p300_multi.drop_tip()

    # --- Step 3: Prepare 2x salt buffers in Reservoir 4 ---
    protocol.comment('Preparing 2x salt buffers in Reservoir 4.')

    p300_multi.pick_up_tip()

    for i, salt_c in enumerate(salt_concs[:n_salt]):
        dest_well = reservoir4.wells()[i]

        # Desired 2x concentration = 2 * salt_c
        desired_c = 2.0 * salt_c
        if salt_stock_conc <= 0:
            raise RuntimeError('Salt stock concentration must be > 0.')
        frac_high = desired_c / salt_stock_conc
        frac_high = max(0.0, min(1.0, frac_high))
        frac_low = 1.0 - frac_high

        low_vol_total = target_reservoir_volume_ul * frac_low
        high_vol_total = target_reservoir_volume_ul * frac_high

        low_per_channel = low_vol_total / 8.0
        high_per_channel = high_vol_total / 8.0

        if low_per_channel > 0:
            low_salt_pool.dispense(p300_multi, low_per_channel, dest_well)
        if high_per_channel > 0:
            high_salt_pool.dispense(p300_multi, high_per_channel, dest_well)

        mix_vol = min(p300_multi.max_volume, target_reservoir_volume_ul / 10.0 / 8.0)
        p300_multi.mix(5, mix_vol, dest_well)

    p300_multi.drop_tip()

    # --- Step 4: Prepare ligand dilutions in mixing plate using single-channel ---
    protocol.comment('Preparing ligand dilutions in mixing plate.')

    # Total volume for each well in mixing plate
    per_well_total = (total_volume / 2.0) * replicates * 1.5

    # For each salt concentration, one column in mixing plate
    for salt_idx in range(n_salt):
        col = mixing_plate.columns()[salt_idx]
        for lig_idx, lig_c in enumerate(ligand_concs):
            if lig_idx >= 8:
                break  # mixing plate has 8 rows A-H
            dest_well = col[lig_idx]

            # Desired concentration is 2x required ligand concentration
            desired_c = 2.0 * lig_c

            # Calculate ligand volume from high stock; if <20 uL, use low stock and adjust
            if ligand_stock_conc <= 0:
                raise RuntimeError('Ligand stock concentration must be > 0.')

            vol_ligand_high = per_well_total * desired_c / ligand_stock_conc if ligand_stock_conc > 0 else 0

            use_low_stock = False
            if vol_ligand_high < 20.0:
                use_low_stock = True

            if use_low_stock:
                # low stock is 10x diluted
                effective_stock = ligand_stock_conc / 10.0
                vol_ligand = per_well_total * desired_c / effective_stock
                ligand_source = ligand_low_well
            else:
                vol_ligand = vol_ligand_high
                ligand_source = ligand_high_well

            # Guard against impossible combinations only after substitution; for simulation allow
            if not _unreplaced(PLACEHOLDER_LIGAND_CONCENTRATIONS) and vol_ligand > per_well_total:
                raise RuntimeError('Ligand volume exceeds total per-well volume; check concentrations.')

            vol_ligand = min(vol_ligand, per_well_total)
            vol_buffer = max(0.0, per_well_total - vol_ligand)

            # Use reservoir0 well A1 as generic low salt buffer for ligand dilutions
            ligand_buffer_source = reservoir0.wells()[0]

            # Add buffer first, then ligand
            # Buffer
            remaining = vol_buffer
            while remaining > 0:
                chunk = min(remaining, p300_single.max_volume)
                p300_single.pick_up_tip()
                p300_single.aspirate(chunk, ligand_buffer_source)
                p300_single.dispense(chunk, dest_well)
                p300_single.mix(2, min(chunk, p300_single.max_volume), dest_well)
                p300_single.drop_tip()
                remaining -= chunk

            # Ligand
            remaining = vol_ligand
            while remaining > 0:
                chunk = min(remaining, p300_single.max_volume)
                p300_single.pick_up_tip()
                p300_single.aspirate(chunk, ligand_source)
                p300_single.dispense(chunk, dest_well)
                p300_single.mix(3, min(chunk, p300_single.max_volume), dest_well)
                p300_single.drop_tip()
                remaining -= chunk

    protocol.comment('Protocol complete.')
