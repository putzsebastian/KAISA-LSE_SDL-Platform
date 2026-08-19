from opentrons import protocol_api

metadata = {
    'protocolName': '[protocol name by user]',
    'author': '[user name]',
    'description': "[what is the protocol about]"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # =====================
    # Placeholders (templatable)
    # =====================
    REPLICATES_PLACEHOLDER = "3"
    SALT_CONC_PLACEHOLDER = "500"  # semicolon-separated string
    EQ_VOL_PLACEHOLDER = "300"    # in uL per well
    SHAKER_SPEED_PLACEHOLDER = "1300"  # RPM
    CYCLE_DURATION_PLACEHOLDER = "5"  # minutes

    # Helper parsing with safe defaults (enables simulation even if placeholders not replaced)
    def parse_int(val, default):
        try:
            if isinstance(val, str) and val.strip().startswith("[["):
                return int(default)
            return int(val)
        except Exception:
            return int(default)

    def parse_float(val, default):
        try:
            if isinstance(val, str) and val.strip().startswith("[["):
                return float(default)
            return float(val)
        except Exception:
            return float(default)

    def parse_semicolon_list(val, default_list_str):
        if isinstance(val, str) and val.strip().startswith("[["):
            base = default_list_str
        else:
            base = str(val)
        return [s.strip() for s in base.split(";") if s.strip()]

    # Defaults for simulation; replace placeholders before running actual protocol
    replicates = parse_int(REPLICATES_PLACEHOLDER, 3)
    salt_concentrations = parse_semicolon_list(SALT_CONC_PLACEHOLDER, "0;100;200;500")
    equilibration_volume_ul = parse_float(EQ_VOL_PLACEHOLDER, 100)
    shaker_speed_rpm = parse_int(SHAKER_SPEED_PLACEHOLDER, 500)
    cycle_duration_min = parse_float(CYCLE_DURATION_PLACEHOLDER, 5)

    # =====================
    # Modules
    # =====================
    hs_mod = protocol.load_module("heaterShakerModuleV1", 1)
    # Open latch before placing labware, then close once loaded
    hs_mod.open_labware_latch()

    # =====================
    # Labware
    # =====================
    # Filter plate on Heater-Shaker (custom labware). Fallback to a common 96-well flat plate if custom is unavailable.
    try:
        filter_plate = hs_mod.load_labware("cytiva_96_filterwellplate_1ml")
    except Exception:
        filter_plate = hs_mod.load_labware("nest_96_wellplate_200ul_flat")

    # Tip racks
    tiprack_slot4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_slot7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_slot10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs and mixing plate (as specified deck layout)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)   # Reservoir 4
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)   # Reservoir 3 (buffers)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)   # Reservoir 2
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)   # Reservoir 1
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)   # Reservoir 0
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Close latch after labware is on the Heater-Shaker before pipetting
    hs_mod.close_labware_latch()

    # =====================
    # Pipettes
    # =====================
    # Multi-channel uses only Slot 4 for tips per user requirement
    p300m = protocol.load_instrument('p300_multi_gen2', mount='left', tip_racks=[tiprack_slot4])
    # Single-channel (loaded per config; not used in this protocol step)
    p300s = protocol.load_instrument('p300_single_gen2', mount='right', tip_racks=[tiprack_slot7, tiprack_slot10])

    # =====================
    # Step 1: Determine number of transfers
    # =====================
    num_concentrations = len(salt_concentrations)
    num_transfers = replicates * num_concentrations

    # Cap to available columns/wells (12) to avoid index errors
    max_positions = min(num_transfers, 12)

    # =====================
    # Step 2: For each i, transfer equilibration buffer from Reservoir 3 well i to EVERY well of Filterplate column i
    # Use multi-channel, tips from slot 4, always return tips
    # =====================
    # Prepare source wells and destination columns
    source_wells = reservoir_3.wells()[:max_positions]  # A1..A12 in order (Well[0] -> A1, etc.)
    dest_columns = filter_plate.columns()[:max_positions] # Columns 1..12
    dest_locations = [col[0].bottom(z=7) for col in dest_columns]  # 7mm from bottom

    if max_positions > 0 and equilibration_volume_ul > 0:
        p300m.transfer(
            equilibration_volume_ul,
            source_wells,
            dest_locations,
            new_tip='always',  # new tip per column
            trash=False        # always return tips to slot 4
        )

    # =====================
    # Step 3: Shake for equilibration
    # =====================
    # Ensure latch is closed before shaking
    hs_mod.close_labware_latch()
    hs_mod.set_and_wait_for_shake_speed(shaker_speed_rpm)
    protocol.delay(minutes=cycle_duration_min)
    hs_mod.deactivate_shaker()
    hs_mod.open_labware_latch()