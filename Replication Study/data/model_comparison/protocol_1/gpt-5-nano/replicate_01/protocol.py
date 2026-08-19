from opentron import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Dilution Template (templated placeholders)',
    'author': 'Lab 167',
    'description': 'Template protocol with placeholders for salts and ligands, designed to be templated by [[REPLICATES]] etc.',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # ----------------------------
    # Labware setup (deck layout as provided)
    # ----------------------------
    import math
    # Slot 1: Custom labware (cytiva_96_filterwellplate_1ml) with fallback for simulation
    try:
        cytiva_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception:
        protocol.comment('WARNING: custom labware cy tiva 96 filter plate not found; using fallback labware for simulation.')
        cytiva_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Slot 4,7,10: Tip racks (300 uL)
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Right mount: P300 Single-Channel Gen2
    # Left mount: P300 8-Channel Gen2
    p300s_tip_racks = [tiprack_slot10]
    p300m_tip_racks = [tiprack_slot7]
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=p300s_tip_racks)
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=p300m_tip_racks)

    # Reservoirs and mixing plate based on the deck layout described
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)  # Mixing Plate (Deep well)

    # Tip usage tracking helpers (not strictly needed, but useful for clarity)
    def vol_to_ul(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    # ----------------------------
    # Placeholder constants (templated values)
    # Placeholders are strings that will be replaced by the templating wizard
    # ----------------------------
    REPLICATES = '[[REPLICATES]]'  # int
    TOTAL_VOLUME = '[[TOTAL_VOLUME]]'  # numeric (in uL for some steps, in mL for others depending on context)
    SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'  # semicolon-separated string of concentrations
    LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'  # semicolon-separated string of concentrations
    SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'  # numeric
    LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'  # numeric
    NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'  # int
    NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'  # int

    # ----------------------------
    # Parse placeholders into usable Python values once substituted
    # ----------------------------
    def to_int(x, default=0):
        try:
            return int(float(x))
        except Exception:
            return default

    def to_float_list(s, max_len=None):
        try:
            items = [float(x) for x in str(s).split(';') if str(x).strip() != '']
            if max_len is not None:
                items = items[:max_len]
            return items
        except Exception:
            return []

    replicates = to_int(REPLICATES, 1)
    total_volume_value = 0.0
    try:
        total_volume_value = float(TOTAL_VOLUME)
    except Exception:
        total_volume_value = 10000.0  # default to 10 mL for simulation fallback

    salt_concs = to_float_list(SALT_CONCENTRATIONS, int(NUMBER_OF_SALT_CONCENTRATIONS) if NUMBER_OF_SALT_CONCENTRATIONS != '[[NUMBER_OF_SALT_CONCENTRATIONS]]' else None)
    ligand_concs = to_float_list(LIGAND_CONCENTRATIONS, int(NUMBER_OF_LIGAND_CONCENTRATIONS) if NUMBER_OF_LIGAND_CONCENTRATIONS != '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]' else None)

    try:
        salt_stock_conc = float(SALT_STOCK_CONCENTRATION)
    except Exception:
        salt_stock_conc = 1.0
    try:
        ligand_stock_conc = float(LIGAND_STOCK_CONCENTRATION)
    except Exception:
        ligand_stock_conc = 1.0

    # Ensure we have at least one of the two stock wells
    low_salt_source = reservoir0.wells()[0]
    high_salt_source = reservoir2.wells()[6] if len(reservoir2.wells()) > 6 else reservoir2.wells()[0]
    ligand_stock_source = reservoir1.wells()[0]
    ligand_stock_source2 = reservoir1.wells()[1] if len(reservoir1.wells()) > 1 else reservoir1.wells()[0]

    # ----------------------------
    # Step 2: Create salts in Reservoir 3 wells using multi-channel pipette
    # For each salt concentration, fill REPLICATES wells in Reservoir 3 with total 10 mL
    # Final concentration in the dest well is the target; we mix low salt (0) + high salt (stock_conc)
    # ----------------------------
    max_reservoir3_wells = len(reservoir3.wells())
    reservoir3_wells = reservoir3.wells()
    per_dest_total_ml = 10.0  # total per destination well in mL (as described)
    per_dest_total_ul = per_dest_total_ml * 1000.0
    used_well_count = 0
    if salt_concs:
        for conc in salt_concs:
            if used_well_count >= max_reservoir3_wells:
                break
            for rep in range(replicates):
                if used_well_count >= max_reservoir3_wells:
                    break
                dest_well = reservoir3_wells[used_well_count]
                used_well_count += 1
                ratio = conc / salt_stock_conc if salt_stock_conc != 0 else 0.0
                v_high_total = per_dest_total_ul * ratio
                v_high_per_channel = v_high_total / 8.0
                v_low_total = per_dest_total_ul - v_high_total
                v_low_per_channel = v_low_total / 8.0
                # Transfer from high salt into destination well
                p300m.transfer(v_low_per_channel, low_salt_source, dest_well, new_tip='never')
                p300m.transfer(v_high_per_channel, high_salt_source, dest_well, new_tip='never')

    # ----------------------------
    # Step 3: For each required salt concentration, fill Reservoir 4 wells with 2x conc
    # ----------------------------
    reservoir4_wells = reservoir4.wells()
    used_well_count4 = 0
    if salt_concs:
        for conc in salt_concs:
            if used_well_count4 >= len(reservoir4_wells):
                break
            dest_well = reservoir4_wells[used_well_count4]
            used_well_count4 += 1
            two_times = conc * 2.0
            ratio2 = two_times / salt_stock_conc if salt_stock_conc != 0 else 0.0
            v_high_total = per_dest_total_ul * ratio2
            v_high_per_channel = v_high_total / 8.0
            v_low_total = per_dest_total_ul - v_high_total
            v_low_per_channel = v_low_total / 8.0
            p300m.transfer(v_low_per_channel, low_salt_source, dest_well, new_tip='never')
            p300m.transfer(v_high_per_channel, high_salt_source, dest_well, new_tip='never')

    # ----------------------------
    # Step 4: Dilutions in Mixing Plate (deep well 2 mL) for ligand concentrations
    # All dilutions use low salt buffer as diluent. Use Ligand Stock from Reservoir 1 Well 0.
    # The final well volume calculation uses: TOTAL_VOLUME * 0.5 * REPLICATES * 1.5 (per spec)
    # Concentrations in the mixing plate ascend row-wise from A-H; Columns correspond to SALT_CONCENTRATIONS entries
    # ----------------------------
    if ligand_concs:
        # Determine mixing plate geometry
        # 8 rows x Ncolumns where Ncolumns equals number of salt concentrations (length of salt_concs)
        num_columns = min(len(salt_concs), len(mixing_plate.columns()) ) if salt_concs else 0
        # Compute per-well destination volumes (in uL) per spec
        try:
            per_well_volume_ul = (float(TOTAL_VOLUME) / 2.0) * float(replicates) * 1.5
        except Exception:
            per_well_volume_ul = 1000.0
        stock_source = ligand_stock_source
        low_buffer_source = low_salt_source
        # Use 8 rows (A-H)
        for col_idx in range(min(len(ligand_concs), int(NUMBER_OF_LIGAND_CONCENTRATIONS) if NUMBER_OF_LIGAND_CONCENTRATIONS != '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]' else len(ligand_concs))):
            conc_value_row = ligand_concs[col_idx]  # concentration for this column per row
            target_conc_row = conc_value_row * 2.0  # 2x for the dilution as described
            for row_idx in range(8):  # A-H
                dest_well = mixing_plate.rows()[row_idx][col_idx]
                # Determine per-channel volumes for a single channel transfer logic
                ratio = target_conc_row / ligand_stock_conc if ligand_stock_conc != 0 else 0.0
                total_dest_vol = per_well_volume_ul
                stock_vol = total_dest_vol * ratio
                buffer_vol = total_dest_vol - stock_vol
                if stock_vol > 0:
                    p300s.transfer(stock_vol, stock_source, dest_well, new_tip='never')
                if buffer_vol > 0:
                    p300s.transfer(buffer_vol, low_buffer_source, dest_well, new_tip='never')

    # End protocol
    protocol.comment('Template protocol run complete. Values used are placeholders and will be substituted for actual runs.')
