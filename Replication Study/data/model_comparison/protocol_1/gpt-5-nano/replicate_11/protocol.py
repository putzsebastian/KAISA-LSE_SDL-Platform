from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Template with Placeholders',
    'author': 'Lab 167',
    'description': 'OT-2 protocol template using placeholders for replicates and concentrations',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder handling helpers (keep placeholders as strings for templating)

def _unreplaced(token: str) -> bool:
    return isinstance(token, str) and token.startswith('[[') and token.endswith(']]')


def _to_int(token: str, default: int) -> int:
    if _unreplaced(token):
        return int(default)
    try:
        return int(float(token))
    except Exception:
        return int(default)


def _to_float(token: str, default: float) -> float:
    if _unreplaced(token):
        return float(default)
    try:
        return float(token)
    except Exception:
        return float(default)


def _parse_float_list(token: str, default: list) -> list:
    if _unreplaced(token):
        return list(default)
    try:
        parts = [p for p in str(token).split(';') if p.strip()]
        return [float(p) for p in parts]
    except Exception:
        return list(default)


# Placeholders (strings will be substituted by wizard; defaults provide simulation fallbacks)
REPLICATES = '[[REPLICATES]]'
TOTAL_VOLUME = '[[TOTAL_VOLUME]]'  # ml per well in Step 4 total volume context
SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'  # semicolon-delimited
LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'  # semicolon-delimited
SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

# Resolve placeholders with safe defaults when not replaced (simulation fallbacks)
REPLICATES_N = _to_int(REPLICATES, 2)
TOTAL_VOLUME_ML = _to_float(TOTAL_VOLUME, 10.0)
SALT_CONCS = _parse_float_list(SALT_CONCENTRATIONS, [0.1, 0.2, 0.3, 0.4])
LIGAND_CONCS = _parse_float_list(LIGAND_CONCENTRATIONS, [0.1, 0.2])
SALT_STOCK_CONC = _to_float(SALT_STOCK_CONCENTRATION, 1.0)
LIGAND_STOCK_CONC = _to_float(LIGAND_STOCK_CONCENTRATION, 1.0)
NUM_SALT_CONC = _to_int(NUMBER_OF_SALT_CONCENTRATIONS, len(SALT_CONCS) if len(SALT_CONCS) > 0 else 2)
NUM_LIGAND_CONC = _to_int(NUMBER_OF_LIGAND_CONCENTRATIONS, len(LIGAND_CONCS) if len(LIGAND_CONCS) > 0 else 2)

# Use provided concentrations or defaults if empty
if not SALT_CONCS:
    SALT_CONCS = [0.1, 0.2]
if not LIGAND_CONCS:
    LIGAND_CONCS = [0.1, 0.2]

# Constants derived from protocol requirements
PER_CHANNEL_VOL_UL = (10.0 * 1000.0) / 8.0  # 1250 uL per channel for a 10 mL total


