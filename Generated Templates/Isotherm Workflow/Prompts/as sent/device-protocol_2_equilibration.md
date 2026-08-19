# Device protocol agent — prompt as sent

> The payload the device protocol agent received for *OT Protocol 2 (resin equilibration)* in the replication study, identical across all 20 replicates.
> The wizard prompt it was built from is `../device-protocol_2_equilibration.md`.

---

```text


USER REQUIREMENTS:
Generate an Opentrons protocol using placeholder syntax instead of specific values. Make it templatable.

Available placeholders:
- [[REPLICATES]]
- [[SALT_CONCENTRATIONS]]
- [[EQUILIBRATION_VOLUME]]
- [[SHAKER_SPEED_INCUBATION]]
- [[EQUILIBRATION_CYCLE_DURATION]]

Protocol Requirements:
Deck Layout:
Slot 1: Filter Plate ('cytiva_96_filterwellplate_1ml') mounted directly on Heater Shaker Module V1 (no adapter - load the labware straight onto the module).
Slot 4: Opentrons 96 Tiprack 300 uL
Slot 7: Opentrons 96 Tiprack 300 uL
Slot 10: Opentrons 96 Tiprack 300 uL
Slot 3: NEST 12-Well 15 mL Reservoir (Reservoir 4)
Slot 6: NEST 12-Well 15 mL Reservoir (Reservoir 3)
Slot 8: NEST 12-Well 15 mL Reservoir (Reservoir 2)
Slot 9: NEST 12-Well 15 mL Reservoir (Reservoir 1)
Slot 5: NEST 12-Well 15 mL Reservoir (Reservoir 0)
Slot 11: NEST 96 Deep-Well Plate 2 ml (Mixing Plate)

Pipette Configuration:
Right Mount: P300 Single-Channel GEN2
Left Mount: P300 8-Channel GEN2

Liquids:
Slot 6: NEST 12-Well 15 mL Reservoir (Reservoir 3):
This  reservoir contains buffers with different salt concentrations with 14 mL per well. [[SALT_CONCENTRATIONS]] contains the salt concentrations of the buffers (string separated by semicolons). For each salt concentration there [[REPLICATES]] Wells in Reservoir 3. The concentrations are ascending with ascending well number and replicates are next to each other. E.g. if [[SALT_CONCENTRATIONS]]=’0;100;200;500’ and [[REPLICATES]]=3, the reservoir contains the following liquids:
Well 0: 0 mM salt buffer (14 mL)
Well 1: 0 mM salt buffer (14 mL)
Well 2: 0 mM salt buffer (14 mL)
Well 3: 100 mM salt buffer (14 mL)
Well 4: 100 mM salt buffer (14 mL)
Well 5: 100 mM salt buffer (14 mL)
Well 6: 200 mM salt buffer (14 mL)
Well 7: 200 mM salt buffer (14 mL)
Well 8: 200 mM salt buffer (14 mL)
Well 9: 500 mM salt buffer (14 mL)
Well 10: 500 mM salt buffer (14 mL)
Well 11: 500 mM salt buffer (14 mL)

Protocol Steps:
1.	Multiply number of concentrations in [[SALT_CONCENTRATIONS]] with [[REPLICATES]]. This is the number of transfers ‘num_transfers’.
2.	For each i in num_transfers transfer [[EQUILIBRATION_VOLUME]] uL of the buffer in Well [i] of Reservoir 3 to each well in Column [i] of the Filter Plate ('cytiva_96_filterwellplate_1ml') in Slot 1. Use an offset of 7mm to the bottom of the filterplate. Use the multi-pipette. Use tips from Slot 4. Always return tips.
3.	Set the heater shaker to shake at a speed of [[SHAKER_SPEED_INCUBATION]] RPM for [[EQUILIBRATION_CYCLE_DURATION]] minutes. Then stop shaking and open the latch.
```
