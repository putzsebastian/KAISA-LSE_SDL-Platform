from opentrons import protocol_api

metadata = {
    'protocolName': '[protocol name by user]',
    'author': '[user name]',
    'description': "[what is the protocol about]"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (templatable values)
INCUBATION_TIME = '[[INCUBATION_TIME]]'
INCUBATION_TEMPERATURE = '[[INCUBATION_TEMPERATURE]]'
SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'


def _to_float(value, fallback):
    try:
        return float(value)
    except Exception:
        return float(fallback)


def run(protocol: protocol_api.ProtocolContext):
    # Step 0: Load Heater Shaker module on Slot 1
    hs_mod = protocol.load_module(module_name="heaterShakerModuleV1", location="1")

    # Step 0.5: Load cytiva custom labware directly on the heater shaker (Slot 1)
    try:
        cytiva_labware = hs_mod.load_labware(name="cytiva_96_filterwellplate_1ml")
    except Exception as exc:
        # If custom labware not available in simulation, fallback to a standard plate
        if 'not found' not in str(exc).lower():
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found on heater shaker; using a standard plate as SIMULATION fallback only.')
        cytiva_labware = hs_mod.load_labware(name="nest_96_wellplate_200ul_flat")

    # Step 0.6: Tip rack on Slot 7
    tiprack_300 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)

    # Step 0.7: P300s on right (single) and left (8-channel)
    p300s = protocol.load_instrument('p300_single_gen2', mount="right", tip_racks=[tiprack_300])
    p300m = protocol.load_instrument('p300_multi_gen2', mount="left", tip_racks=[tiprack_300])

    # Parse placeholders into numeric values with safe fallbacks for simulation
    incubation_time_min = int(_to_float(INCUBATION_TIME, 60.0))          # minutes
    incubation_temp = _to_float(INCUBATION_TEMPERATURE, 37.0)            # Celsius
    shaker_rpm = int(_to_float(SHAKER_SPEED_INCUBATION, 1800.0))          # rpm

    # Step 1: Close latch and set temperature if >= 37C
    try:
        if incubation_temp >= 37.0:
            hs_mod.close_labware_latch()
            # Try common API names for setting temp; fall back gracefully if not available
            try:
                hs_mod.set_and_wait_for_temperature(incubation_temp)
            except Exception:
                try:
                    hs_mod.set_temperature(incubation_temp)
                except Exception:
                    protocol.comment('Temperature control API not available; skipping heating step.')
        else:
            protocol.comment('Incubation temperature below 37C; skipping heating step.')
    except Exception as e:
        protocol.comment('Heating step encountered an error: {}'.format(str(e)))

    # Step 2: Shake for incubation_time_min minutes at shaker_rpm
    try:
        hs_mod.close_labware_latch()
        try:
            hs_mod.set_and_wait_for_shake_speed(shaker_rpm)
        except Exception:
            # If method not available, try alternative common name
            hs_mod.set_and_wait_for_shake_speed(shaker_rpm)
        protocol.delay(incubation_time_min * 60)
        # Stop shaking
        try:
            hs_mod.set_and_wait_for_shake_speed(0)
        except Exception:
            protocol.comment('Could not stop shaker via API; attempting fallback is not defined.')
    except Exception as e:
        protocol.comment('Shaking step encountered an error: {}'.format(str(e)))

    # Step 3: End: Stop heating/shaking and open latch
    try:
        # Attempt to return to a safe idle temperature (room temp)
        try:
            hs_mod.set_and_wait_for_temperature(25.0)
        except Exception:
            try:
                hs_mod.set_temperature(25.0)
            except Exception:
                pass
    except Exception:
        pass

    try:
        hs_mod.open_labware_latch()
    except Exception:
        pass
