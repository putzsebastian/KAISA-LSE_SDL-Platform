from opentrons import protocol_api

metadata = {
    'protocolName': '[Heater Shaker Incubation Protocol]',
    'author': '[user name]',
    'description': "Incubation on a Heater-Shaker with placeholders: [[INCUBATION_TEMPERATURE]], [[INCUBATION_TIME]], [[SHAKER_SPEED_INCUBATION]]"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Deck layout per user:
    # Slot 1: Heater Shaker Module (V1) with cytiva_96_filterwellplate_1ml directly on the module
    hs_mod = protocol.load_module(module_name="heaterShakerModuleV1", location=1)
    lab_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')

    # Slot 7: Tip rack
    tiprack_300ul = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes (configured for templating; not used for liquid transfer in this protocol)
    p300s = protocol.load_instrument('p300_single_gen2', mount="right", tip_racks=[tiprack_300ul])
    p300m = protocol.load_instrument('p300_multi_gen2', mount="left", tip_racks=[tiprack_300ul])

    # Open/close latch as required by steps
    hs_mod.close_labware_latch()

    # Placeholders
    INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # degrees Celsius
    INCUBATION_TIME = '[[INCUBATION_TIME]]'  # minutes
    SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'  # rpm

    # Step 1: Conditional heating
    temp_val = None
    try:
        temp_val = int(INCUBATION_TEMPERATURE)
    except Exception:
        temp_val = None

    if isinstance(temp_val, int) and temp_val >= 37:
        hs_mod.set_target_temperature(temp_val)
        hs_mod.wait_for_temperature()
    else:
        protocol.comment("INCUBATION_TEMPERATURE not substituted or below 37; skipping heating step.")

    # Step 2: Shake for INCUBATION_TIME minutes at SHAKER_SPEED_INCUBATION rpm
    hs_mod.close_labware_latch()

    incubation_time = None
    try:
        incubation_time = int(INCUBATION_TIME)
    except Exception:
        incubation_time = None
    if incubation_time is None:
        incubation_time = 120  # fallback in simulation/template when not substituted

    shaker_speed = None
    try:
        shaker_speed = int(SHAKER_SPEED_INCUBATION)
    except Exception:
        shaker_speed = 1000  # fallback rpm

    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(seconds=incubation_time * 60)

    # Step 3: End - stop heating and shaking, then open latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()
