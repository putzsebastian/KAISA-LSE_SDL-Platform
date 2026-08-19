# Replication statistics

All intervals are Clopper-Pearson (exact binomial) at 95%. `width` is the interval span in percentage points — at n = 20 it never falls below ~17, which bounds what this study can resolve.


## 1. Reliability by agent

| agent | rate | % | 95% CI | width | notes |
|---|---|---|---|---|---|
| **isotherm (Opentrons)** | | | | | |
| &nbsp;&nbsp;orchestration | 20/20 | 100.0% | [83.2%, 100.0%] | 16.8 | — |
| &nbsp;&nbsp;analysis | 16/20 | 80.0% | [56.3%, 94.3%] | 37.9 | — |
| &nbsp;&nbsp;report | 19/20 | 95.0% | [75.1%, 99.9%] | 24.7 | — |
| &nbsp;&nbsp;database | 19/20 | 95.0% | [75.1%, 99.9%] | 24.7 | 6 excluded: failed analysis |
| **FPLC gradient (ÄKTA)** | | | | | |
| &nbsp;&nbsp;orchestration | 20/20 | 100.0% | [83.2%, 100.0%] | 16.8 | — |
| &nbsp;&nbsp;analysis | 19/20 | 95.0% | [75.1%, 99.9%] | 24.7 | — |
| &nbsp;&nbsp;report | 17/20 | 85.0% | [62.1%, 96.8%] | 34.7 | — |
| &nbsp;&nbsp;database | 18/20 | 90.0% | [68.3%, 98.8%] | 30.5 | 1 excluded: failed analysis |
| **device protocol** (isotherm) | | | | | |
| &nbsp;&nbsp;protocol 1 | 10/20 | 50.0% | [27.2%, 72.8%] | 45.6 | — |
| &nbsp;&nbsp;protocol 2 | 19/20 | 95.0% | [75.1%, 99.9%] | 24.7 | — |
| &nbsp;&nbsp;protocol 3 | 18/20 | 90.0% | [68.3%, 98.8%] | 30.5 | — |
| &nbsp;&nbsp;protocol 4 | 20/20 | 100.0% | [83.2%, 100.0%] | 16.8 | — |
| &nbsp;&nbsp;protocol 5 | 20/20 | 100.0% | [83.2%, 100.0%] | 16.8 | — |
| &nbsp;&nbsp;**pooled 1–5** | 87/100 | 87.0% | [78.8%, 92.9%] | 14.1 | — |
| &nbsp;&nbsp;**pooled 2–5** | 77/80 | 96.2% | [89.4%, 99.2%] | 9.8 | excludes protocol 1 |

## 2. Two protocols across four models

Identical payload prompt, identical system prompt, identical harness and comparator; one variable changed. Measuring an easy protocol alongside a hard one is what distinguishes 'this model is worse' from 'this task needs capability'.

