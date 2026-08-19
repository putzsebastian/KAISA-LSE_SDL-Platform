from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt Equilibration Templated Protocol',
    'author': 'User',
    'description': 'Templated protocol for equilibrating a filter plate with salt buffers using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (literal strings so the template engine can substitute values)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_EQUILIBRATION_VOLUME = '[[EQUILIBRATION_VOLUME]]'
PLACEHOLDER_SHAKER_SPEED_INCUBATION = '[[SHAKER_SPEED_INCUBATION]]'
PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION = '[[EQUILIBRATION_CYCLE_DURATION]]'


def _unreplaced(s: str) -> bool:
    """Return True if the string is still a [[PLACEHOLDER]]."""
    s_stripped = str(s).strip()
    return s_stripped.startswith('[' * 2) and s_stripped.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder to a number, with a simulation default.

    Uses the default only when the placeholder has not yet been substituted.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(default)
    return cast(float(s))


def parse_int(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated placeholder into a list."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ----------------------------------------------------------------------
    # Parse template parameters
    # ----------------------------------------------------------------------
    # For simulation, use a worst-case style default up to the physical limit
    # of 12 columns. Real runs must ensure (len(SALT_CONCENTRATIONS) * REPLICATES)
    # does not exceed 12.
    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100],
        cast=float,
    )
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=1)
    equilibration_volume = parse_scalar(PLACEHOLDER_EQUILIBRATION_VOLUME, default=300.0)
    shaker_speed = parse_scalar(PLACEHOLDER_SHAKER_SPEED_INCUBATION, default=2000.0)
    equilibration_cycle_duration_min = parse_scalar(
        PLACEHOLDER_EQUILIBRATION_CYCLE_DURATION,
        default=120.0,
    )

    num_concs = len(salt_concs)
    num_transfers = num_concs * replicates

    # NOTE for real runs: it is the caller's responsibility to ensure that
    # num_transfers <= 12 (number of columns of the filter plate).
    if num_transfers > 12:
        protocol.comment(
            f"WARNING: concentrations x replicates = {num_transfers} exceeds 12 columns; "
            f"using first 12 for SIMULATION ONLY. Ensure your template values fit 12 columns."
        )
        num_transfers = 12

    # ----------------------------------------------------------------------
    # Modules
    # ----------------------------------------------------------------------
    # Heater-Shaker Module GEN1 in slot 1, with filter plate loaded directly
    hs_mod = protocol.load_module('heaterShakerModuleV1', '1')

    # ----------------------------------------------------------------------
    # Labware
    # ----------------------------------------------------------------------
    # Slot 1: Cytiva 96 Filter Plate on Heater-Shaker (custom labware)
    try:
        filter_plate = hs_mod.load_labware(
            'cytiva_96_filterwellplate_1ml',
            label='Cytiva 96 Filter Plate',
        )
    except Exception as exc:
        # Simulator does not have custom definitions; use a 96-well plate as a
        # fallback with identical well layout. On real robot, the custom
        # definition must be present and this branch will not execute.
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware definition not available; using NEST 96 flat ' \
            'plate as SIMULATION fallback only.'
        )
        filter_plate = hs_mod.load_labware(
            'nest_96_wellplate_200ul_flat',
            label='SIMULATION ONLY fallback for Cytiva filter plate',
        )

    # Slot 4, 7, 10: Tipracks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', '4')
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', '7')
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', '10')

    # Slot 3, 5, 6, 8, 9: NEST 12-well reservoirs (0–4)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', '3')  # Reservoir 4
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', '6')  # Reservoir 3 (salt buffers)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', '8')  # Reservoir 2
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', '9')  # Reservoir 1
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', '5')  # Reservoir 0

    # Slot 11: NEST 96 Deep-Well Plate (Mixing Plate)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', '11')

    # ----------------------------------------------------------------------
    # Pipettes
    # ----------------------------------------------------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10],
    )
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10],
    )

    # Use tips from slot 4 first, then 7 and 10 as needed
    p300_multi.starting_tip = tiprack_4['A1']

    # ----------------------------------------------------------------------
    # Step 2: Equilibration transfers from Reservoir 3 to filter plate
    # ----------------------------------------------------------------------
    # Reservoir 3 (slot 6) holds the buffers with different salt concentrations.
    # For each i in num_transfers, transfer [[EQUILIBRATION_VOLUME]] µL from
    # well i (0-based, A1..A12) of Reservoir 3 to all wells in column i of the
    # filter plate, using a 7 mm bottom offset and the multi-channel pipette.

    # Close latch before any pipetting on the Heater-Shaker labware
    hs_mod.close_labware_latch()

    # Select the source wells and destination columns for the first num_transfers
    source_wells = reservoir3.wells()[:num_transfers]
    dest_columns = filter_plate.columns()[:num_transfers]

    transfer_volume = equilibration_volume

    for src, dest_col in zip(source_wells, dest_columns):
        # For a multi-channel pipette, address row A of the destination column;
        # the 8 channels then cover the full column.
        dest_well = dest_col[0]
        p300_multi.pick_up_tip()
        p300_multi.transfer(
            transfer_volume,
            src,
            dest_well.bottom(z=7.0),  # 7 mm offset from bottom of filter plate
            new_tip='never',          # reuse the same tip for each column transfer
        )
        # Return the used tip to its original position in the rack
        p300_multi.return_tip()

    # ----------------------------------------------------------------------
    # Step 3: Incubation on Heater-Shaker
    # ----------------------------------------------------------------------
    hs_mod.set_and_wait_for_shake_speed(shaker_speed)
    protocol.delay(seconds=equilibration_cycle_duration_min * 60)
    hs_mod.deactivate_shaker()

    # Open latch after shaking is complete
    hs_mod.open_labware_latch()
