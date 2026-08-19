from opentrons import protocol_api

metadata = {
  "protocolName": "Salt-Ligand Template",
  "author": "[[AUTHOR]]",
  "description": "Minimal placeholder protocol."
}

def run(protocol: protocol_api.ProtocolContext):
  protocol.comment("This is a placeholder protocol script.")
