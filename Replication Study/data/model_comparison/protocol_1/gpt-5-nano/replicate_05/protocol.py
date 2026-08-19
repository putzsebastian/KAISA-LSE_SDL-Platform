from opentrons import protocol_api

metadata = {
  'protocolName': 'Salt-Ligand Dilution Template',
  'author': 'Lab 167',
  'description': 'Template protocol using placeholders for salts and ligands on OT-2'
}

REPLICATES = '[[REPLICATES]]'
TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def run(protocol):
    protocol.comment('Template protocol execution with placeholders; substitute data at deploy time.')
    # Substitution placeholder: actual labware and steps will be templated in downstream tooling
