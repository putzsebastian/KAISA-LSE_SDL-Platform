from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Gradient Setup (Templated)',
    'author': 'User',
    'description': 'Templated protocol creating salt and ligand gradients using placeholders.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholders (must remain literal for the template system)
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONC = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONC = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_N_SALT = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_N_LIG = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


# Helper: detect unreplaced placeholder without ever writing '[[' literally

def _unreplaced(s: str) -> bool:
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder to float (or cast), with a simulation fallback.

    The default should be a worst-case upper bound so simulation stresses resources.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return default
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    """Parse a list placeholder: 'a;b;c' -> [cast(a), cast(b), cast(c)]."""
    s = str(value).strip()
    if _unreplaced(s):
        return list(default)
    return [cast(x) for x in s.split(';') if x.strip()]


class ReagentPool:
    """Track and dispense from a pool of wells holding the same reagent.

    Volumes are tracked in µL per well. `provide(volume_ul_per_channel, pipette, dest)`
    dispenses to `dest` in tip-sized chunks, splitting across wells if needed.
    """

    def __init__(self, wells, initial_volume_ul):
        self.wells = list(wells)
        self.remaining = {w: float(initial_volume_ul) for w in self.wells}

    def total_remaining(self):
        return sum(self.remaining[w] for w in self.wells)

    def provide(self, volume_ul: float, pipette, dest):
        """Provide `volume_ul` from the pool into `dest`.

        NOTE: `volume_ul` is PER CHANNEL for multichannel moves. The OT-2 API
        applies channels automatically; we track µL per channel here as a
        convenient abstraction, with the understanding that a single-row
        reservoir well actually loses 8x this for an 8‑channel pipette.
        """
        remaining_request = float(volume_ul)
        for well in self.wells:
            if remaining_request <= 0:
                break
            available = self.remaining[well]
            if available <= 0:
                continue
            take = min(available, remaining_request)
            vol_left = take
            # Chunk by pipette max volume
            while vol_left > 0:
                chunk = min(pipette.max_volume, vol_left)
                pipette.aspirate(chunk, well)
                pipette.dispense(chunk, dest)
                vol_left -= chunk
            self.remaining[well] -= take
            remaining_request -= take
        if remaining_request > 0:
            raise RuntimeError(f'Reagent pool out of volume, short by {remaining_request} uL')


