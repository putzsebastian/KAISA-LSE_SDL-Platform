from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt-Ligand Dilutions (templated placeholders)',
    'author': 'Lab 167',
    'description': 'OT-2 protocol template using placeholders for salts and ligands; deck layout as requested.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Load labware with fallback for simulation
    try:
        plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception:
        protocol.comment('WARNING: cytiva labware not found; using nest_96_wellplate_200ul_flat as simulation fallback.')
        plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    res0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0
    res1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    res2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    res3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3
    res4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4
    mixing_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 11)

    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack4, tiprack10])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack7])

    # Placeholders for templating
    REPLICATES = '[[REPLICATES]]'
    TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
    SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
    LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
    SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
    NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

    def _to_float(x, default):
        try:
            if isinstance(x, (int, float)):
                return float(x)
            if isinstance(x, str) and x.strip().startswith('[['):
                return default
            return float(x)
        except:
            return default

    def _to_int(x, default):
        try:
            if isinstance(x, int):
                return int(x)
            if isinstance(x, float):
                return int(x)
            if isinstance(x, str) and x.strip().startswith('[['):
                return default
            return int(float(x))
        except:
            return default

    def _parse_semicolon_floats(tok):
        if isinstance(tok, str) and tok.strip().startswith('[['):
            return None
        if tok is None or tok == '':
            return []
        parts = [p.strip() for p in str(tok).split(';') if p.strip() != '']
        vals = []
        for p in parts:
            try:
                vals.append(float(p))
            except:
                pass
        return vals

    REPLICATES_NUM = _to_int(REPLICATES, 1)
    TOTAL_VOLUME_ML = _to_float(TOTAL_VOLUME, 10.0)
    SALT_CONCS = _parse_semicolon_floats(SALT_CONCENTRATIONS) or []
    LIGAND_CONCS = _parse_semicolon_floats(LIGAND_CONCENTRATIONS) or []
    SALT_STOCK = _to_float(SALT_STOCK_CONCENTRATION, 1.0)
    LIGAND_STOCK = _to_float(LIGAND_STOCK_CONCENTRATION, 1.0)
    NUM_SALT = _to_int(NUMBER_OF_SALT_CONCENTRATIONS, len(SALT_CONCS) or 0)
    NUM_LIG = _to_int(NUMBER_OF_LIGAND_CONCENTRATIONS, len(LIGAND_CONCS) or 0)

    if REPLICATES_NUM <= 0:
        REPLICATES_NUM = 1
    if TOTAL_VOLUME_ML <= 0:
        TOTAL_VOLUME_ML = 10.0

    if SALT_CONCS:
        res3_wells = res3.wells()
        max_wells_needed = len(SALT_CONCS) * REPLICATES_NUM
        for i, conc in enumerate(SALT_CONCS[:max(1, NUM_SALT if NUM_SALT>0 else len(SALT_CONCS))]):
            high_source = res2.wells()[6 + (i % 6)]
            low_source = res0.wells()[i % len(res0.wells())]
            for r in range(REPLICATES_NUM):
                idx = i * REPLICATES_NUM + r
                if idx >= len(res3_wells):
                    break
                dest = res3_wells[idx]
                Vtotal = 10000.0
                f = 0.0 if SALT_STOCK == 0 else float(conc) / float(SALT_STOCK)
                if f > 1.0:
                    f = 1.0
                high_vol = f * Vtotal
                low_vol = Vtotal - high_vol
                high_per = high_vol / 8.0
                low_per = low_vol / 8.0
                if high_per > 0:
                    p300_multi.transfer(high_per, high_source, dest, new_tip='never')
                if low_per > 0:
                    p300_multi.transfer(low_per, low_source, dest, new_tip='never')

    if SALT_CONCS:
        res4_wells = res4.wells()
        for i, conc in enumerate(SALT_CONCS[:max(1, NUM_SALT if NUM_SALT>0 else len(SALT_CONCS))]):
            target = 2.0 * float(conc)
            high_vol = (target / (SALT_STOCK if SALT_STOCK != 0 else 1.0)) * 10000.0
            if high_vol > 10000.0:
                high_vol = 10000.0
            low_vol = 10000.0 - high_vol
            high_per = high_vol / 8.0
            low_per = low_vol / 8.0
            dest = res4_wells[i % len(res4_wells)]
            high_source = res2.wells()[6 + (i % 6)]
            low_source = res0.wells()[i % len(res0.wells())]
            if high_per > 0:
                p300_multi.transfer(high_per, high_source, dest, new_tip='never')
            if low_per > 0:
                p300_multi.transfer(low_per, low_source, dest, new_tip='never')

    if SALT_CONCS and LIGAND_CONCS:
        vol_well_ul = (TOTAL_VOLUME_ML / 2.0) * REPLICATES_NUM * 1.5 * 1000.0
        ligand_stock_well = res1.wells()[0] if len(res1.wells()) > 0 else None
        if ligand_stock_well:
            for row_idx in range(min(8, len(LIGAND_CONCS))):
                ligand_conc = float(LIGAND_CONCS[row_idx])
                for col_idx in range(min(len(SALT_CONCS), 12)):
                    dest_well = mixing_plate.rows()[row_idx][col_idx]
                    target = 2.0 * ligand_conc
                    vol_ligand = (target / (LIGAND_STOCK if LIGAND_STOCK != 0 else 1.0)) * vol_well_ul
                    if vol_ligand > vol_well_ul:
                        vol_ligand = vol_well_ul
                    vol_buffer = vol_well_ul - vol_ligand
                    p300_single.pick_up_tip()
                    p300_single.transfer(vol_ligand, ligand_stock_well, dest_well, new_tip='never')
                    p300_single.transfer(vol_buffer, res0.wells()[0], dest_well, new_tip='never')
                    p300_single.drop_tip()

    protocol.comment('Template protocol run complete; placeholders indicate values for substitution.')
