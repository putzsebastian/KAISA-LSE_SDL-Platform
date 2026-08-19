from opentrons import protocol_api

# Placeholder tokens (to be substituted by the templating system):
INCUBATION_TIME = '[[INCUBATION_TIME]]'          # minutes
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'  # Celsius
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'  # RPM

metadata = {
    'protocolName': 'Heater Shaker Incubation Template',
    'author': 'Lab 167',
    'description': 'Template protocol with placeholders for incubation time/temperature and shaker speed'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def _parse_int(value, default):
    # If the value is a real int, return it
    if isinstance(value, int):
        return value
    # If the value is a string of digits, convert
    if isinstance(value, str) and value.strip().isdigit():
        try:
            return int(value.strip())
        except ValueError:
            return default
    # If the value is still a placeholder (not substituted yet), return default
    if isinstance(value, str) and value.strip().startswith('[['):
        return default
    # Fallback attempt
    try:
        return int(value)
    except Exception:
        return default


def run(protocol: protocol_api.ProtocolContext):
    # 1) Load Heater-Shaker module and place Cytiva labware on it (Slot 1).
    hs_mod = protocol.load_module(module_name="heaterShakerModuleV1", location="D1")

    # Attempt to load the custom Cytiva labware directly on the heater shaker. If not found in simulation,
    # fall back to a standard plate for simulation purposes.
    try:
        source_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception:
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; using a standard plate for SIMULATION fallback only.')
        source_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Ensure latch is closed before shaking (required by the API)
    hs_mod.close_labware_latch()

    # 2) Temperature step: only if INCUBATION_TEMPERATURE >= 37
    incubation_temp = _parse_int(INCUBATION_TEMPERATURE, 37)
    if incubation_temp >= 37:
        hs_mod.set_target_temperature(incubation_temp)
        hs_mod.wait_for_temperature()

    # Shake step: incubate for INCUBATION_TIME minutes at SHAKER_SPEED_INCUBATION RPM
    incubation_time_min = _parse_int(INCUBATION_TIME, 1)
    shaker_rpm = _parse_int(SHAKER_SPEED_INCUBATION, 600)

    # Start shaking at target RPM (blocks until speed is reached)
    hs_mod.set_and_wait_for_shake_speed(shaker_rpm)
    # Use a delay to represent incubation time (convert minutes to seconds)
    protocol.delay(seconds=max(incubation_time_min, 0) * 60)

    # 3) End state: stop heating/shaking and open latch
    hs_mod.deactivate_heater()
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()

    # 4) Deck setup post-incubation: load tip rack and destination plate
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    destination = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 9)

    # 5) Pipettes: Right mount -> P300 Single-Channel Gen2; Left mount -> P300 Multi-Channel Gen2
    p300_single = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack])
    p300_multi = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack])

    # 6) Transfer: from first tube (A1) of Cytiva labware to every well of destination using a single tip
    src_well = source_plate.wells_by_name()['A1']
    dest_wells = destination.wells()
    p300_single.transfer(1.0, src_well, dest_wells, new_tip='once')
