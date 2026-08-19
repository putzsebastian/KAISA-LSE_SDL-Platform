# A Unified AI-Agent-Assisted Platform for Coupled Experimental and Simulation-Based Workflows in Self-Driving Laboratories

[![DOI](https://zenodo.org/badge/1264957528.svg)](https://zenodo.org/badge/latestdoi/1264957528)

Accompanying code and data for the publication:

> **A Unified AI-Agent-Assisted Platform for Coupled Experimental and Simulation-Based Workflows in Self-Driving Laboratories**
> Sebastian Putz, Ahmed Khalil Mama, Matthias Franzreb
> Karlsruhe Institute of Technology (KIT), Institute of Functional Interfaces (IFG), Department for Bioengineering and Biosystems, 76344 Eggenstein-Leopoldshafen, Germany

This repository contains the AI-agent system prompts and n8n workflows, the generated workflow templates, the raw experimental/simulative run data used to produce the results in the work, and the replication study quantifying how reliably the agents generate those templates.

---

## Overview

The platform separates **workflow design** from **workflow execution**. Domain scientists create reusable workflow templates through no-code wizard interfaces combined with AI-agent-assisted code generation; the templates then execute deterministically, without any large language model (LLM) in the loop. A template bundles an electronic lab notebook (ELN, eLabFTW) parameter structure, device-orchestration / simulation scripts, a data-analysis script, a report script, and a database-commit script. Once created, a template is instantiated repeatedly with varying parameters for screening or Bayesian-optimization campaigns.

The demonstrated use case is chromatographic process development: automated batch-adsorption isotherm determination, mechanistic simulation (CADET-Process) of separation strategies, and experimental column validation (ÄKTA FPLC) for separating two proteins on a mixed-mode resin.

---

## Repository structure

```
Repo/
├── README.md                 ← this file
├── LICENSE                   ← MIT license (code); data is CC-BY-4.0 (see License section)
├── .gitignore
├── .gitattributes            ← line-ending normalization + binary file rules
├── Experiments/              ← raw runs (input JSON, generated main.py, analysis, reports, data)
│   ├── Batch Adsorption Isotherms/   (Ovalbumin, Transferrin)
│   ├── CADET Simulations/            (Gradient Elution, Step Elution)
│   └── FPLC Validation/              (Gradient Elution, Step Elution)
├── Generated Templates/      ← wizard-created reusable workflow templates + their Prompts/
│                               (Prompts/ holds the wizard user prompts; Prompts/as sent/ holds the
│                                payloads the agents actually received)
│   ├── Isotherm Workflow/
│   ├── Gradient Elution Simulation Workflow/
│   ├── Step Elution Simulation Workflow/
│   ├── FPLC Gradient Elution Workflow/
│   └── FPLC Step Elution Workflow/
├── n8n AI Agents/            ← the AI-agent layer
│   ├── docker-compose.yml    ← runs n8n + agent-tools + qdrant together (see guide below)
│   ├── System Prompts/       ← agent system prompts (Markdown; verbatim copies of the
│   │                            `systemMessage` field of the matching workflow node)
│   ├── Workflows/            ← n8n workflow exports (JSON)
│   ├── Minimal Application/  ← standalone agent-tools service (Docker)
│   └── Documentation Retrieval/  ← Qdrant doc-retrieval: corpus build/ingest scripts + shipped snapshot (own README)
├── Data Analysis/            ← figure-reproduction & isotherm-fitting code + input data
│   ├── Figure 5/             ← self-contained scripts + data to regenerate Figure 5 (own README)
│   │   ├── analysis/             ← resolution / Rs calculation (Table S5.1)
│   │   ├── figures/             ← per-panel plotting scripts (Fig. 5a–f, Fig. S5.1)
│   │   ├── data/                ← input data (isotherms, simulations, FPLC)
│   │   └── output/              ← generated figures (git-ignored)
│   └── MPM Fit/              ← Mobile-Phase-Modulator isotherm fitting scripts
├── Replication Study/        ← agent-reliability replication: every replicate, the comparators
│                               that judged it, and the code that turns it into the reported
│                               statistics and figures (own README + METHODS)
└── (Supplementary videos)    ← not in Git; hosted on Zenodo + journal SI (see below)
```

---

## ⚠️ Configuration & scrubbed identifiers

**All real infrastructure identifiers in this repository have been replaced with placeholders before publication.** Before running any script you must substitute the placeholders below with values for your own environment.

| Placeholder | Meaning |
|---|---|
| `<APP_SERVER>` | Host/IP of the central application server (Flask API on `:5001`, n8n on `:5678`) |
| `<ELAB_HOST>` | eLabFTW server hostname (used as `https://<ELAB_HOST>`) |
| `<UR5_IP>` | Universal Robots UR5 arm IP |
| `<UR10_IP>` | Universal Robots UR10 arm IP |
| `<OPENTRONS_IP>` | Opentrons liquid handler IP (Flex in Lab 168, OT-2 in Lab 167) |
| `<VACUUPUMP_IP>` | VacuuPump (VACUUBRAND) IP — Lab 167 |
| `<ZETASIZER_IP>` | Malvern Zetasizer Nano device-server IP — Lab 167 |
| `<SCIEX_IP>` | Sciex X500R MS control-service host — Lab 168 |
| `<TECAN_ALIAS>` | Tecan Spark device serial/alias |
| `<WEBHOOK_ID>` | n8n webhook node identifier — the node's internal `webhookId`, which n8n regenerates on import. **Not** the webhook *path*: each agent ships a real, distinct path (see step 5) |
| `<CREDENTIAL_ID>` | n8n stored-credential reference ID (the secret itself lives in the n8n credential store) |
| `<USER>` | Local OS username in device file paths |
| `<DLS_CONFIG_PATH>` | CETONI/DLS sampler configuration directory — Lab 167 |
| `<PROCESS_ROOT>` | Local process/working-directory root |

### Required environment variables (set in your own deployment)

| Variable | Used by |
|---|---|
| `ELAB_API_TOKEN` | eLabFTW report upload / data fetch (report & analysis scripts) |
| `ELAB_API_BASE` | eLabFTW base URL, `https://<ELAB_HOST>` (report generators) |
| `DEVICE_CONTROL_SERVER`, `DEVICE_API_KEY` | Tecan/device data API (analysis scripts) |
| `AKTA_CONTROL_SERVER`, `AKTA_API_KEY` | ÄKTA Pure control server |
| `UNICORN_USER`, `UNICORN_PASSWORD` | UNICORN login the ÄKTA control server authenticates with (orchestration scripts) |
| `EXPERIMENT_DB_HOST`, `EXPERIMENT_DB_PORT`, `EXPERIMENT_DB_INTERNAL_PORT`, `EXPERIMENT_DB_NAME`, `EXPERIMENT_DB_USER`, `EXPERIMENT_DB_PASSWORD` | PostgreSQL results database (database-commit scripts) |
| OpenAI API key | configured as an n8n credential (`<CREDENTIAL_ID>`), not in code |

Two further variables belong to the agent layer rather than the generated scripts, and are
documented where they are used: `SCRIPTS_BASE_DIR` (agent-tools, see `Minimal Application/README.md`)
and `QDRANT_URL` / `OPENAI_API_KEY` / `PINECONE_API_KEY` (corpus build and ingest, see
`Documentation Retrieval/README.md`). None of them is needed to run the agents from the shipped
Qdrant snapshot.

---

## Reproducing / using the code

The generated `main.py`, analysis, report and simulation scripts under `Experiments/` and `Generated Templates/` are provided as the **exact artifacts** produced by the platform for each run, for transparency and inspection.

> **These device-dependent scripts are illustrative and cannot be run as-is.** The orchestration scripts import hardware driver modules (`Devices/`, `Devices_167/`) that are **intentionally not included** in this repository — some are proprietary. The scripts also assume the surrounding SDL infrastructure (device control servers, eLabFTW, PostgreSQL). They are published to document exactly what the platform generated, not as standalone runnable programs.

The part you **can** stand up independently is the **AI-agent layer** — the n8n workflows plus the agent-tools service — described below. The CADET-Process simulation scripts, the isotherm-fitting scripts (`Data Analysis/MPM Fit/`) and the figure-reproduction scripts (`Data Analysis/Figure 5/`, which has its own README) are likewise self-contained Python and run with the appropriate scientific Python packages installed.

---

## Running the n8n agent layer with Docker

This reproduces the code-generation agents (device-protocol, orchestration, analysis, report, database agents) that turn a workflow description into runnable scripts. It does **not** require any lab hardware.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2 (`docker compose …`)
- An OpenAI API key (the agents use OpenAI chat models)
- *(Optional)* an eLabFTW instance and a PostgreSQL database if you want the report/database steps to actually write somewhere

### 1. Start the stack (n8n + agent-tools + Qdrant)

The agents call the **agent-tools** service (script validation, saving, Opentrons simulation) over HTTP. A ready-to-run **combined compose file** is provided at **`n8n AI Agents/docker-compose.yml`** — it starts n8n, agent-tools, and a Qdrant vector store (pre-loaded with the opentrons_docs collection from the shipped snapshot) on one Docker network, so n8n can reach the tools service by name at `http://agent-tools:8100`. No file editing needed; just run it from the `n8n AI Agents/` directory:

```bash
cd "n8n AI Agents"
docker compose up -d
docker compose ps                    # all three containers should be "running"
curl http://localhost:8100/health    # agent-tools sanity check
curl http://localhost:6333/collections/opentrons_docs  # Qdrant: snapshot restored (~606 pts)
```

Open **http://localhost:5678** and create the local owner account when prompted.

> To run *only* the agent-tools service (without n8n/Qdrant), use the standalone `n8n AI Agents/Minimal Application/docker-compose.yml` instead.

### 2. Import the workflows

In the n8n UI, for **each** file in `n8n AI Agents/Workflows/`:

1. **Workflows → Add workflow → ⋮ (top-right menu) → Import from File…**
2. Select the JSON (e.g. `Device Protocol Agent SDL1.json`), then **Save**.

`SDL1` and `SDL2` denote the two laboratories — SDL1 is Lab 167 (Opentrons OT-2), SDL2 is Lab 168 (Opentrons Flex). Only SDL1 has a device-protocol agent here; the Opentrons protocols reported in the work were generated for that laboratory.

Import the sub-workflow tools too (`Save Script.json`, `Simulate Opentrons.json`, and `Tool - Validate Script.json`) — the agent workflows call these via *Execute Workflow* nodes. `Data Analysis Agent with Verification Agent.json` is a variant of the analysis agent with a second agent that reviews the generated script; `Template Editor.json` is the in-application template editor and needs the Flask app (see below).

> On import, n8n regenerates each webhook node's internal `webhookId` (the scrubbed `<WEBHOOK_ID>`
> value). It does **not** touch the webhook **path**, which is what the URL is built from. Each
> agent ships with its own fixed path:
>
> | Workflow | Webhook path |
> |---|---|
> | `Device Protocol Agent SDL1.json` | `opentrons-ai-lab167-p3` |
> | `Orchestration Agent SDL1.json` | `workflow-generator-lab167-p3` |
> | `Orchestration Agent SDL2.json` | `workflow-generator-lab168-p3` |
> | `Data Analysis Agent.json` | `evaluation-script-generator-p3` |
> | `Data Analysis Agent with Verification Agent.json` | `evaluation-script-generator-verification` |
> | `Report Agent.json` | `report-generator-p3` |
> | `Database Agent.json` | `generate-db-schema-p3` |
> | `Template Editor.json` | `template-editor-agent` |
>
> Change them only if they clash with workflows you already have; if two active workflows share a
> path, n8n registers only one of them and requests silently reach the wrong agent. A deployment of
> the Flask application expects the paths its configuration names, so change both sides together.

### 3. Attach credentials

The imported nodes reference credentials by ID (shown as `<CREDENTIAL_ID>`), which won't exist in your instance, so they appear unset:

- **OpenAI** — open each *OpenAI Chat Model* and *Embeddings OpenAI* node → **Credential → Create new** → paste your OpenAI API key.
- **eLabFTW** *(only if used)* — the report/analysis HTTP nodes send the token in the `Authorization` header (raw token, no `Bearer` prefix). Add it as **Header Auth** or set the `ELAB_API_TOKEN` env var in your deployment.
- **PostgreSQL** *(only if used by the database agent)* — create a Postgres credential with your DB host/user/password.
- **Qdrant** *(for the `Get_Opentrons_API_Info` documentation-retrieval tool)* — create a **Qdrant API** credential with URL `http://qdrant:6333` and an **empty** API key. See "Documentation retrieval (vector store)" below. (Not required for a basic smoke test.)

### 4. Fill in the placeholders

Replace the placeholders from the legend above with your environment's values, in particular:

- **agent-tools URL** — the sub-workflow tools (`Tool - Validate Script`, `Save Script`, `Simulate Opentrons`) are **already pre-pointed** at `http://agent-tools:8100/{validate,save/upload,simulate/opentrons}`, which resolves on the shared Docker network out of the box. Only change this if you run agent-tools somewhere else (then use that host:port).
- **`sdl-app:5001` nodes** — the device-protocol agent, both orchestration agents and the template editor save or read through the **Flask application** rather than agent-tools (`http://sdl-app:5001/api/upload_script`, `/api/agent/template/…`). That service is not part of this repository, so with the agent stack alone those nodes fail at the save step: the agent still generates, validates and simulates its script, but cannot store it. Repoint the save node at `http://agent-tools:8100/save/upload` — the `Save Script` sub-workflow already does exactly this — to make them self-contained. The analysis and report agents already save this way, through the `Save Script` sub-workflow; the database agent only validates and returns its script.
- **`<APP_SERVER>`** — your host/IP wherever a node posts execution logs or callbacks (`:5001` API, `:5678` n8n).
- **`<ELAB_HOST>`** — your eLabFTW hostname in the report/analysis nodes.
- Device IP placeholders (`<UR5_IP>`, `<OPENTRONS_IP>`, …) only matter when you later run the *generated* `main.py` against real hardware.

### 5. Run an agent

Open n8n in your browser at **http://localhost:5678** (or your override port). Each agent is triggered by a **Webhook** node. Three ways to run one:

**A. Easiest — "Test workflow" with the built-in example.** Each agent's Webhook node carries **pinned example data** (a sample request). Open the workflow and click **Test workflow** in the bottom bar — it runs the whole agent on that example and you watch every node execute, no payload to craft. Best for a first look. Results appear under **Executions**.

**B. Programmatic — POST to the webhook.** Toggle the workflow **Active** to get its production webhook URL (or use **"Listen for test event"** in the editor for a one-shot test URL), then POST the JSON the agent expects. Example for a **device-protocol** agent (PowerShell):

```powershell
$body = @{
  job_id           = "demo-1"
  prompt           = "Transfer 100 uL of reagent from tube A1 of a 24-tube NEST snapcap rack in slot 3 to every well of a NEST 96-well flat plate in slot 9, using a 1000 uL single-channel pipette on the left mount and a 1000 uL Flex tip rack in slot 1. Reuse the same tip."
  metadata         = @{ template_id = "demo" }
  step_id          = "1"
  protocols_folder = "/data/demo"
  timestamp        = "2026-01-01T00:00:00Z"
} | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:5678/webhook/opentrons-ai-lab167-p3 -Method Post -ContentType 'application/json' -Body $body
```

The agent generates the protocol, validates/simulates it via agent-tools, and saves it under the `agent-tools` `/data` volume.

> **Payload differs per agent.** The device-protocol agents expect an Opentrons `prompt` + template metadata; the orchestration/analysis agents expect device/loop or experiment metadata. Open the workflow's **Webhook** node (its pinned data shows the exact body), or check the matching system prompt in `System Prompts/`.

**C. Interactive chat (optional).** For a chat box instead of webhooks, add an n8n **Chat Trigger** node and feed its `chatInput` into the agent where the webhook's `prompt` is used. This is a nice demo for the device-protocol agent (free-text Opentrons requests), but the orchestration/analysis agents need structured fields a chat box can't supply, so A/B above are the general-purpose paths.

See `n8n AI Agents/Minimal Application/README.md` for the agent-tools endpoints and how script validation/simulation work.

### Documentation retrieval (vector store)

The device-protocol agents include a `Get_Opentrons_API_Info` tool that retrieves Opentrons Python API documentation from a **Qdrant** vector store to ground protocol generation. Qdrant ships **pre-loaded**: the `qdrant` service in `docker-compose.yml` restores a snapshot of the `opentrons_docs` collection on startup, so it's available out of the box — **no OpenAI key or ingestion step required to use it**.

To wire it up you only need to create a **Qdrant credential** in n8n (URL `http://qdrant:6333`, empty API key) and select it on the *Qdrant Vector Store* node (already present in the device-protocol workflows). The embeddings node must use `text-embedding-3-small` (the model the shipped snapshot was built with). See [`n8n AI Agents/Documentation Retrieval/`](n8n%20AI%20Agents/Documentation%20Retrieval/README.md) to rebuild/re-ingest the corpus or regenerate the snapshot from the Apache-2.0 Opentrons docs source. The agents also work without the tool (model's built-in knowledge, reduced grounding).

---

## Replication study

`Replication Study/` quantifies how reliably the agents generate a template. Each agent was run 20
times from an identical starting point for two experiments — the isotherm workflow and the FPLC
gradient elution workflow — and every artifact was compared against the published reference by a
comparator specific to its type.

It is self-contained: `data/` holds every replicate (the artifact, the request it answered, its
telemetry, the full n8n execution and the comparator's verdict), `code/` holds the runner, the
comparators and the analysis scripts, and `results/` holds the statistics and figures. A clean run
of the five scripts in `code/analysis/` reproduces every shipped result file from `data/` alone,
needing only `scipy` and `matplotlib`. See its own `README.md` and `METHODS.md`.

The prompts it sent are also republished beside the templates they belong to, under
`Generated Templates/*/Prompts/as sent/`.

---

## Supplementary videos

The supplementary videos are **not included in this Git repository** (they exceed GitHub's 100 MB file limit). They are provided with the article and archived separately on Zenodo (https://zenodo.org/records/22015154):

1. **Video 1** — Planning an experiment in the application (`Video_1_SDL_App_Plan_Experiment`)
2. **Video 2** — Executing an experiment (`Video_2_SDL_App_Execute_Experiment`)
3. **Video 3** — Executing a simulation (`Video_3_SDL_App_Simulation_Execution`)
4. **Video 4** — Implementing a simulation template (`Video_4_SDL_App_Simulation_Template_Implementation`)
5. **Video 5** — Implementing an experiment template (`Video_5_SDL_App_Implement_Experiment_Template`)

---

## License

This repository is **dual-licensed**:

- **Code** (scripts, n8n workflows, system prompts, agent-tools service) — [MIT License](LICENSE).
- **Data** (experimental results, processed datasets, simulation outputs under `Experiments/`, `Generated Templates/`, and `Data Analysis/`) — **Creative Commons Attribution 4.0 International (CC-BY-4.0)**, applied via the Zenodo deposition of this repository.

When reusing data, please cite the manuscript and the Zenodo archive (https://doi.org/10.5281/zenodo.20625765).

## Citation

If you use this code or data, please cite the publication above and the Zenodo archive (https://doi.org/10.5281/zenodo.20625765). Full bibliographic details for the article will be added upon publication.
