# Prompts — Isotherm Workflow (eLabFTW template 278, Lab 167)

The natural-language prompts a domain scientist entered in the wizard when this template was
created. One file per agent invocation. These are the **user** prompts; the agents' fixed system
prompts are published separately under `n8n AI Agents/System Prompts/`.

| File | Agent | Produces |
|---|---|---|
| `device-protocol_1_mixing.md` | Device protocol agent | OT Protocol 1 (buffer + dilution mixing) |
| `device-protocol_2_equilibration.md` | Device protocol agent | OT Protocol 2 (resin equilibration, run inside the workflow loop) |
| `device-protocol_3_binding.md` | Device protocol agent | OT Protocol 3 (binding / sample application) |
| `device-protocol_4_incubation.md` | Device protocol agent | OT Protocol 4 (incubation on the heater-shaker) |
| `device-protocol_5_transfer_supernatant.md` | Device protocol agent | OT Protocol 5 (supernatant transfer to the measurement plate) |
| `analysis.md` | Analysis agent | loading-isotherm evaluation of Tecan absorbance data |
| `report.md` | Report agent | ELN report body |

`isotherm_mock_data.xlsx` is the example data file uploaded alongside the analysis prompt (it is
also embedded, base64-encoded, in the pinned webhook data of `n8n AI Agents/Workflows/Data
Analysis Agent.json`).

## How the application enriches these prompts

The text below is not what reaches the agent verbatim — the wizard and the backend wrap it. For an
**automated** template the device-protocol agent receives:

```
"\n\nUSER REQUIREMENTS:\n"
+ "Generate an Opentrons protocol using placeholder syntax instead of specific values. Make it templatable.\n\n"
+ "Available placeholders:\n- [[FIELD_A]]\n- [[FIELD_B]]\n...\n\n"
+ "Protocol Requirements:\n"
+ <the file below>
```

where the placeholder list is the subset of the ELN template's extra fields the scientist ticked
for that protocol step. The analysis and report agents are wrapped comparably (a generated data-
access guide, and the workflow JSON, respectively). See the pinned webhook data in
`n8n AI Agents/Workflows/*.json` for complete worked examples of the final payloads.

**`as sent/` holds the wrapped result for every prompt below**, taken from the recorded requests of
the replication study — the exact text each agent received, rather than the text typed into the
wizard. Read a file here against its namesake there to see what the application added.

Placeholders use `[[UPPER_SNAKE_CASE]]`, derived from the ELN extra-field name. Note that
`device-protocol_1_mixing.md` contains `[[TOTAL-VOLUME]]` with a hyphen where the field resolves to
`[[TOTAL_VOLUME]]`; this typo is reproduced as written, and the generated protocol uses the correct
underscore form.

Transcribed from the authors' `.docx` working files. Word's automatic list numbering is stored
outside the paragraph text, so it is reconstructed and rendered as `<number>.<TAB><text>` — the
form the captured agent payloads show it took when the text was pasted into the wizard.
