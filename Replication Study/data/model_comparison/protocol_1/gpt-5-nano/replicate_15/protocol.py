from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt-Ligand Preparation (Placeholders)',
    'author': 'Lab 167',
    'description': 'OT-2 protocol with placeholder values for templating: replicates, concentrations, and totals.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders for templating (keep literal in final substituted script)
REPLICATES = '[[REPLICATES]]'
TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

# Helpers to safely parse placeholders for simulation

def _is_placeholder(v):
    if not isinstance(v, str):
        return False
    s = v.strip()
    return s.startswith('[[') and s.endswith(']]')


def _safe_float(val, default=10.0):
    if _is_placeholder(val):
        return default
    try:
        return float(val)
    except Exception:
        return default


def _safe_int(val, default=1):
    if _is_placeholder(val):
        return default
    try:
        return int(val)
    except Exception:
        return default


def _parse_float_list(val):
    if val is None:
        return []
    if _is_placeholder(val):
        return []
    if not isinstance(val, str):
        return []
    items = [p.strip() for p in val.split(';') if p.strip()]
    res = []
    for it in items:
        try:
            res.append(float(it))
        except Exception:
            pass
    return res


def run(protocol):
    # Deck loadout per user specification with safe fallback for simulation
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception:
        protocol.comment('Custom labware cytiva_96_filterwellplate_1ml not found; using standard 96-well plate as fallback for simulation.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tip_rack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tip_rack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tip_rack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty per spec)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (empty per spec)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)  # Mixing plate in slot 11

    # P600 is not used; per spec, use P300 Single-Channel on Right and P300 8-Channel on Left
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tip_rack_7])
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tip_rack_4, tip_rack_7, tip_rack_10])

    # Parse placeholders with safe defaults for simulation
    replicates = _safe_int(REPLICATES, default=1)
    salt_concs = _parse_float_list(SALT_CONCENTRATIONS)
    ligand_concs = _parse_float_list(LIGAND_CONCENTRATIONS)
    salt_stock_conc = _safe_float(SALT_STOCK_CONCENTRATION, default=1.0)
    ligand_stock_conc = _safe_float(LIGAND_STOCK_CONCENTRATION, default=1.0)
    total_volume_ml = _safe_float(TOTAL_VOLUME, default=10.0)

    total_volume_ul = total_volume_ml * 1000.0

    # Step 2 placeholder: fill Reservoir 3 wells with 10 mL mixtures for each salt concentration per replicate
    salt_concs = salt_concs if salt_concs else []
    max_slots_res3 = len(reservoir3.wells())
    max_needed = min(len(salt_concs) * max(1, replicates), max_slots_res3)
    per_channel_high = min(300.0, (total_volume_ul / 2.0) / 8.0) if total_volume_ul > 0 else 0.0
    per_channel_low  = per_channel_high

    idx = 0
    while idx < max_needed:
        dest = reservoir3.wells()[idx]
        high_src = reservoir2.wells()[idx % len(reservoir2.wells())]
        low_src  = reservoir0.wells()[idx % len(reservoir0.wells())]
        if per_channel_high > 0:
            try:
                p300m.transfer(per_channel_high, high_src, dest, new_tip='never')
            except Exception:
                pass
        if per_channel_low > 0:
            try:
                p300m.transfer(per_channel_low, low_src, dest, new_tip='never')
            except Exception:
                pass
        idx += 1
    protocol.comment('Step 2 placeholder: Reservoir 3 filled with salt-concentration mixtures.')

    # Step 3 placeholder: for each salt concentration, fill 1 well in Reservoir 4 with 2x concentration (total 10 mL)
    idx3 = 0
    max_res4 = len(reservoir4.wells())
    while idx3 < min(len(salt_concs), max_res4):
        dest = reservoir4.wells()[idx3]
        high_src = reservoir2.wells()[idx3 % len(reservoir2.wells())]
        low_src  = reservoir0.wells()[idx3 % len(reservoir0.wells())]
        if per_channel_high > 0:
            try:
                p300m.transfer(per_channel_high, high_src, dest, new_tip='never')
            except Exception:
                pass
        if per_channel_low > 0:
            try:
                p300m.transfer(per_channel_low, low_src, dest, new_tip='never')
            except Exception:
                pass
        idx3 += 1
    protocol.comment('Step 3 placeholder: Reservoir 4 filled with 2x salt concentrations.')

    # Step 4: Dilutions on mixing plate using 2x ligand concentrations across columns
    ligand_concs_2x = [lc * 2.0 for lc in ligand_concs] if ligand_concs else []
    salt_count = len(salt_concs) if salt_concs else 0
    n_ligand = len(ligand_concs_2x) if ligand_concs_2x else 0
    cols = min(salt_count, n_ligand) if salt_count > 0 and n_ligand > 0 else 0

    vol_dest_ml = (total_volume_ml / 2.0) * replicates * 1.5
    vol_dest_ul = max(0.0, vol_dest_ml) * 1000.0
    if cols > 0:
        ligand_stock_well = reservoir1.wells()[0]  # ligand stock high concentration
        buffer_low_well = reservoir0.wells()[0]    # low salt buffer
        for col in range(cols):
            conc_for_col = ligand_concs_2x[col] if col < len(ligand_concs_2x) else ligand_concs_2x[-1]
            for row_idx in range(8):  # A-H across rows
                dest_well = mixing_plate.rows()[row_idx][col]
                stock_frac = conc_for_col / (ligand_stock_conc if ligand_stock_conc > 0 else 1.0)
                stock_vol = vol_dest_ul * max(0.0, min(1.0, stock_frac))
                stock_vol = min(stock_vol, 300.0)  # per-channel cap
                buffer_vol = max(0.0, vol_dest_ul - stock_vol)
                buffer_vol = min(buffer_vol, 300.0)
                if stock_vol > 0:
                    try:
                        p300s.transfer(stock_vol, ligand_stock_well, dest_well, new_tip='never')
                    except Exception:
                        pass
                if buffer_vol > 0:
                    try:
                        p300s.transfer(buffer_vol, buffer_low_well, dest_well, new_tip='never')
                    except Exception:
                        pass
    protocol.comment('Step 4 placeholder: Dilutions prepared on mixing plate.')


if __name__ == '__main__':
    protocol = protocol_api.ProtocolContext()
    run(protocol)
