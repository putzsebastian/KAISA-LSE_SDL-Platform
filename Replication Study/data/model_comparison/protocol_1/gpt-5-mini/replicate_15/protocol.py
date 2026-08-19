from opentrons import protocol_api
import math

metadata = {
    'protocolName': 'Salt and Ligand Prep Template',
    'author': 'Lab 167',
    'description': 'Prepare salt gradients in reservoirs and ligand dilutions in a deep-well mixing plate using placeholders'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Placeholders (literal strings for substitution)
    PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
    PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
    PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
    PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
    PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
    PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

    # helpers to detect unreplaced tokens (build brackets at runtime)
    def _unreplaced(s: str) -> bool:
        return str(s).startswith('[' * 2) and str(s).endswith(']' * 2)

    def parse_scalar(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return default
        return cast(s)

    def parse_list(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return list(default)
        return [cast(x) for x in s.split(';') if x.strip()]

    # Parse placeholders with sensible simulation fallbacks
    REPLICATES = parse_scalar(PLACEHOLDER_REPLICATES, default=3, cast=int)
    TOTAL_VOLUME = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=200.0)  # uL per final use
    SALT_CONCS = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=[0, 50, 150, 300])
    LIGAND_CONCS = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS, default=[0.001, 0.01, 0.1, 1, 10, 100, 1000, 10000])
    SALT_STOCK = parse_scalar(PLACEHOLDER_SALT_STOCK_CONCENTRATION, default=1000.0)
    LIGAND_STOCK = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONCENTRATION, default=10000.0)

    NUMBER_OF_SALT_CONCENTRATIONS = parse_scalar(PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS, default=len(SALT_CONCS), cast=int)
    NUMBER_OF_LIGAND_CONCENTRATIONS = parse_scalar(PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS, default=len(LIGAND_CONCS), cast=int)

    # Safety: if explicit number placeholders are unreplaced but lists are provided, derive them
    if NUMBER_OF_SALT_CONCENTRATIONS is None:
        NUMBER_OF_SALT_CONCENTRATIONS = len(SALT_CONCS)
    if NUMBER_OF_LIGAND_CONCENTRATIONS is None:
        NUMBER_OF_LIGAND_CONCENTRATIONS = len(LIGAND_CONCS)

    # Validate layout constraints
    if REPLICATES * NUMBER_OF_SALT_CONCENTRATIONS > 12:
        raise RuntimeError('REPLICATES x NUMBER_OF_SALT_CONCENTRATIONS must not exceed 12 (Reservoir 3 capacity)')

    # Labware
    # Custom labware in slot 1 with simulation fallback
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # Reservoir 4 (empty target)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # Reservoir 3 (target for step 2)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # Reservoir 2 (slot 8)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # Reservoir 1 (slot 9)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # Reservoir 0 (slot 5)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_10])
    # Put tiprack_7 first so multi-channel uses slot7 first (user requested)
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_7, tiprack_4])

    # Initial volume bookkeeping (uL). All reservoir wells initially 14 mL = 14000 uL where specified
    def init_remaining(initial=14000, count=12):
        return [initial for _ in range(count)]

    rem_res2 = init_remaining(14000, 12)
    rem_res1 = init_remaining(14000, 12)
    rem_res0 = init_remaining(14000, 12)

    # Build lists of source wells (objects) for high and low salt pools in a stable order
    # reservoir2: wells 0-5 low, 6-11 high
    low_sources = [reservoir2.wells()[i] for i in range(0, 6)] + [reservoir1.wells()[i] for i in range(2, 7)] + [reservoir0.wells()[i] for i in range(0, 12)]
    low_rem = rem_res2[0:6] + rem_res1[2:7] + rem_res0[0:12]

    high_sources = [reservoir2.wells()[i] for i in range(6, 12)] + [reservoir1.wells()[i] for i in range(7, 12)]
    high_rem = rem_res2[6:12] + rem_res1[7:12]

    # Helper to draw from multichannel pool while holding a tip on the multi-channel pipette
    def draw_multichannel_with_tip(pip, total_ul, pool_wells, pool_remaining, dest):
        remaining_needed = total_ul
        pool_index = 0
        while remaining_needed > 1e-6:
            while pool_index < len(pool_wells) and pool_remaining[pool_index] <= 10:
                pool_index += 1
            if pool_index >= len(pool_wells):
                raise RuntimeError('Multichannel source pool exhausted while trying to withdraw {:.1f} uL'.format(total_ul))
            src = pool_wells[pool_index]
            avail = pool_remaining[pool_index]
            take = min(avail, remaining_needed)
            per_channel = take / 8.0
            # transfer per_channel (pipette is holding tips)
            pip.transfer(per_channel, src, dest, new_tip='never')
            pool_remaining[pool_index] -= take
            remaining_needed -= take

    # Helper to draw from single-channel pool while holding a tip on single-channel pipette
    def draw_single_with_tip(pip, total_ul, pool_wells, pool_remaining, dest):
        remaining_needed = total_ul
        pool_index = 0
        while remaining_needed > 1e-6:
            while pool_index < len(pool_wells) and pool_remaining[pool_index] <= 10:
                pool_index += 1
            if pool_index >= len(pool_wells):
                raise RuntimeError('Single-channel source pool exhausted while trying to withdraw {:.1f} uL'.format(total_ul))
            src = pool_wells[pool_index]
            avail = pool_remaining[pool_index]
            take = min(avail, remaining_needed)
            pip.transfer(take, src, dest, new_tip='never')
            pool_remaining[pool_index] -= take
            remaining_needed -= take

    # Step 2: Fill Reservoir 3 with mixtures of low/high salt (10 mL each)
    protocol.comment('Starting salt gradient creation in Reservoir 3 (slot 6)')
    target_wells_res3 = reservoir3.wells()[: REPLICATES * NUMBER_OF_SALT_CONCENTRATIONS]
    salt_values = sorted([float(x) for x in SALT_CONCS])[:NUMBER_OF_SALT_CONCENTRATIONS]

    dest_index = 0
    for conc in salt_values:
        for rep in range(REPLICATES):
            dest = target_wells_res3[dest_index]
            dest_index += 1
            total_ul = 10000.0
            frac = float(conc) / float(SALT_STOCK) if SALT_STOCK != 0 else 0.0
            vol_high = frac * total_ul
            vol_low = total_ul - vol_high
            protocol.comment(f'Preparing {total_ul} uL at {conc} in {dest} -> high {vol_high:.1f} uL , low {vol_low:.1f} uL')
            # pick up a set of tips and perform both withdrawals with same tips
            p300m.pick_up_tip()
            if vol_high > 0.5:
                draw_multichannel_with_tip(p300m, vol_high, high_sources, high_rem, dest)
            if vol_low > 0.5:
                draw_multichannel_with_tip(p300m, vol_low, low_sources, low_rem, dest)
            # mix
            mix_vol = min(200, p300m.max_volume)
            p300m.mix(5, mix_vol, dest)
            p300m.drop_tip()

    # Step 3: For each salt conc, prepare 1 well in Reservoir 4 at 2x concentration (10 mL)
    protocol.comment('Preparing Reservoir 4 (slot 3) with 2x salt concentrations')
    for idx, conc in enumerate(salt_values):
        dest = reservoir4.wells()[idx]
        total_ul = 10000.0
        target_conc = conc * 2.0
        frac = float(target_conc) / float(SALT_STOCK) if SALT_STOCK != 0 else 0.0
        vol_high = frac * total_ul
        vol_low = total_ul - vol_high
        protocol.comment(f'Preparing {total_ul} uL at 2x {target_conc} in {dest} -> high {vol_high:.1f} uL , low {vol_low:.1f} uL')
        p300m.pick_up_tip()
        if vol_high > 0.5:
            draw_multichannel_with_tip(p300m, vol_high, high_sources, high_rem, dest)
        if vol_low > 0.5:
            draw_multichannel_with_tip(p300m, vol_low, low_sources, low_rem, dest)
        p300m.mix(5, min(200, p300m.max_volume), dest)
        p300m.drop_tip()

    # Step 4: Prepare ligand dilutions in mixing plate (deep well)
    protocol.comment('Preparing ligand dilutions in mixing plate (slot 11)')
    ligand_values = sorted([float(x) for x in LIGAND_CONCS])[:NUMBER_OF_LIGAND_CONCENTRATIONS]
    salt_count = NUMBER_OF_SALT_CONCENTRATIONS
    total_per_well = (TOTAL_VOLUME / 2.0) * REPLICATES * 1.5

    # Pools for single-channel
    low_salt_pool_single = [reservoir0.wells()[i] for i in range(0, 12)]
    low_salt_rem_single = rem_res0
    ligand_pool = [reservoir1.wells()[0], reservoir1.wells()[1]]
    ligand_rem = [rem_res1[0], rem_res1[1]]

    # Ensure ligand rows do not exceed 8
    if NUMBER_OF_LIGAND_CONCENTRATIONS > 8:
        raise RuntimeError('NUMBER_OF_LIGAND_CONCENTRATIONS cannot exceed 8 (rows A-H)')

    for col_idx in range(salt_count):
        for row_idx, ligand_conc in enumerate(ligand_values[:NUMBER_OF_LIGAND_CONCENTRATIONS]):
            dest = mixing_plate.wells()[row_idx * 12 + col_idx]
            target_conc = float(ligand_conc) * 2.0
            vol_stock = (target_conc / float(LIGAND_STOCK)) * total_per_well if LIGAND_STOCK != 0 else 0.0
            use_low_stock = False
            stock_index = 0
            if vol_stock < 20.0:
                low_stock_conc = float(LIGAND_STOCK) / 10.0
                vol_stock = (target_conc / low_stock_conc) * total_per_well if low_stock_conc != 0 else 0.0
                use_low_stock = True
                stock_index = 1
            vol_low_salt = total_per_well - vol_stock
            protocol.comment(f'Preparing ligand well {dest} total {total_per_well:.1f} uL at {target_conc} -> stock {vol_stock:.1f} uL (use_low_stock={use_low_stock})')
            # pick up a single tip and do both stock and low salt additions
            p300s.pick_up_tip()
            if vol_stock > 0.5:
                draw_single_with_tip(p300s, vol_stock, [ligand_pool[stock_index]], [ligand_rem[stock_index]], dest)
            if vol_low_salt > 0.5:
                draw_single_with_tip(p300s, vol_low_salt, low_salt_pool_single, low_salt_rem_single, dest)
            p300s.mix(5, min(200, p300s.max_volume), dest)
            p300s.drop_tip()

    protocol.comment('Protocol complete')
