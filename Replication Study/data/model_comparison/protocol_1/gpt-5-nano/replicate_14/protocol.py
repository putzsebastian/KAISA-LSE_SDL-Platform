from opentrons import protocol_api

metadata = {
  'protocolName': 'Salt and Ligand Template (templated)',
  'author': '[user name]'
}


def run(protocol: protocol_api.ProtocolContext):
  protocol.comment('This is a placeholder protocol.')
