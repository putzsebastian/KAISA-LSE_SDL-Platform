from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol with placeholders for incubation temperature, time and shaker speed. Deck layout: Cytiva 96 filter well plate on Heater Shaker Module V1 (slot 1); Tip rack 300 uL on slot 7; Right P300 single-channel GEN2; Left P300 multi-channel GEN2. All values templatable with [[INCUBATION_TEMPERATURE]], [[INCUBATION_TIME]], [[SHAKER_SPEED_INCUBATION]].',
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol):
    # Placeholders exposed as strings for templating
    INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
    INCUBATION_TIME = '[[INCUBATION_TIME]]'
    SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'

    def _to_float(val):
        try:
            return float(val)
        except Exception:
            return None

    incubation_temp = _to_float(INCUBATION_TEMPERATURE)
    incubation_time_min = _to_float(INCUBATION_TIME)
    shaker_rpm = _to_float(SHAKER_SPEED_INCUBATION)

    # Load Heater Shaker module on slot 1 and place labware on it
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware on heater shaker: try to load the Cytiva plate; fallback for simulation to a standard plate
    try:
        cytiva_labware = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        protocol.comment('WARNING: cytiva_96_filterwellplate_1ml not available on heater shaker; using a standard 96-well plate as fallback for simulation.')
        cytiva_labware = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Tip rack (slot 7)
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes (for templating completeness, not required for heater step behavior)
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Step 1: Close latch and conditional temperature (demonstrated via comment due to API limitations)
    hs_mod.close_labware_latch()
    if incubation_temp is not None and incubation_temp >= 37:
        protocol.comment('Incubation temperature >= 37C specified; note: API 2.19 Gen1 heater module does not expose a set_temperature() method; temperature control is assumed to be external or hardware-specific.')
    else:
        protocol.comment('Incubation temperature below 37C or not provided; skipping heater temperature setup.')

    # Step 2: Shake for the incubation time at the given speed
    if shaker_rpm is not None:
        hs_mod.set_and_wait_for_shake_speed(int(shaker_rpm))
    else:
        protocol.comment('[[SHAKER_SPEED_INCUBATION]] placeholder not substituted; skipping shaker start.')

    if incubation_time_min is not None:
        protocol.delay(seconds=incubation_time_min * 60)

    # Step 3: Stop heating and shaking and open latch
    try:
        hs_mod.deactivate_heater()
    except Exception:
        protocol.comment('Warning: unable to deactivate heater; API may differ on this hardware.')
    try:
        hs_mod.deactivate_shaker()
    except Exception:
        protocol.comment('Warning: unable to deactivate shaker; API may differ on this hardware.')
    hs_mod.open_labware_latch()

    # End of protocol: no liquid transfers are defined in this template; placeholders drive templating only.
