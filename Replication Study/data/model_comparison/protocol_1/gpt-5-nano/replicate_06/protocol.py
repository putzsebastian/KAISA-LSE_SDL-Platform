from opentrons import protocol_api

metadata = {'protocolName': '[protocol name by user]', 'author': '[user name]', 'description': 'Templated protocol with placeholders for salts and ligands'}
requirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}

def run(protocol: protocol_api.ProtocolContext):
    # Deck setup (placeholders)
    try:
        source_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
        protocol.comment('Loaded Cytiva 96 filter well plate (slot 1).')
    except Exception:
        protocol.comment('WARNING: cytiva_96_filterwellplate_1ml not found; using a standard plate as fallback in slot 1.')
        source_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)

    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    reservoir_4_for_template = protocol.load_labware('nest_12_reservoir_15ml', 3)
    reservoir_2_for_template = reservoir_2
    reservoir_1_for_template = reservoir_1
    reservoir_0_for_template = reservoir_0

    p300_s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_slot10])
    p300_m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_slot7])

    REPLICATES = [[REPLICATES]]
    TOTAL_VOLUME = [[TOTAL_VOLUME]]
    SALT_CONCENTRATIONS = [[SALT_CONCENTRATIONS]]
    LIGAND_CONCENTRATIONS = [[LIGAND_CONCENTRATIONS]]
    SALT_STOCK_CONCENTRATION = [[SALT_STOCK_CONCENTRATION]]
    LIGAND_STOCK_CONCENTRATION = [[LIGAND_STOCK_CONCENTRATION]]
    NUMBER_OF_SALT_CONCENTRATIONS = [[NUMBER_OF_SALT_CONCENTRATIONS]]
    NUMBER_OF_LIGAND_CONCENTRATIONS = [[NUMBER_OF_LIGAND_CONCENTRATIONS]]

    protocol.comment('Step 2: Prepare Reservoir 3 wells for salt concentrations.')
    protocol.comment('REPLICATES = {}, NUMBER_OF_SALT_CONCENTRATIONS = {}'.format(REPLICATES, NUMBER_OF_SALT_CONCENTRATIONS))
    protocol.comment('Salt concentrations (ascending) = [{}]'.format(SALT_CONCENTRATIONS))

    protocol.comment('Step 3: Prepare Reservoir 4 wells with 2x concentration for each salt concentration.')
    protocol.comment('In Reservoir 4 the salt concentrations should be ascending with increasing well number.')

    protocol.comment('Step 4: In the mixing plate, create dilutions with concentrations of 2x the required ligand concentrations.')
    protocol.comment('In the mixing plate, concentrations should be ascending row-wise from A-H, with one column per salt concentration value.')

    protocol.comment('Note: This is a templated protocol. Replace placeholders with numeric values to enable execution.')