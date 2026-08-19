from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol that runs an incubation on the Heater-Shaker module using placeholders for time, temperature and speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (must remain literal so the external system can substitute them)
INCUBATION_TIME = '[[INCUBATION_TIME]]'                # minutes
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # degrees Celsius
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]' # rpm


def _unreplaced(s: str) -> bool:
    """Detect an unreplaced placeholder token without writing '[[' or ']]' literally.
    """
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    # When a real value is present, cast from the string. For float use float(s) first to accept things like '37.0'
    if cast is float:
        return float(s)
    return cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Load custom labware on the Heater-Shaker module with simulation fallback
    try:
        plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Parse placeholders into numeric values for simulation/runtime
    # Use conservative fallbacks so the simulation exercises heating/shaking paths
    incubation_minutes = parse_scalar(INCUBATION_TIME, default=60, cast=float)
    incubation_temp_c = parse_scalar(INCUBATION_TEMPERATURE, default=37, cast=float)
    shaker_rpm = parse_scalar(SHAKER_SPEED_INCUBATION, default=600, cast=int)

    # Step 1: Ensure the labware latch is closed before heating/shaking.
    # close_labware_latch is idempotent and safe to call even if already closed.
    hs_mod.close_labware_latch()

    # If the requested temperature is >= 37 C, set and wait for temperature.
    if incubation_temp_c >= 37:
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)

    # Step 2: Start shaking at requested speed and incubate for the requested time.
    # set_and_wait_for_shake_speed blocks until target rpm is reached.
    hs_mod.set_and_wait_for_shake_speed(shaker_rpm)

    # Delay for the incubation duration
    protocol.delay(minutes=incubation_minutes)

    # Step 3: Stop shaking and heating, then open the labware latch.
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()

    protocol.comment('Incubation step complete.')
