from opentrons import protocol_api
import math

metadata = {
    'protocolName': '[protocol name by user]',
    'author': '[user name]',
    'description': "[what is the protocol about]"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # -----------------------
    # Placeholder parameters (templated)
    # -----------------------
    PLACEHOLDERS = {
        'REPLICATES': '3',
        'TOTAL_VOLUME': '300',  # used in step 4 formula
        'SALT_CONCENTRATIONS': '500',  # semicolon-separated string
        'LIGAND_CONCENTRATIONS': '0.2;0.5;1.0;1.5;2;5;7',  # semicolon-separated string
        'SALT_STOCK_CONCENTRATION': '1000',
        'LIGAND_STOCK_CONCENTRATION': '14',
        'NUMBER_OF_SALT_CONCENTRATIONS': '1',
        'NUMBER_OF_LIGAND_CONCENTRATIONS': '7',
    }

    # Helper parsing utilities to allow simulation if placeholders are not replaced
    def is_placeholder(val):
        return isinstance(val, str) and val.strip().startswith('[[') and val.strip().endswith(']]')

    def parse_float(val, default):
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            if is_placeholder(val):
                return float(default)
            try:
                return float(val)
            except Exception:
                return float(default)
        return float(default)

    def parse_int(val, default):
        if isinstance(val, int):
            return int(val)
        if isinstance(val, float):
            return int(val)
        if isinstance(val, str):
            if is_placeholder(val):
                return int(default)
            try:
                return int(val)
            except Exception:
                try:
                    return int(float(val))
                except Exception:
                    return int(default)
        return int(default)

    def parse_float_list(val, default_list):
        if isinstance(val, str) and not is_placeholder(val):
            parts = [p.strip() for p in val.split(';') if p.strip()]
            try:
                return [float(p) for p in parts]
            except Exception:
                return list(default_list)
        # if placeholder or non-string, return default
        return list(default_list)

    # Defaults for simulation if placeholders are not replaced
    DEFAULTS = {
        'REPLICATES': 2,
        'TOTAL_VOLUME': 100.0,  # uL, used in step 4 formula
        'SALT_CONCENTRATIONS': [50.0, 150.0, 300.0],  # example mM
        'LIGAND_CONCENTRATIONS': [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0],  # example uM (8 values)
        'SALT_STOCK_CONCENTRATION': 1000.0,  # example mM
        'LIGAND_STOCK_CONCENTRATION': 1000.0,  # example uM
        'NUMBER_OF_SALT_CONCENTRATIONS': 3,
        'NUMBER_OF_LIGAND_CONCENTRATIONS': 8,
    }

    REPLICATES = parse_int(PLACEHOLDERS['REPLICATES'], DEFAULTS['REPLICATES'])
    TOTAL_VOLUME = parse_float(PLACEHOLDERS['TOTAL_VOLUME'], DEFAULTS['TOTAL_VOLUME'])
    SALT_CONCS = parse_float_list(PLACEHOLDERS['SALT_CONCENTRATIONS'], DEFAULTS['SALT_CONCENTRATIONS'])
    LIG_CONCS = parse_float_list(PLACEHOLDERS['LIGAND_CONCENTRATIONS'], DEFAULTS['LIGAND_CONCENTRATIONS'])
    SALT_STOCK = parse_float(PLACEHOLDERS['SALT_STOCK_CONCENTRATION'], DEFAULTS['SALT_STOCK_CONCENTRATION'])
    LIG_STOCK = parse_float(PLACEHOLDERS['LIGAND_STOCK_CONCENTRATION'], DEFAULTS['LIGAND_STOCK_CONCENTRATION'])
    NUM_SALT = parse_int(PLACEHOLDERS['NUMBER_OF_SALT_CONCENTRATIONS'], len(SALT_CONCS))
    NUM_LIG = parse_int(PLACEHOLDERS['NUMBER_OF_LIGAND_CONCENTRATIONS'], len(LIG_CONCS))

    # sort concentrations ascending as required
    SALT_CONCS = sorted(SALT_CONCS)[:NUM_SALT]
    LIG_CONCS = sorted(LIG_CONCS)[:min(NUM_LIG, 8)]  # up to 8 rows (A-H)

    # -----------------------
    # Labware
    # -----------------------
    # Tip racks
    tiprack_300_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Custom 96 filter-well plate (Slot 1)
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception:
        protocol.comment('WARNING: Custom labware cytiva_96_filterwellplate_1ml not found. Using opentrons_96_wellplate_200ul_pcr_full_skirt as placeholder for simulation.')
        filter_plate = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 1)

    # Reservoirs
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # empty, will hold 2x salts (10 mL each)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # empty, will hold salts (14 mL each)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # low/high salt buffers pre-loaded
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # ligand stocks and buffers pre-loaded
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # low salt buffer pre-loaded

    # Mixing plate (96 deep well, 2 mL)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -----------------------
    # Pipettes
    # -----------------------
    # Use only Slot 7 tips for multi, only Slot 10 for single as specified
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300_slot7])
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300_slot10])

    # -----------------------
    # Liquid inventory tracking (uL)
    # -----------------------
    ML_TO_UL = 1000.0
    INIT_VOL_PER_SOURCE_WELL = 14.0 * ML_TO_UL  # 14 mL = 14000 uL

    class SourcePool:
        def __init__(self, wells_list, init_volume_ul):
            # each well starts with init_volume_ul
            self.entries = [{'well': w, 'remaining': float(init_volume_ul)} for w in wells_list]
            self.index = 0

        def current(self):
            if self.index >= len(self.entries):
                raise RuntimeError('SourcePool ran out of volume across all wells.')
            return self.entries[self.index]

        def advance(self):
            self.index += 1
            if self.index >= len(self.entries):
                raise RuntimeError('SourcePool ran out of volume across all wells.')
        
        def reset(self):
            """Reset pool index to beginning (for reuse with different buffer type)."""
            self.index = 0

    # Define source pools according to provided reservoir content maps
    # Reservoir 2 (slot 8): wells 0-5 low salt; 6-11 high salt (SALT_STOCK)
    res2_wells = reservoir2.wells()  # A1..A12
    low_salt_sources_res2 = [res2_wells[i] for i in range(0, 6)]
    high_salt_sources_res2 = [res2_wells[i] for i in range(6, 12)]

    # Reservoir 1 (slot 9): mapping given
    res1_wells = reservoir1.wells()
    ligand_stock_high = res1_wells[0]      # Well 0: high ligand stock
    ligand_stock_low = res1_wells[1]       # Well 1: low ligand stock (1/10)
    low_salt_sources_res1 = [res1_wells[i] for i in range(2, 7)]  # Wells 2-6: low salt buffer
    high_salt_sources_res1 = [res1_wells[i] for i in range(7, 12)] # Wells 7-11: high salt buffer (stock)

    # Reservoir 0 (slot 5): low salt in wells 0-11
    res0_wells = reservoir0.wells()
    low_salt_sources_res0 = [res0_wells[i] for i in range(0, 12)]

    # Create pools (prefer reservoir2 for buffers; then reservoir1 as needed)
    low_salt_pool_for_salt_mixing = SourcePool(low_salt_sources_res2 + low_salt_sources_res1 + low_salt_sources_res0, INIT_VOL_PER_SOURCE_WELL)
    high_salt_pool_for_salt_mixing = SourcePool(high_salt_sources_res2 + high_salt_sources_res1, INIT_VOL_PER_SOURCE_WELL)

    low_salt_pool_for_dilutions = SourcePool(low_salt_sources_res1 + low_salt_sources_res2, INIT_VOL_PER_SOURCE_WELL)
    # Ligand stock pools (track volumes too)
    ligand_high_pool = SourcePool([ligand_stock_high], INIT_VOL_PER_SOURCE_WELL)
    ligand_low_pool = SourcePool([ligand_stock_low], INIT_VOL_PER_SOURCE_WELL)

    # -----------------------
    # Helper for mixing target wells after additions
    # -----------------------
    def mix_multi_in_place(mix_wells, total_volume_ul):
        mix_vol = min(300, max(50, total_volume_ul / 10.0))
        for w in mix_wells:
            p300m.mix(5, mix_vol, w)

    def mix_single_in_place(well, total_volume_ul):
        mix_vol = min(300, max(30, total_volume_ul / 5.0))
        p300s.mix(5, mix_vol, well)

    # -----------------------
    # Build step 2 groups: replicates per salt concentration
    # -----------------------
    reservoir3_wells = reservoir3.wells()  # A1..A12
    total_wells_needed_step2 = len(SALT_CONCS) * REPLICATES
    if total_wells_needed_step2 > len(reservoir3_wells):
        raise RuntimeError('Not enough wells in Reservoir 3 to accommodate SALT_CONCENTRATIONS x REPLICATES.')

    step2_groups = []  # list of dicts: {'dests': [wells], 'V_low': uL, 'V_high': uL, 'V_total': uL}
    for idx_c, c_target in enumerate(SALT_CONCS):
        V_total = 10.0 * ML_TO_UL
        if SALT_STOCK <= 0:
            raise RuntimeError('SALT_STOCK_CONCENTRATION must be > 0')
        V_high = max(0.0, V_total * (c_target / SALT_STOCK))
        V_low = max(0.0, V_total - V_high)
        start = idx_c * REPLICATES
        end = start + REPLICATES
        dest_group = reservoir3_wells[start:end]
        step2_groups.append({'dests': dest_group, 'V_low': V_low, 'V_high': V_high, 'V_total': V_total})

    NUM_MULTI_CHANNELS = 8  # p300_multi_gen2 has 8 channels

    # FIX: Rewritten function that does NOT mutate groups and reuses tips properly
    def execute_multi_across_groups(pool: SourcePool, groups, vol_key: str, integrate_mix=False):
        """
        Transfer liquid from pool to groups using multi-channel pipette.
        
        Key fixes:
        1. Uses manual copy of groups to avoid mutating original data
        2. Reuses tips when aspirating from same source (per spec)
        """
        # Make a working copy - can't use deepcopy because well objects contain async generators
        # We only need to copy the list of dests, not the well objects themselves
        working_groups = []
        for g in groups:
            working_groups.append({
                'dests': list(g['dests']),  # Copy the list, keep well references
                'V_low': g.get('V_low', 0),
                'V_high': g.get('V_high', 0),
                'V_total': g.get('V_total', 0),
            })
        
        group_idx = 0
        p300m.pick_up_tip()

        while group_idx < len(working_groups):
            g = working_groups[group_idx]
            dests = g['dests']
            # this is the TOTAL volume you want to end up in each destination well
            vol_per_dest_total = g[vol_key]

            if vol_per_dest_total <= 0:
                group_idx += 1
                continue

            # Opentrons volume argument is per channel for a multi pipette.
            # To get vol_per_dest_total into one well (all 8 tips into same well),
            # each channel needs to handle vol_per_dest_total / 8.
            vol_per_channel = vol_per_dest_total / float(NUM_MULTI_CHANNELS)

            src = pool.current()

            # Each destination consumes vol_per_dest_total from the source
            possible = int(src['remaining'] // vol_per_dest_total)
            if possible <= 0:
                # Current source well is empty, move to next well and retry
                pool.advance()
                continue  # Restart loop with new source

            take_count = min(possible, len(dests))

            # Perform the transfer. Internally, transfer will split into multiple
            # aspirations/dispenses if vol_per_channel > pipette max volume.
            p300m.transfer(
                vol_per_channel,
                src['well'],
                dests[:take_count],
                new_tip='never'
            )

            # Bookkeeping in TERMS OF REAL VOLUME REMOVED FROM THE WELL
            # (= total µL actually taken from the source)
            src['remaining'] -= vol_per_dest_total * take_count

            if integrate_mix:
                mix_multi_in_place(
                    dests[:take_count],
                    g.get('V_total', vol_per_dest_total)
                )

            if take_count < len(dests):
                # This group still has remaining destination wells to fill
                # Update the working copy, not the original
                g['dests'] = dests[take_count:]
                # Don't increment group_idx - continue with remaining dests
            else:
                group_idx += 1

        p300m.drop_tip()  # Drop tip at the end instead of return_tip

    # Step 2 execution: add low salt then high salt with mixing
    execute_multi_across_groups(low_salt_pool_for_salt_mixing, step2_groups, 'V_low', integrate_mix=False)
    execute_multi_across_groups(high_salt_pool_for_salt_mixing, step2_groups, 'V_high', integrate_mix=True)

    # -----------------------
    # Step 3: 2x salt buffers in Reservoir 4 (10 mL per well)
    # -----------------------
    reservoir4_wells = reservoir4.wells()  # A1..A12
    if len(SALT_CONCS) > len(reservoir4_wells):
        raise RuntimeError('Not enough wells in Reservoir 4 to accommodate SALT_CONCENTRATIONS for 2x mixes.')

    step3_groups = []  # Combined groups with both V_low and V_high
    for idx_c, c_target in enumerate(SALT_CONCS):
        V_total = 10.0 * ML_TO_UL
        c_2x = 2.0 * c_target
        if c_2x > SALT_STOCK:
            raise RuntimeError('Requested 2x salt concentration exceeds stock concentration.')
        V_high = max(0.0, V_total * (c_2x / SALT_STOCK))
        V_low = max(0.0, V_total - V_high)
        dest = [reservoir4_wells[idx_c]]
        step3_groups.append({'dests': dest, 'V_low': V_low, 'V_high': V_high, 'V_total': V_total})

    execute_multi_across_groups(low_salt_pool_for_salt_mixing, step3_groups, 'V_low', integrate_mix=False)
    execute_multi_across_groups(high_salt_pool_for_salt_mixing, step3_groups, 'V_high', integrate_mix=True)

    # -----------------------
    # Step 4: Ligand dilutions (2x) in mixing plate (slot 11)
    # In mixing plate, concentrations ascending row-wise A (lowest) to H (highest). Columns correspond to SALT_CONCS.
    # Total volume per well: 300/2 * 2 * 1.5
    # -----------------------
    if len(SALT_CONCS) > 12:
        raise RuntimeError('Number of salt concentrations exceeds number of columns in 96-well plate.')

    per_well_total = (TOTAL_VOLUME / 2.0) * float(REPLICATES) * 1.5

    columns = mixing_plate.columns()  # 12 columns, each is a list [A..H]
    for col_idx in range(len(SALT_CONCS)):
        col_wells = columns[col_idx]  # 8 wells A..H in this column
        target_rows = col_wells[:len(LIG_CONCS)]
        # For each row (ligand concentration), compute stock and buffer volumes
        for row_idx, c_lig in enumerate(LIG_CONCS):
            desired_2x = 2.0 * c_lig
            if LIG_STOCK <= 0:
                raise RuntimeError('LIGAND_STOCK_CONCENTRATION must be > 0')
            # compute using high stock first
            v_stock_high = per_well_total * (desired_2x / LIG_STOCK)
            use_low_stock = v_stock_high < 20.0  # threshold 20 uL
            if use_low_stock:
                stock_conc = LIG_STOCK / 10.0
                ligand_source_pool = ligand_low_pool
            else:
                stock_conc = LIG_STOCK
                ligand_source_pool = ligand_high_pool
            v_stock = per_well_total * (desired_2x / stock_conc)
            v_buffer = max(0.0, per_well_total - v_stock)

            dest = target_rows[row_idx]
            # Add buffer first with a fresh tip
            if v_buffer > 0:
                # Ensure adequate buffer in current source; advance as needed
                # Use one tip per destination to avoid contaminating buffer pool
                src = low_salt_pool_for_dilutions.current()
                if src['remaining'] < v_buffer:
                    low_salt_pool_for_dilutions.advance()
                    src = low_salt_pool_for_dilutions.current()
                p300s.pick_up_tip()
                p300s.transfer(v_buffer, src['well'], [dest], new_tip='never')
                src['remaining'] -= v_buffer
                p300s.drop_tip()
            # Add ligand stock with a fresh tip, then mix
            if v_stock > 0:
                src = ligand_source_pool.current()
                if src['remaining'] < v_stock:
                    ligand_source_pool.advance()
                    src = ligand_source_pool.current()
                p300s.pick_up_tip()
                p300s.transfer(v_stock, src['well'], [dest], new_tip='never')
                src['remaining'] -= v_stock
                # Gentle mix after additions with same tip
                mix_single_in_place(dest, per_well_total)
                p300s.drop_tip()

    # Protocol complete
