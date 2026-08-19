# Device protocol agent — OT Protocol 1 (buffer + dilution mixing)

Deck Layout:
Slot 1: Custom labware: 'cytiva_96_filterwellplate_1ml'
Slot 4: Opentrons 96 Tiprack 300 uL
Slot 7: Opentrons 96 Tiprack 300 uL
Slot 10: Opentrons 96 Tiprack 300 uL
Slot 3: NEST 12-Well 15 mL Reservoir (Reservoir 4) (empty)
Slot 6: NEST 12-Well 15 mL Reservoir (Reservoir 3) (empty)
Slot 8: NEST 12-Well 15 mL Reservoir (Reservoir 2)
Slot 9: NEST 12-Well 15 mL Reservoir (Reservoir 1)
Slot 11: NEST 96 Deep-Well Plate 2 ml (Mixing Plate)
Pipette Configuration:
Right Mount: P300 Single-Channel GEN2
Left Mount: P300 8-Channel GEN2
Liquids:
Slot 8: NEST 12-Well 15 mL Reservoir (Reservoir 2):
Well 0: Low salt buffer with 0 salt (14 mL)
Well 1: Low salt buffer with 0 salt (14 mL)
Well 2: Low salt buffer with 0 salt (14 mL)
Well 3: Low salt buffer with 0 salt (14 mL)
Well 4: Low salt buffer with 0 salt (14 mL)
Well 5: Low salt buffer with 0 salt (14 mL)
Well 6: High salt buffer with salt  concentration of [[SALT_STOCK_CONCENTRATION]]  (14 mL)
Well 7: High salt buffer with salt  concentration of [[SALT_STOCK_CONCENTRATION]] (14 mL)
Well 8: High salt buffer with salt  concentration of [[SALT_STOCK_CONCENTRATION]]  (14 mL)
Well 9: High salt buffer with salt  concentration of [[SALT_STOCK_CONCENTRATION]] (14 mL)
Well 10: High salt buffer with salt  concentration of [[SALT_STOCK_CONCENTRATION]] (14 mL)
Well 11: High salt buffer with salt  concentration of [[SALT_STOCK_CONCENTRATION]] (14 mL)
Slot 9: NEST 12-Well 15 mL Reservoir (Reservoir 1):
Well 0: Ligand Stock Solution with high concentration [[LIGAND_STOCK_CONCENTRATION]] (14 mL)
Well 1: Ligand Stock Solution with low concentration [[LIGAND_STOCK_CONCENTRATION]]/10 (14 mL)
Well 2: Low salt buffer with 0 salt (14 mL)
Well 3: Low salt buffer with 0 salt (14 mL)
Well 4: Low salt buffer with 0 salt (14 mL)
Well 5: Low salt buffer with 0 salt (14 mL)
Well 6: Low salt buffer with 0 salt (14 mL)
Well 7: High salt buffer with salt  concentration of [[SALT_STOCK_CONCENTRATION]]  (14 mL)
Well 8: High salt buffer with salt  concentration of [[SALT_STOCK_CONCENTRATION]] (14 mL)
Well 9: High salt buffer with salt  concentration of [[SALT_STOCK_CONCENTRATION]]  (14 mL)
Well 10: High salt buffer with salt  concentration of [[SALT_STOCK_CONCENTRATION]] (14 mL)
Well 11: High salt buffer with salt  concentration of [[SALT_STOCK_CONCENTRATION]] (14 mL)
Protocol Steps:
1.	Do all required calculations for the following steps.
2.	[[SALT_CONCENTRATIONS]] contains the salt concentrations of the buffers that need to be created (string separated by semicolons). For each required concentration, fill up [[REPLICATES]] Wells in Reservoir 3 by mixing Low and high salt buffer in the correct ratio with a total volume of 10 ml. In Reservoir 3 the salt concentrations should be ascending with increasing well number. Mix once low and high salt buffer are fully dispensed in the target well. When aspirating from the same source well you can use the same tips. Use the multi-channel pipette, keep in mind each transfer move will dispense 8x the specified volume, as there are 8 channels. Use tips from Slot 7 for multi-pipette..  Keep in mind, that each Well in Reservoir 1 and Reservoir 2 initially contains 14 mL of liquid (liquid may not run out). Therefore, it would be best to track and check remaining liquid volumes inside the wells and adjust from which wells to aspirate buffers.
3.	For each required salt concentration in [[SALT_CONCENTRATIONS]], fill up 1 well with a total volume of 10 ml in Reservoir 4 with 2x the required salt concentration by mixing low and high salt buffer. In Reservoir 4 the salt concentrations should be ascending with increasing well number. Mix once low and high salt buffer are fully dispensed in the target well. When aspirating from the same source well you can use the same tips. Use the multi-channel pipette, keep in mind each transfer move will dispense 8x the specified volume, as there are 8 channels. Use tips from Slot 7 for multi-pipette. Keep in mind, that each Well in Reservoir 1 and Reservoir 2 initially contains 14 mL of liquid (liquid may not run out). Therefore, it would be best to track and check remaining liquid volumes inside the wells and adjust from which wells to aspirate buffers.
4.	In the deep well-plate for mixing, create dilutions with concentrations of 2x the required ligand concentrations given in [[LIGAND_CONCENTRATIONS]] (string separated by semicolons). In the mixing plate, concentrations should be ascending row-wise from A-H, i.e. lowest concentration in Row A. For each salt concentration given in [[SALT_CONCENTRATIONS]], create one column of the mixing plate. E.g., if [[LIGAND_CONCENTRATIONS]] contains 8 values and [[SALT_CONCENTRATIONS]] 4 values, you need to create the dilutions in Row A-H and Column 1-4. A1-A4, B1-B4 etc. will have the same concentrations. The total volume for each well is [[TOTAL-VOLUME]]/2*[[REPLICATES]]*1.5. You will need to use the single channel pipette. Use tips from Slot 10 for single channel pipette.  All dilutions need to be prepared with low salt buffer. Use Well 0 of Reservoir 1 for Ligand Stock Solution with high concentration [[LIGAND_STOCK_CONCENTRATION]]. If the stock solution volume would be below 20 µL use Well 1 with the low stock concentration of [[LIGAND_STOCK_CONCENTRATION]]/10.  Keep in mind you need to adjust the volumes if using this lower stock concentration.
