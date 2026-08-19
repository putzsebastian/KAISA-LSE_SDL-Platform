# Agent reliability replication — data and code

Five AI agents generate the artifacts of an automated experiment: the device protocols, the
orchestration script, the analysis script, the report template and the database commit script. Each
was run 20 times from an identical starting point, and each artifact was compared against the
published reference for that experiment.

Two experiments, on different instruments:

| | isotherm | FPLC gradient |
|---|---|---|
| instrument | Opentrons OT-2 + Tecan | ÄKTA Pure |
| eLabFTW template | 278 | 387 |
| agent-generated device protocols | 5 | none — assembled deterministically by the application |

## Results

| agent | isotherm | FPLC gradient |
|---|---|---|
| orchestration | 20/20 | 20/20 |
| analysis | 16/20 | 19/20 |
| report | 19/20 | 17/20 |
| database | 19/20 | 18/20 |

Device protocols (isotherm): 1 → 10/20, 2 → 19/20, 3 → 18/20, 4 → 20/20, 5 → 20/20.

Model arm, protocol 4 (easy) against protocol 1 (hard): gpt-5-nano 11/20 vs 0/20, gpt-5-mini 20/20
vs 11/20, gpt-5.1 20/20 vs 10/20, gpt-5.5 20/20 vs 20/20.

Full tables, confidence intervals, significance tests and cost: `results/STATISTICS.md`.

## Layout

```
data/
  isotherm/                orchestration, analysis, report, database,
                           device_protocol_1 … device_protocol_5
  fplc_gradient/           orchestration, analysis, report, database
  model_comparison/        protocol_1/ and protocol_4/, each across four models
code/
  runner/                  runs the agent chain and scores each replicate
  comparators/             one comparator per artifact type
  analysis/                turns data/ into results/
  payloads/                what each agent was sent
  config/                  agent registry; secrets are NOT included
results/                   results.json, STATISTICS.md, FAILURES.md, CORRECTION_CYCLES.md,
                           both figures as PNG and PDF
METHODS.md                 the configuration that produced these numbers
INDEX.json                 what each series contains
```

Every replicate directory is self-describing — the artifact the agent produced, the request it
answered, its telemetry, the full n8n execution, and the comparator's verdict on it:

```
data/isotherm/analysis/replicate_07/
    analysis_script.py  request.json  response.json  result.json
    execution.json  toolruns.json  toolcalls.json  tokens.json  timing.json  score.json
```

Each artifact directory also carries a `series.json` naming its comparator, its rate, and — where an
agent depends on an earlier one — how many replicates were excluded because that earlier step
failed.

## Reproducing the numbers

From `code/analysis/`, with no agent calls and nothing outside this bundle. Each script overwrites
its own output in `results/`, so a clean run reproduces the shipped files byte for byte:

```bash
python collect_results.py     # data/ -> results.json
python stats_table.py         # -> STATISTICS.md
python failure_taxonomy.py    # -> FAILURES.md
python correction_cycles.py   # -> CORRECTION_CYCLES.md
python make_figures.py        # -> figure1_reliability.*, figure2_models.*
```

Requires Python with `scipy` and `matplotlib`; nothing else.

## Regenerating the runs

Requires the n8n workflows, an OpenAI key and the application stack;
`code/config/secrets.env.example` lists what is needed. The published reference experiments are an
external input — point at them with `--reference-repo` or `SDL_REFERENCE_REPO`. One run is N
replicates of one configuration, and re-issuing the same `--run-id` resumes an interrupted one.

```bash
python code/runner/launch.py --run-id <id> --replicates 20 --concurrency 3 \
       --template isotherm|fplc_gradient [--only <steps>] --agent-retries 0

# the model arm switches one workflow's chat nodes, then restores them
python code/runner/helpers/set_model.py --agent device_protocol --model gpt-5.5
python code/runner/helpers/set_model.py --agent device_protocol --restore
```

## Reading the numbers

**n = 20 resolves differences of about 5 in 20 and nothing finer.** 20/20 against 16/20 is
p = 0.106 — not distinguishable. `STATISTICS.md` §3 tabulates where the resolution lies, and no
ordering between agents should be read from these figures unless that table supports it.

Each series is one experiment at fixed conditions: the intervals cover sampling noise, not
generalisation to an unseen protocol.

## What is not here

The n8n agent workflows and their system prompts, which are versioned separately, and `secrets.env`.
Internal infrastructure identifiers are replaced by placeholders throughout; artifacts, prompts,
scores, token counts and timings are untouched.
