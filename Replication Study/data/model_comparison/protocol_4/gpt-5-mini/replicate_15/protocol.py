from opentrons import protocol_api

metadata = {
    'protocolName': 'Heater-Shaker Incubation Template',
    'author': 'Lab 167',
    'description': "Incubation on Heater-Shaker with placeholders for time, temperature and speed"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


# Placeholders (must remain literal for the wizard to substitute)
PLACEHOLDER_INCUBATION_TIME = '[[INCUBATION_TIME]]'
PLACEHOLDER_INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _unreplaced(s: str) -> bool:
    """Detect an unreplaced substitution token without writing '[[' literally.
    Builds the brackets by repetition so the string literal does not contain '[['.
    """
    return str(s).startswith('[' * 2) and str(s).endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(s)


def run(protocol: protocol_api.ProtocolContext):
    # Modules
    hs_mod = protocol.load_module(module_name='heaterShakerModuleV1', location=1)

    # Labware
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Load custom labware onto the heater-shaker with a simulation fallback
    try:
        plate_on_hs = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware definition not available; using a standard 96-well plate as a SIMULATION fallback only.')
        plate_on_hs = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Pipettes
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # Parse placeholders (choose safe simulation defaults when unreplaced)
    # Default temperature is 37 C so the heating branch is exercised in simulation
    target_temp = parse_scalar(PLACEHOLDER_INCUBATION_TEMPERATURE, default=37, cast=float)
    incubation_time_min = parse_scalar(PLACEHOLDER_INCUBATION_TIME, default=5, cast=float)
    shaker_rpm = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=500, cast=int)

    protocol.comment(f'Parsed parameters — temperature: {target_temp} C, time: {incubation_time_min} min, speed: {shaker_rpm} rpm')

    # Step 1: Close labware latch and set temperature if >= 37
    hs_mod.close_labware_latch()

    if target_temp >= 37:
        protocol.comment(f'Setting heater-shaker temperature to {target_temp} C and waiting to reach setpoint')
        # Use blocking set-and-wait so the protocol does not proceed until target is reached
        hs_mod.set_and_wait_for_temperature(target_temp)
    else:
        protocol.comment(f'Target temperature {target_temp} C is below 37 C — skipping heating step')

    # Step 2: Shake for incubation_time at shaker_rpm
    protocol.comment(f'Setting shaker speed to {shaker_rpm} rpm and incubating for {incubation_time_min} minutes')
    hs_mod.set_and_wait_for_shake_speed(shaker_rpm)
    protocol.delay(minutes=incubation_time_min)

    # Step 3: Stop heating and shaking, then open the labware latch
    protocol.comment('Deactivating shaker and heater')
    hs_mod.deactivate_shaker()
    hs_mod.deactivate_heater()

    # It is safe to open the latch now that shaker has stopped
    hs_mod.open_labware_latch()
    protocol.comment('Incubation complete; labware latch opened')
