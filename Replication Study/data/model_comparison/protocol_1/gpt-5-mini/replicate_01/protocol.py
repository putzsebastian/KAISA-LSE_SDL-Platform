from opentrons import protocol_api

metadata = {
    'protocolName': 'Template: Salt and Ligand Preparation',
    'author': 'Lab 167',
    'description': 'Prepare salt gradients in reservoirs and ligand dilutions in a deep-well mixing plate using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings for substitution)
REPLICATES = '[[REPLICATES]]'
TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    # built brackets so tokens are detectable in the final templating step but safe for python now
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def parse_list(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return [cast(x) for x in default]
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with safe fallbacks for simulation
    replicates = parse_scalar(REPLICATES, 3, int)
    total_volume = parse_scalar(TOTAL_VOLUME, 100.0, float)  # in uL
    salt_concs = parse_list(SALT_CONCENTRATIONS, [0, 50, 100, 200], float)
    ligand_concs = parse_list(LIGAND_CONCENTRATIONS, [1,2,4,8,16,32,64,128], float)
    salt_stock = parse_scalar(SALT_STOCK_CONCENTRATION, 1000.0, float)
    ligand_stock = parse_scalar(LIGAND_STOCK_CONCENTRATION, 1000.0, float)

    # Derived counts
    n_salt = parse_scalar(NUMBER_OF_SALT_CONCENTRATIONS, len(salt_concs), int)
    n_ligand = parse_scalar(NUMBER_OF_LIGAND_CONCENTRATIONS, len(ligand_concs), int)

    # Safety checks (simulation-time only)
    if replicates * n_salt > 12:
        protocol.comment('WARNING: REPLICATES * NUMBER_OF_SALT_CONCENTRATIONS exceeds 12; simulation will proceed but real run may fail.')

    # Labware
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware not found; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # dest for 2x salt set
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)  # dest for salt gradient replicates
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)  # source: contains low and high salt wells
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)  # source: contains ligand stock and some buffers
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)  # source: low salt buffers

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack10])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack7])

    # Initialize a ledger for source volumes (uL)
    INITIAL_SRC_VOL = 14000  # 14 mL in uL
    src_remaining = {}

    # Mark which wells are low-salt and high-salt in reservoir_2 (slot8)
    # reservoir_2 wells: 0-5 low salt, 6-11 high salt
    for i, w in enumerate(reservoir_2.wells()):
        src_remaining[w] = INITIAL_SRC_VOL

    # reservoir_1 wells: well0: ligand high stock, well1: ligand low stock, wells2-6 low salt, wells7-11 high salt
    for i, w in enumerate(reservoir_1.wells()):
        src_remaining[w] = INITIAL_SRC_VOL

    # reservoir_0 wells: all low salt
    for i, w in enumerate(reservoir_0.wells()):
        src_remaining[w] = INITIAL_SRC_VOL

    # Helper to consume from a pool of wells for multi-channel transfers (volumes in uL total to DEST well)
    def consume_pool_multi_with_tip(pool_wells, volume_total_ul, dest, pip):
        """Assumes pipette already has a tip. Takes volume_total_ul from pool_wells and transfers to dest.
        All volumes are total (not per-channel)."""
        remaining = volume_total_ul
        for src in pool_wells:
            if remaining <= 0:
                break
            avail = src_remaining[src]
            while avail > 0 and remaining > 0:
                max_take = min(avail, 8 * pip.max_volume)
                take = min(max_take, remaining)
                per_channel = take / 8.0
                pip.transfer(per_channel, src, dest, new_tip='never')
                src_remaining[src] -= take
                avail -= take
                remaining -= take
        if remaining > 0:
            raise RuntimeError(f'Pool ran dry when trying to transfer {volume_total_ul} uL to {dest}')
        try:
            pip.mix(3, min(200, pip.max_volume), dest)
        except Exception:
            pass

    # Helper to consume from pool for single-channel volumes (total uL)
    def consume_pool_single(pool_wells, volume_total_ul, dest, pip):
        remaining = volume_total_ul
        pip.pick_up_tip()
        for src in pool_wells:
            if remaining <= 0:
                break
            avail = src_remaining[src]
            while avail > 0 and remaining > 0:
                max_take = min(avail, pip.max_volume)
                take = min(max_take, remaining)
                pip.transfer(take, src, dest, new_tip='never')
                src_remaining[src] -= take
                avail -= take
                remaining -= take
        if remaining > 0:
            raise RuntimeError(f'Pool ran dry when trying to transfer {volume_total_ul} uL to {dest}')
        try:
            pip.mix(3, min(200, pip.max_volume), dest)
        except Exception:
            pass
        pip.drop_tip()

    # Build pools
    low_pool = []
    high_pool = []
    # reservoir_2 wells 0-5 low, 6-11 high
    for i, w in enumerate(reservoir_2.wells()):
        if i <= 5:
            low_pool.append(w)
        else:
            high_pool.append(w)
    # reservoir_1 wells with low salt (2-6) and high salt (7-11)
    for i, w in enumerate(reservoir_1.wells()):
        if 2 <= i <= 6:
            low_pool.append(w)
        if i >= 7:
            high_pool.append(w)
    # reservoir_0 all low
    for w in reservoir_0.wells():
        low_pool.append(w)

    # Step 2: For each required salt concentration, fill up REPLICATES wells in Reservoir 3 (slot6) with total 10 mL
    protocol.comment('Step 2: Preparing salt gradients in Reservoir 3')
    total_target_ul = 10000  # 10 mL
    r3_wells = reservoir_3.wells()
    r3_index = 0

    # Pick up a single multi-channel tip and reuse for all multi-channel reservoir operations to conserve tips
    p300m.pick_up_tip()
    for sc in salt_concs[:n_salt]:
        for rep in range(replicates):
            if r3_index >= len(r3_wells):
                raise RuntimeError('Not enough wells in Reservoir 3 for requested replicates and salt concentrations')
            dest = r3_wells[r3_index]
            v_high = total_target_ul * (sc / salt_stock)
            v_low = total_target_ul - v_high
            if v_low > 0:
                consume_pool_multi_with_tip(low_pool, v_low, dest, p300m)
            if v_high > 0:
                consume_pool_multi_with_tip(high_pool, v_high, dest, p300m)
            r3_index += 1

    # Step 3: For each required salt concentration, fill up 1 well in Reservoir 4 with total 10 mL at 2x concentration
    protocol.comment('Step 3: Preparing 2x salt solutions in Reservoir 4')
    r4_wells = reservoir_4.wells()
    r4_index = 0
    for sc in salt_concs[:n_salt]:
        if r4_index >= len(r4_wells):
            raise RuntimeError('Not enough wells in Reservoir 4 for requested salt concentrations')
        dest = r4_wells[r4_index]
        sc_2x = sc * 2.0
        v_high = total_target_ul * (sc_2x / salt_stock)
        v_low = total_target_ul - v_high
        if v_low > 0:
            consume_pool_multi_with_tip(low_pool, v_low, dest, p300m)
        if v_high > 0:
            consume_pool_multi_with_tip(high_pool, v_high, dest, p300m)
        r4_index += 1
    p300m.drop_tip()

    # Step 4: In the deep-well mixing plate create ligand dilutions (2x ligand concentrations)
    protocol.comment('Step 4: Preparing ligand dilutions in the mixing plate')
    # Total volume for each well: TOTAL_VOLUME/2 * REPLICATES * 1.5
    per_well_total = (total_volume / 2.0) * replicates * 1.5

    # Build mapping of columns: one column per salt concentration
    n_cols_needed = n_salt
    if n_cols_needed > 12:
        raise RuntimeError('Too many salt concentrations for mixing plate columns')

    # Ensure ligand concentrations length is 8 (rows A-H); if fewer, repeat or truncate
    if len(ligand_concs) < 8:
        times = (8 // len(ligand_concs)) + 1
        ligand_concs = (ligand_concs * times)[:8]
    elif len(ligand_concs) > 8:
        ligand_concs = ligand_concs[:8]

    # Pools for ligand stock: reservoir_1 well0 = high stock, well1 = low stock
    ligand_high = reservoir_1.wells()[0]
    ligand_low = reservoir_1.wells()[1]

    for col_idx in range(n_cols_needed):
        for row_idx in range(8):
            row = mixing_plate.rows()[row_idx]
            dest = row[col_idx]
            ligand_target_conc = ligand_concs[row_idx] * 2.0
            v_stock = (ligand_target_conc * per_well_total) / ligand_stock
            stock_source = ligand_high
            if v_stock < 20:
                stock_source = ligand_low
                v_stock = (ligand_target_conc * per_well_total) / (ligand_stock / 10.0)
            v_diluent = per_well_total - v_stock
            consume_pool_single([reservoir_1.wells()[2]] + list(reservoir_0.wells()), v_diluent, dest, p300s)
            consume_pool_single([stock_source], v_stock, dest, p300s)

    protocol.comment('Protocol complete.')
