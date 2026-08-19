from opentrons import protocol_api

metadata = {
    'protocolName': '[protocol name by user]',
    'author': '[user name]',
    'description': "[what is the protocol about]"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # Placeholders to be substituted by wizard
    INCUBATION_TIME = '[[INCUBATION_TIME]]'
    INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'

    # Heater Shaker Module on slot 1
    hs_mod = protocol.load_module(module_name="heaterShakerModuleV1", location=1)

    # Step 1: Load labware onto the module and ensure latch state
    hs_mod.open_labware_latch()
    cytiva_labware = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    hs_mod.close_labware_latch()

    # Step 1 continued: Temperature control if required
    inc_temp = None
    try:
        inc_temp = float(INCUBATION_TEMPERATURE)
    except Exception:
        inc_temp = None

    if inc_temp is not None and inc_temp >= 37:
        if hasattr(hs_mod, 'set_temperature'):
            hs_mod.set_temperature(inc_temp)
        elif hasattr(hs_mod, 'set_and_wait_for_temperature'):
            hs_mod.set_and_wait_for_temperature(inc_temp)
        else:
            protocol.comment('Warning: Heater-Shaker temperature control not available; skipping temperature set.')

    # Step 2: Shake for INCUBATION_TIME minutes at given rpm
    duration_min = 0.0
    try:
        duration_min = float(INCUBATION_TIME)
    except Exception:
        duration_min = 0.0

    shaker_rpm = 0.0
    try:
        shaker_rpm = float(SHAKER_SPEED_INCUBATION)
    except Exception:
        shaker_rpm = 0.0

    if shaker_rpm > 0:
        if hasattr(hs_mod, 'set_and_wait_for_shake_speed'):
            hs_mod.set_and_wait_for_shake_speed(shaker_rpm)
        elif hasattr(hs_mod, 'start_shake'):
            hs_mod.start_shake(shaker_rpm)
        else:
            protocol.comment('Warning: Heater-Shaker shake API not available; proceeding without shaking.')

        if duration_min > 0:
            protocol.delay(minutes=duration_min)

        # Stop shaking
        if hasattr(hs_mod, 'set_and_wait_for_shake_speed'):
            hs_mod.set_and_wait_for_shake_speed(0)
        elif hasattr(hs_mod, 'stop_shake'):
            hs_mod.stop_shake()

    # Stop heating if started
    if inc_temp is not None and inc_temp >= 37:
        if hasattr(hs_mod, 'set_temperature'):
            hs_mod.set_temperature(0)
        elif hasattr(hs_mod, 'set_and_wait_for_temperature'):
            hs_mod.set_and_wait_for_temperature(0)

    # Step 3: Open latch after operations
    hs_mod.open_labware_latch()

    # Optional: Load tiprack and pipettes (template)
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])
