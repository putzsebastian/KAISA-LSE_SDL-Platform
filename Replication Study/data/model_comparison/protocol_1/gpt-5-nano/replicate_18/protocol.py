from opentrons import protocol_api

metadata = {'protocolName': 'Template Salt/Ligand Dilution Protocol (with placeholders)', 'author': 'Lab 167', 'description': 'Template protocol using placeholders for replicates, salt and ligand concentrations'}
requirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}

# Placeholder strings (will be substituted by the templating wizard)
REPLICATES = '[[REPLICATES]]'
SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def run(protocol: protocol_api.ProtocolContext):
    # Minimal template: initialize basic components
    cytiva_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1, label='Cytiva 96 Filter Well Plate 1 mL')
    tipracks = [protocol.load_labware('opentrons_96_tiprack_300ul', slot) for slot in [4, 7, 10]]
    reservoir = protocol.load_labware('nest_12_reservoir_15ml', 5)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)
    left_pipette = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=tipracks)
    right_pipette = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=tipracks)
    # Placeholder: computation and transfers would go here
    return
