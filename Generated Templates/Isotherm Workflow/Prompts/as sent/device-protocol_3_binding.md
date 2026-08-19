# Device protocol agent — prompt as sent

> The payload the device protocol agent received for *OT Protocol 3 (binding / sample application)* in the replication study, identical across all 20 replicates.
> The wizard prompt it was built from is `../device-protocol_3_binding.md`.

---

```text


USER REQUIREMENTS:
Generate an Opentrons protocol using placeholder syntax instead of specific values. Make it templatable.

Available placeholders:
- [[REPLICATES]]
- [[TOTAL_VOLUME]]
- [[SALT_CONCENTRATIONS]]
- [[LIGAND_CONCENTRATIONS]]
- [[NUMBER_OF_SALT_CONCENTRATIONS]]
- [[NUMBER_OF_LIGAND_CONCENTRATIONS]]

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
Slot 3: NEST 12-Well 15 mL Reservoir (Reservoir 4):
This reservoir contains buffers with different salt concentrations. [[SALT_CONCENTRATIONS]] contains the salt concentrations of the buffers (string separated by semicolons). For each salt concentration there is one Well in Reservoir 3 with 2x the concentration. The concentrations are ascending with ascending well. E.g. if [[SALT_CONCENTRATIONS]]=’0;100;200;500’ and the reservoir contains the following liquids:
Well 0: 0 mM salt buffer (14 mL)
Well 1: 200 mM salt buffer (14 mL)
Well 2: 400 mM salt buffer (14 mL)
Well 3: 1000 mM salt buffer (14 mL)

Slot 11: NEST 96 Deep-Well Plate 2 ml (Mixing Plate):
In the deep well-plate for mixing, there are dilutions with concentrations of 2x the required ligand concentrations given in [[LIGAND_CONCENTRATIONS]] (string separated by semicolons). In the mixing plate, concentrations are ascending row-wise from A-H, i.e. lowest concentration in Row A. For each salt concentration given in [[SALT_CONCENTRATIONS]], there is one column filled of the mixing plate. E.g., if [[LIGAND_CONCENTRATIONS]] contains 8 values and [[SALT_CONCENTRATIONS]] 4 values, there are dilutions in Row A-H and Column 1-4. A1-A4, B1-B4 etc. will have the same concentrations.
Protocol Steps:
1.	Transfer the buffers from Reservoir 4 to the Filter Plate ('cytiva_96_filterwellplate_1ml') on Slot 1. Each buffer needs to be transferred into [[REPLICATES]] columns of the filterplate with a volume of [[TOTAL_VOLUME]]/2 uL per well. E.g. if, [[SALT_CONCENTRATIONS]] =’0;100;200;500’ and [[REPLICATES]]=3, transfer:
-	[[TOTAL_VOLUME]]/2 uL of 0mM salt buffer from Well 0 of Reservoir 4 into each well of columns 0,1,2 in the Filter Plate ('cytiva_96_filterwellplate_1ml')
-	[[TOTAL_VOLUME]]/2 uL of 200mM salt buffer from Well 1 of Reservoir 4 into each well of columns 3,4,5 in the Filter Plate ('cytiva_96_filterwellplate_1ml')
-	[[TOTAL_VOLUME]]/2 uL of 400mM salt buffer from Well 2 of Reservoir 4 into each well of columns 6,7,8 in the Filter Plate ('cytiva_96_filterwellplate_1ml')
-	[[TOTAL_VOLUME]]/2 uL of 1000mM salt buffer from Well 3 of Reservoir 4 into each well of columns 9,10,11 in the Filter Plate ('cytiva_96_filterwellplate_1ml')
Use the 8-channel pipette for these transfers and tips from Slot 4. Use an offset of 7mm to the bottom of the filterplate. Return tips after transfer. Tips will be reused in the next step.
2.	Transfer the ligands from the Mixing Plate in Slot 11 to the Filter Plate ('cytiva_96_filterwellplate_1ml') on Slot 1. Each column in the mixing plate needs to be transferred into [[REPLICATES]] columns of the filterplate with a volume of [[TOTAL_VOLUME]]/2 uL per well. E.g. if, [[SALT_CONCENTRATIONS]] =’0;100;200;500’ and [[REPLICATES]]=3, transfer:
-	[[TOTAL_VOLUME]]/2 uL of ligand from each well in Column 0 of Mixing Plate into each well of columns 0,1,2 in the Filter Plate ('cytiva_96_filterwellplate_1ml')
-	[[TOTAL_VOLUME]]/2 uL of ligand from each well in Column 1 of Mixing Plate into each well of columns 3,4,5 in the Filter Plate ('cytiva_96_filterwellplate_1ml')
-	[[TOTAL_VOLUME]]/2 uL of ligand from each well in Column 0 of Mixing Plate into each well of columns 6,7,8 in the Filter Plate ('cytiva_96_filterwellplate_1ml')
-	[[TOTAL_VOLUME]]/2 uL of ligand from each well in Column 0 of Mixing Plate into each well of columns 9,10,11 in the Filter Plate ('cytiva_96_filterwellplate_1ml')
Use the 8-channel pipette for these transfers and reuse tips from Slot 4. Use an offset of 7mm to the bottom of the filterplate.
```
