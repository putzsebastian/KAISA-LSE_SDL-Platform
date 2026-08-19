from opentrons import protocol_api
import math

metadata = {
    'protocolName': 'Salt and Ligand Mixing Template',
    'author': 'User',
    'description': 'Prepare salt gradients and ligand dilutions using placeholders',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings for external substitution)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

# Helpers to detect unreplaced tokens in a simulation-safe way
def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware not found; using a standard plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_300_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 per user naming (slot 3)
    reservoir_6 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (slot 6)
    reservoir_8 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (slot 8)
    reservoir_9 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (slot 9)
    reservoir_5 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (slot 5)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes - follow user guidance: multi-channel use tips from slot 7, single-channel use tips from slot 10
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300_10])
    # allow an extra tiprack (slot 4) as simulation fallback to ensure sufficient columns if needed
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300_7, tiprack_300_4])

    # Parse placeholders with safe fallbacks for simulation
    REPLICATES = parse_scalar(PLACEHOLDER_REPLICATES, default=3, cast=int)
    TOTAL_VOLUME = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=100.0, cast=float)  # in uL
    SALT_CONCENTRATIONS = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=[0.0, 50.0, 100.0, 200.0], cast=float)
    LIGAND_CONCENTRATIONS = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, default=[0.1,1,10,100,1000,10000,0.01,0.001], cast=float)
    SALT_STOCK_CONCENTRATION = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, default=500.0, cast=float)
    LIGAND_STOCK_CONCENTRATION = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, default=100000.0, cast=float)
    NUMBER_OF_SALT_CONCENTRATIONS = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, default=len(SALT_CONCENTRATIONS), cast=int)
    NUMBER_OF_LIGAND_CONCENTRATIONS = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, default=len(LIGAND_CONCENTRATIONS), cast=int)

    # Safety checks
    if REPLICATES * NUMBER_OF_SALT_CONCENTRATIONS > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12')

    # Initialize source volume tracking (uL). Each specified source well initially has 14 mL = 14000 uL
    def make_pool(labware, indexes, start_volume=14000):
        return [{'labware': labware, 'index': i, 'remaining': start_volume} for i in indexes]

    # Define pools according to the user description
    low_salt_pool = []
    high_salt_pool = []

    # reservoir_8 wells 0-5 low, 6-11 high
    low_salt_pool += make_pool(reservoir_8, list(range(0,6)))
    high_salt_pool += make_pool(reservoir_8, list(range(6,12)))

    # reservoir_9: well0 ligand high stock, well1 ligand low stock, wells2-6 low salt, 7-11 high salt
    low_salt_pool += make_pool(reservoir_9, list(range(2,7)))
    high_salt_pool += make_pool(reservoir_9, list(range(7,12)))

    # reservoir_5 (Reservoir 0) all low salt wells 0-11
    low_salt_pool += make_pool(reservoir_5, list(range(0,12)))

    # Ligand stocks
    ligand_stock_high = {'labware': reservoir_9, 'index': 0, 'remaining': 14000}
    ligand_stock_low = {'labware': reservoir_9, 'index': 1, 'remaining': 14000}

    # Utility: get a human-readable well object
    def well_from_pool(entry):
        return entry['labware'].wells()[entry['index']]

    # Utility to consume volume from a pool for a multi-channel transfer where total volume needed = per_channel * 8
    def consume_from_pool(pool, total_needed):
        # total_needed in uL
        taken = []  # list of (entry, vol_taken)
        remaining = total_needed
        for entry in pool:
            if remaining <= 0:
                break
            avail = entry['remaining']
            take = min(avail, remaining)
            if take <= 0:
                continue
            entry['remaining'] -= take
            taken.append((entry, take))
            remaining -= take
        if remaining > 0:
            raise RuntimeError(f'Pool ran dry: need {total_needed} uL but short by {remaining} uL')
        return taken

    # Perform a multi-channel transfer from a pool of source wells into a single destination well (single-row reservoir)
    # This helper DOES NOT pick up or drop tips; caller must manage tips to allow mixing after transfers
    def multi_channel_transfer_from_pool(pip, pool, dest_well, per_channel_volume_uL):
        # per_channel_volume_uL is the amount each channel must aspirate and dispense; total volume = per_channel * 8
        total_needed = per_channel_volume_uL * 8.0
        # Determine how to split across source wells
        segments = consume_from_pool(pool, total_needed)
        # For each segment, perform a transfer with volume = (segment_volume / 8) per channel
        for entry, seg_vol in segments:
            per_channel_seg = seg_vol / 8.0
            src = well_from_pool(entry)
            pip.transfer(per_channel_seg, src, dest_well, new_tip='never')

    # Step 2: For each required salt concentration, fill up REPLICATES wells in reservoir_6 (slot 6) by mixing low and high salt to total 10 mL
    protocol.comment('STEP 2: Preparing salt dilutions in Reservoir 3 (slot 6)')
    target_total_uL = 10000.0
    # Sort concentrations ascending
    salt_concs = SALT_CONCENTRATIONS[:NUMBER_OF_SALT_CONCENTRATIONS]
    salt_concs.sort()

    # Determine target destination wells in reservoir_6 (0..11)
    dest_indices_step2 = []
    for i in range(NUMBER_OF_SALT_CONCENTRATIONS):
        for r in range(REPLICATES):
            dest_indices_step2.append(i * REPLICATES + r)

    # Map each dest to its concentration by order: concentrations ascending with increasing well number
    dest_to_conc = {}
    idx = 0
    for c in salt_concs:
        for r in range(REPLICATES):
            dest_to_conc[dest_indices_step2[idx]] = c
            idx += 1

    # Use multi-channel pipette with tips from slot 7 primarily
    for dest_idx in dest_indices_step2:
        conc = dest_to_conc[dest_idx]
        dest = reservoir_6.wells()[dest_idx]
        # compute volumes
        vol_high = target_total_uL * (conc / SALT_STOCK_CONCENTRATION)
        vol_low = target_total_uL - vol_high
        protocol.comment(f'Preparing well {dest_idx} in reservoir_6 at {conc} mM: high={vol_high} uL low={vol_low} uL')
        # pick up tip column
        p300m.pick_up_tip()
        # transfer high salt (may span multiple source wells)
        if vol_high > 0:
            multi_channel_transfer_from_pool(p300m, high_salt_pool, dest, vol_high/8.0)
        # transfer low salt
        if vol_low > 0:
            multi_channel_transfer_from_pool(p300m, low_salt_pool, dest, vol_low/8.0)
        # mix the destination well gently (mix volume per channel must be <= pip.max_volume)
        mix_vol = min(200, p300m.max_volume)
        p300m.mix(5, mix_vol, dest)
        p300m.drop_tip()

    # Step 3: For each salt concentration, fill up 1 well in reservoir_3 (slot 3) with total 10 mL at 2x concentration
    protocol.comment('STEP 3: Preparing 2x salt dilutions in Reservoir 4 (slot 3)')
    dest_indices_step3 = list(range(NUMBER_OF_SALT_CONCENTRATIONS))  # place them in wells 0..n-1 ascending
    for i, conc in enumerate(salt_concs):
        dest = reservoir_3.wells()[dest_indices_step3[i]]
        target_conc = conc * 2.0
        vol_high = target_total_uL * (target_conc / SALT_STOCK_CONCENTRATION)
        vol_low = target_total_uL - vol_high
        protocol.comment(f'Preparing well {dest_indices_step3[i]} in reservoir_3 at {target_conc} mM: high={vol_high} uL low={vol_low} uL')
        p300m.pick_up_tip()
        if vol_high > 0:
            multi_channel_transfer_from_pool(p300m, high_salt_pool, dest, vol_high/8.0)
        if vol_low > 0:
            multi_channel_transfer_from_pool(p300m, low_salt_pool, dest, vol_low/8.0)
        mix_vol = min(200, p300m.max_volume)
        p300m.mix(5, mix_vol, dest)
        p300m.drop_tip()

    # Step 4: Ligand dilutions in mixing_plate (deep well) using single channel pipette
    protocol.comment('STEP 4: Preparing ligand dilutions in mixing plate')
    # The mixing plate: for each salt concentration create one column. Rows A-H ascending ligand concentration row-wise: lowest in Row A
    n_salt = NUMBER_OF_SALT_CONCENTRATIONS
    n_ligand = NUMBER_OF_LIGAND_CONCENTRATIONS
    ligand_concs = LIGAND_CONCENTRATIONS[:n_ligand]
    ligand_concs.sort()

    # compute total per well volume (uL)
    total_per_well = (TOTAL_VOLUME / 2.0) * REPLICATES * 1.5

    # For each salt concentration (columns 1..n_salt), for each row (A-H up to n_ligand rows), place ligand at 2x concentration
    rows = ['A','B','C','D','E','F','G','H']
    if n_ligand > 8:
        raise RuntimeError('Too many ligand concentrations for 8 rows')

    # Decide ligand stock to use depending on computed stock volume
    for col in range(n_salt):
        for r_idx, conc in enumerate(ligand_concs[:8]):
            target_conc = conc * 2.0
            # compute volume of stock needed: V_stock = C_target * V_total / C_stock
            v_stock_high = target_conc * total_per_well / LIGAND_STOCK_CONCENTRATION
            use_low_stock = False
            stock_conc = LIGAND_STOCK_CONCENTRATION
            if v_stock_high < 20.0:
                # use low stock conc (stock/10)
                stock_conc = LIGAND_STOCK_CONCENTRATION / 10.0
                v_stock = target_conc * total_per_well / stock_conc
                use_low_stock = True
            else:
                v_stock = v_stock_high
            # ensure v_stock is not more than total_per_well
            v_stock = min(v_stock, total_per_well)
            v_buffer = total_per_well - v_stock
            # choose destination well: rows ascend A-H and columns 1..n_salt placed in plate columns 0..n_salt-1
            dest_well = mixing_plate.rows()[r_idx][col]
            protocol.comment(f'Preparing mixing plate well {rows[r_idx]}{col+1} at {target_conc} uM: stock={v_stock}uL buffer={v_buffer}uL (using low stock={use_low_stock})')
            # pick up tip for single channel
            p300s.pick_up_tip()
            # transfer stock from ligand stock wells
            if use_low_stock:
                # ensure ligand_stock_low has enough remaining
                if ligand_stock_low['remaining'] < v_stock:
                    raise RuntimeError('Ligand low stock ran out')
                p300s.transfer(v_stock, well_from_pool(ligand_stock_low), dest_well, new_tip='never')
                ligand_stock_low['remaining'] -= v_stock
            else:
                if ligand_stock_high['remaining'] < v_stock:
                    # if high stock ran out, try low stock
                    if ligand_stock_low['remaining'] >= v_stock:
                        p300s.transfer(v_stock, well_from_pool(ligand_stock_low), dest_well, new_tip='never')
                        ligand_stock_low['remaining'] -= v_stock
                    else:
                        raise RuntimeError('Ligand stocks ran out')
                else:
                    p300s.transfer(v_stock, well_from_pool(ligand_stock_high), dest_well, new_tip='never')
                    ligand_stock_high['remaining'] -= v_stock
            # transfer buffer (low salt) from low_salt_pool using single-channel aspirations
            # We implement splitting across pool wells if needed
            remaining = v_buffer
            while remaining > 0:
                # find a pool entry with >0 remaining
                found = None
                for entry in low_salt_pool:
                    if entry['remaining'] > 0:
                        found = entry
                        break
                if found is None:
                    raise RuntimeError('Low salt pool ran dry while filling mixing plate')
                take = min(found['remaining'], remaining)
                src = well_from_pool(found)
                p300s.transfer(take, src, dest_well, new_tip='never')
                found['remaining'] -= take
                remaining -= take
            # mix
            mix_vol = min(200, p300s.max_volume)
            p300s.mix(3, mix_vol, dest_well)
            p300s.drop_tip()

    protocol.comment('Protocol complete')
