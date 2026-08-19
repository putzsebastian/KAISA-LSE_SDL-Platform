from opentrons import protocol_api

metadata = {
    'protocolName': 'Salt and Ligand Dilution Template',
    'author': 'User',
    'description': 'Templated protocol using placeholders for salt buffers and ligand dilutions in reservoirs and a 96-deepwell mixing plate.'
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# -----------------------------
# Placeholders (literal strings for external substitution)
# -----------------------------
PLACEHOLDER_REPLICATES = '[[REPLICATES]]'
PLACEHOLDER_TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
PLACEHOLDER_SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
PLACEHOLDER_LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
PLACEHOLDER_SALT_STOCK_CONC = '[[SALT_STOCK_CONCENTRATION]]'
PLACEHOLDER_LIGAND_STOCK_CONC = '[[LIGAND_STOCK_CONCENTRATION]]'
PLACEHOLDER_NUM_SALT_CONC = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
PLACEHOLDER_NUM_LIGAND_CONC = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'


# -----------------------------
# Helper functions for placeholders
# -----------------------------

def _unreplaced(s: str) -> bool:
    """Return True if s still looks like a [[PLACEHOLDER]] token.

    Uses constructed brackets so the literal '[[' never appears in code,
    making it safe for the templating engine to substitute.
    """
    s = str(s).strip()
    return s.startswith('[' * 2) and s.endswith(']' * 2)


def parse_scalar(value, default, cast=float):
    """Parse a scalar placeholder as float via `cast`.

    If the token is unreplaced (still like [[TOKEN]]), return the provided
    default (used only for SIMULATION). After substitution, any
    unparsable value should raise.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return cast(float(default))
    return cast(float(s))


def parse_int(value, default):
    return int(parse_scalar(value, default, cast=float))


def parse_list(value, default, cast=float):
    """Parse a semicolon-separated list placeholder into a list of values.

    Example literal: '0;50;100;150'. Defaults are used only for simulation.
    """
    s = str(value).strip()
    if _unreplaced(s):
        return [cast(x) for x in default]
    return [cast(x) for x in s.split(';') if s.strip()]


# -----------------------------
# Reagent pool with volume tracking across multiple wells
# -----------------------------

class ReagentPool:
    """Represents a pooled reagent split across several reservoir wells.

    Volumes are tracked in µL. For multi-channel moves into a single-row
    reservoir, the `volume_ul_per_channel` argument is per channel; the
    pool accounts for 8x when computing how much to draw from a well.
    """

    def __init__(self, name, wells, start_volumes_ul):
        self.name = name
        self.wells = list(wells)
        self.remaining = {w: float(v) for w, v in zip(self.wells, start_volumes_ul)}

    def total_remaining(self):
        return sum(self.remaining[w] for w in self.wells)

    def provide(self, volume_ul_per_channel, channels=8):
        """Allocate volume from the pool for one move.

        `volume_ul_per_channel` is the per-channel volume requested.
        The total drawn from the pool is volume_ul_per_channel * channels.

        Returns a list of (well, per_channel_volume_from_that_well) chunks.
        """
        total_needed = volume_ul_per_channel * channels
        if total_needed <= 0:
            return []
        moves = []
        remaining_need = total_needed
        for well in self.wells:
            if remaining_need <= 0:
                break
            avail = self.remaining[well]
            if avail <= 0:
                continue
            use = min(avail, remaining_need)
            if use > 0:
                self.remaining[well] -= use
                # convert total volume taken from that well back to per-channel
                moves.append((well, use / channels))
                remaining_need -= use
        if remaining_need > 0:
            raise RuntimeError(
                f"Not enough volume in pool {self.name}: need {total_needed} uL, "
                f"short by {remaining_need} uL"
            )
        return moves


# -----------------------------
# Main protocol
# -----------------------------

def run(protocol: protocol_api.ProtocolContext):
    # -------------------------
    # 1) Parse template parameters
    # -------------------------
    # Defaults are for SIMULATION only. In real use, the placeholders
    # are replaced before the run.

    replicates = parse_int(PLACEHOLDER_REPLICATES, 1)
    total_volume = parse_scalar(PLACEHOLDER_TOTAL_VOLUME, 50.0)  # used in step 4 formula

    salt_concs = parse_list(
        PLACEHOLDER_SALT_CONCENTRATIONS,
        default=[0.0, 50.0, 100.0]
    )
    ligand_concs = parse_list(
        PLACEHOLDER_LIGAND_CONCENTRATIONS,
        default=[0.0, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]
    )

    num_salt_conc = parse_int(PLACEHOLDER_NUM_SALT_CONC, len(salt_concs))
    num_ligand_conc = parse_int(PLACEHOLDER_NUM_LIGAND_CONC, len(ligand_concs))

    # Limit counts to available list lengths, in case the user supplies
    # NUMBER_* larger than the actual list length.
    salt_concs = salt_concs[:num_salt_conc]
    ligand_concs = ligand_concs[:num_ligand_conc]

    # -------------------------
    # 2) Deck layout
    # -------------------------
    # Slot 1: custom Cytiva 96 filter plate with SIMULATION fallback
    try:
        filter_plate = protocol.load_labware('cytiva_96_filterwellplate_1ml', 1)
    except Exception as exc:
        if 'not found' not in str(exc):
            raise
        protocol.comment(
            'WARNING: custom labware cytiva_96_filterwellplate_1ml not found; '
            'using nest_96_wellplate_200ul_flat as SIMULATION fallback only.'
        )
        filter_plate = protocol.load_labware('nest_96_wellplate_200ul_flat', 1)

    # Tip racks
    tiprack_multi_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 7)   # multi-channel tips
    tiprack_single_1 = protocol.load_labware('opentrons_96_tiprack_300ul', 10) # single-channel tips
    tiprack_spare = protocol.load_labware('opentrons_96_tiprack_300ul', 4)

    # Reservoirs (NEST 12-well 15 mL)
    # Slot 3: Reservoir 4 (empty at start, for 2x salt buffers)
    reservoir4 = protocol.load_labware('nest_12_reservoir_15ml', 3)
    # Slot 6: Reservoir 3 (empty at start, for working salt buffers)
    reservoir3 = protocol.load_labware('nest_12_reservoir_15ml', 6)
    # Slot 8: Reservoir 2 (low and high salt buffers)
    reservoir2 = protocol.load_labware('nest_12_reservoir_15ml', 8)
    # Slot 9: Reservoir 1 (ligand stocks, low salt, high salt)
    reservoir1 = protocol.load_labware('nest_12_reservoir_15ml', 9)
    # Slot 5: Reservoir 0 (low salt buffer)
    reservoir0 = protocol.load_labware('nest_12_reservoir_15ml', 5)

    # Slot 11: NEST 96 Deep-Well Plate 2 mL (mixing plate)
    mixing_plate = protocol.load_labware('nest_96_wellplate_2ml_deep', 11)

    # -------------------------
    # 3) Pipettes
    # -------------------------
    # Right mount: P300 Single-Channel GEN2
    p300_single = protocol.load_instrument(
        'p300_single_gen2',
        mount='right',
        tip_racks=[tiprack_single_1, tiprack_spare]
    )

    # Left mount: P300 8-Channel GEN2
    p300_multi = protocol.load_instrument(
        'p300_multi_gen2',
        mount='left',
        tip_racks=[tiprack_multi_1]
    )

    # -------------------------
    # 4) Define reagent pools and initial volumes
    # -------------------------
    # Each reservoir well starts with 14 mL = 14,000 µL

    # Reservoir 2: low salt (wells 0–5), high salt (wells 6–11)
    low_salt_wells_res2 = [reservoir2.wells()[i] for i in range(0, 6)]
    high_salt_wells_res2 = [reservoir2.wells()[i] for i in range(6, 12)]

    # Reservoir 1: ligand high (well 0), ligand low (well 1),
    # low salt buffer (wells 2–6), high salt buffer (wells 7–11)
    low_salt_wells_res1 = [reservoir1.wells()[i] for i in range(2, 7)]
    high_salt_wells_res1 = [reservoir1.wells()[i] for i in range(7, 12)]

    # Reservoir 0: low salt buffer (wells 0–11)
    low_salt_wells_res0 = list(reservoir0.wells())

    # Pooled low salt buffer across Reservoirs 2, 1, and 0
    low_salt_pool = ReagentPool(
        'Low salt buffer',
        wells=low_salt_wells_res2 + low_salt_wells_res1 + low_salt_wells_res0,
        start_volumes_ul=[14000.0] * (
            len(low_salt_wells_res2) + len(low_salt_wells_res1) + len(low_salt_wells_res0)
        ),
    )

    # Pooled high salt buffer across Reservoirs 2 and 1
    high_salt_pool = ReagentPool(
        'High salt buffer',
        wells=high_salt_wells_res2 + high_salt_wells_res1,
        start_volumes_ul=[14000.0] * (len(high_salt_wells_res2) + len(high_salt_wells_res1)),
    )

    # Ligand stocks (Reservoir 1)
    ligand_high_stock_well = reservoir1.wells()[0]
    ligand_low_stock_well = reservoir1.wells()[1]

    ligand_high_pool = ReagentPool('Ligand high stock', [ligand_high_stock_well], [14000.0])
    ligand_low_pool = ReagentPool('Ligand low stock', [ligand_low_stock_well], [14000.0])

    # -------------------------
    # 5) Helper: mix a reservoir well with multi-channel
    # -------------------------
    def mix_reservoir_well_multi(pip, well, volume_per_channel, repetitions=5):
        """Simple up/down mixing in a reservoir well using a multi-channel pipette.

        `volume_per_channel` is the per-channel volume; the API applies it
        to all 8 channels automatically.
        """
        for _ in range(repetitions):
            pip.aspirate(volume_per_channel, well)
            pip.dispense(volume_per_channel, well)

    # -------------------------
    # 6) Step 2 – Prepare salt buffers in Reservoir 3
    # -------------------------
    protocol.comment('Step 2: Preparing salt buffers in Reservoir 3')

    if replicates * len(salt_concs) > 12:
        raise RuntimeError(
            'replicates x number of salt concentrations exceeds 12 wells of Reservoir 3'
        )

    # Total volume per well in Reservoir 3: 10 mL = 10,000 µL (per user spec).
    # For simulation we use a smaller default, but this variable is where the
    # 10,000 µL should be applied once real parameters are set.
    total_vol_reservoir_well_ul = 2000.0  # SIMULATION default; template for 10000.0

    # Use multi-channel pipette; volumes are per channel.
    p300_multi.flow_rate.aspirate = 150
    p300_multi.flow_rate.dispense = 300

    dest_index = 0
    max_salt = max(salt_concs) if salt_concs else 0.0

    for salt in salt_concs:
        for _ in range(replicates):
            dest_well = reservoir3.wells()[dest_index]
            dest_index += 1

            # Fraction of high vs. low salt for this target concentration.
            # The exact conversion from concentration to fraction should be
            # supplied by the user; here we treat it as linear relative to
            # max(salt_concs) as a template.
            fraction_high = (salt / max_salt) if max_salt > 0 else 0.0
            fraction_low = 1.0 - fraction_high

            vol_high_total = total_vol_reservoir_well_ul * fraction_high
            vol_low_total = total_vol_reservoir_well_ul * fraction_low

            vol_high_per_channel = vol_high_total / 8.0
            vol_low_per_channel = vol_low_total / 8.0

            chunk = p300_multi.max_volume

            # Low salt first
            remaining_low = vol_low_per_channel
            if remaining_low > 0:
                p300_multi.pick_up_tip()
                while remaining_low > 0:
                    move_vol = min(chunk, remaining_low)
                    for src_well, vol_pc in low_salt_pool.provide(move_vol):
                        # vol_pc is per-channel volume from that well
                        p300_multi.aspirate(vol_pc, src_well)
                        p300_multi.dispense(vol_pc, dest_well)
                    remaining_low -= move_vol
                mix_reservoir_well_multi(
                    p300_multi,
                    dest_well,
                    min(200.0, vol_low_per_channel),
                )
                p300_multi.drop_tip()

            # High salt next
            remaining_high = vol_high_per_channel
            if remaining_high > 0:
                p300_multi.pick_up_tip()
                while remaining_high > 0:
                    move_vol = min(chunk, remaining_high)
                    for src_well, vol_pc in high_salt_pool.provide(move_vol):
                        p300_multi.aspirate(vol_pc, src_well)
                        p300_multi.dispense(vol_pc, dest_well)
                    remaining_high -= move_vol
                mix_reservoir_well_multi(
                    p300_multi,
                    dest_well,
                    min(200.0, max(vol_low_per_channel, vol_high_per_channel)),
                )
                p300_multi.drop_tip()

    # -------------------------
    # 7) Step 3 – Prepare 2x salt buffers in Reservoir 4
    # -------------------------
    protocol.comment('Step 3: Preparing 2x salt buffers in Reservoir 4')

    dest_index = 0
    for salt in salt_concs:
        dest_well = reservoir4.wells()[dest_index]
        dest_index += 1

        # Template: 2x target concentration. The exact relationship between
        # salt_concs and stock concentration [[SALT_STOCK_CONCENTRATION]]
        # should be implemented by the user.
        fraction_high = (
            min(1.0, 2.0 * (salt / max_salt)) if max_salt > 0 else 0.0
        )
        fraction_low = 1.0 - fraction_high

        vol_high_total = total_vol_reservoir_well_ul * fraction_high
        vol_low_total = total_vol_reservoir_well_ul * fraction_low

        vol_high_per_channel = vol_high_total / 8.0
        vol_low_per_channel = vol_low_total / 8.0

        chunk = p300_multi.max_volume

        # Low salt
        remaining_low = vol_low_per_channel
        if remaining_low > 0:
            p300_multi.pick_up_tip()
            while remaining_low > 0:
                move_vol = min(chunk, remaining_low)
                for src_well, vol_pc in low_salt_pool.provide(move_vol):
                    p300_multi.aspirate(vol_pc, src_well)
                    p300_multi.dispense(vol_pc, dest_well)
                remaining_low -= move_vol
            mix_reservoir_well_multi(
                p300_multi,
                dest_well,
                min(200.0, vol_low_per_channel),
            )
            p300_multi.drop_tip()

        # High salt
        remaining_high = vol_high_per_channel
        if remaining_high > 0:
            p300_multi.pick_up_tip()
            while remaining_high > 0:
                move_vol = min(chunk, remaining_high)
                for src_well, vol_pc in high_salt_pool.provide(move_vol):
                    p300_multi.aspirate(vol_pc, src_well)
                    p300_multi.dispense(vol_pc, dest_well)
                remaining_high -= move_vol
            mix_reservoir_well_multi(
                p300_multi,
                dest_well,
                min(200.0, max(vol_low_per_channel, vol_high_per_channel)),
            )
            p300_multi.drop_tip()

    # -------------------------
    # 8) Step 4 – Ligand dilutions in mixing plate (96‑deepwell)
    # -------------------------
    protocol.comment('Step 4: Preparing ligand dilutions in mixing plate')

    # Total per-well volume (user formula):
    #   TOTAL_VOLUME / 2 * REPLICATES * 1.5
    vol_per_well = (total_volume / 2.0) * replicates * 1.5

    # Decide which ligand stock to use based on a simple check: if the
    # volume needed from the high stock for the highest concentration
    # would be < 20 µL, switch to the 10x lower stock.
    max_ligand_factor = max(ligand_concs) if ligand_concs else 0.0
    if max_ligand_factor == 0:
        use_low_stock = False
    else:
        # Placeholder for [[LIGAND_STOCK_CONCENTRATION]]; in real use,
        # this is replaced by a numeric concentration value.
        stock_conc = parse_scalar(PLACEHOLDER_LIGAND_STOCK_CONC, 1.0)
        vol_stock_for_max = vol_per_well * (max_ligand_factor / stock_conc)
        use_low_stock = vol_stock_for_max < 20.0

    ligand_pool = ligand_low_pool if use_low_stock else ligand_high_pool

    protocol.comment(
        f"Using {'low' if use_low_stock else 'high'} ligand stock for dilutions."
    )

    # Use single-channel pipette, row-wise A–H, columns per salt concentration.
    # Concentrations ascend row-wise from A to H.
    rows = list('ABCDEFGH')
    num_rows = min(len(rows), len(ligand_concs))

    for col_idx, _salt in enumerate(salt_concs):
        if col_idx >= 12:
            break  # 96‑well plate has 12 columns
        for row_idx in range(num_rows):
            row_name = rows[row_idx]
            dest_well = mixing_plate[f"{row_name}{col_idx + 1}"]

            # Template: ligand_concs[row_idx] encodes relative target; we
            # scale relative to max(ligand_concs) to get a fraction.
            target_factor = ligand_concs[row_idx]
            if max_ligand_factor > 0:
                ligand_fraction = target_factor / max_ligand_factor
            else:
                ligand_fraction = 0.0

            ligand_vol = vol_per_well * ligand_fraction
            buffer_vol = vol_per_well - ligand_vol

            # Add buffer (low salt) first
            remaining_buffer = buffer_vol
            if remaining_buffer > 0:
                p300_single.pick_up_tip()
                while remaining_buffer > 0:
                    move = min(p300_single.max_volume, remaining_buffer)
                    # For single-channel, `.provide(move)` treats `move`
                    # as total µL per move.
                    for src_well, vol_pc in low_salt_pool.provide(move, channels=1):
                        p300_single.aspirate(vol_pc, src_well)
                        p300_single.dispense(vol_pc, dest_well)
                    remaining_buffer -= move
                p300_single.drop_tip()

            # Add ligand stock
            remaining_ligand = ligand_vol
            if remaining_ligand > 0:
                p300_single.pick_up_tip()
                while remaining_ligand > 0:
                    move = min(p300_single.max_volume, remaining_ligand)
                    for src_well, vol_pc in ligand_pool.provide(move, channels=1):
                        p300_single.aspirate(vol_pc, src_well)
                        p300_single.dispense(vol_pc, dest_well)
                    remaining_ligand -= move
                p300_single.mix(
                    3,
                    min(0.5 * vol_per_well, p300_single.max_volume),
                    dest_well,
                )
                p300_single.drop_tip()

    # Final volume report (for debugging / tracking)
    protocol.comment(
        f"Low salt total remaining: {low_salt_pool.total_remaining():.1f} uL"
    )
    protocol.comment(
        f"High salt total remaining: {high_salt_pool.total_remaining():.1f} uL"
    )
    protocol.comment(
        f"Ligand high stock remaining: {ligand_high_pool.total_remaining():.1f} uL"
    )
    protocol.comment(
        f"Ligand low stock remaining: {ligand_low_pool.total_remaining():.1f} uL"
    )
