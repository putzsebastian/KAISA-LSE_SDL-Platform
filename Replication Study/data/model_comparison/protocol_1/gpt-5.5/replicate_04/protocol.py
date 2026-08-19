from opentrons import protocol_api

metadata = {
    'protocolName': 'Templated Salt and Ligand Dilution Preparation',
    'author': 'Lab 167',
    'description': 'Prepare salt buffers in reservoirs and ligand dilutions in a deep-well mixing plate using placeholder parameters.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # -------------------------
    # Placeholder declarations
    # -------------------------
    PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
    PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
    PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
    PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
    PLACEHOLDER_SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    PLACEHOLDER_LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
    PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

    # -------------------------
    # Simulation-safe placeholder parsing
    # -------------------------
    def _unreplaced(value):
        s = str(value).strip()
        return s.startswith('[' * 2) and s.endswith(']' * 2)

    def parse_scalar(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return default
        return cast(float(s))

    def parse_list(value, default, cast=float):
        s = str(value).strip()
        if _unreplaced(s):
            return list(default)
        return [cast(x.strip()) for x in s.split(';') if x.strip()]

    # Worst-case simulation defaults exercise 12 salt columns and 8 ligand rows.
    default_salt_concentrations = [0, 45, 90, 135, 180, 225, 270, 315, 360, 405, 450, 500]
    default_ligand_concentrations = [0, 1, 2, 5, 10, 25, 50, 100]

    replicates = parse_scalar(PLACEHOLDER_REPLICATES, 1, int)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 300.0, float)
    salt_concentrations = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default_salt_concentrations,
        float
    )
    ligand_concentrations = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        default_ligand_concentrations,
        float
    )
    salt_stock_concentration = parse_scalar(
        PLACEHOLDER_SALT_STOCK_CONCENTRATION,
        1000.0,
        float
    )
    ligand_stock_concentration = parse_scalar(
        PLACEHOLDER_LIGAND_STOCK_CONCENTRATION,
        1000.0,
        float
    )
    number_of_salt_concentrations = parse_scalar(
        PLACEHOLDER_NUMBER_OF_SALT_CONCENTRATIONS,
        len(salt_concentrations),
        int
    )
    number_of_ligand_concentrations = parse_scalar(
        PLACEHOLDER_NUMBER_OF_LIGAND_CONCENTRATIONS,
        len(ligand_concentrations),
        int
    )

    salt_concentrations = sorted(salt_concentrations)
    ligand_concentrations = sorted(ligand_concentrations)

    # -------------------------
    # Labware
    # -------------------------
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition cytiva_96_filterwellplate_1ml not available; '
            'using NEST 96 well plate as a SIMULATION fallback only.'
        )
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_slot_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    multi_tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    single_tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------
    # Pipettes
    # -------------------------
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[single_tiprack]
    )
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[multi_tiprack]
    )

    # Keep slot 4 tip rack loaded according to the requested deck layout.
    _ = filter_plate, tiprack_slot_4

    # -------------------------
    # Validation and calculations
    # -------------------------
    if replicates < 1:
        raise RuntimeError('[[REPLICATES]] must be at least 1.')
    if number_of_salt_concentrations != len(salt_concentrations):
        raise RuntimeError(
            '[[NUMBER_OF_SALT_CONCENTRATIONS]] must equal the number of values in [[SALT_CONCENTRATIONS]].'
        )
    if number_of_ligand_concentrations != len(ligand_concentrations):
        raise RuntimeError(
            '[[NUMBER_OF_LIGAND_CONCENTRATIONS]] must equal the number of values in [[LIGAND_CONCENTRATIONS]].'
        )
    if len(salt_concentrations) < 1 or len(salt_concentrations) > 12:
        raise RuntimeError('The number of salt concentrations must be between 1 and 12.')
    if len(ligand_concentrations) < 1 or len(ligand_concentrations) > 8:
        raise RuntimeError('The number of ligand concentrations must be between 1 and 8.')
    if replicates * len(salt_concentrations) > 12:
        raise RuntimeError(
            'Reservoir 3 has 12 wells: [[REPLICATES]] x [[NUMBER_OF_SALT_CONCENTRATIONS]] must not exceed 12.'
        )
    if salt_stock_concentration <= 0:
        raise RuntimeError('[[SALT_STOCK_CONCENTRATION]] must be greater than 0.')
    if ligand_stock_concentration <= 0:
        raise RuntimeError('[[LIGAND_STOCK_CONCENTRATION]] must be greater than 0.')

    target_reservoir_total_ul = 10000.0
    deepwell_target_total_ul = total_volume / 2.0 * replicates * 1.5
    if deepwell_target_total_ul > 2000.0:
        raise RuntimeError(
            'Calculated mixing-plate volume exceeds the 2 mL deep-well capacity: '
            f'{deepwell_target_total_ul:.2f} uL.'
        )

    for c in salt_concentrations:
        if c < 0:
            raise RuntimeError('Salt concentrations must not be negative.')
        if c > salt_stock_concentration:
            raise RuntimeError(
                f'Salt concentration {c} exceeds [[SALT_STOCK_CONCENTRATION]].'
            )
        if 2.0 * c > salt_stock_concentration:
            raise RuntimeError(
                f'2x salt concentration {2.0 * c} exceeds [[SALT_STOCK_CONCENTRATION]].'
            )
    for c in ligand_concentrations:
        if c < 0:
            raise RuntimeError('Ligand concentrations must not be negative.')
        if 2.0 * c > ligand_stock_concentration:
            raise RuntimeError(
                f'2x ligand concentration {2.0 * c} exceeds [[LIGAND_STOCK_CONCENTRATION]].'
            )

    # -------------------------
    # Source liquid pools with manual volume tracking.
    # Amounts are actual reservoir-well amounts in uL.
    # For the 8-channel pipette, each per-channel aspiration removes 8x that amount.
    # -------------------------
    initial_reservoir_volume_ul = 14000.0
    dead_volume_ul = 100.0

    low_salt_pool = []
    high_salt_pool = []

    for well in reservoir_2.wells()[:6]:
        low_salt_pool.append({'well': well, 'remaining': initial_reservoir_volume_ul})
    for well in reservoir_2.wells()[6:12]:
        high_salt_pool.append({'well': well, 'remaining': initial_reservoir_volume_ul})
    for well in reservoir_1.wells()[2:7]:
        low_salt_pool.append({'well': well, 'remaining': initial_reservoir_volume_ul})
    for well in reservoir_1.wells()[7:12]:
        high_salt_pool.append({'well': well, 'remaining': initial_reservoir_volume_ul})
    for well in reservoir_0.wells():
        low_salt_pool.append({'well': well, 'remaining': initial_reservoir_volume_ul})

    ligand_high_pool = [{'well': reservoir_1.wells()[0], 'remaining': initial_reservoir_volume_ul}]
    ligand_low_pool = [{'well': reservoir_1.wells()[1], 'remaining': initial_reservoir_volume_ul}]

    def transfer_from_pool(pipette, pool, actual_volume_ul, dest, channel_count, label):
        if actual_volume_ul <= 0.000001:
            return
        remaining_request = actual_volume_ul
        for source in pool:
            available_from_source = max(0.0, source['remaining'] - dead_volume_ul)
            if available_from_source <= 0.0:
                continue
            use_from_source = min(remaining_request, available_from_source)
            while use_from_source > 0.000001:
                actual_chunk = min(use_from_source, pipette.max_volume * channel_count)
                per_channel_chunk = actual_chunk / channel_count
                pipette.aspirate(per_channel_chunk, source['well'])
                pipette.dispense(per_channel_chunk, dest.top())
                source['remaining'] -= actual_chunk
                use_from_source -= actual_chunk
                remaining_request -= actual_chunk
            if remaining_request <= 0.000001:
                break
        if remaining_request > 0.000001:
            raise RuntimeError(
                f'Insufficient volume in {label} pool. Shortfall: {remaining_request:.2f} uL.'
            )

    reservoir_3_plan = []
    reservoir_4_plan = []

    for salt_index, salt_conc in enumerate(salt_concentrations):
        high_fraction = salt_conc / salt_stock_concentration
        high_volume = target_reservoir_total_ul * high_fraction
        low_volume = target_reservoir_total_ul - high_volume
        for replicate_index in range(replicates):
            target_index = salt_index * replicates + replicate_index
            reservoir_3_plan.append({
                'well': reservoir_3.wells()[target_index],
                'salt_concentration': salt_conc,
                'low_volume': low_volume,
                'high_volume': high_volume
            })

    for salt_index, salt_conc in enumerate(salt_concentrations):
        high_fraction = (2.0 * salt_conc) / salt_stock_concentration
        high_volume = target_reservoir_total_ul * high_fraction
        low_volume = target_reservoir_total_ul - high_volume
        reservoir_4_plan.append({
            'well': reservoir_4.wells()[salt_index],
            'salt_concentration': 2.0 * salt_conc,
            'low_volume': low_volume,
            'high_volume': high_volume
        })

    all_salt_buffer_targets = reservoir_3_plan + reservoir_4_plan

    # -------------------------
    # Step 2 and 3: prepare salt buffers in Reservoir 3 and 2x salt buffers in Reservoir 4.
    # Use one multi-channel tip for all low-salt-buffer additions and one for all high-salt-buffer additions.
    # -------------------------
    protocol.comment('Preparing Reservoir 3 and Reservoir 4 salt buffers with the P300 multi-channel pipette.')

    p300_multi.pick_up_tip()
    for target in all_salt_buffer_targets:
        transfer_from_pool(
            p300_multi,
            low_salt_pool,
            target['low_volume'],
            target['well'],
            8,
            'low salt buffer'
        )
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target in all_salt_buffer_targets:
        transfer_from_pool(
            p300_multi,
            high_salt_pool,
            target['high_volume'],
            target['well'],
            8,
            'high salt buffer'
        )
    p300_multi.drop_tip()

    p300_multi.pick_up_tip()
    for target in all_salt_buffer_targets:
        mix_volume_per_channel = min(250.0, max(20.0, target_reservoir_total_ul / 8.0 * 0.2))
        p300_multi.mix(3, mix_volume_per_channel, target['well'])
    p300_multi.drop_tip()

    # -------------------------
    # Step 4: prepare ligand dilutions in the deep-well mixing plate.
    # Rows A-H hold ascending ligand concentrations; columns correspond to salt concentrations.
    # The dilutions are made with low salt buffer.
    # -------------------------
    protocol.comment('Preparing ligand dilutions in the deep-well mixing plate with the P300 single-channel pipette.')

    ligand_plan = []
    row_names = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    for ligand_index, ligand_conc in enumerate(ligand_concentrations):
        target_2x_ligand = 2.0 * ligand_conc
        if target_2x_ligand == 0:
            stock_pool = None
            stock_volume = 0.0
            stock_label = 'no ligand stock'
        else:
            high_stock_volume = deepwell_target_total_ul * target_2x_ligand / ligand_stock_concentration
            if high_stock_volume >= 20.0:
                stock_pool = ligand_high_pool
                stock_volume = high_stock_volume
                stock_label = 'high ligand stock'
            else:
                low_stock_concentration = ligand_stock_concentration / 10.0
                stock_volume = deepwell_target_total_ul * target_2x_ligand / low_stock_concentration
                stock_pool = ligand_low_pool
                stock_label = 'low ligand stock'
        if stock_volume > deepwell_target_total_ul:
            raise RuntimeError(
                f'Ligand stock volume {stock_volume:.2f} uL exceeds target well volume '
                f'{deepwell_target_total_ul:.2f} uL for ligand concentration {ligand_conc}.'
            )
        buffer_volume = deepwell_target_total_ul - stock_volume
        for salt_index, salt_conc in enumerate(salt_concentrations):
            well_name = row_names[ligand_index] + str(salt_index + 1)
            ligand_plan.append({
                'well': mixing_plate.wells_by_name()[well_name],
                'ligand_concentration': ligand_conc,
                'salt_concentration': salt_conc,
                'stock_pool': stock_pool,
                'stock_volume': stock_volume,
                'stock_label': stock_label,
                'buffer_volume': buffer_volume
            })

    # Add ligand stock first while wells are empty, grouping by source stock pool for tip economy.
    high_stock_targets = [item for item in ligand_plan if item['stock_label'] == 'high ligand stock']
    low_stock_targets = [item for item in ligand_plan if item['stock_label'] == 'low ligand stock']

    if high_stock_targets:
        p300_single.pick_up_tip()
        for item in high_stock_targets:
            transfer_from_pool(
                p300_single,
                ligand_high_pool,
                item['stock_volume'],
                item['well'],
                1,
                'high ligand stock'
            )
        p300_single.drop_tip()

    if low_stock_targets:
        p300_single.pick_up_tip()
        for item in low_stock_targets:
            transfer_from_pool(
                p300_single,
                ligand_low_pool,
                item['stock_volume'],
                item['well'],
                1,
                'low ligand stock'
            )
        p300_single.drop_tip()

    # Add low salt buffer to bring each well to the requested dilution volume.
    p300_single.pick_up_tip()
    for item in ligand_plan:
        transfer_from_pool(
            p300_single,
            low_salt_pool,
            item['buffer_volume'],
            item['well'],
            1,
            'low salt buffer'
        )
    p300_single.drop_tip()

    protocol.comment('Protocol complete.')
