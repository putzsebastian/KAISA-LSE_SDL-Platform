from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol with placeholders for incubation parameters and a custom labware mounted on heater shaker'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (templatable values)
INCUBATION_TIME = '[[INCUBATION_TIME]]'         # minutes
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # degrees Celsius
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]' # rpm

# Helper to allow simulation without substituted tokens

def _unreplaced(s):
    return isinstance(s, str) and s.startswith('[[') and s.endswith(']]')


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # Resolve placeholders with simulation-friendly defaults when not substituted
    inc_time_min = parse_scalar(INCUBATION_TIME, 60, int)
    inc_temp = parse_scalar(INCUBATION_TEMPERATURE, 37.0, float)
    shaker_rpm = parse_scalar(SHAKER_SPEED_INCUBATION, 1200, int)

    # 1) Deck: Slot 1 -> Heater Shaker Module, direct labware on module
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load the custom labware directly on the heater shaker (Slot 1)
    try:
        plate_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml', label='Plate on HS')
    except Exception as exc:
        if 'not found' in str(exc).lower():
            protocol.comment('WARNING: cytiva_96_filterwellplate_1ml not found; using a standard plate as simulation fallback.')
            plate_on_hs = hs_mod.load_labware('nest_96_wellplate_200ul_flat', label='Plate on HS (fallback)')
        else:
            raise

    # 2) Slot 7 -> Tip Rack 300 uL
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # 3) Pipettes
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # 4) Step 1: Close latch and conditionally set temperature
    hs_mod.close_labware_latch()
    if inc_temp >= 37:
        if hasattr(hs_mod, 'set_and_wait_for_temperature'):
            hs_mod.set_and_wait_for_temperature(inc_temp)
        elif hasattr(hs_mod, 'set_target_temperature'):
            hs_mod.set_target_temperature(inc_temp)
        else:
            protocol.comment('Temperature control not available on Heater Shaker module.')

    # 5) Step 2: Shake for INCUBATION_TIME minutes at SHAKER_SPEED_INCUBATION rpm
    hs_mod.close_labware_latch()
    if hasattr(hs_mod, 'set_and_wait_for_shake_speed'):
        hs_mod.set_and_wait_for_shake_speed(shaker_rpm)
    elif hasattr(hs_mod, 'set_target_shake_speed'):
        hs_mod.set_target_shake_speed(shaker_rpm)
    else:
        protocol.comment('Shake control not available on Heater Shaker module; continuing without shaking.')

    protocol.delay(inc_time_min * 60)

    # 6) Step 3: End and open latch
    if hasattr(hs_mod, 'set_and_wait_for_shake_speed'):
        try:
            hs_mod.set_and_wait_for_shake_speed(0)
        except Exception:
            pass
    if hasattr(hs_mod, 'set_and_wait_for_temperature'):
        try:
            # Return to ambient temperature
            hs_mod.set_and_wait_for_temperature(25)
        except Exception:
            pass

    hs_mod.open_labware_latch()

    # Notes for templating:
    # - This protocol uses placeholders in the top of the file for INCUBATION_TIME, INCUBATION_TEMPERATURE,
    #   and SHAKER_SPEED_INCUBATION. The OT-2 template system should substitute them before execution.
    # - The cytiva labware may not be available in the simulator; a simulation fallback is provided.