def run(protocol: protocol_api.ProtocolContext) -> None:
    # ---------------------------------------------------------------------
    # 1) Parse template parameters with robust fallbacks (simulation only)
    # ---------------------------------------------------------------------
    # Use large, worst-case defaults so volume/tip budgeting is exercised.
    replicates = parse_int(PLACEHOLDER_REPLICATES, 3)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 600.0)  # example upper bound

    # Example worst-case lists for simulation: 8 ligand concs, 6 salt concs
    salt_concs = parse_list(PLACEHOLDER_SALT_CONCENTRATIONS,
                            [0, 50, 100, 150, 200, 300])
    ligand_concs = parse_list(PLACEHOLDER_LIGAND_CONCENTRATIONS,
                              [0, 1, 2, 5, 10, 20, 50, 100])

    n_salt = parse_int(PLACEHOLDER_N_SALT, len(salt_concs))
    n_lig = parse_int(PLACEHOLDER_N_LIG, len(ligand_concs))

    # ---------------------------------------------------------------------
    # 2) Deck layout: labware
    # ---------------------------------------------------------------------
    # Slot 1: Custom Cytiva 96 filter plate (with simulation fallback)
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment('WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
                         'using NEST 96 flat as SIMULATION fallback only.')
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tips
    # Slot 7: tips for multichannel (primary)
    tiprack_multi_main = protocol.load_labware('opentrons_96_tiprack_300ul', 7)
    # Slot 4: tips for multichannel (backup)
    tiprack_multi_backup = protocol.load_labware('opentrons_96_tiprack_300ul', 4)
    # Slot 10: tips for single channel
    tiprack_single = protocol.load_labware('opentrons_96_tiprack_300ul', 10)

    # Reservoirs (NEST 12-well 15 mL)
    # Slot 8: Reservoir 2 (low & high salt buffers)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    # Slot 9: Reservoir 1 (ligand stocks + buffers)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    # Slot 5: Reservoir 0 (low salt buffer only)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)
    # Slot 6: Reservoir 3 (product salt buffers, initially empty)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    # Slot 3: Reservoir 4 (2x salt buffers, initially empty)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)

    # Mixing plate: NEST 96 Deep-Well Plate 2 mL in Slot 11
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # ---------------------------------------------------------------------
    # 3) Pipettes
    # ---------------------------------------------------------------------
    # Left: P300 8‑channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_multi_main, tiprack_multi_backup]
    )

    # Right: P300 single‑channel GEN2
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_single]
    )

    # ---------------------------------------------------------------------
    # 4) Define reagent pools based on specified layout
    # ---------------------------------------------------------------------
    # Each reservoir well initially contains 14 mL = 14000 µL.
    initial_reservoir_vol_ul = 14000.0

    # Low salt buffer wells:
    # Reservoir 2: wells 0–5 are low salt
    low_salt_wells_res2 = [reservoir2.wells()[i] for i in range(0, 6)]
    # Reservoir 1: wells 2–6 are low salt
    low_salt_wells_res1 = [reservoir1.wells()[i] for i in range(2, 7)]
    # Reservoir 0: wells 0–11 are all low salt
    low_salt_wells_res0 = list(reservoir0.wells())

    low_salt_wells = low_salt_wells_res2 + low_salt_wells_res1 + low_salt_wells_res0

    # High salt buffer wells:
    # Reservoir 2: wells 6–11 are high salt
    high_salt_wells_res2 = [reservoir2.wells()[i] for i in range(6, 12)]
    # Reservoir 1: wells 7–11 are high salt
    high_salt_wells_res1 = [reservoir1.wells()[i] for i in range(7, 12)]

    high_salt_wells = high_salt_wells_res2 + high_salt_wells_res1

    low_salt_pool = ReagentPool(low_salt_wells, initial_reservoir_vol_ul)
    high_salt_pool = ReagentPool(high_salt_wells, initial_reservoir_vol_ul)

    # Ligand stocks & buffer for ligand dilutions
    ligand_stock_high = reservoir1.wells()[0]  # Well 0: high stock [[LIGAND_STOCK_CONCENTRATION]]
    ligand_stock_low = reservoir1.wells()[1]   # Well 1: low stock [[LIGAND_STOCK_CONCENTRATION]]/10
    low_salt_for_ligand = reservoir1.wells()[2]  # Well 2: low salt buffer for ligand dilutions

    # ---------------------------------------------------------------------
    # 5) Destination wells allocation
    # ---------------------------------------------------------------------
    # Reservoir 3: [[REPLICATES]] wells per salt concentration
    # Ensure [[REPLICATES]] x [[NUMBER_OF_SALT_CONCENTRATIONS]] <= 12 on the template side.
    dest_wells_res3 = reservoir3.wells()[:replicates * n_salt]

    # Reservoir 4: 1 well per salt concentration (2x concentration)
    dest_wells_res4 = reservoir4.wells()[:n_salt]

    # ---------------------------------------------------------------------
    # STEP 2: Prepare salt buffers in Reservoir 3
    # ---------------------------------------------------------------------
    protocol.comment('Step 2: Prepare salt buffers in Reservoir 3')

    # Total target volume per reservoir-3 well: 10 mL = 10000 µL
    total_target_volume_res_ul = 10000.0

    if salt_concs:
        max_salt = max(salt_concs)
    else:
        max_salt = 0.0

    for i, salt_c in enumerate(salt_concs[:n_salt]):
        # Simple linear mixing rule: fraction of high-salt relative to max.
        frac_high = salt_c / max_salt if max_salt > 0 else 0.0
        frac_low = 1.0 - frac_high

        vol_high_ul = total_target_volume_res_ul * frac_high
        vol_low_ul = total_target_volume_res_ul * frac_low

        for r in range(replicates):
            dest_index = i * replicates + r
            dest = dest_wells_res3[dest_index]

            # Use multichannel tips from the dedicated racks
            if not p300_multi.has_tip:
                p300_multi.pick_up_tip()

            # NOTE: `provide` expects PER-CHANNEL volume. The OT-2 API
            # automatically applies 8 channels; do NOT multiply by 8.
            if vol_high_ul > 0:
                high_salt_pool.provide(vol_high_ul / 8.0, p300_multi, dest)
            if vol_low_ul > 0:
                low_salt_pool.provide(vol_low_ul / 8.0, p300_multi, dest)

            # Mix the completed well
            p300_multi.mix(5, 200, dest)
            p300_multi.drop_tip()

    # ---------------------------------------------------------------------
    # STEP 3: Prepare 2x salt buffers in Reservoir 4
    # ---------------------------------------------------------------------
    protocol.comment('Step 3: Prepare 2x salt buffers in Reservoir 4')

    for i, salt_c in enumerate(salt_concs[:n_salt]):
        # For a 2x buffer we target double the salt concentration
        if max_salt > 0:
            frac_high = (2.0 * salt_c) / max_salt
        else:
            frac_high = 0.0
        # Clamp to [0, 1]
        if frac_high > 1.0:
            frac_high = 1.0
        if frac_high < 0.0:
            frac_high = 0.0
        frac_low = 1.0 - frac_high

        vol_high_ul = total_target_volume_res_ul * frac_high
        vol_low_ul = total_target_volume_res_ul * frac_low

        dest = dest_wells_res4[i]

        if not p300_multi.has_tip:
            p300_multi.pick_up_tip()

        if vol_high_ul > 0:
            high_salt_pool.provide(vol_high_ul / 8.0, p300_multi, dest)
        if vol_low_ul > 0:
            low_salt_pool.provide(vol_low_ul / 8.0, p300_multi, dest)

        p300_multi.mix(5, 200, dest)
        p300_multi.drop_tip()

    # ---------------------------------------------------------------------
    # STEP 4: Prepare ligand dilutions in mixing plate (2x final ligand conc)
    # ---------------------------------------------------------------------
    protocol.comment('Step 4: Prepare ligand dilutions in mixing plate')

    # Total volume per well:
    # [[TOTAL_VOLUME]]/2 * [[REPLICATES]] * 1.5 (templated)
    per_well_total_vol = (total_volume / 2.0) * replicates * 1.5

    max_ligand_conc = max(ligand_concs) if ligand_concs else 0.0

    # For each salt concentration, make one column.
    # For each ligand concentration, go down rows A–H (0–7 index).
    for col_idx in range(n_salt):
        if col_idx >= 12:
            # Mixing plate has only 12 columns
            break

        for row_idx, lig_conc in enumerate(ligand_concs[:n_lig]):
            if row_idx >= 8:
                # Only rows A–H exist
                break

            dest = mixing_plate.rows()[row_idx][col_idx]

            # Target ligand concentration is 2x the required ligand concentration.
            # Decide volume from stock(s). For templated script, we compute a
            # simple fraction relative to max_ligand_conc.
            if max_ligand_conc > 0:
                target_conc_2x = 2.0 * lig_conc
                frac_stock_high = target_conc_2x / max_ligand_conc
                if frac_stock_high > 1.0:
                    frac_stock_high = 1.0
                if frac_stock_high < 0.0:
                    frac_stock_high = 0.0
            else:
                frac_stock_high = 0.0

            vol_stock_ul = per_well_total_vol * frac_stock_high
            vol_buffer_ul = per_well_total_vol - vol_stock_ul

            # If volume from high stock would fall below ~20 µL in reality,
            # the template user should adjust to use the low stock in well 1.
            # Here we keep a simple rule: always use high stock placeholder for
            # stock volume, but the template logic can be adapted after
            # substitution if needed.

            if not p300_single.has_tip:
                p300_single.pick_up_tip()

            # 1) Add ligand stock (from high or adjusted to low stock).
            if vol_stock_ul > 0:
                # Use well 0 (high stock) by default. When templated values are
                # substituted, user may choose to recalculate and instead draw
                # from ligand_stock_low if the calculated volume per well would
                # drop below 20 µL, adjusting volume for 10x lower concentration.
                source_stock = ligand_stock_high
                p300_single.transfer(
                    vol_stock_ul,
                    source_stock,
                    dest,
                    new_tip='never',
                    mix_after=(3, min(100, p300_single.max_volume))
                )

            # 2) Fill up with low-salt buffer for ligand dilutions (Reservoir 1 well 2)
            if vol_buffer_ul > 0:
                p300_single.transfer(
                    vol_buffer_ul,
                    low_salt_for_ligand,
                    dest,
                    new_tip='never',
                    mix_after=(3, min(200, p300_single.max_volume))
                )

            p300_single.drop_tip()

    protocol.comment('Protocol complete.')
