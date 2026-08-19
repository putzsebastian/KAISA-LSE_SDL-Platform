from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for incubation time, temperature, and shaker speed.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder.

    During real runs, the wizard replaces the placeholders with numeric
    strings, which will be cast. During simulation (when the placeholder
    text is still present), this returns the provided default.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # Labware (custom labware on heater shaker). Use simulation fallback if custom is missing.
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Pipettes (loaded as specified; not used for this simple incubation, but
    # present for deck configuration completeness)
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', '7')
    p300_single = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', 'left', tip_racks=[tiprack_300])

    # Parse placeholders with simulation-safe defaults (upper-bound style)
    incubation_temp = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=75.0, cast=float)
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=60.0, cast=float)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=1200.0, cast=float)

    # Step 1: Close latch and set temperature if incubation_temp >= 37; else skip heating
    hs_mod.close_labware_latch()
    if incubation_temp >= 37.0:
        hs_mod.set_and_wait_for_temperature(incubation_temp)

    # Step 2: Shake for incubation_time_min at shaker_speed
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking and open labware latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()

    protocol.comment('Incubation and shaking sequence complete.')
