from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Transfer Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand transfers into a filter plate on a Heater Shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (these will be replaced by the template system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_NUM_SALT_CONC = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIGAND_CONC = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string is still a [[PLACEHOLDER]]."""
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder, using a simulation fallback if unreplaced.

    The fallback should be the worst-case expected value, to exercise tip usage
    and volume budgeting in simulation.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_int(value, default):
    """Parse an integer placeholder (via float to accept '3.0' etc.)."""
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # -------------------------------------------------------------------------
    # Parameter parsing (templated)
    # -------------------------------------------------------------------------
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 200.0)  # uL per well total
    step_volume = total_volume / 2.0  # uL per well for each of the two steps

    # Number of salt and ligand concentrations
    num_salt_conc = parse_int(PLACEHOLDER_NUM_SALT_CONC, 4)
    num_ligand_conc = parse_int(PLACEHOLDER_NUM_LIGAND_CONC, 8)

    # Basic validation: ensure total columns used do not exceed 12
    if replicates * num_salt_conc > 12:
        raise RuntimeError(
            'Total number of filter plate columns (replicates x salt concentrations) exceeds 12.'
        )

    # -------------------------------------------------------------------------
    # Modules
    # -------------------------------------------------------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # -------------------------------------------------------------------------
    # Labware
    # -------------------------------------------------------------------------
    # Filter plate on Heater-Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom filter plate definition not available; '
            'using a standard 96-well plate as a SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # buffers (Reservoir 4)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # Reservoir 3 (unused)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # Reservoir 2 (unused)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # Reservoir 1 (unused)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # Reservoir 0 (unused)

    # Mixing plate
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------------------------------------------------------
    # Pipettes
    # -------------------------------------------------------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # Single-channel not used in this protocol but loaded per configuration
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # Ensure Heater-Shaker latch is closed before pipetting
    hs_mod.close_labware_latch()

    # List of columns on the filter plate (each entry is a list of 8 wells A-H)
    filter_columns = list(filter_plate.columns())  # 12 columns for a 96-well plate

    # -------------------------------------------------------------------------
    # Step 1: Transfer buffers from Reservoir 4 to Filter Plate
    # -------------------------------------------------------------------------
    # Each buffer (salt concentration) in reservoir_4.wells()[i] is transferred
    # into `replicates` consecutive columns on the filter plate, volume
    # step_volume (= [[TOTAL_VOLUME]]/2) per well.

    p300_multi.flow_rate.aspirate = 150
    p300_multi.flow_rate.dispense = 300

    # Use first column of tips from slot 4 and return them for reuse in Step 2
    p300_multi.pick_up_tip(tiprack_4.columns()[0][0])

    for salt_index in range(num_salt_conc):
        src_well = reservoir_4.wells()[salt_index]
        start_col = salt_index * replicates
        end_col = start_col + replicates
        dest_cols = filter_columns[start_col:end_col]

        for col in dest_cols:
            # Multi-channel uses row A well of the column; 7 mm above well bottom
            dest_location = col[0].bottom(7)
            p300_multi.transfer(
                step_volume,
                src_well,
                dest_location,
                new_tip='never',
                blow_out=True,
                blowout_location='destination well'
            )

    # Return tips for reuse
    p300_multi.return_tip()

    # -------------------------------------------------------------------------
    # Step 2: Transfer ligands from Mixing Plate to Filter Plate
    # -------------------------------------------------------------------------
    # Each column in the mixing plate (one per salt concentration) is
    # transferred into the corresponding `replicates` columns on the filter
    # plate, volume step_volume (= [[TOTAL_VOLUME]]/2) per well.

    # Reuse the same tips from the first column of slot 4
    p300_multi.pick_up_tip(tiprack_4.columns()[0][0])

    mixing_columns = list(mixing_plate.columns())

    for lig_index in range(num_salt_conc):
        src_col = mixing_columns[lig_index]
        start_col = lig_index * replicates
        end_col = start_col + replicates
        dest_cols = filter_columns[start_col:end_col]

        for dest_col in dest_cols:
            src_well = src_col[0]  # row A for multichannel
            dest_location = dest_col[0].bottom(7)
            p300_multi.transfer(
                step_volume,
                src_well,
                dest_location,
                new_tip='never',
                blow_out=True,
                blowout_location='destination well'
            )

    p300_multi.return_tip()

    protocol.comment('Templated salt and ligand transfer protocol complete.')
