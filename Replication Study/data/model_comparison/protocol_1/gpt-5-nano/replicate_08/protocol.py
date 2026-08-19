from opentrons import protocol_api

metadata = {
  'protocolName': 'Template',
  'author': 'user',
  'description': 'Template protocol skeleton with placeholders'
}


def run(protocol: protocol_api.ProtocolContext):
  # Example labware and instruments placeholders
  cytiva_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
  tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
  reservoir = protocol.load_labware('nest_12_reservoir_15ml', 5)
  plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)
  p300 = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
  protocol.comment('This is a placeholder for a templated protocol.')
