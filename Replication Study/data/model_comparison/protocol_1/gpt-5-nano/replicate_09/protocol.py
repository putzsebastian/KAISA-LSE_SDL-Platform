from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Dilution Template',
    'author': 'Lab 167',
    'description': 'Template protocol with placeholders for replicates and concentrations',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Deck setup according to user specification
    # Slot 1: Custom labware: cytiva_96_filterwellplate_1ml
    try:
        cytiva_filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception:
        protocol.comment('WARNING: cytiva_96_filterwellplate_1ml not found; using nest_96_wellplate_200ul_flat as simulation fallback.')
        cytiva_filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Slot 4,7,10: Tip racks for 300uL
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0

    # Slot 11: Mixing plate (deep-well)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pips
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_slot4, tiprack_slot10])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_slot7])

    # Placeholders (the wizard will substitute these values)
    REPLICATES = '[[REPLICATES]]'
    SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'  # e.g. '0;0.1;0.2'
    LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'  # e.g. '1;5;10'
    SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    TOTAL_VOLUME = '[[TOTAL_VOLUME]]'  # e.g. '1000' for µL

    # Helpers to safely parse placeholders; if unreplaced, provide safe defaults
    def _unreplaced(tok):
        return isinstance(tok, str) and tok.strip().startswith('[[') and tok.strip().endswith(']]')

    def _to_int(tok, default):
        if _unreplaced(tok):
            return default
        try:
            return int(tok)
        except Exception:
            return default

    def _to_float(tok, default):
        if _unreplaced(tok):
            return default
        try:
            return float(tok)
        except Exception:
            return default

    def _to_float_list(tok, default):
        if _unreplaced(tok):
            return default
        try:
            s = str(tok)
            parts = [p.strip() for p in s.split(';') if p.strip()]
            if not parts:
                return default
            return [float(p) for p in parts]
        except Exception:
            return default

    # Parse with fallback defaults
    REPLICATES = _to_int(REPLICATES, 1)
    SALT_CONC_LIST = _to_float_list(SALT_CONCENTRATIONS, [])
    LIGAND_CONC_LIST = _to_float_list(LIGAND_CONCENTRATIONS, [])
    SALT_STOCK_CONC = _to_float(SALT_STOCK_CONCENTRATION, 1.0)
    LIGAND_STOCK_CONC = _to_float(LIGAND_STOCK_CONCENTRATION, 1.0)
    TOTAL_VOLUME_PER_WELL = _to_float(TOTAL_VOLUME, 1000.0)

    NUM_SALT_CONCS = len(SALT_CONC_LIST) if SALT_CONC_LIST else 0
    NUM_LIGAND_CONCS = len(LIGAND_CONC_LIST) if LIGAND_CONC_LIST else 0

    # Step 2: Fill Reservoir 3 wells for each salt concentration via 8-channel transfers
    # Use reservoir0 as low-salt source and reservoir2 as high-salt source; destination is reservoir3
    low_salt_well = reservoir0.wells()[0]
    high_salt_well = reservoir2.wells()[6]

    reservoir3_wells = reservoir3.wells()  # 12 wells

    if NUM_SALT_CONCS > 0 and REPLICATES > 0:
        total_per_well = 10.0  # mL per well
        p300_multi.pick_up_tip()
        for i in range(min(REPLICATES * NUM_SALT_CONCS, len(reservoir3_wells))):
            conc = SALT_CONC_LIST[i % max(1, NUM_SALT_CONCS)] if NUM_SALT_CONCS > 0 else 0.0
            vol_high_total = 0.0
            if SALT_STOCK_CONC > 0:
                vol_high_total = (conc / SALT_STOCK_CONC) * total_per_well
                if vol_high_total > total_per_well:
                    vol_high_total = total_per_well
            vol_low_total = max(0.0, total_per_well - vol_high_total)
            dest_well = reservoir3_wells[i]
            if vol_high_total > 0:
                per_channel_high = vol_high_total / 8.0
                p300_multi.transfer(per_channel_high, high_salt_well, dest_well, new_tip='never')
            if vol_low_total > 0:
                per_channel_low = vol_low_total / 8.0
                p300_multi.transfer(per_channel_low, low_salt_well, dest_well, new_tip='never')
        p300_multi.drop_tip()

    reservoir4_wells = reservoir4.wells()
    if NUM_SALT_CONCS > 0:
        total_per_well = 10.0
        p300_multi.pick_up_tip()
        for idx in range(min(NUM_SALT_CONCS, len(reservoir4_wells))):
            conc = SALT_CONC_LIST[idx] if NUM_SALT_CONCS > 0 else 0.0
            target_conc = 2.0 * conc
            vol_high_total = 0.0
            if SALT_STOCK_CONC > 0:
                vol_high_total = (target_conc / SALT_STOCK_CONC) * total_per_well
                if vol_high_total > total_per_well:
                    vol_high_total = total_per_well
            vol_low_total = max(0.0, total_per_well - vol_high_total)
            dest_well = reservoir4_wells[idx]
            if vol_high_total > 0:
                per_channel_high = vol_high_total / 8.0
                p300_multi.transfer(per_channel_high, high_salt_well, dest_well, new_tip='never')
            if vol_low_total > 0:
                per_channel_low = vol_low_total / 8.0
                p300_multi.transfer(per_channel_low, low_salt_well, dest_well, new_tip='never')
        p300_multi.drop_tip()

    ligand_stock_well = reservoir1.wells()[0]
    low_salt_buffer = reservoir0.wells()[0]

    if NUM_LIGAND_CONCS == 0:
        NUM_LIGAND_CONCS = 1
        LIGAND_CONC_LIST = [0.0]
    if NUM_SALT_CONCS == 0:
        NUM_SALT_CONCS = 1
        SALT_CONC_LIST = [0.0]

    total_vol_well = (TOTAL_VOLUME_PER_WELL / 2.0) * REPLICATES * 1.5

    p300_single.pick_up_tip()
    rows = mixing_plate.rows()  # A-H
    for c in range(NUM_SALT_CONCS):
        for r in range(NUM_LIGAND_CONCS):
            dest_well = rows[r][c]
            target_lig_conc = LIGAND_CONC_LIST[r]
            target_conc_final = 2.0 * target_lig_conc
            vol_ligand_total = 0.0
            if LIGAND_STOCK_CONC > 0:
                vol_ligand_total = (target_conc_final / LIGAND_STOCK_CONC) * total_vol_well
                if vol_ligand_total > total_vol_well:
                    vol_ligand_total = total_vol_well
            vol_buffer_total = max(0.0, total_vol_well - vol_ligand_total)
            if vol_ligand_total > 0:
                p300_single.transfer(vol_ligand_total, ligand_stock_well, dest_well, new_tip='never')
            if vol_buffer_total > 0:
                p300_single.transfer(vol_buffer_total, low_salt_buffer, dest_well, new_tip='never')
    p300_single.drop_tip()
