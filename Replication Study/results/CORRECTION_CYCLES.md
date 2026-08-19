# Internal correction cycles

A correction cycle is an invocation of the agent's own validation tool beyond the first: it ran its artifact, rejected what came back, revised and ran it again. `never validated` is reported separately from `0 cycles`, because not checking and checking once are different behaviours.


## Per series

| series | n | never validated | 0 cycles | 1 | 2+ | mean cycles | mean retrievals | mean tool calls | max tool calls |
|---|---|---|---|---|---|---|---|---|---|
| isotherm / device protocol 1 | 20 | 0 | 8 | 9 | 3 | 0.75 | 1.20 | 3.95 | 7 |
| isotherm / device protocol 2 | 20 | 0 | 10 | 7 | 3 | 0.65 | 1.10 | 3.75 | 6 |
| isotherm / device protocol 3 | 20 | 0 | 12 | 8 | 0 | 0.40 | 1.10 | 3.50 | 5 |
| isotherm / device protocol 4 | 20 | 0 | 15 | 4 | 1 | 0.30 | 1.30 | 3.60 | 6 |
| isotherm / device protocol 5 | 20 | 0 | 8 | 10 | 2 | 0.70 | 1.20 | 3.90 | 6 |
| isotherm / orchestration | 20 | 0 | 18 | 2 | 0 | 0.10 | 0.00 | 3.10 | 4 |
| isotherm / analysis | 20 | 0 | 18 | 2 | 0 | 0.10 | 0.00 | 2.05 | 3 |
| isotherm / report | 20 | 4 | 11 | 5 | 0 | 0.25 | 0.00 | 2.05 | 3 |
| isotherm / database | 20 | 0 | 20 | 0 | 0 | 0.00 | 0.00 | 1.00 | 1 |
| FPLC / orchestration | 20 | 20 | 0 | 0 | 0 | 0.00 | 0.00 | 2.00 | 2 |
| FPLC / analysis | 20 | 0 | 20 | 0 | 0 | 0.00 | 0.00 | 2.00 | 2 |
| FPLC / report | 20 | 14 | 2 | 3 | 1 | 0.25 | 0.00 | 1.55 | 4 |
| FPLC / database | 20 | 0 | 20 | 0 | 0 | 0.00 | 0.00 | 1.00 | 1 |

## Does self-correction help?

Every replicate in the study, split by whether the agent revised its own output at least once.

| group | replicates | passed | rate |
|---|---|---|---|
| never validated | 38 | 35 | 92.1% |
| validated once, no revision | 162 | 148 | 91.4% |
| revised >=1 time | 60 | 52 | 86.7% |

Revised at least once vs validated once without revising: Fisher exact **p = 0.3164** (not distinguishable).

A revision is triggered by the agent seeing a problem, so the two groups are not comparable populations - replicates that needed fixing were harder to begin with. The number answers whether revision RESCUES them to the level of the ones that never needed it, not whether revision is beneficial in the abstract.

