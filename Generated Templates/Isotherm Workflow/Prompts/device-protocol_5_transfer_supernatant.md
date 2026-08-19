# Device protocol agent — OT Protocol 5 (supernatant transfer to the measurement plate)

Deck Layout:
Slot 1: 360 uL 96-well plate mounted on Heater Shaker Module V1.
Slot 5: NEST 96 Deep-Well Plate 2 ml
Slot 7: Opentrons 96 Tiprack 300 uL

Pipette Configuration:
Right Mount: P300 Single-Channel GEN2
Left Mount: P300 8-Channel GEN2

Protocol Steps:
1.	Multiply number of concentrations in [[SALT_CONCENTRATIONS]] with [[REPLICATES]]. This is the number of transfers ‘num_transfers’.
2.	For each i in num_transfers transfer 300 uL each of wells in Column [i] of the Deep Well Plate into wells of column [i] in the Filterplate.
Use the 8-channel pipette for these transfers.
