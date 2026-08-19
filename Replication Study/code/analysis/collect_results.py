#!/usr/bin/env python3
"""Gather every figure input from the published bundle.

Reads the bundle's own layout — `data/<experiment>/<artifact>/replicate_NN/` — rather than the
run directories the runner produces. Each replicate already carries the verdict on it
(`score.json`) and its telemetry (`result.json`), so no run index or cross-reference is needed:
walking the tree IS the collection.

    python collect_results.py [--data DIR] [--out results.json]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

HERE = pathlib.Path(__file__).resolve().parent

# Running inside the published bundle this script sits in code/analysis/ and its outputs belong in
# results/ at the bundle root; in the working tree there is no such directory and they stay beside
# the script. A reproduction run therefore refreshes exactly the files it is checking.
OUT = HERE.parent.parent / "results"
if not OUT.is_dir():
    OUT = HERE
TARGET_N = 20

AGENTS = ["orchestration", "analysis", "report", "database"]
EXPERIMENTS = ["isotherm", "fplc_gradient"]


def load(p: pathlib.Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def series(dirpath: pathlib.Path):
    """(passed, n, telemetry) for one artifact directory."""
    reps = sorted(dirpath.glob("replicate_*"))
    if not reps:
        return None
    passed = n = 0
    tok, wc, tools, prompt, completion, per_rep = [], [], [], [], [], []
    for rep in reps:
        score = load(rep / "score.json")
        if score is None or score.get("pass") is None:
            continue
        n += 1
        passed += 1 if score.get("pass") else 0
        r = load(rep / "result.json") or {}
        if r.get("tokens_total"):
            tok.append(r["tokens_total"])
        if r.get("wall_clock_s"):
            wc.append(r["wall_clock_s"])
        if r.get("tool_calls") is not None:
            tools.append(r["tool_calls"])
        # Input and output tokens are priced differently and behave differently: prompt tokens
        # grow with how much context the agent drags along, completion tokens with how much it
        # reasons and writes. A single total hides which of the two a weaker model is spending.
        tt = (load(rep / "tokens.json") or {}).get("totals") or {}
        if tt.get("prompt"):
            prompt.append(tt["prompt"])
        if tt.get("completion"):
            completion.append(tt["completion"])
        # Kept per replicate, not only as a summary: cost is priced per run and its percentiles
        # have to be taken over the runs. The 25th percentile of input tokens and the 25th of
        # output tokens come from different replicates and are billed at different rates, so
        # multiplying the two summaries out gives a figure no actual run cost.
        if tt.get("prompt") or tt.get("completion"):
            per_rep.append({"replicate": rep.name.replace("replicate_", ""),
                            "in": tt.get("prompt") or 0, "out": tt.get("completion") or 0,
                            "status": r.get("status"), "pass": bool(score.get("pass"))})

    def summary(v):
        if not v:
            return None
        s = sorted(v)
        return {"n": len(s), "median": st.median(s), "q1": s[len(s) // 4],
                "q3": s[min(len(s) - 1, 3 * len(s) // 4)], "min": s[0], "max": s[-1]}

    return passed, n, {"tokens": summary(tok), "wall_clock_s": summary(wc),
                       "tool_calls": summary(tools),
                       "tokens_in": summary(prompt), "tokens_out": summary(completion),
                       "per_replicate": per_rep}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE.parent.parent / "data"))
    ap.add_argument("--out", default=str(OUT / "results.json"))
    args = ap.parse_args()
    data = pathlib.Path(args.data)
    out = {"agents": {}, "device_protocol": {}, "models": {}, "missing": []}

    # ---------------------------------------------------------------- agents
    for experiment in EXPERIMENTS:
        out["agents"][experiment] = {}
        for agent in AGENTS:
            got = series(data / experiment / agent)
            if not got:
                out["missing"].append("%s/%s: no replicates under data/" % (experiment, agent))
                continue
            passed, n, tel = got
            if n != TARGET_N:
                out["missing"].append("%s/%s: n=%d, expected %d" % (experiment, agent, n, TARGET_N))
            entry = {"passed": passed, "n": n, "comparator": agent, "telemetry": tel}
            # how many replicates the upstream gate removed - recoverable only from the series
            # record, since the gating happened before anything was published
            meta = load(data / experiment / agent / "series.json") or {}
            if meta.get("upstream_gate"):
                entry["upstream_gate"] = meta["upstream_gate"]
                entry["excluded_bad_upstream"] = meta.get("excluded_bad_upstream", 0)
            out["agents"][experiment][agent] = entry

    # ---------------------------------------------------------------- device protocols
    by_protocol, tel_by_protocol = {}, {}
    for d in sorted((data / "isotherm").glob("device_protocol_*")):
        got = series(d)
        if not got:
            continue
        passed, n, tel = got
        num = d.name.rsplit("_", 1)[1]
        by_protocol[num] = {"pass": passed, "n": n}
        tel_by_protocol[d.name] = tel
    if by_protocol:
        out["device_protocol"]["isotherm"] = {
            "by_protocol": by_protocol, "telemetry": tel_by_protocol,
            "comparator": "destinations (net volume per destination well)"}
    else:
        out["missing"].append("device_protocol: nothing under data/isotherm/device_protocol_*")

    # ---------------------------------------------------------------- models
    for proto_dir in sorted((data / "model_comparison").glob("protocol_*")):
        protocol = proto_dir.name.rsplit("_", 1)[1]
        out["models"][protocol] = {}
        for model_dir in sorted(proto_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            got = series(model_dir)
            if not got:
                continue
            passed, n, tel = got
            out["models"][protocol][model_dir.name] = {
                "passed": passed, "n": n, "comparator": "destinations", "telemetry": tel}

    pathlib.Path(args.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    for experiment, s in out["agents"].items():
        for agent, v in s.items():
            print("  %-14s %-16s %2d/%d" % (experiment, agent, v["passed"], v["n"]))
    for num, v in sorted(out["device_protocol"].get("isotherm", {})
                         .get("by_protocol", {}).items()):
        print("  %-14s device protocol %-2s %2d/%d" % ("isotherm", num, v["pass"], v["n"]))
    for protocol, s in sorted(out["models"].items()):
        for model, v in s.items():
            print("  %-14s protocol %-2s %-12s %2d/%d"
                  % ("models", protocol, model, v["passed"], v["n"]))
    if out["missing"]:
        print("\nMISSING:")
        for m in out["missing"]:
            print("  - " + m)
    print("\nwrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
