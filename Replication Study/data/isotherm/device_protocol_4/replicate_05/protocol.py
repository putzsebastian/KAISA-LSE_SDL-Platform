from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'User',
    'description': 'Templated incubation on Heater Shaker with Cytiva 96 filter plate'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    s_val = str(s).strip()
    return s_val.startswith('[' * 2) and s_val.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder.

    During simulation, the placeholder tokens are still literal strings like
    '[[INCUBATION_TIME]]'. In that case, return a simulation default. On the
    real robot, the wizard will substitute numeric strings, which we cast.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def run(protocol: protocol_api.ProtocolContext):
    # Parse placeholders with simulation-safe defaults (use large/worst-case values)
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=60.0)  # minutes
    incubation_temp_c = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=60.0)  # deg C
    shaker_speed_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0)  # rpm

    # Modules
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # Labware: custom Cytiva filter plate directly on Heater-Shaker
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Labware: tiprack in slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Pipettes (not actively used in this template, but loaded per requirements)
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_300])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_300])

    # Step 1: Close labware latch and set temperature if incubation_temp >= 37
    # Latch must be closed before any shaking or pipetting on module
    hs_mod.close_labware_latch()

    if incubation_temp_c >= 37.0:
        hs_mod.set_and_wait_for_temperature(incubation_temp_c)
    else:
        protocol.comment(
            f'Skipping heater activation because incubation temperature ({incubation_temp_c} C) < 37 C.'
        )

    # Step 2: Shake the Heater-Shaker for incubation_time_min minutes at shaker_speed_rpm
    if shaker_speed_rpm > 0 and incubation_time_min > 0:
        hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
        protocol.delay(minutes=incubation_time_min)
    else:
        protocol.comment('Skipping shaking because shaker speed or incubation time is not positive.')

    # Step 3: Stop heating and shaking and open labware latch
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()
    hs_mod.open_labware_latch()

    protocol.comment('Incubation template on Heater Shaker completed.')
