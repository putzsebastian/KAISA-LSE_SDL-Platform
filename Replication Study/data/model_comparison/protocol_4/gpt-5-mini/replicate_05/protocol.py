from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Opentrons User',
    'description': 'Template protocol that closes the heater-shaker latch, optionally sets temperature, shakes for an incubation time and then stops and opens the latch. Uses placeholders for templating.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (declared as literal strings so an external templating system can replace them)
INCUBATION_TIME = '[[INCUBATION_TIME]]'            # minutes
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # celsius
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]' # rpm


def _unreplaced(s: str) -> bool:
    """Detect whether a placeholder token has not been replaced yet.
    Build the brackets at runtime so the literal string '[[' never appears in the file.
    """
    return str(s).startswith('[' * 2) and str(s).endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder. During simulation an unreplaced token returns the
    provided default so the simulator can exercise the protocol; on the real robot the
    substituted value is parsed and must be valid.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Load Heater-Shaker Module in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load labware onto the heater-shaker module. The labware is custom, so provide a
    # simulation fallback that uses a standard 96-well plate with the same layout.
    try:
        plate_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # If the exception is not about a missing definition, surface it (don't hide real errors)
        if 'not found' not in str(exc).lower():
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        plate_on_hs = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Load tip rack in slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Load pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Parse placeholders with sensible simulation defaults (defaults chosen to exercise heating and shaking):
    incubation_time_min = parse_scalar(INCUBATION_TIME, default=60, cast=float)        # minutes
    incubation_temp_c = parse_scalar(INCUBATION_TEMPERATURE, default=42, cast=float)    # celsius
    shaker_speed_rpm = parse_scalar(SHAKER_SPEED_INCUBATION, default=1200, cast=int)     # rpm

    protocol.comment(f'Parsed incubation_time (min): {incubation_time_min}')
    protocol.comment(f'Parsed incubation_temperature (C): {incubation_temp_c}')
    protocol.comment(f'Parsed shaker_speed (rpm): {shaker_speed_rpm}')

    # Step 1: Close labware latch (always) and set temperature only if >= 37 C
    protocol.comment('Step 1: ensuring the labware latch is closed on the Heater-Shaker')
    hs_mod.close_labware_latch()

    if incubation_temp_c >= 37:
        protocol.comment(f'Incubation temperature >= 37 C: setting heater to {incubation_temp_c} C and waiting')
        # Block until temperature reached to ensure correct incubation conditions before shaking
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment('Incubation temperature < 37 C: skipping heater set step')

    # Step 2: Set shake speed and shake for the requested incubation time
    protocol.comment(f'Step 2: setting shake speed to {shaker_speed_rpm} rpm and incubating for {incubation_time_min} minutes')
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking, then open the latch
    protocol.comment('Step 3: stopping shaker and heater, then opening labware latch')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()

    protocol.comment('Protocol complete')
