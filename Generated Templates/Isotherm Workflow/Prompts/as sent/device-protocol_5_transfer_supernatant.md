# Device protocol agent — prompt as sent

> The payload the device protocol agent received for *OT Protocol 5 (supernatant transfer)* in the replication study, identical across all 20 replicates.
> The wizard prompt it was built from is `../device-protocol_5_transfer_supernatant.md`.

---

```text


USER REQUIREMENTS:
Generate an Opentrons protocol using placeholder syntax instead of specific values. Make it templatable.

Available placeholders:
- [[REPLICATES]]
- [[SALT_CONCENTRATIONS]]

Protocol Requirements:
Deck Layout:
Slot 1: NEST 96 Deep-Well Plate 2 ml mounted directly on Heater Shaker Module V1 (no adapter - load the labware straight onto the module).
Slot 5: 360 uL 96-well plate
Slot 7: Opentrons 96 Tiprack 300 uL

Pipette Configuration:
Right Mount: P300 Single-Channel GEN2
Left Mount: P300 8-Channel GEN2

Protocol Steps:
1.	Multiply number of concentrations in [[SALT_CONCENTRATIONS]] with [[REPLICATES]]. This is the number of transfers ‘num_transfers’.
2.	For each i in num_transfers transfer 200 uL each of wells in Column [i] of the Deep Well Plate into wells of column [i] of the 360 uL 96-well plate in Slot 5.
Use the 8-channel pipette for these transfers.
```
