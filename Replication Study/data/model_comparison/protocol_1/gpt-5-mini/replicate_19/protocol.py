from opentrons import protocol_api

metadata = {
    'protocolName': 'Template: Salt and Ligand Prep with Placeholders',
    'author': 'Lab 167',
    'description': 'Prepare salt series in reservoirs and ligand dilutions in a deep-well mixing plate using placeholders',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


# PLACEHOLDERS (literal strings for the wizard to replace)
REPLICATES = '[[REPLICATES]]'
TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Detect an unreplaced token by building the brackets at runtime."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        # convert default entries to the requested cast
        return [cast(x) for x in default]
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using fallback for simulation only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs: mapping user-specified reservoir numbers to slots
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (target for salt series)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (slot 8) - contains low and high salt
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (slot 9) - ligand stocks + buffers
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (slot 5) - low salt buffers

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)  # Mixing plate

    # Pipettes - give both pipettes access to all tipracks to avoid OutOfTips in simulation
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_10, tiprack_4, tiprack_7])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_4, tiprack_7, tiprack_10])

    # Parse placeholders with fallbacks (worst-case style fallback where reasonable)
    replicates = parse_scalar(REPLICATES, 3, int)
    total_volume = parse_scalar(TOTAL_VOLUME, 200, float)  # uL, fallback 200 uL

    salt_concs = parse_list(SALT_CONCENTRATIONS, ['50', '100', '200', '400'], float)
    ligand_concs = parse_list(LIGAND_CONCENTRATIONS, ['0.1', '1', '10', '100', '1000', '10000', '100000', '1000000'], float)

    salt_stock = parse_scalar(SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock = parse_scalar(LIGAND_STOCK_CONCENTRATION, 10000.0, float)

    num_salt = parse_scalar(NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concs), int)
    num_ligand = parse_scalar(NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concs), int)

    # Use actual list lengths if tokens were replaced
    if not _unreplaced(str(SALT_CONCENTRATIONS)):
        num_salt = len(salt_concs)
    if not _unreplaced(str(LIGAND_CONCENTRATIONS)):
        num_ligand = len(ligand_concs)

    # Safety checks
    if replicates * num_salt > 12:
        protocol.comment('ERROR: replicates x number_of_salt_concentrations exceeds 12 wells in Reservoir 3')
        return

    # Initialize remaining volumes in source reservoirs (uL), each well has 14 mL = 14000 uL
    def init_reservoir_volumes(reservoir):
        return {i: 14000.0 for i, _ in enumerate(reservoir.wells())}

    rem_res2 = init_reservoir_volumes(reservoir_2)  # contains low salt (wells 0-5) and high salt (6-11)
    rem_res1 = init_reservoir_volumes(reservoir_1)  # contains ligand stocks and some buffers
    rem_res0 = init_reservoir_volumes(reservoir_0)  # low salt buffers

    protocol.comment(f'Parsed: replicates={replicates}, total_volume={total_volume} uL, num_salt={num_salt}, num_ligand={num_ligand}')

    # Helper to get next source well index from a reservoir pool that has at least needed_total_uL available
    def pick_source_well(remaining_dict, needed_uL):
        for idx, vol in remaining_dict.items():
            if vol >= needed_uL:
                return idx
        # otherwise return first with any volume (we will error if none)
        for idx, vol in remaining_dict.items():
            if vol > 0:
                return idx
        return None

    # Helper to consume volume from a reservoir well (uL)
    def consume_from(res_dict, well_idx, uL):
        res_dict[well_idx] = max(0.0, res_dict[well_idx] - uL)

    # Step 2: For each salt concentration, fill replicates wells in Reservoir 3 with total 10 mL by mixing low and high salt
    total_target_uL = 10000.0  # 10 mL per well
    protocol.comment('Step 2: Creating salt dilutions in Reservoir 3 (slot 6)')

    res3_wells = reservoir_3.wells()
    res2_wells = reservoir_2.wells()
    res1_wells = reservoir_1.wells()

    res3_index = 0
    # Use multi-channel pipette and tips from tipracks
    for salt in sorted(salt_concs):
        for rep in range(replicates):
            if res3_index >= len(res3_wells):
                protocol.comment('Reservoir 3 is full, cannot place more replicates')
                break
            dest = res3_wells[res3_index]
            # compute volumes needed (uL)
            vol_high = total_target_uL * (salt / salt_stock)
            vol_low = total_target_uL - vol_high
            # per-channel volumes (what we pass to multi-channel pipette)
            per_high = vol_high / 8.0 if vol_high > 0 else 0.0
            per_low = vol_low / 8.0 if vol_low > 0 else 0.0

            protocol.comment(f'Preparing salt {salt} (replicate {rep+1}) in Reservoir3 well {res3_index+1} : high {vol_high} uL, low {vol_low} uL')

            # Choose sources (prefer reservoir_2 for both)
            needed_total_high = vol_high
            high_src = pick_source_well(rem_res2, needed_total_high)
            high_lab = reservoir_2
            if high_src is None:
                high_src = pick_source_well(rem_res1, needed_total_high)
                high_lab = reservoir_1
            if high_src is None:
                protocol.comment('ERROR: No high salt source available')
                return

            needed_total_low = vol_low
            low_src = pick_source_well(rem_res2, needed_total_low)
            low_lab = reservoir_2
            if low_src is None:
                low_src = pick_source_well(rem_res0, needed_total_low)
                low_lab = reservoir_0
            if low_src is None:
                low_src = pick_source_well(rem_res1, needed_total_low)
                low_lab = reservoir_1
            if low_src is None:
                protocol.comment('ERROR: No low salt source available')
                return

            # perform transfers with multi-channel
            p300m.pick_up_tip()
            if per_high > 0:
                src_well_high = high_lab.wells()[high_src]
                p300m.transfer(per_high, src_well_high, dest, new_tip='never')
                consume_from(rem_res2 if high_lab == reservoir_2 else rem_res1, high_src, vol_high)

            if per_low > 0:
                src_well_low = low_lab.wells()[low_src]
                p300m.transfer(per_low, src_well_low, dest, new_tip='never')
                consume_from(rem_res2 if low_lab == reservoir_2 else rem_res0 if low_lab == reservoir_0 else rem_res1, low_src, vol_low)

            mix_vol = min(p300m.max_volume, per_high + per_low)
            if mix_vol > 0:
                p300m.mix(3, mix_vol, dest)
            p300m.drop_tip()

            res3_index += 1

    # Step 3: For each salt concentration, fill 1 well in Reservoir 4 with 10 mL at 2x concentration
    protocol.comment('Step 3: Creating 2x salt solutions in Reservoir 4 (slot 3)')
    res4_wells = reservoir_4.wells()
    res4_idx = 0
    for salt in sorted(salt_concs):
        if res4_idx >= len(res4_wells):
            protocol.comment('Reservoir 4 is full, cannot place more salts')
            break
        dest = res4_wells[res4_idx]
        desired_conc = salt * 2.0
        vol_high = total_target_uL * (desired_conc / salt_stock)
        vol_low = total_target_uL - vol_high
        per_high = vol_high / 8.0 if vol_high > 0 else 0.0
        per_low = vol_low / 8.0 if vol_low > 0 else 0.0

        protocol.comment(f'Preparing 2x salt {desired_conc} (original {salt}) in Reservoir4 well {res4_idx+1} : high {vol_high} uL, low {vol_low} uL')

        # choose sources
        high_src = pick_source_well(rem_res2, vol_high) or pick_source_well(rem_res1, vol_high)
        high_lab = reservoir_2 if high_src in rem_res2 else reservoir_1
        low_src = pick_source_well(rem_res2, vol_low) or pick_source_well(rem_res0, vol_low)
        low_lab = reservoir_2 if low_src in rem_res2 else reservoir_0

        if high_src is None or low_src is None:
            protocol.comment('ERROR: Not enough source volume for Reservoir4')
            return

        p300m.pick_up_tip()
        if per_high > 0:
            src_well_h = (reservoir_2 if high_src in rem_res2 else reservoir_1).wells()[high_src]
            p300m.transfer(per_high, src_well_h, dest, new_tip='never')
            if high_src in rem_res2:
                rem_res2[high_src] = max(0.0, rem_res2[high_src] - vol_high)
            else:
                rem_res1[high_src] = max(0.0, rem_res1[high_src] - vol_high)

        if per_low > 0:
            src_well_l = (reservoir_2 if low_src in rem_res2 else reservoir_0).wells()[low_src]
            p300m.transfer(per_low, src_well_l, dest, new_tip='never')
            if low_src in rem_res2:
                rem_res2[low_src] = max(0.0, rem_res2[low_src] - vol_low)
            else:
                rem_res0[low_src] = max(0.0, rem_res0[low_src] - vol_low)

        mix_vol = min(p300m.max_volume, per_high + per_low)
        if mix_vol > 0:
            p300m.mix(3, mix_vol, dest)
        p300m.drop_tip()

        res4_idx += 2

    # Step 4: Prepare ligand dilutions in mixing plate (deep-well) using single channel pipette
    protocol.comment('Step 4: Preparing ligand dilutions in mixing plate (slot 11)')

    # Compute per-well target volume
    target_per_well = (total_volume / 2.0) * replicates * 1.5
    protocol.comment(f'Target volume per mixing well: {target_per_well} uL')

    # Ligand stock: reservoir_1 well 0 is high stock; well1 is 10x diluted stock
    ligand_high_idx = 0
    ligand_low_idx = 1

    # Ensure we only use up to 8 rows A-H for ligands
    max_rows = 8
    n_rows = min(max_rows, num_ligand)
    n_cols = num_salt

    row_letters = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']

    for col_idx in range(n_cols):
        for row_idx in range(n_rows):
            if row_idx >= len(ligand_concs):
                break
            ligand_conc = ligand_concs[row_idx]
            desired_conc = ligand_conc * 2.0  # 2x as requested
            # volume of stock needed
            vol_stock = target_per_well * (desired_conc / ligand_stock)
            stock_well_idx = ligand_high_idx
            effective_stock = ligand_stock
            # if below 20 uL, use low stock well and adjust stock concentration
            if vol_stock < 20.0:
                protocol.comment(f'Using low-concentration ligand stock for target {desired_conc} because stock volume {vol_stock:.1f} uL < 20 uL')
                effective_stock = ligand_stock / 10.0
                vol_stock = target_per_well * (desired_conc / effective_stock)
                stock_well_idx = ligand_low_idx

            vol_buffer = target_per_well - vol_stock
            if vol_buffer < 0:
                protocol.comment('ERROR: stock concentration too low to prepare target volume with given total volume')
                return

            dest_well = mixing_plate.wells_by_name()[f'{row_letters[row_idx]}{col_idx+1}']
            protocol.comment(f'Preparing ligand {ligand_conc} (2x={desired_conc}) in {row_letters[row_idx]}{col_idx+1}: stock {vol_stock:.1f} uL, buffer {vol_buffer:.1f} uL')

            # Use one tip per destination and perform both transfers
            p300s.pick_up_tip()
            stock_src = reservoir_1.wells()[stock_well_idx]
            if vol_stock > 0:
                p300s.transfer(vol_stock, stock_src, dest_well, new_tip='never')
                rem_res1[stock_well_idx] = max(0.0, rem_res1[stock_well_idx] - vol_stock)

            buffer_src = reservoir_0.wells()[0]
            if vol_buffer > 0:
                p300s.transfer(vol_buffer, buffer_src, dest_well, new_tip='never')
                rem_res0[0] = max(0.0, rem_res0[0] - vol_buffer)

            mix_vol = min(p300s.max_volume, max(50, target_per_well / 10))
            if mix_vol > 0:
                p300s.mix(3, mix_vol, dest_well)
            p300s.drop_tip()

    protocol.comment('Protocol complete')
