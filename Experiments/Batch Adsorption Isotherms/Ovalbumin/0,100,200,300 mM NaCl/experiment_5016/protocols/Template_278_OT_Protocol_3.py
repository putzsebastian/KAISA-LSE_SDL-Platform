from opentrons import protocol_api

metadata = {
    'protocolName': '[protocol name by user]',
    'author': '[user name]',
    'description': "[what is the protocol about]"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

def run(protocol: protocol_api.ProtocolContext):
    # =====================
    # Modules
    # =====================
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')
    hs_mod.open_labware_latch()
    # Load custom filter plate onto Heater-Shaker (no adapter)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        protocol.comment('Custom labware cytiva_96_filterwellplate_1ml not found; using nest_96_wellplate_200ul_flat as simulation fallback.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')
    hs_mod.close_labware_latch()

    # =====================
    # Labware
    # =====================
    tiprack_300_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_300_2 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_300_3 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Buffer (2x salt) sources
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # =====================
    # Pipettes
    # =====================
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300_1, tiprack_300_2, tiprack_300_3])


    # =====================
    # Parameters (templated placeholders with safe defaults for simulation)
    # =====================
    raw_replicates = '3'
    raw_total_volume = '300'
    raw_salt_concs = '0;100;200;300'  # semicolon-separated string
    raw_ligand_concs = '0.2;0.5;1.0;1.5;2;5;7'  # semicolon-separated string
    raw_num_salts = '4'
    raw_num_ligands = '7'

    # Parse or set fallbacks when placeholders are not yet replaced
    try:
        replicates = int(raw_replicates)  # number of replicate columns per salt condition
    except Exception:
        replicates = 3

    try:
        total_volume = float(raw_total_volume)
    except Exception:
        total_volume = 100.0

    if '[[' in raw_salt_concs:
        salt_concs = ['0', '100', '200', '500']
    else:
        salt_concs = [s.strip() for s in raw_salt_concs.split(';') if s.strip()]

    if '[[' in raw_ligand_concs:
        ligand_concs = ['L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8']
    else:
        ligand_concs = [s.strip() for s in raw_ligand_concs.split(';') if s.strip()]

    try:
        number_of_salt_concentrations = int(raw_num_salts)
    except Exception:
        number_of_salt_concentrations = len(salt_concs) if len(salt_concs) > 0 else 4

    try:
        number_of_ligand_concentrations = int(raw_num_ligands)
    except Exception:
        number_of_ligand_concentrations = len(ligand_concs) if len(ligand_concs) > 0 else 8

    vol_per_well = total_volume / 2.0

    # Safety checks and info
    total_required_columns = number_of_salt_concentrations * replicates
    if total_required_columns > 12:
        protocol.pause(f'Required columns ({total_required_columns}) exceed 96-well plate capacity (12 columns). Adjust 2 or 2.')

    # =====================
    # Step 1: Transfer buffers from Reservoir 4 to Filterplate on HS
    # Each buffer (2x concentration) -> 2 columns, 300/2 uL per well
    # Use 8-channel pipette, tips from Slot 4, then return tips for reuse.
    # =====================
    reusable_tip_location = tiprack_300_1.columns()[0][0]  # First column in Slot 4 for reuse across steps
    p300m.pick_up_tip(reusable_tip_location)

    all_sources_step1 = []
    all_dests_step1 = []

    # Build source/destination lists for a single transfer() call
    for salt_idx in range(number_of_salt_concentrations):
        src_well = reservoir_4.wells()[salt_idx]
        start_col = salt_idx * replicates + 1  # 1-indexed column numbering on OT-2
        dest_cols = [filter_plate.columns_by_name()[str(c)] for c in range(start_col, start_col + replicates)]
        # For transfer(), pair one source well with each destination column
        all_sources_step1.extend([src_well] * len(dest_cols))
        all_dests_step1.extend(dest_cols)

    all_dests_step1 = [col[0].bottom(z=7) for col in all_dests_step1]

    # Perform transfers with the same tip, then return it
    if len(all_sources_step1) > 0:
        p300m.transfer(vol_per_well, all_sources_step1, all_dests_step1, new_tip='never')
    p300m.return_tip()

    # =====================
    # Step 2: Transfer ligands from Mixing Plate (Slot 11) to Filterplate on HS
    # Each column in the mixing plate -> 2 columns in the filterplate, 300/2 uL per well
    # Reuse the same tips from Slot 4.
    # =====================
    p300m.pick_up_tip(reusable_tip_location)

    all_sources_step2 = []
    all_dests_step2 = []

    for col_idx in range(number_of_salt_concentrations):
        src_column = mixing_plate.columns_by_name()[str(col_idx + 1)]
        start_col = col_idx * replicates + 1
        dest_cols = [filter_plate.columns_by_name()[str(c)] for c in range(start_col, start_col + replicates)]
        # Repeat the source column once per destination column
        all_sources_step2.extend([src_column] * len(dest_cols))
        all_dests_step2.extend(dest_cols)

    all_dests_step2 = [col[0].bottom(z=7) for col in all_dests_step2]

    if len(all_sources_step2) > 0:
        p300m.transfer(vol_per_well, all_sources_step2, all_dests_step2, new_tip='never')
    p300m.return_tip()

    protocol.comment('Protocol complete. Buffers and ligands transferred using templated parameters.')