from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Dilution Template',
    'author': 'Lab 167',
    'description': 'OT-2 protocol template with placeholders for replicates, salt and ligand concentrations.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Helper helpers to gracefully handle unreplaced placeholders during simulation
    def _unreplaced(val):
        try:
            s = str(val)
        except Exception:
            return False
        return s.strip().startswith('[[') and s.strip().endswith(']]')

    def _to_int(val, default=1):
        if _unreplaced(val):
            return default
        try:
            return int(float(str(val).strip()))
        except Exception:
            return default

    def _to_float(val, default=1.0):
        if _unreplaced(val):
            return default
        try:
            return float(str(val).strip())
        except Exception:
            return default

    def _to_list_of_floats(val, default=None):
        if default is None:
            default = []
        if _unreplaced(val):
            return default
        s = str(val).strip()
        if s == '':
            return default
        pieces = [p.strip() for p in s.split(';') if p.strip() != '']
        try:
            return [float(p) for p in pieces]
        except Exception:
            return default

    # Placeholders (strings by design; templating system will replace them)
    REPLICATES = '[[REPLICATES]]'
    TOTAL_VOLUME = '[[TOTAL_VOLUME]]'  # in mL
    SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'  # semicolon-separated values
    LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'  # semicolon-separated values
    SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
    NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

    # Convert placeholders to numeric with safe fallbacks when unreplaced
    REPLICATES_INT = _to_int(REPLICATES, default=1)
    TOTAL_VOLUME_ML = _to_float(TOTAL_VOLUME, default=10.0)
    SALT_CONCS = _to_list_of_floats(SALT_CONCENTRATIONS, default=[])
    LIGAND_CONCS = _to_list_of_floats(LIGAND_CONCENTRATIONS, default=[])
    SALT_STOCK = _to_float(SALT_STOCK_CONCENTRATION, default=1.0)
    LIGAND_STOCK = _to_float(LIGAND_STOCK_CONCENTRATION, default=1.0)
    NUM_SALT_CONCS = _to_int(NUMBER_OF_SALT_CONCENTRATIONS, default=len(SALT_CONCS))
    NUM_LIGAND_CONCS = _to_int(NUMBER_OF_LIGAND_CONCENTRATIONS, default=len(LIGAND_CONCS))

    # Deck setup per user specification
    # Slot 1: Custom labware with fallback for simulation
    try:
        cytiva_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc).lower():
            raise
        protocol.comment('WARNING: custom labware not available; using a standard plate as a SIMULATION fallback only.')
        cytiva_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Slot 3 - Reservoir 4 (empty)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Slot 6 - Reservoir 3 (empty)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Slot 8 - Reservoir 2
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Slot 9 - Reservoir 1
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Slot 5 - Reservoir 0

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)  # Slot 11 - Mixing Plate

    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_slot10])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_slot7])

    # Ligand stocks and buffers (as described by user)
    ligand_stock_high = reservoir1.wells()[0]
    ligand_stock_low = reservoir1.wells()[1]

    low_buffer_source = reservoir0.wells()[0]
    high_buffer_source = reservoir2.wells()[6]  # a high-salt buffer well in Reservoir 2

    # Step 2: Fill Reservoir 3 replicates with 10 mL each at ascending salt concentrations
    idx = 0
    for conc_index, conc in enumerate(SALT_CONCS[:max(0, NUM_SALT_CONCS)]):
        for rep in range(REPLICATES_INT):
            if idx >= len(reservoir3.wells()):
                break
            target_well = reservoir3.wells()[idx]
            # Volume math: full 10 mL total per well; high portion follows conc/stock ratio
            total_vol_ul = 10000.0
            high_total_ul = total_vol_ul * (conc / SALT_STOCK if SALT_STOCK != 0 else 0.0)
            low_total_ul = total_vol_ul - high_total_ul
            high_per_channel = max(0.0, high_total_ul / 8.0)
            low_per_channel = max(0.0, low_total_ul / 8.0)
            high_source = reservoir2.wells()[6 + (conc_index % 6)]
            low_source = reservoir0.wells()[conc_index % 12]
            if high_per_channel > 0:
                p300_multi.transfer(high_per_channel, high_source, target_well, new_tip='never')
            if low_per_channel > 0:
                p300_multi.transfer(low_per_channel, low_source, target_well, new_tip='never')
            idx += 1
            # If we run out of wells, stop
            if idx >= len(reservoir3.wells()):
                break

    # Step 3: Fill Reservoir 4 wells with 2x the required salt concentrations
    for conc_index, conc in enumerate(SALT_CONCS[:max(0, NUM_SALT_CONCS)]):
        target_well = reservoir4.wells()[conc_index]
        total_vol_ul = 10000.0
        high_total_ul = total_vol_ul * (2.0 * conc / (SALT_STOCK if SALT_STOCK != 0 else 1.0))
        low_total_ul = total_vol_ul - high_total_ul
        high_per_channel = max(0.0, high_total_ul / 8.0)
        low_per_channel = max(0.0, low_total_ul / 8.0)
        high_source = reservoir2.wells()[6 + (conc_index % 6)]
        low_source = reservoir0.wells()[conc_index % 12]
        if high_per_channel > 0:
            p300_multi.transfer(high_per_channel, high_source, target_well, new_tip='never')
        if low_per_channel > 0:
            p300_multi.transfer(low_per_channel, low_source, target_well, new_tip='never')

    # Step 4: Dilutions in the mixing plate for ligand concentrations (2x logic)
    ligand_concs_2x = [lc * 2.0 for lc in LIGAND_CONCS[:max(0, NUM_LIGAND_CONCS)]]
    # Ligand stock wells (use high unless too small, then use low)
    ligand_stock_well_high = reservoir1.wells()[0]
    ligand_stock_well_low = reservoir1.wells()[1]
    stock_high_vol = 14000.0

    # Destination grid: rows A-H, columns 1..NUM_SALT_CONCS
    for col in range(min(NUM_SALT_CONCS, len(SALT_CONCS))):
        for row in range(min(NUM_LIGAND_CONCS, len(LIGAND_CONCS))):
            dest = mixing_plate.rows()[row][col]
            if total_vol_ul is None:
                total_vol_ul = 10000.0
            final_vol_ul = (TOTAL_VOLUME_ML / 2.0) * REPLICATES_INT * 1.5 * 1000.0
            target_conc_2x = ligand_concs_2x[row] if row < len(ligand_concs_2x) else ligand_concs_2x[-1]
            # Compute stock volume needed to achieve target concentration
            stock_conc = LIGAND_STOCK if LIGAND_STOCK != 0 else 1.0
            stock_vol_ul = final_vol_ul * (target_conc_2x / stock_conc)
            if stock_vol_ul > stock_high_vol:
                # Use diluted stock
                stock_source = ligand_stock_well_low
                stock_conc_eff = LIGAND_STOCK / 10.0
                stock_vol_ul = final_vol_ul * (target_conc_2x / stock_conc_eff)
            else:
                stock_source = ligand_stock_well_high
                stock_conc_eff = LIGAND_STOCK
            diluent_vol_ul = final_vol_ul - stock_vol_ul
            if stock_vol_ul > 0:
                p300_single.transfer(stock_vol_ul, stock_source, dest, new_tip='never')
            if diluent_vol_ul > 0:
                p300_single.transfer(diluent_vol_ul, reservoir0.wells()[0], dest, new_tip='never')

    protocol.comment('Template protocol complete.')