def run(protocol: protocol_api.ProtocolContext):
    # Deck setup: Tip racks and pipettes
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_slot4, tiprack_slot7])
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_slot10])

    # Labware per deck layout
    # Slot 1: Custom labware (cytiva). Use fallback if not available in simulation
    try:
        cytiva_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception:
        protocol.comment('Custom labware not found; using fallback labware for simulation on slot 1')
        cytiva_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5, 'Reservoir 0')  # Low salt buffer pool
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9, 'Reservoir 1')  # Ligand stock
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8, 'Reservoir 2')  # Salt buffers (low/high)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6, 'Reservoir 3')  # Will hold salt concs (10 mL each)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3, 'Reservoir 4')  # Reserved for Step 3 use

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11, 'Mixing Plate')

    # Step 2 and Step 3 require reservoirs 2, 3, and 4 - map as per deck description
    low_salt_source = reservoir_2.wells()[0]
    high_salt_source = reservoir_2.wells()[6]
    ligand_stock_high = reservoir_1.wells()[0]
    ligand_stock_low = reservoir_1.wells()[1]
    low_buffer = reservoir_0.wells()[0]

    # Step 2: Fill Reservoir 3 wells (reservoir_3) with 10 mL total per required salt conc
    res3_wells = reservoir_3.wells()
    reps = REPLICATES_N
    for c_idx, conc in enumerate(SALT_CONCS[:NUM_SALT_CONC]):
        # fraction of high salt per target well
        frac_high = conc / SALT_STOCK_CONC if SALT_STOCK_CONC != 0 else 0
        if frac_high < 0:
            frac_high = 0
        if frac_high > 1:
            frac_high = 1
        vol_high_per_channel_ul = PER_CHANNEL_VOL_UL * frac_high
        vol_low_per_channel_ul = PER_CHANNEL_VOL_UL * (1.0 - frac_high)
        for r in range(reps):
            idx = c_idx * reps + r
            if idx >= len(res3_wells):
                break
            target_well = res3_wells[idx]
            p300m.transfer(vol_low_per_channel_ul, low_salt_source, target_well, new_tip='never')
            p300m.transfer(vol_high_per_channel_ul, high_salt_source, target_well, new_tip='never')
    p300m.drop_tip()

    # Step 3: For each required salt conc, fill 1 well in Reservoir 4 with 2x conc (10 mL total)
    res4_wells = reservoir_4.wells()
    for i, conc in enumerate(SALT_CONCS[:NUM_SALT_CONC]):
        two_x = (2.0 * conc) / SALT_STOCK_CONC if SALT_STOCK_CONC != 0 else 0
        if two_x > 1:
            two_x = 1
        vol_high_per_well_ul = PER_CHANNEL_VOL_UL * two_x
        vol_low_per_well_ul = PER_CHANNEL_VOL_UL - vol_high_per_well_ul
        target_well = res4_wells[i]  # ensure within bounds
        p300m.transfer(vol_low_per_well_ul, low_salt_source, target_well, new_tip='never')
        p300m.transfer(vol_high_per_well_ul, high_salt_source, target_well, new_tip='never')
    # Do not force a drop_tip here; re-use tips for subsequent steps if desired

    # Step 4: Dilutions in Mixing Plate using single-channel pipette (slot 10 tip rack)
    # Mixing plate dilution volumes: TOTAL_VOLUME_ML / 2 * REPLICATES * 1.5
    mixing_well_volume_ml = (TOTAL_VOLUME_ML / 2.0) * REPLICATES_N * 1.5
    mixing_well_volume_ul = mixing_well_volume_ml * 1000.0

    ligand_stock_source_high = reservoir_1.wells()[0]  # High concentration stock
    ligand_stock_source_low = reservoir_1.wells()[1]   # Lower stock if needed
    stock_conc = LIGAND_STOCK_CONC
    stock_conc_low = LIGAND_STOCK_CONC / 10.0
    low_buffer_for_dilution = reservoir_0.wells()[0]

    # 8-channel p300 not used here; single-channel pipette handles row-wise dilutions across columns
    # Use tip rack on slot 10 for single-channel access
    # We'll reuse p300s (already loaded on right)
    p300s.pick_up_tip()
    max_cols = min(len(LIGAND_CONCS), 12)  # ensure within plate dimension
    # Ensure we iterate over 12 columns max and 8 rows (A-H)
    for col in range(min(NUM_SALT_CONC, 12)):
        for row_index, row_label in enumerate(['A','B','C','D','E','F','G','H']):
            dest_well = mixing_plate.rows()[row_index][col]
            ligand_conc = LIGAND_CONCS[row_index % len(LIGAND_CONCS)] if LIGAND_CONCS else 0.0
            final_conc = 2.0 * ligand_conc
            stock_frac = final_conc / stock_conc if stock_conc != 0 else 0
            stock_frac = max(0.0, min(1.0, stock_frac))
            stock_vol = mixing_well_volume_ul * stock_frac
            buffer_vol = mixing_well_volume_ul - stock_vol
            # If stock_vol would be too small, use diluted stock
            if stock_vol < 20.0:
                stock_vol = mixing_well_volume_ul * (final_conc / stock_conc_low) if stock_conc_low != 0 else 0.0
                stock_source = ligand_stock_source_low
            else:
                stock_source = ligand_stock_source_high
            p300s.transfer(stock_vol, stock_source, dest_well, new_tip='never')
            p300s.transfer(buffer_vol, low_buffer_for_dilution, dest_well, new_tip='never')
    p300s.drop_tip()
