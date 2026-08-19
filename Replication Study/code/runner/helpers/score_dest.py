#!/usr/bin/env python3
"""Score a whole run with the destination-equivalence comparator, next to the other two.

    docker exec -e HOME=/tmp -w /tmp/harness sdl-app \
        /opt/conda/envs/sdl-app/bin/python score_dest.py --run /tmp/harness/runs/dpfinal20
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import destinations as ds  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--reference", default="/tmp/harness/reference_experiment/protocols")
    ap.add_argument("--protocols", default="2,3,4,5")
    ap.add_argument("--out", default="/tmp/harness/dest_scores.json")
    args = ap.parse_args()

    lw = pathlib.Path(args.reference)
    nums = [p.strip() for p in args.protocols.split(",") if p.strip()]
    refs = {}
    for n in nums:
        try:
            refs[n] = ds.delivered(lw / ("Template_278_OT_Protocol_%s.py" % n), lw)
        except Exception as exc:  # noqa: BLE001
            print("reference P%s failed to simulate: %s" % (n, str(exc)[:90]))

    by = {n: {"pass": 0, "n": 0, "codes": {}} for n in nums}
    rows = []
    for rep in sorted(pathlib.Path(args.run).glob("replicate_*")):
        inst = next(iter(sorted((rep / "instantiated").glob("experiment_*"))), None)
        if not inst:
            continue
        for n in nums:
            cand = inst / "protocols" / ("Template_278_OT_Protocol_%s.py" % n)
            if not cand.exists() or n not in refs:
                continue
            by[n]["n"] += 1
            try:
                r = ds.compare(refs[n], ds.delivered(cand, lw))
            except Exception as exc:  # noqa: BLE001
                r = {"pass": False, "codes": ["SIM_FAILED"],
                     "candidate_error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
            if r.get("pass"):
                by[n]["pass"] += 1
            for c in r.get("codes", []):
                by[n]["codes"][c] = by[n]["codes"].get(c, 0) + 1
            rows.append({"replicate": rep.name, "protocol": n, "pass": r.get("pass"),
                         "codes": r.get("codes", []),
                         # the candidate's own exception, so the failure taxonomy can name what
                         # went wrong without re-simulating or reading a second comparator's output
                         "candidate_error": r.get("candidate_error"),
                         "multiples": r.get("systematic_multiples"),
                         "matched": r.get("matched_within_2pct"),
                         "of": r.get("destinations_reference")})

    print("%-12s %10s   %s" % ("protocol", "delivered", "dominant codes"))
    tp = tn = 0
    for n in nums:
        s = by[n]
        tp, tn = tp + s["pass"], tn + s["n"]
        codes = ", ".join("%s x%d" % (k, v) for k, v in
                          sorted(s["codes"].items(), key=lambda x: -x[1])[:3])
        print("P%-11s %6d/%-3d   %s" % (n, s["pass"], s["n"], codes))
    print("%-12s %6d/%-3d" % ("TOTAL", tp, tn))
    mult = [r for r in rows if r.get("multiples")]
    if mult:
        print("\nsystematic volume multiples (multi-channel double-count signature):")
        for r in mult[:10]:
            print("  %-14s P%s  %s" % (r["replicate"], r["protocol"], r["multiples"]))
    pathlib.Path(args.out).write_text(json.dumps({"by_protocol": by, "rows": rows}, indent=2)
                                      + "\n", encoding="utf-8")
    print("\nwrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
