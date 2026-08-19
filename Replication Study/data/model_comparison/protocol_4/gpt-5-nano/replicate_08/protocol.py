from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Incubation protocol template using placeholders for temperature, time and shaker speed'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders for templating
PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _is_placeholder(value):
    s = str(value)
    return s.startswith('[[') and s.endswith(']]')


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _is_placeholder(s):
        return default                 # simulation fallback: use default
    return cast(float(s))              # parse substituted value


def run(protocol):
    # Module: Heater Shaker Gen1 loaded in slot 1
    hs = protocol.load_module(module_name="heaterShakerModuleV1", location=1)

    # Labware on HS (slot 1): try to load custom cytiva plate; fall back to standard plate if not found (simulation)
    try:
        incubation_labware = hs.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; using a standard plate as a SIMULATION fallback.')
        incubation_labware = hs.load_labware('nest_96_wellplate_200ul_flat')

    # Deck: Tip rack on slot 7
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes (as requested): Right - P300 Single-Channel Gen2; Left - P300 8-Channel Gen2
    p300s = protocol.load_instrument('p300_single_gen2', mount="right", tip_racks=[tiprack])
    p300m = protocol.load_instrument('p300_multi_gen2', mount="left", tip_racks=[tiprack])

    # Placeholders values (float). In the templated run these will be numeric; defaults provided for simulation
    incubation_time = parse_scalar(PLACEHOLDER_INCUBATION_TIME, 60.0, float)            # minutes
    incubation_temperature = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, 37.0, float) # °C
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, 1200.0, float)        # rpm

    # Step 1: Close latch and conditionally heat
    hs.close_labware_latch()
    if incubation_temperature >= 37:
        hs.set_target_temperature(incubation_temperature)
        hs.wait_for_temperature()

    # Step 2: Shake for the specified duration at the specified speed
    hs.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=incubation_time)

    # Step 3: Stop heating and shaking and open latch
    hs.deactivate_heater()
    hs.deactivate_shaker()
    hs.open_labware_latch()
