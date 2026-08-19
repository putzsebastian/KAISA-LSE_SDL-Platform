from opentrons import protocol_api

metadata = {
    'protocolName': '[templated salt-ligand protocol]',
    'author': '[user]',
    'description': '[templated protocol with placeholders for salts and ligands]'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

def run(protocol: protocol_api.ProtocolContext):
    # Deck layout as specified
    cytiva_filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)
    tip_racks = [tiprack_4, tiprack_7, tiprack_10]

    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (empty)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0
    stirring_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)  # Mixing Plate

    # Pipettes
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=tip_racks)
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=tip_racks)

    # Placeholders for templating
    REPLICATES = '[[REPLICATES]]'
    TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
    SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
    LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
    SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
    NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

    def _unreplaced(token):
        return isinstance(token, str) and token.startswith('[[') and token.endswith(']]')

    def _parse_int(tok, default=1):
        if _unreplaced(tok):
            return default
        try:
            return int(tok)
        except Exception:
            return default

    def _parse_float(tok, default=1.0):
        if _unreplaced(tok):
            return default
        try:
            return float(tok)
        except Exception:
            return default

    def _split_semis(tok, default_list):
        if _unreplaced(tok):
            return default_list
        try:
            parts = [p for p in str(tok).split(';') if str(p).strip() != '']
            return [float(p) for p in parts]
        except Exception:
            return default_list

    replicates = _parse_int(REPLICATES, 1)
    salt_concs = _split_semis(SALT_CONCENTRATIONS, [0.0])
    lig_concs = _split_semis(LIGAND_CONCENTRATIONS, [0.0])
    salt_stock = _parse_float(SALT_STOCK_CONCENTRATION, 1.0)
    lig_stock = _parse_float(LIGAND_STOCK_CONCENTRATION, 1.0)

    protocol.comment('Template protocol: placeholders - replicates={}, salt_concs={}, ligand_concs={}, total_vol={}, stock_salt={}, stock_ligand={}'
                      .format(replicates, salt_concs, lig_concs, TOTAL_VOLUME, SALT_STOCK_CONCENTRATION, LIGAND_STOCK_CONCENTRATION))

    # Step 2 placeholder: Fill Reservoir 3 wells with 10 mL total per replicate
    protocol.comment('Step 2: Prepare Reservoir 3 wells with salt gradients. Replicates: {}. Salt concentrations: {}. 10 mL per replicate.'.format(replicates, salt_concs))
    per_well_total_ml = 10.0
    for i in range(min(replicates, 12)):
        conc = salt_concs[i] if i < len(salt_concs) else 0.0
        stock = salt_stock if not _unreplaced(SALT_STOCK_CONCENTRATION) else 1.0
        high_total = (conc / stock) * per_well_total_ml if stock != 0 else 0.0
        low_total = max(0.0, per_well_total_ml - high_total)
        protocol.comment('Replica {}: target conc {}. Reservoir 3 well {} high {:.2f} mL, low {:.2f} mL.'.format(i+1, conc, i, high_total, low_total))

    # Step 3 placeholder: Fill Reservoir 4 wells with 2x concentrations
    protocol.comment('Step 3: For each required salt concentration, fill Reservoir 4 well with 2x conc to make 10 mL total.')
    for i in range(min(replicates, 12)):
        conc = salt_concs[i] if i < len(salt_concs) else 0.0
        conc_2x = conc * 2.0
        stock = salt_stock if not _unreplaced(SALT_STOCK_CONCENTRATION) else 1.0
        high_total = (conc_2x / stock) * per_well_total_ml if stock != 0 else 0.0
        low_total = max(0.0, per_well_total_ml - high_total)
        protocol.comment('Reservoir 4 well {}: target 2x conc {}, high {:.2f} mL, low {:.2f} mL.'.format(i, conc_2x, high_total, low_total))

    # Step 4 placeholder: Dilutions on Mixing Plate using 2x ligand concentrations
    protocol.comment('Step 4: Create dilutions on Mixing Plate with 2x ligand concentrations. Concentrations ascend row-wise from A-H; columns correspond to salt concentrations.')
    ligand_stock_well = reservoir_1.wells()[0]
    try:
        total_vol = float(TOTAL_VOLUME)
    except Exception:
        total_vol = 0.0
    for col in range(max(1, len(lig_concs)))):
        for row_idx in range(8):
            dest = stirring_plate.rows()[row_idx][col]
            conc_lig = lig_concs[col] if col < len(lig_concs) else 0.0
            target_conc = conc_lig * 2.0
            protocol.comment('Dilution dest {}: row {}, col {} -> target conc {} (2x ligand) using stock at {} with per-well total ~{} mL.'.format(dest, chr(65+row_idx), col+1, target_conc, ligand_stock_well, total_vol))
            # Real transfers would occur here using p300_single with per-well volumes

    protocol.comment('Template steps complete. Placeholder steps will be replaced with actual transfer commands to execute on the OT-2.')
