from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt-Ligand Titration on Filter Plate',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt and ligand titrations in a Cytiva 96-well filter plate on a Heater-Shaker.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (kept as literal strings for the template system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_N_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_N_LIGAND = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


def _unreplaced(s: str) -> bool:
    """Return True if a placeholder token like '[[TOKEN]]' is still unreplaced.

    Uses constructed brackets to avoid literal '[['/']]' in the source, as
    required by the templating system.
    """
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder value with a worst-case default for simulation.

    The default should be the largest sensible value the protocol might see.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_int(value, default):
    s = str(value).strip()
    if _unreplaced(s):
        return int(default)
    return int(float(s))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder, with a worst-case default.

    Example: '0;100;200;500' -> [0.0, 100.0, 200.0, 500.0]
    """
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


def run(protocol: protocol_api.ProtocolContext):
    # ---------------------------------------------------------------------
    # Parse placeholders (with conservative, worst-case defaults for simulation)
    # ---------------------------------------------------------------------
    # Use relatively large defaults to stress-test tip usage and volumes.
    replicates = parse_int(PLACEHOLDER_REPLICATES, default=3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, default=200.0)  # uL per well total

    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS, default=[0, 100, 200, 500])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS,
                              default=[1, 2, 3, 4, 5, 6, 7, 8])

    # NUMBER_OF_* placeholders can override list length if desired
    n_salt = parse_int(PLACEHOLDER_N_SALT, default=len(salt_concs))
    n_ligand = parse_int(PLACEHOLDER_N_LIGAND, default=len(ligand_concs))

    # Clamp to plate dimensions (96-well: 12 columns x 8 rows)
    n_salt = min(n_salt, 12)
    n_ligand = min(n_ligand, 8)

    # Derived volume: each step uses half of TOTAL_VOLUME per well
    half_volume = total_volume / 2.0

    # ---------------------------------------------------------------------
    # Modules
    # ---------------------------------------------------------------------
    hs_mod = protocol.load_module('heaterShakerModuleV1', 1)

    # ---------------------------------------------------------------------
    # Labware
    # ---------------------------------------------------------------------
    # Filter plate on Heater-Shaker (custom labware with simulation fallback)
    try:
        filter_plate = hs_mod.load_labware('cytiva_96_filterwellplate_1ml')
    except Exception as exc:
        # Only fall back if the definition is actually missing
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.')
        filter_plate = hs_mod.load_labware('nest_96_wellplate_200ul_flat')

    # Reservoirs (deck layout as provided; only Reservoir 4 is actively used here)
    reservoir_4 = protocol.load_labware('nest_12_reservoir_15ml', 3)  # salt buffers
    reservoir_3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    reservoir_2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    reservoir_1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    reservoir_0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    # Mixing plate: NEST 96 Deep-Well 2 mL in slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # Tip racks
    tiprack_4 = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    tiprack_7 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    tiprack_10 = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # ---------------------------------------------------------------------
    # Pipettes
    # ---------------------------------------------------------------------
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_4, tiprack_7, tiprack_10]
    )

    # Ensure Heater-Shaker latch is closed before any pipetting on the module
    hs_mod.close_labware_latch()

    # ---------------------------------------------------------------------
    # Step 1: Transfer salt buffers from Reservoir 4 to Filter Plate
    # ---------------------------------------------------------------------
    # Each buffer (one per salt concentration) goes into REPLICATES columns
    # of the filter plate, TOTAL_VOLUME/2 uL per well, using the 8-channel.
    # Tips from slot 4 are picked up once and returned for reuse.

    # Use the first column of tips (A1) from slot 4, as specified
    p300_multi.pick_up_tip(tiprack_4['A1'])

    # Loop over salt concentrations by index; each index corresponds to one
    # reservoir_4 well and one block of REPLICATES columns on the filter plate.
    for salt_index in range(n_salt):
        src_well = reservoir_4.wells()[salt_index]

        # Compute destination column range for this salt
        # Example (REPLICATES=3):
        #  salt_index 0 -> columns 0,1,2
        #  salt_index 1 -> columns 3,4,5
        #  salt_index 2 -> columns 6,7,8
        #  salt_index 3 -> columns 9,10,11
        start_col = salt_index * replicates
        end_col = start_col + replicates

        # Stop if we run out of columns
        if start_col >= 12:
            break
        end_col = min(end_col, 12)

        for col_idx in range(start_col, end_col):
            # Multi-channel must address the top row of the column (row A)
            dest_col = filter_plate.columns()[col_idx]
            dest_location = dest_col[0].bottom(z=7.0)  # 7 mm above bottom as requested

            # Single 8-channel shot per column: half_volume per well
            p300_multi.aspirate(half_volume, src_well)
            p300_multi.dispense(half_volume, dest_location)

    # Return tips to rack for reuse
    p300_multi.return_tip()

    # ---------------------------------------------------------------------
    # Step 2: Transfer ligands from Mixing Plate to Filter Plate
    # ---------------------------------------------------------------------
    # For each column in the mixing plate (one per salt concentration),
    # transfer TOTAL_VOLUME/2 uL per well into the corresponding REPLICATES
    # columns on the filter plate, using the same tips and 7 mm offset.

    # Reuse the same tip column from slot 4
    p300_multi.pick_up_tip(tiprack_4['A1'])

    for ligand_col_index in range(n_salt):
        # One mixing-plate column per salt condition; clamp to 12 columns
        if ligand_col_index >= 12:
            break
        src_col = mixing_plate.columns()[ligand_col_index]

        # Same replicate column mapping as in Step 1
        start_col = ligand_col_index * replicates
        end_col = start_col + replicates

        if start_col >= 12:
            break
        end_col = min(end_col, 12)

        for dest_col_index in range(start_col, end_col):
            dest_col = filter_plate.columns()[dest_col_index]

            # Multi-channel uses row A of each column for location
            src_location = src_col[0]
            dest_location = dest_col[0].bottom(z=7.0)

            p300_multi.aspirate(half_volume, src_location)
            p300_multi.dispense(half_volume, dest_location)

    # Return tips again after ligand transfers
    p300_multi.return_tip()

    protocol.comment('Protocol complete.')
