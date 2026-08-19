#!/usr/bin/env python3
"""Compare what a protocol DELIVERS, ignoring where it drew from.

The reference comparator matches transfers as (source, destination, volume) triples. That makes the
choice of source well load-bearing - and for these protocols it is not. Protocol 1's prompt says
outright: "it would be best to track and check remaining liquid volumes inside the wells and adjust
from which wells to aspirate buffers". Which well of a 12-well buffer reservoir a draw comes from is
the agent's business; two protocols that fill the same destinations with the same liquid are
equivalent regardless of the order they emptied their reservoirs in.

Scoring that free variable cost real verdicts. Protocol 1 replicates 001 and 002 deliver all 44
destination wells within 2% of the reference - 178,900 uL, identical - and were marked as failures
purely for draining source wells fully (14,000 each) where the reference spread its draws (10,000
each).

So this comparator looks only at net volume per DESTINATION well:

  DEST_MISSING           the reference fills a well and the candidate never does
  DEST_EXTRA             the candidate fills a well the reference does not
  DEST_VOLUME_MISMATCH   both fill it, by different amounts

A destination is any well that ends with net positive volume, and a source any well that ends
negative - no slot list, no per-protocol configuration.

Where the volume is a clean multiple of the reference, that multiple is reported: an exact 8x is
the multi-channel double-count (protocol 1 replicate 003 put 80,000 uL into wells wanting 10,000),
and naming it separates a systematic factor error from a scattering of small ones.

Usage:
    python destinations.py --reference ref.py --candidate cand.py --labware DIR [--out r.json]
    python destinations.py --reference-dir R --candidate-dir C --labware DIR [--protocols 1,2,3]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import device_protocol as dp  # noqa: E402

TOL_FRAC = 0.02
TOL_ABS = 1.0


def delivered(path: pathlib.Path, labware: pathlib.Path) -> dict:
    """Net volume per destination well. Sources (net negative) are deliberately discarded."""
    net = dp.parse_runlog(dp.simulate_protocol(path, labware))["net"]
    return {k: round(v, 3) for k, v in net.items() if v > 0}


def near(a: float, b: float) -> bool:
    return abs(a - b) <= max(TOL_ABS, TOL_FRAC * abs(a))


def compare(ref: dict, cand: dict) -> dict:
    findings, multiples = [], {}
    for well, want in sorted(ref.items()):
        got = cand.get(well)
        if got is None:
            findings.append({"code": "DEST_MISSING", "well": well, "reference": want})
        elif not near(want, got):
            f = {"code": "DEST_VOLUME_MISMATCH", "well": well, "reference": want,
                 "candidate": got, "delta": round(got - want, 3)}
            if want:
                ratio = got / want
                # a clean integer multiple is a systematic error, not a scattering of small ones
                if abs(ratio - round(ratio)) < 0.01 and round(ratio) > 1:
                    f["multiple"] = int(round(ratio))
                    multiples[int(round(ratio))] = multiples.get(int(round(ratio)), 0) + 1
            findings.append(f)
    for well, got in sorted(cand.items()):
        if well not in ref:
            findings.append({"code": "DEST_EXTRA", "well": well, "candidate": got})

    matched = sum(1 for w, v in ref.items() if w in cand and near(v, cand[w]))
    result = {"pass": not findings,
              "codes": sorted({f["code"] for f in findings}),
              "n_findings": len(findings), "findings": findings[:30],
              "destinations_reference": len(ref), "destinations_candidate": len(cand),
              "matched_within_2pct": matched,
              "total_ul_reference": round(sum(ref.values()), 1),
              "total_ul_candidate": round(sum(cand.values()), 1)}
    if multiples:
        result["systematic_multiples"] = multiples
        top = max(multiples, key=lambda k: multiples[k])
        result["codes"] = sorted(set(result["codes"]) | {"DEST_VOLUME_MULTIPLE"})
        result["detail"] = ("%d destination well(s) hold exactly %dx the intended volume - the "
                            "signature of a multi-channel double-count"
                            % (multiples[top], top))
    return result


def score_one(ref_path, cand_path, labware) -> dict:
    try:
        ref = delivered(ref_path, labware)
    except Exception as exc:  # noqa: BLE001
        return {"pass": None, "codes": ["REFERENCE_SIM_FAILED"], "error": str(exc)[:200]}
    try:
        cand = delivered(cand_path, labware)
    except Exception as exc:  # noqa: BLE001
        return {"pass": False, "codes": ["SIM_FAILED"],
                "candidate_error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    return compare(ref, cand)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference")
    ap.add_argument("--candidate")
    ap.add_argument("--reference-dir")
    ap.add_argument("--candidate-dir")
    ap.add_argument("--protocols", default="1,2,3,4,5")
    ap.add_argument("--labware", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    lw = pathlib.Path(args.labware)

    if args.reference_dir and args.candidate_dir:
        out, fatal = {}, []
        for n in [p.strip() for p in args.protocols.split(",") if p.strip()]:
            name = "Template_278_OT_Protocol_%s.py" % n
            rp = pathlib.Path(args.reference_dir) / name
            cp = pathlib.Path(args.candidate_dir) / name
            if not rp.exists() or not cp.exists():
                out[name] = {"pass": None, "codes": ["NOT_RUN"]}
                continue
            out[name] = score_one(rp, cp, lw)
            fatal += out[name].get("codes", []) if not out[name].get("pass") else []
            v = out[name]
            print("  %-34s %s   %s/%s wells within 2%%"
                  % (name, "PASS" if v.get("pass") else
                     ("SKIP" if v.get("pass") is None else "FAIL " + ",".join(v.get("codes", []))),
                     v.get("matched_within_2pct", "?"), v.get("destinations_reference", "?")))
        judged = [v for v in out.values() if v.get("pass") is not None]
        r = {"pass": bool(judged) and all(v["pass"] for v in judged),
             "codes": sorted(set(fatal)), "protocols": out,
             "scored": len(judged), "passed": sum(1 for v in judged if v["pass"])}
        print("  %d/%d protocols deliver what the reference delivers" % (r["passed"], r["scored"]))
        if args.out:
            pathlib.Path(args.out).write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
        return 0 if r["pass"] else 1

    if not (args.reference and args.candidate):
        ap.error("give either --reference/--candidate or --reference-dir/--candidate-dir")
    r = score_one(pathlib.Path(args.reference), pathlib.Path(args.candidate), lw)
    print("  destinations: reference %s, candidate %s, matched within 2%%: %s"
          % (r.get("destinations_reference"), r.get("destinations_candidate"),
             r.get("matched_within_2pct")))
    print("  total uL    : reference %s, candidate %s"
          % (r.get("total_ul_reference"), r.get("total_ul_candidate")))
    print("  verdict     : %s" % ("PASS" if r.get("pass") else "FAIL " + ",".join(r["codes"])))
    if r.get("detail"):
        print("      %s" % r["detail"])
    for f in r.get("findings", [])[:6]:
        print("      %s" % json.dumps(f)[:170])
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    return 0 if r.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
