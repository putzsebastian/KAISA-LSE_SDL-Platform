# Device protocol agent — OT Protocol 4 (incubation on the heater-shaker)

Deck Layout:
Slot 1: Custom labware: 'cytiva_96_filterwellplate_1ml' mounted on Heater Shaker Module V1.
Slot 7: Opentrons 96 Tiprack 300 uL

Pipette Configuration:
Right Mount: P300 Single-Channel GEN2
Left Mount: P300 8-Channel GEN2

Protocol Steps:
1.	Close labware latch of heater shaker module and set temperature to [[INCUBATION_TEMPERATURE]] if [[INCUBATION_TEMPERATURE]]>=37. Else skip this step.
2.	Shake the Heater Shaker Module for [[INCUBATION_TIME]] minutes at a speed of [[SHAKER_SPEED_INCUBATION]] minutes.
3.	In the end stop heating and shaking and open labware latch.