| model | protocol 4 (shake and incubate - no arithmetic) | protocol 1 (dilution series - the study's hardest logic) |
|---|---|---|
| gpt-5-nano | 11/20 — 55.0%  [31.5, 76.9] | 0/20 — 0.0%  [0.0, 16.8] |
| gpt-5-mini | 20/20 — 100.0%  [83.2, 100.0] | 11/20 — 55.0%  [31.5, 76.9] |
| gpt-5.1 | 20/20 — 100.0%  [83.2, 100.0] | 10/20 — 50.0%  [27.2, 72.8] |
| gpt-5.5 | 20/20 — 100.0%  [83.2, 100.0] | 20/20 — 100.0%  [83.2, 100.0] |

### Within model: does the hard protocol separate what the easy one does not?

| model | protocol 4 | protocol 1 | Fisher p |
|---|---|---|---|
| gpt-5-nano | 11/20 | 0/20 | 0.00015 |
| gpt-5-mini | 20/20 | 11/20 | 0.00123 |
| gpt-5.1 | 20/20 | 10/20 | 0.00044 |
| gpt-5.5 | 20/20 | 20/20 | 1.00000  (not distinguishable) |

### Pairwise on protocol 4 (shake and incubate - no arithmetic) (Fisher exact, two-sided)

| comparison | p | |
|---|---|---|
| gpt-5-nano vs gpt-5-mini | 0.00123 | distinguishable |
| gpt-5-nano vs gpt-5.1 | 0.00123 | distinguishable |
| gpt-5-nano vs gpt-5.5 | 0.00123 | distinguishable |
| gpt-5-mini vs gpt-5.1 | 1.00000 | not distinguishable |
| gpt-5-mini vs gpt-5.5 | 1.00000 | not distinguishable |
| gpt-5.1 vs gpt-5.5 | 1.00000 | not distinguishable |

### Pairwise on protocol 1 (dilution series - the study's hardest logic) (Fisher exact, two-sided)

| comparison | p | |
|---|---|---|
| gpt-5-nano vs gpt-5-mini | 0.00015 | distinguishable |
| gpt-5-nano vs gpt-5.1 | 0.00044 | distinguishable |
| gpt-5-nano vs gpt-5.5 | 0.00000 | distinguishable |
| gpt-5-mini vs gpt-5.1 | 1.00000 | not distinguishable |
| gpt-5-mini vs gpt-5.5 | 0.00123 | distinguishable |
| gpt-5.1 vs gpt-5.5 | 0.00044 | distinguishable |

## 3. What n = 20 can resolve

Every pair below is one series against another at the same n, to show where the study's resolution actually lies.

| comparison | Fisher p | |
|---|---|---|
| 20/20 vs 19/20 | 1.0000 | not distinguishable |
| 20/20 vs 18/20 | 0.4872 | not distinguishable |
| 20/20 vs 17/20 | 0.2308 | not distinguishable |
| 20/20 vs 16/20 | 0.1060 | not distinguishable |
| 20/20 vs 15/20 | 0.0471 | distinguishable |
| 20/20 vs 14/20 | 0.0202 | distinguishable |
| 20/20 vs 13/20 | 0.0083 | distinguishable |
| 20/20 vs 12/20 | 0.0033 | distinguishable |
| 20/20 vs 10/20 | 0.0004 | distinguishable |

## 4. Cost per artifact

Every column carries its interquartile range, tool calls included: the tool-call spread is what backs the retry reading of the weaker models, and a point value cannot support it.

| series | tokens (median) | tokens IQR | wall clock s | s IQR | tool calls | calls IQR |
|---|---|---|---|---|---|---|
| **isotherm (Opentrons)** | | | | | | |
| &nbsp;&nbsp;orchestration | 84.0k | 76.7k–100.3k | 171 | 147–223 | 2.0 | 2.0–2.0 |
| &nbsp;&nbsp;analysis | 110.0k | 105.1k–116.4k | 198 | 179–255 | 2.0 | 2.0–2.0 |
| &nbsp;&nbsp;report | 31.6k | 27.4k–46.7k | 67 | 59–86 | 2.0 | 2.0–3.0 |
| &nbsp;&nbsp;database | 45.7k | 44.9k–48.0k | 94 | 87–98 | 1.0 | 1.0–1.0 |
| **FPLC gradient (ÄKTA)** | | | | | | |
| &nbsp;&nbsp;orchestration | 51.7k | 51.2k–51.8k | 70 | 66–73 | 1.0 | 1.0–1.0 |
| &nbsp;&nbsp;analysis | 82.3k | 78.9k–85.2k | 119 | 110–134 | 2.0 | 2.0–2.0 |
| &nbsp;&nbsp;report | 15.2k | 14.1k–22.0k | 27 | 23–38 | 1.0 | 1.0–2.0 |
| &nbsp;&nbsp;database | 37.6k | 37.0k–38.8k | 67 | 65–72 | 1.0 | 1.0–1.0 |
| **device protocol** | | | | | | |
| &nbsp;&nbsp;protocol 1 | 98.7k | 87.5k–130.5k | 122 | 108–145 | 3.0 | 2.0–3.0 |
| &nbsp;&nbsp;protocol 2 | 64.7k | 55.7k–75.8k | 60 | 50–73 | 3.0 | 2.0–3.0 |
| &nbsp;&nbsp;protocol 3 | 61.1k | 59.6k–68.4k | 62 | 58–73 | 2.0 | 2.0–3.0 |
| &nbsp;&nbsp;protocol 4 | 49.3k | 48.2k–62.5k | 37 | 32–41 | 2.0 | 2.0–3.0 |
| &nbsp;&nbsp;protocol 5 | 65.0k | 49.9k–67.3k | 47 | 41–53 | 3.0 | 2.0–3.0 |
| **models, protocol 1** | | | | | | |
| &nbsp;&nbsp;gpt-5-nano | 191.3k | 143.6k–276.2k | 671 | 527–954 | 4.0 | 3.0–6.0 |
| &nbsp;&nbsp;gpt-5-mini | 186.1k | 172.1k–240.8k | 523 | 388–634 | 4.0 | 4.0–5.0 |
| &nbsp;&nbsp;gpt-5.1 | 98.7k | 87.5k–130.5k | 122 | 108–145 | 3.0 | 2.0–3.0 |
| &nbsp;&nbsp;gpt-5.5 | 131.3k | 98.9k–158.6k | 272 | 258–316 | 2.0 | 2.0–3.0 |
| **models, protocol 4** | | | | | | |
| &nbsp;&nbsp;gpt-5-nano | 149.8k | 126.6k–192.5k | 323 | 277–501 | 5.0 | 4.0–6.0 |
| &nbsp;&nbsp;gpt-5-mini | 55.4k | 54.5k–57.2k | 113 | 99–123 | 2.0 | 2.0–2.0 |
| &nbsp;&nbsp;gpt-5.1 | 49.3k | 48.2k–62.5k | 37 | 32–41 | 2.0 | 2.0–3.0 |
| &nbsp;&nbsp;gpt-5.5 | 61.3k | 45.8k–62.6k | 69 | 62–82 | 3.0 | 2.0–3.0 |

### 4a. Input and output tokens, by model

Prompt and completion tokens are priced differently and driven by different things — input by how much context the agent carries, output by how much it reasons and writes — so a single total hides which of the two a model is actually spending. Medians across the same 20 replicates as above; `out %` is the completion share of the total.

| protocol | model | in (median) | in IQR | out (median) | out IQR | out % |
|---|---|---|---|---|---|---|
| 1 | gpt-5-nano | 110.1k | 75.6k–156.4k | 85.7k | 63.5k–116.9k | 43.8% |
| 1 | gpt-5-mini | 157.1k | 147.2k–206.2k | 29.9k | 28.9k–38.9k | 16.0% |
| 1 | gpt-5.1 | 83.7k | 79.0k–113.1k | 14.4k | 11.7k–16.8k | 14.7% |
| 1 | gpt-5.5 | 115.1k | 81.3k–140.8k | 17.8k | 16.5k–20.9k | 13.4% |
| 4 | gpt-5-nano | 105.6k | 81.8k–138.6k | 44.9k | 38.4k–64.2k | 29.8% |
| 4 | gpt-5-mini | 45.0k | 44.3k–45.7k | 10.4k | 9.7k–11.7k | 18.8% |
| 4 | gpt-5.1 | 45.5k | 44.4k–58.7k | 3.9k | 3.5k–4.5k | 7.9% |
| 4 | gpt-5.5 | 57.3k | 42.2k–58.5k | 4.3k | 3.8k–4.9k | 6.9% |

### 4b. Cost per generation run, by model

List price of one replicate: its own input and output tokens at the rates in Supplementary Table S7. Cost is computed per replicate and only then summarised — quartiles are taken over the 20 per-run costs, not derived from the token quartiles in 4a, which come from different replicates and are billed at different rates, so their product is the cost of no actual run. Median input and output tokens are repeated here for cross-checking.

| protocol | model | median cost | cost IQR | range | in (median) | out (median) | n |
|---|---|---|---|---|---|---|---|
| 1 | gpt-5-nano | $0.0392 | $0.0292–$0.0547 | $0.0139–$0.0749 | 110.1k | 85.7k | 20 |
| 1 | gpt-5-mini | $0.1008 | $0.0934–$0.1239 | $0.0805–$0.1506 | 157.1k | 29.9k | 20 |
| 1 | gpt-5.1 | $0.2542 | $0.2115–$0.2880 | $0.1772–$0.4261 | 83.7k | 14.4k | 20 |
| 1 | gpt-5.5 | $1.0874 | $0.9356–$1.2384 | $0.7876–$2.0592 | 115.1k | 17.8k | 20 |
| 4 | gpt-5-nano | $0.0226 | $0.0206–$0.0315 | $0.0121–$0.0521 | 105.6k | 44.9k | 20 |
| 4 | gpt-5-mini | $0.0322 | $0.0306–$0.0348 | $0.0237–$0.0399 | 45.0k | 10.4k | 20 |
| 4 | gpt-5.1 | $0.0959 | $0.0925–$0.1120 | $0.0846–$0.1386 | 45.5k | 3.9k | 20 |
| 4 | gpt-5.5 | $0.4045 | $0.3241–$0.4300 | $0.3050–$0.5453 | 57.3k | 4.3k | 20 |

Every replicate is included regardless of outcome — the tokens were spent whether or not the artifact was usable, and a cost conditioned on success would understate what the weaker models actually cost to obtain their failures. **No replicate hit the harness timeout**: all 160 completed in a single attempt, and the wall-clock distributions have no pile-up at a ceiling, so no cost here is a truncated lower bound. One replicate (protocol 1, gpt-5-nano) returned without an artifact; it is counted at full cost and as a failure.


### Supplementary Table S7. Price schedule

| model | API identifier | input $/1M | output $/1M | retrieved |
|---|---|---|---|---|
| gpt-5-nano | `gpt-5-nano` | 0.05 | 0.40 | 2026-08-13 |
| gpt-5-mini | `gpt-5-mini` | 0.25 | 2.00 | 2026-08-13 |
| gpt-5.1 | `gpt-5.1` | 1.25 | 10.00 | 2026-08-13 |
| gpt-5.5 | `gpt-5.5` | 5.00 | 30.00 | 2026-08-13 |

The API identifier is the exact string the n8n chat node requested, verified against the model recorded in every published execution. Rates are **uncached**.

Two things these figures are not. **They are list prices, not billed amounts** — no invoice was reconciled against them, and any discount, credit or tier would move them. **They are an upper bound on input cost**: the executions carry no cached-token accounting, so every prompt token is priced at the uncached rate; had any prompt been served from cache, the true input cost would be lower. Retrieval embeddings (`text-embedding-3-small`) are excluded — they are identical across all eight cells and are not counted in the token totals above.


## 5. Caveats that bear on reading these

* **Each bar is one experiment.** The intervals cover sampling at fixed conditions, not generalisation to an unseen protocol.
* **Gated series.** `database` consumes the analysis script, so replicates whose analysis failed are excluded rather than counted — handing an agent a broken input measures the upstream defect. Replicates were drawn in order until 20 had valid upstream input.
