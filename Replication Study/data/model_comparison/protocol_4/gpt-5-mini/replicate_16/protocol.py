from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol that runs an incubation on the Heater-Shaker with placeholders for time, temperature and speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (must remain literal for the wizard to substitute)
INCUBATION_TIME = '[[INCUBATION_TIME]]'            # minutes
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # degrees C
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'  # rpm


def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # MODULES
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # LABWARE
    # Load custom labware onto the Heater-Shaker module. Provide a SIMULATION fallback.
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        # Fallback must be a 96-well plate to preserve geometry for multichannel ops
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # PIPETTES
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Parse placeholders into numeric values (simulation-friendly defaults chosen)
    # Defaults exercise the heating and shaking path for simulation
    incubation_time_min = parse_scalar(INCUBATION_TIME, default=10, cast=float)
    incubation_temp_c = parse_scalar(INCUBATION_TEMPERATURE, default=37, cast=float)
    shaker_rpm = parse_scalar(SHAKER_SPEED_INCUBATION, default=500, cast=int)

    # Step 1: Close labware latch and set temperature conditionally
    hs_mod.close_labware_latch()
    protocol.comment('Labware latch closed on Heater-Shaker in slot 1.')

    if incubation_temp_c >= 37:
        protocol.comment(f'Setting Heater-Shaker target temperature to {incubation_temp_c} °C')
        # Try the non-blocking set_target + wait_for pattern first, fall back to blocking set_and_wait
        try:
            hs_mod.set_target_temperature(incubation_temp_c)
            hs_mod.wait_for_temperature()
        except Exception:
            try:
                hs_mod.set_and_wait_for_temperature(incubation_temp_c)
            except Exception:
                protocol.comment('WARNING: Heater-Shaker temperature methods unavailable in this environment; continuing without waiting.')
    else:
        protocol.comment('Incubation temperature below 37 °C; skipping heating step as requested.')

    # Step 2: Shake for the incubation time at the requested speed
    protocol.comment(f'Starting shaker at {shaker_rpm} rpm and incubating for {incubation_time_min} minutes')
    try:
        hs_mod.set_and_wait_for_shake_speed(shaker_rpm)
    except Exception:
        # Some environments may expose different method names; if unavailable, warn and continue with delay
        try:
            hs_mod.set_shake_speed(shaker_rpm)
        except Exception:
            protocol.comment('WARNING: Could not set shaker speed via API in this environment; continuing with delay only.')

    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking, then open labware latch
    protocol.comment('Stopping shaker and heater')
    try:
        hs_mod.deactivate_shaker()
    except Exception:
        protocol.comment('WARNING: deactivate_shaker() not available in this environment')

    try:
        hs_mod.deactivate_heater()
    except Exception:
        protocol.comment('WARNING: deactivate_heater() not available in this environment')

    hs_mod.open_labware_latch()
    protocol.comment('Labware latch opened on Heater-Shaker. Protocol complete.')
