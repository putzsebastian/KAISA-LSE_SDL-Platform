from opentrons import protocol_api

metadata = {
    'protocolName': 'Template Salt/Ligand Dilution with Placeholders',
    'author': 'Lab 167',
    'description': 'OT-2 protocol template using placeholders for replicates, concentrations, and totals'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def _parse_list_token(token, default_list):
    if isinstance(token, list):
        return token
    if isinstance(token, str) and token.startswith('[[') and token.endswith(']]'):
        return default_list
    if token is None:
        return default_list
    s = str(token)
    if s == '':
        return default_list
    parts = [p.strip() for p in s.split(';') if p.strip() != '']
    try:
        return [float(p) for p in parts]
    except ValueError:
        return parts


def _to_float(token, default=0.0):
    try:
        return float(token)
    except Exception:
        return float(default)


def run(protocol: protocol_api.ProtocolContext):
    # Deck setup with explicit slots per user specification
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1, label='Custom Cytiva 96-filter')
    except Exception:
        protocol.comment('WARNING: Cytiva custom labware not found; using standard 96-well plate for simulation fallback.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)  # Mixing Plate (2 mL deep on Nest)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right',
                                         tip_racks=[tiprack_4, tiprack_7, tiprack_10])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left',
                                        tip_racks=[tiprack_4, tiprack_7, tiprack_10])

    # Placeholders (strings to be substituted at runtime)
    SALT_CONCEN = '[[SALT_CONCENTRATIONS]]'
    REPLICATES = '[[REPLICATES]]'
    TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
    SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    LIGAND_CONCEN = '[[LIGAND_CONCENTRATIONS]]'
    LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
    NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

    # Resolve placeholders to lists/values where possible; if not substituted, use safe defaults
    salt_concs = _parse_list_token(SALT_CONCEN, [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0])
    replicates = int(_parse_list_token(REPLICATES, [1])[0]) if isinstance(REPLICATES, str) else int(REPLICATES)
    total_volume_ml = _to_float(TOTAL_VOLUME, 10.0)
    salt_stock_conc = _to_float(SALT_STOCK_CONCENTRATION, 5.0)
    ligand_concs = _parse_list_token(LIGAND_CONCEN, [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0])
    ligand_stock_conc = _to_float(LIGAND_STOCK_CONCENTRATION, 1.0)

    n_salts = len(salt_concs) if salt_concs else 0
    n_ligs = len(ligand_concs) if ligand_concs else 0

    # Safety cap: ensure replicates * n_salts <= 12 (per user spec)
    if n_salts > 0:
        max_replicates = max(1, 12 // n_salts)
        if replicates > max_replicates:
            replicates = max_replicates

    # Step 2: Fill Reservoir 3 wells with 10 mL mixtures (low/high salt) per concentration
    low_source = reservoir_0.wells()[0]  # Low salt buffer with 0 salt
    high_source = reservoir_2.wells()[6]  # High salt buffer with stock concentration
    dests_step2 = reservoir_3.wells()[: replicates * n_salts]

    V_total_ul = 10000  # 10 mL total per destination well
    if n_salts > 0 and replicates > 0:
        for i, conc in enumerate(salt_concs):
            for r in range(replicates):
                idx = i * replicates + r
                if idx >= len(dests_step2):
                    break
                dest = dests_step2[idx]
                V_high = int((conc) / max(1e-6, salt_stock_conc) * V_total_ul)
                if V_high > V_total_ul:
                    V_high = V_total_ul
                V_low = V_total_ul - V_high
                if V_low > 0:
                    p300_multi.transfer(V_low, low_source, [dest], new_tip='never')
                if V_high > 0:
                    p300_multi.transfer(V_high, high_source, [dest], new_tip='never')

    # Step 3: For each salt conc, fill 1 well in Reservoir 4 with 2x the required salt concentration
    res4_wells = reservoir_4.wells()
    dests_step3 = res4_wells[: replicates * n_salts]
    for i, conc in enumerate(salt_concs):
        for r in range(replicates):
            idx = i * replicates + r
            if idx >= len(dests_step3):
                break
            dest = dests_step3[idx]
            V_high = int((2 * conc) / max(1e-6, salt_stock_conc) * V_total_ul)
            if V_high > V_total_ul:
                V_high = V_total_ul
            V_low = V_total_ul - V_high
            if V_low > 0:
                p300_multi.transfer(V_low, low_source, [dest], new_tip='never')
            if V_high > 0:
                p300_multi.transfer(V_high, high_source, [dest], new_tip='never')

    # Step 4: Dilutions in mixing plate with concentrations 2x the ligand concentrations
    ligand_stock = reservoir_1.wells()[0]
    diluent = reservoir_0.wells()[0]
    per_well_vol_ul = (total_volume_ml / 2.0) * replicates * 1.5 * 1000

    max_cols = min(n_salts, len(mixing_plate.columns()))
    for col_index in range(max_cols):
        column_wells = mixing_plate.columns()[col_index]  # 8 wells A-H
        for row in range(min(n_ligs, len(column_wells))):
            well = column_wells[row]
            target_conc = 2.0 * ligand_concs[row]
            V_ligand = int(target_conc / max(1e-9, ligand_stock_conc) * per_well_vol_ul)
            if V_ligand > per_well_vol_ul:
                V_ligand = per_well_vol_ul
            V_buffer = per_well_vol_ul - V_ligand
            if V_ligand > 0:
                p300_single.transfer(V_ligand, ligand_stock, [well], new_tip='never')
            if V_buffer > 0:
                p300_single.transfer(V_buffer, diluent, [well], new_tip='never')

    protocol.comment('Template protocol with placeholders completed.')
