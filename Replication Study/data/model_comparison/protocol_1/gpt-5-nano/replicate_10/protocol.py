from opentrons import protocol_api

metadata = {
    'protocolName': '[protocol name by user]',
    'author': '[user name]',
    'description': "[what is the protocol about]"
}
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(protocol: protocol_api.ProtocolContext):
    # labware
    try:
        plate = protocol.load_labware(load_name='cytiva_96_filterwellplate_1ml', location=1)
    except Exception:
        protocol.comment('WARNING: cytiva_96_filterwellplate_1ml not found; using Nest 96 wellplate_200ul_flat as fallback')
        plate = protocol.load_labware(load_name='nest_96_wellplate_200ul_flat', location=1)

    tiprack_slot4 = protocol.load_labware(load_name='opentrons_96_tiprack_300ul', location=4)
    tiprack_slot7 = protocol.load_labware(load_name='opentrons_96_tiprack_300ul', location=7)
    tiprack_slot10 = protocol.load_labware(load_name='opentrons_96_tiprack_300ul', location=10)

    reservoir4 = protocol.load_labware(load_name='nest_12_reservoir_15ml', location=3)  # Reservoir 4 (empty)
    reservoir3 = protocol.load_labware(load_name='nest_12_reservoir_15ml', location=6)  # Reservoir 3 (empty)
    reservoir2 = protocol.load_labware(load_name='nest_12_reservoir_15ml', location=8)  # Reservoir 2
    reservoir1 = protocol.load_labware(load_name='nest_12_reservoir_15ml', location=9)  # Reservoir 1
    reservoir0 = protocol.load_labware(load_name='nest_12_reservoir_15ml', location=5)  # Reservoir 0

    mixing_plate = protocol.load_labware(load_name='nest_96_wellplate_2ml_deep', location=11)  # Mixing plate

    p300_single = protocol.load_instrument(instrument_name='p300_single_gen2', mount='right', tip_racks=[tiprack_slot4])
    p300_multi = protocol.load_instrument(instrument_name='p300_multi_gen2', mount='left', tip_racks=[tiprack_slot7, tiprack_slot10])

    REPLICATES = '[[REPLICATES]]'
    TOTAL_VOLUME = '[[TOTAL_VOLUME]]'
    SALT_CONCENTRATIONS = '[[SALT_CONCENTRATIONS]]'
    LIGAND_CONCENTRATIONS = '[[LIGAND_CONCENTRATIONS]]'
    SALT_STOCK_CONCENTRATION = '[[SALT_STOCK_CONCENTRATION]]'
    LIGAND_STOCK_CONCENTRATION = '[[LIGAND_STOCK_CONCENTRATION]]'
    NUMBER_OF_SALT_CONCENTRATIONS = '[[NUMBER_OF_SALT_CONCENTRATIONS]]'
    NUMBER_OF_LIGAND_CONCENTRATIONS = '[[NUMBER_OF_LIGAND_CONCENTRATIONS]]'

    # Utility to parse placeholder strings after substitution
    def _parse_float_list(s):
        try:
            # Expecting a semicolon separated string after substitution
            parts = str(s).split(';')
            return [float(p) for p in parts if str(p).strip() != '']
        except Exception:
            return []

    def _to_int(v):
        try:
            return int(v)
        except Exception:
            return 0

    salt_conc_list = _parse_float_list(SALT_CONCENTRATIONS)
    ligand_conc_list = _parse_float_list(LIGAND_CONCENTRATIONS)
    number_of_salt_concentrations = _to_int(NUMBER_OF_SALT_CONCENTRATIONS) or len(salt_conc_list)
    number_of_ligand_concentrations = _to_int(NUMBER_OF_LIGAND_CONCENTRATIONS) or len(ligand_conc_list)

    reservoir3_wells = reservoir3.wells()
    reservoir0_wells = reservoir0.wells()
    reservoir2_wells = reservoir2.wells()

    # Phase 2: Fill Reservoir 3 wells with 10 mL each by mixing low and high salt buffers
    # Low salt buffer source from Reservoir 0, high salt source from Reservoir 2
    low_salt_source = reservoir0_wells[0]
    high_salt_sources = reservoir2_wells

    reps = 0
    for rep in range(min(_to_int(REPLICATES) if isinstance(REPLICATES, str) else REPLICATES, len(reservoir3_wells))):
        dest_well = reservoir3_wells[rep]
        REQ_CONC = salt_conc_list[rep % len(salt_conc_list)] if salt_conc_list else 0
        protocol_comment = f'Desired salt concentration for replicate {rep}: {REQ_CONC}. Fill approx 10 mL using multi-channel transfers.'
        protocol.comment(protocol_comment)
        reps += 1
    
    reservoir4_wells = reservoir4.wells() if hasattr(reservoir4, 'wells') else []
    for idx, conc in enumerate(salt_conc_list or []):
        if idx >= len(reservoir4_wells):
            break
        target = reservoir4_wells[idx]
        protocol.comment(f'Phase 3: prepare reservoir 4 well {idx} with concentration {conc} (placeholder)')

    ligand_stock_well = reservoir1.wells()[0]
    ligand_stock_low_well = reservoir1.wells()[1]
    protocol.comment('Phase 4: Prepare ligand dilutions on mixing plate (templated; actual dilutions to be defined)')
    protocol.comment('Mixing plate grid: concentrations should be 2x ligand concentrations across columns and rows as specified by placeholders.')

    protocol.comment('Template protocol complete. Replace placeholder values with concrete volumes and concentrations.')