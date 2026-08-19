from opentrons import protocol_api

metadata = {
    "protocolName": "Placeholder Protocol",
    "author": "Generated",
    "description": "Minimal placeholder script"
}


def run(protocol: protocol_api.ProtocolContext):
    protocol.comment("Placeholder protocol; replace with actual steps.")
