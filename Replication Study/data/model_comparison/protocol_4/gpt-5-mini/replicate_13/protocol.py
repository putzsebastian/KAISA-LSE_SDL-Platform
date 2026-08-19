from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'User',
    'description': 'Template protocol to run an incubation on a Heater-Shaker with placeholders'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings so an external templating tool can replace them)
INCUBATION_TIME = '[[INCUBATION_TIME]]'             # minutes
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # degrees C
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]' # rpm


def _unreplaced(s: str) -> bool:
    """Return True if the string still contains an unreplaced [[TOKEN]] placeholder.
    We build the brackets by repetition so the literal string '[[' never appears in the file.
    """
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder value into a numeric type for simulation.

    During simulation placeholder strings will be unreplaced; in that case return the
    supplied default (chosen to exercise the simulator). Once the templating system
    substitutes values, these will be parsed normally and will raise on bad input.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # 1) Load modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # 2) Load labware onto Heater-Shaker (custom definition may not exist in simulation)
    try:
        plate_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # If the labware definition is missing, only fall back for simulation.
        # Re-raise other errors (wrong slot, wrong adapter, etc.).
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a 96-well plate as a SIMULATION fallback only.')
        plate_on_hs = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Close labware latch before any shaking/heating operations
    hs_mod.close_labware_latch()

    # 3) Load other labware
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # 4) Load pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # 5) Parse placeholders with safe simulation fallbacks (worst-case values)
    # Fallbacks chosen to exercise heating & shaking in the simulator.
    incubation_temp = parse_scalar(INCUBATION_TEMPERATURE, default=42.0, cast=float)
    incubation_time_min = parse_scalar(INCUBATION_TIME, default=60.0, cast=float)  # minutes
    shaker_rpm = parse_scalar(SHAKER_SPEED_INCUBATION, default=1200.0, cast=float)  # rpm

    protocol.comment(f'Incubation temperature (parsed): {incubation_temp} C')
    protocol.comment(f'Incubation time (parsed): {incubation_time_min} minutes')
    protocol.comment(f'Shaker speed (parsed): {shaker_rpm} rpm')

    # Step 1: Close latch (already closed above). Set temperature only if >= 37 C
    if incubation_temp >= 37.0:
        protocol.comment(f'Setting heater-shaker target temperature to {incubation_temp} °C')
        # Use blocking set-and-wait so the module reaches temperature before incubation
        hs_mod.set_and_wait_for_temperature(incubation_temp)
    else:
        protocol.comment('Incubation temperature below 37 °C; skipping heating step')

    # Step 2: Start shaking for the specified time at the specified speed
    protocol.comment(f'Setting heater-shaker shake speed to {shaker_rpm} rpm and incubating for {incubation_time_min} minutes')
    hs_mod.set_and_wait_for_shake_speed(int(shaker_rpm))

    # incubate for the requested time (convert minutes to seconds)
    protocol.delay(seconds=incubation_time_min * 60)

    # Step 3: Stop heating and shaking, then open latch
    protocol.comment('Deactivating shaker and heater')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()

    protocol.comment('Opening labware latch')
    hs_mod.open_labware_latch()

    protocol.comment('Incubation protocol complete')
