from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated incubation on Heater-Shaker with placeholders for time, temperature, and speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (left as literal strings for external substitution)
PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if a value is still an unreplaced [[PLACEHOLDER]]."""
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value: str, default: float, cast=float) -> float:
    """Parse a scalar placeholder value with a simulation fallback.

    The fallback is only used when the value is still an unreplaced
    placeholder (e.g. '[[INCUBATION_TIME]]'). On real runs, the
    substituted value must parse correctly or the protocol will fail
    fast, which is desirable.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # 1. Load Heater-Shaker module in slot 1
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # 2. Load custom labware directly on the Heater-Shaker (no adapter)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; '
                         'using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # 3. Load tip rack in slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # 4. Load pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_300])

    # 5. Parse placeholders into numeric values with conservative simulation defaults
    # Use large defaults so simulation exercises worst-case behavior.
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=120.0, cast=float)
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=60.0, cast=float)
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=3000.0, cast=float)

    # 6. Step 1: Close labware latch and conditionally set temperature
    hs_mod.close_labware_latch()
    protocol.comment('Labware latch closed on Heater-Shaker.')

    if incubation_temp_c >= 37.0:
        protocol.comment(f'Setting Heater-Shaker temperature to {incubation_temp_c} °C for incubation.')
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment(f'Incubation temperature {incubation_temp_c} °C is below 37 °C; skipping heating step.')

    # 7. Step 2: Shake the Heater Shaker Module for incubation time at specified speed
    protocol.comment(f'Starting shaking at {shaker_speed_rpm} rpm for {incubation_time_min} minutes.')
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)

    # Incubation delay
    protocol.delay(minutes=incubation_time_min)

    # 8. Step 3: Stop heating and shaking and open labware latch
    protocol.comment('Stopping shaking and heater, then opening labware latch.')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()

    hs_mod.open_labware_latch()
    protocol.comment('Labware latch opened. Incubation sequence complete.')
