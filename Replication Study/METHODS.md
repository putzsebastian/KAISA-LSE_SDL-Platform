# Methods

The configuration that produced the published numbers: the prompts, workflows, comparators and
decision rules used for every run deposited here.

## What was measured

Five AI agents generate the artifacts of an automated experiment: the device protocols, the
orchestration script, the analysis script, the report template, and the database commit script.
Each agent was invoked 20 times from an identical starting point, and each artifact was compared
against the published reference for that experiment by a comparator specific to its type.

Two experiments, on different instruments:

| | isotherm | FPLC gradient |
|---|---|---|
| instrument | Opentrons OT-2 + Tecan | ÄKTA Pure |
| eLabFTW template | 278 | 387 |
| agent-generated device protocols | 5 | none — assembled deterministically by the application |

## Models and reasoning effort

All agents ran on `gpt-5.1` through n8n's OpenAI chat node, confirmed against the model recorded in
every published execution. The model-comparison arm varied only the device-protocol agent's model
across `gpt-5-nano`, `gpt-5-mini`, `gpt-5.1` and `gpt-5.5`; the helper switched the model value and
nothing else, so every other node parameter was identical across the four cells.

**Reasoning effort is not recorded in the executions.** n8n logs aggregate token usage only, with no
effort field and no reasoning-token breakdown; a search of all published executions returns no
occurrence of either. The workflow snapshots taken at measurement time set `reasoningEffort:
medium` on the orchestration agent and set no effort on the device-protocol, analysis and database
agents, leaving those at the provider default. This is reported as the evidence stands rather than
as a configuration choice, because n8n is known to drop the field when a workflow is imported.

## Replicates, and what counts as one

Every series is exactly 20 replicates, so no reported rate rests on a different denominator than
its neighbour.

Agents that consume an upstream artifact are **gated**: a replicate whose upstream step failed is
excluded rather than counted as a failure of the downstream agent, because handing an agent an
input that does not execute measures the upstream defect. The database agent consumes the analysis
script and is the only gated series — 6 replicates excluded for the isotherm, 1 for the FPLC.
Replicates were drawn in order until 20 had valid upstream input. Each artifact directory records
its own gate and exclusion count in `series.json`.

## Comparators

One comparator per artifact type. Each was validated during the study by a perturbation suite that
mutates a known correct artifact and asserts the comparator rejects it, so a comparator that passes
everything could not go unnoticed. Those suites test the tools rather than produce any published
number and are not part of this deposit.

| artifact | comparator | decides on |
|---|---|---|
| device protocol | `destinations.py` | net volume delivered per destination well |
| orchestration | `orchestration.py` | structure and required calls |
| analysis | `analysis.py` | executes, and fitted parameters within tolerance |
| report | `report.py` | required sections, plots produced and embedded, placeholders resolvable |
| database | `database.py` | every eLabFTW parameter stored with the reference's value |
| FPLC analysis | `akta_analysis.py` | peak count, retention time, height, chromatogram produced |

**Device protocols are judged on delivery, not on transfers.** The comparator compares the net
volume arriving in each destination well against the reference, at a tolerance of 2 % or 1 µL,
and ignores which source well supplied it. This follows the prompt: protocol 1 instructs the agent
to *adjust from which wells to aspirate buffers*, making source order a free variable that a
transfer-by-transfer comparison would wrongly penalise. A protocol that fails to simulate fails
outright, whatever its declared volumes. `device_protocol.py` remains in the bundle as a dependency
of `destinations.py` and produced none of the reported verdicts.

Every published verdict carries its comparator name in `score.json`, and the codes that fired.

## Prompts

`code/payloads/` holds exactly what each agent was sent, per experiment: the full payload as
`*.payload.json`, and the instruction text out of it as `*.prompt.txt` beside it.

**Not every agent receives an instruction in its payload.** Two work from their system prompt and
structured fields alone, and have no `.prompt.txt` because there is no text to show:

| agent | instruction |
|---|---|
| orchestration (both experiments) | none — `autonomous_context` is empty; it works from the process definition, template and device list |
| analysis (both) | `prompt` |
| report (both) | `prompt` |
| database (isotherm) | none — the payload carries the analysis script and the eLabFTW field list, nothing else |
| database (FPLC) | `custom_instructions` — the units and meanings of the eLabFTW fields, supplied because the application strips them before the agent sees them |
| device protocol | `prompt` |

An agent with no `.prompt.txt` is therefore **not fully specified by these files alone**: its
behaviour is set by the system prompt in its n8n workflow, which is versioned separately and is not
part of this deposit.

Every shipped payload and every `.prompt.txt` was verified against the requests of all 20 replicates
that used it — 720 requests in total — and each is the instruction that was actually sent. Fields
that legitimately vary per replicate (timestamps, scratch paths, callback URLs, the upstream
artifact a chained agent consumes) differ from the payload by design; the instruction itself does
not. Each replicate's own `request.json` is the record, so the check can be repeated from the
deposited data.

One line of the **report** prompt is chain-derived rather than fixed: the report agent is told which
plots exist in its own replicate, and that list is built from what the upstream analysis script
actually produced — the file it wrote, the display name it chose, and whether it emitted a path
variable or an f-string. Across the 20 FPLC replicates four such lines occur, differing only in
those three respects. This is the chain behaving as designed, not a change of specification
mid-series. The four variants differ only in the plot's filename, its display name, and whether the
analysis script emitted a path variable or an f-string; each replicate's `request.json` shows which
it received.

System prompts live in the n8n workflows, which are versioned separately and are not part of this
deposit.

## Cost

Token counts are taken per replicate from the executions and split into input and output. Cost is
computed per replicate at list prices and only then summarised, so its quartiles are quartiles of
actual runs. All replicates are included regardless of outcome. See `STATISTICS.md` §4a, §4b and
Supplementary Table S7.

## Resolution

n = 20 resolves a difference of roughly 5 in 20 and nothing finer: 20/20 against 16/20 is
p = 0.106. `STATISTICS.md` §3 tabulates where the resolution lies, and no ordering between agents
should be read from these figures unless that table supports it.

Internal infrastructure identifiers — service hostnames and platform credential references — are
replaced by placeholders throughout, consistently, so a given identifier maps to the same
placeholder everywhere. Artifacts, prompts, scores, token counts and timings are untouched.
