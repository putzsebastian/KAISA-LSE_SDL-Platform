#!/usr/bin/env python3
"""Internal correction cycles: how often an agent tested its own output and revised it.

Reviewer #1 asked for this alongside convergence rate, and the tool-call log supports it directly.
Each agent has a validation tool - `OT_Script_Simulation` for device protocols, `Validate Script`
for the rest - and calling it more than once means the agent ran its artifact, did not like what
came back, changed it and ran it again. So:

    validation calls  = invocations of the agent's own checking tool
    correction cycles = validation calls - 1, floored at zero

A replicate with zero cycles either validated once and was satisfied, or never validated at all;
those two are reported separately, because "never checked" and "checked once, passed" are very
different behaviours.

The question worth asking of this metric is not how many cycles occur but whether they HELP:
if replicates that self-corrected pass no more often than those that did not, the loop is costing
tokens without buying reliability.

Reads the bundle layout, `data/<experiment>/<artifact>/replicate_NN/`. The upstream gating the
runner applies is already baked in - a replicate is in the bundle only if it counted.

    python correction_cycles.py [--data DIR] [--out CORRECTION_CYCLES.md]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib

from scipy.stats import fisher_exact

HERE = pathlib.Path(__file__).resolve().parent

# Running inside the published bundle this script sits in code/analysis/ and its outputs belong in
# results/ at the bundle root; in the working tree there is no such directory and they stay beside
# the script. A reproduction run therefore refreshes exactly the files it is checking.
OUT = HERE.parent.parent / "results"
if not OUT.is_dir():
    OUT = HERE

# node name -> role. Matched case-insensitively on a normalised name, because the same node is
# spelled `Validate Script`, `Validate_Script` and `Validate Script1` across the workflows.
VALIDATE = ("validatescript", "otscriptsimulation")
RETRIEVE = ("getopentronsapiinfo",)
SAVE = ("savescript",)

# (label, experiment directory, artifact directory) - device protocols first, then the four agents
# per experiment, which is the order the manuscript discusses them in.
SERIES = (
    [("isotherm / device protocol %d" % i, "isotherm", "device_protocol_%d" % i)
     for i in range(1, 6)]
    + [("isotherm / %s" % a, "isotherm", a)
       for a in ("orchestration", "analysis", "report", "database")]
    + [("FPLC / %s" % a, "fplc_gradient", a)
       for a in ("orchestration", "analysis", "report", "database")]
)


def norm(name):
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum()).rstrip("0123456789")


# `Qdrant Vector Store` is the retrieval tool's own implementation, invoked BY
# Get_Opentrons_API_Info rather than by the agent. Counting both would report two retrievals where
# the agent made one.
INNER = ("qdrantvectorstore",)


def role(node):
    n = norm(node)
    if n in INNER:
        return "inner"
    if any(n.startswith(v) for v in VALIDATE):
        return "validate"
    if any(n.startswith(r) for r in RETRIEVE):
        return "retrieve"
    if any(n.startswith(s) for s in SAVE):
        return "save"
    return "other"


def tool_runs(rep: pathlib.Path):
    """Every tool node the agent invoked.

    toolcalls.json only records nodes named in the agent registry, and those lists are incomplete -
    the device-protocol config omits `Qdrant Vector Store` and both orchestration configs omit one
    of the two save nodes. Validation counts happen to be exact either way (verified against the raw
    executions), but total tool calls are not, so `toolruns.json` - the per-node run counts taken
    straight from the execution - is preferred, and toolcalls.json is only a fallback.
    """
    counts = collections.Counter()
    extracted = rep / "toolruns.json"
    if extracted.exists():
        try:
            d = json.loads(extracted.read_text(encoding="utf-8"))
            for node, k in (d.get("node_runs") or {}).items():
                r = role(node)
                if r in ("validate", "retrieve", "save"):
                    counts[r] += k
            return counts
        except Exception:  # noqa: BLE001
            pass
    raw = rep / "execution.json"
    if raw.exists():
        try:
            d = json.loads(raw.read_text(encoding="utf-8"))
            runs = ((d.get("data") or {}).get("resultData") or {}).get("runData") or {}
            for node, rs in runs.items():
                r = role(node)
                if r in ("validate", "retrieve", "save"):
                    counts[r] += len(rs or [])
            return counts
        except Exception:  # noqa: BLE001
            pass
    tc = rep / "toolcalls.json"
    if tc.exists():
        try:
            for c in (json.loads(tc.read_text(encoding="utf-8")).get("calls") or []):
                r = role(c.get("node"))
                if r in ("validate", "retrieve", "save"):
                    counts[r] += 1
        except Exception:  # noqa: BLE001
            pass
    return counts


def collect(path: pathlib.Path):
    """Per replicate: (passed, validation calls, retrieval calls, total tool calls)."""
    rows = []
    for rep in sorted(path.glob("replicate_*")):
        try:
            s = json.loads((rep / "score.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if s.get("pass") is None:
            continue
        c = tool_runs(rep)
        rows.append((bool(s["pass"]), c["validate"], c["retrieve"],
                     c["validate"] + c["retrieve"] + c["save"]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE.parent.parent / "data"))
    ap.add_argument("--out", default=str(OUT / "CORRECTION_CYCLES.md"))
    args = ap.parse_args()
    data = pathlib.Path(args.data)

    L = ["# Internal correction cycles\n",
         "A correction cycle is an invocation of the agent's own validation tool beyond the "
         "first: it ran its artifact, rejected what came back, revised and ran it again. "
         "`never validated` is reported separately from `0 cycles`, because not checking and "
         "checking once are different behaviours.\n"]

    L.append("\n## Per series\n")
    L.append("| series | n | never validated | 0 cycles | 1 | 2+ | mean cycles | mean retrievals | mean tool calls | max tool calls |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")

    pooled = []
    for label, experiment, artifact in SERIES:
        rows = collect(data / experiment / artifact)
        if not rows:
            continue
        pooled += rows
        never = sum(1 for p, v, r, t in rows if v == 0)
        zero = sum(1 for p, v, r, t in rows if v == 1)
        one = sum(1 for p, v, r, t in rows if v == 2)
        more = sum(1 for p, v, r, t in rows if v >= 3)
        cyc = [max(0, v - 1) for p, v, r, t in rows]
        ret = [r for p, v, r, t in rows]
        tot = [t for p, v, r, t in rows]
        L.append("| %s | %d | %d | %d | %d | %d | %.2f | %.2f | %.2f | %d |"
                 % (label, len(rows), never, zero, one, more,
                    sum(cyc) / len(cyc), sum(ret) / len(ret),
                    sum(tot) / len(tot), max(tot)))

    L.append("\n## Does self-correction help?\n")
    L.append("Every replicate in the study, split by whether the agent revised its own output at "
             "least once.\n")
    L.append("| group | replicates | passed | rate |")
    L.append("|---|---|---|---|")
    grp = {"never validated": [], "validated once, no revision": [], "revised >=1 time": []}
    for p, v, r, t in pooled:
        key = ("never validated" if v == 0
               else "validated once, no revision" if v == 1 else "revised >=1 time")
        grp[key].append(p)
    for k, vals in grp.items():
        if vals:
            L.append("| %s | %d | %d | %.1f%% |"
                     % (k, len(vals), sum(vals), 100.0 * sum(vals) / len(vals)))

    a = grp["validated once, no revision"]
    b = grp["revised >=1 time"]
    if a and b:
        _o, p = fisher_exact([[sum(a), len(a) - sum(a)], [sum(b), len(b) - sum(b)]])
        L.append("\nRevised at least once vs validated once without revising: "
                 "Fisher exact **p = %.4f** (%s).\n"
                 % (p, "different" if p < 0.05 else "not distinguishable"))
        L.append("A revision is triggered by the agent seeing a problem, so the two groups are "
                 "not comparable populations - replicates that needed fixing were harder to begin "
                 "with. The number answers whether revision RESCUES them to the level of the ones "
                 "that never needed it, not whether revision is beneficial in the abstract.\n")

    text = "\n".join(L) + "\n"
    pathlib.Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
