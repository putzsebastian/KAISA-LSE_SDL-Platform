#!/usr/bin/env python3
"""Classify every failing replicate in the bundle.

A pass rate says how often an agent was wrong; it says nothing about HOW, and the before/after
claims in STATISTICS section 5 are only credible if the categories they moved are named.

Classes describe what the artifact would do to an experiment, not which comparator code fired,
because several codes describe the same underlying mistake.

    python failure_taxonomy.py [--data DIR] [--out FAILURES.md]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent

# Running inside the published bundle this script sits in code/analysis/ and its outputs belong in
# results/ at the bundle root; in the working tree there is no such directory and they stay beside
# the script. A reproduction run therefore refreshes exactly the files it is checking.
OUT = HERE.parent.parent / "results"
if not OUT.is_dir():
    OUT = HERE

CODE_CLASS = {
    "SIM_FAILED": ("does_not_run", "protocol raises during simulation"),
    "SIM_TIMEOUT": ("blocks_real_time", "blocks the instrument in wall-clock time"),
    "EXEC_FAILED": ("does_not_run", "script exits non-zero"),
    "SCRIPT_FAILED": ("does_not_run", "script exits non-zero"),
    "SYNTAX_ERROR": ("does_not_run", "generated code does not parse"),
    "MALFORMED_TEMPLATE": ("does_not_run", "template is not parseable"),
    "DEST_VOLUME_MULTIPLE": ("wrong_quantity", "destination receives an integer multiple"),
    "DEST_VOLUME_MISMATCH": ("wrong_quantity", "destination receives a different volume"),
    "VOLUME_MISMATCH": ("wrong_quantity", "transfer volume differs"),
    "FIT_OUT_OF_TOLERANCE": ("wrong_quantity", "fitted parameter outside tolerance"),
    "PEAK_HEIGHT_MISMATCH": ("wrong_quantity", "peak height differs"),
    "RETENTION_TIME_MISMATCH": ("wrong_quantity", "peak found at the wrong retention time"),
    "PARAMETER_VALUE_WRONG": ("wrong_quantity", "stored parameter value differs"),
    "DEST_MISSING": ("wrong_target", "a destination the reference fills is never filled"),
    "DEST_EXTRA": ("wrong_target", "liquid delivered to a destination the reference does not use"),
    "WELL_MISMATCH": ("wrong_target", "wrong wells addressed"),
    "TRANSFER_MISSING": ("wrong_target", "a transfer is absent"),
    "TRANSFER_EXTRA": ("wrong_target", "an extra transfer is performed"),
    "DECK_MISMATCH": ("wrong_target", "labware on an undeclared slot or an invented adapter"),
    "DECK_LABWARE_WRONG": ("wrong_target", "declared slot holds different labware"),
    "MODULE_ACTION_MISSING": ("wrong_target", "a module action the reference performs is absent"),
    "MISSING_OUTPUT": ("silent_omission", "reports success, produces no required output"),
    "NO_PEAKS_DETECTED": ("silent_omission", "no peaks reported where the data contains one"),
    "PLOT_NOT_PRODUCED": ("silent_omission", "required plot never written"),
    "REQUESTED_SECTION_MISSING": ("silent_omission", "a section the prompt asks for is absent"),
    "PLOT_NOT_EMBEDDED": ("silent_omission", "plot produced but not placed in the template"),
    "FIELD_MAPPING_MISSING_FIELD": ("silent_omission", "a stored parameter is dropped"),
    "PARAMETER_NOT_STORED": ("silent_omission", "a parameter reaches no column"),
    "PLACEHOLDER_NOT_AVAILABLE": ("unusable_reference",
                                  "invents a placeholder nothing will substitute"),
    "FIELD_MAPPING_UNKNOWN_FIELD": ("unusable_reference", "maps a field eLabFTW does not declare"),
    "FIELD_MAPPING_NO_SUCH_COLUMN": ("unusable_reference",
                                     "maps to a column the schema does not declare"),
    "UNIT_MISMATCH": ("data_loss", "unit contradicts the one eLabFTW declares"),
}

SEVERITY = ["does_not_run", "blocks_real_time", "silent_omission", "wrong_target",
            "wrong_quantity", "unusable_reference", "data_loss"]

MEANING = {
    "does_not_run": "raises before finishing — a crash, a syntax error, an exhausted resource",
    "blocks_real_time": "runs, but halts the instrument in wall-clock time",
    "silent_omission": "reports success and leaves a required output missing",
    "wrong_target": "runs, and acts on the wrong wells or labware",
    "wrong_quantity": "runs, and moves or records the wrong amount",
    "unusable_reference": "emits a name nothing will resolve",
    "data_loss": "stores a value in a form that discards information",
    "other": "unclassified",
}

# Signatures worth naming rather than reducing to an exception class, because "RuntimeError" says
# nothing about what the agent got wrong.
SIGNATURES = [
    (r"Token \"NaN\" is invalid", "writes NaN into a JSON column; Postgres rejects it"),
    (r"OutOfTipsError", "runs out of tips mid-protocol"),
    (r"Invalid source for multichannel transfer",
     "multi-channel transfer starting outside row A"),
    (r"labware latch", "moves or shakes with the Heater-Shaker latch open"),
    (r"Cannot aspirate .* when only .* available in the tip", "aspirates more than the tip holds"),
    (r"Not enough volume|insufficient volume|No reservoir well has enough",
     "exhausts a reagent pool instead of splitting across wells"),
    (r"not found with version", "custom labware definition unavailable"),
    (r"InvalidTargetSpeedError", "sets the shaker to a speed outside its range"),
    (r"time\.sleep|blocks real time",
     "calls time.sleep() instead of protocol.delay(), idling the robot for the full incubation"),
    (r"is missing", "misreads the plate layout"),
    (r"SyntaxError", "generated code does not parse"),
]


def classify(codes):
    found = {CODE_CLASS[c][0] for c in codes if c in CODE_CLASS}
    for cls in SEVERITY:
        if cls in found:
            return cls
    return "other"


def detail_for(codes, error, multiples):
    if multiples:
        return "delivers %sx the intended volume" % max(int(m) for m in multiples)
    if error:
        for pat, text in SIGNATURES:
            if re.search(pat, str(error)):
                return text
        m = re.search(r"(\w+Error)\b", str(error))
        if m:
            return m.group(1)
    for c in codes:
        if c in CODE_CLASS:
            return CODE_CLASS[c][1]
    return ", ".join(codes) or "unclassified"


# (series label, path under data/). `model_comparison/*/gpt-5.1/` is deliberately absent: it is the
# same output as isotherm/device_protocol_{1,4}, because the model arm reuses the main run as its
# gpt-5.1 cell, and would otherwise be counted twice. The other model cells ARE included, so the
# taxonomy covers every model measured; a defect that appears solely in the weakest model is still
# worth naming.
SERIES = (
    [("isotherm / %s" % a, "isotherm/%s" % a)
     for a in ("orchestration", "analysis", "report", "database")]
    + [("FPLC / %s" % a, "fplc_gradient/%s" % a)
       for a in ("orchestration", "analysis", "report", "database")]
    + [("isotherm / device protocol %d" % i, "isotherm/device_protocol_%d" % i)
       for i in range(1, 6)]
    + [("%s / protocol %d" % (m, p), "model_comparison/protocol_%d/%s" % (p, m))
       for p in (1, 4) for m in ("gpt-5-nano", "gpt-5-mini", "gpt-5.5")]
)


def sleeps(rep: pathlib.Path):
    """A wall-clock sleep is settled by inspecting the source; no execution is needed, and the
    comparator refuses to simulate it, so the error text alone would not say what happened."""
    f = rep / "protocol.py"
    if not f.exists():
        return False
    src = f.read_text(encoding="utf-8", errors="replace")
    return "import time" in src and bool(re.search(r"(?m)^\s*(?:time\.)?sleep\s*\(", src))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE.parent.parent / "data"))
    ap.add_argument("--out", default=str(OUT / "FAILURES.md"))
    args = ap.parse_args()

    data = pathlib.Path(args.data)
    rows = []
    for label, rel in SERIES:
        for rep in sorted((data / rel).glob("replicate_*")):
            try:
                s = json.loads((rep / "score.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if s.get("pass") is None or s.get("pass"):
                continue
            codes = s.get("fatal_codes") or s.get("codes") or []
            # comparators record the candidate's own error under three different keys
            err = s.get("candidate_error") or s.get("error") or ""
            if not err:
                for f in s.get("findings") or []:
                    if isinstance(f, dict):
                        err = f.get("candidate_error") or f.get("error") or f.get("detail") or ""
                        if err:
                            break
            if sleeps(rep):
                codes = [c for c in codes if c != "SIM_FAILED"] + ["SIM_TIMEOUT"]
                err = "time.sleep"
            rows.append({"series": label, "replicate": rep.name.replace("replicate_", ""),
                         "codes": codes, "class": classify(codes),
                         "detail": detail_for(codes, err, s.get("systematic_multiples"))})

    L = ["# Failure taxonomy\n",
         "Every failing replicate in the bundle, classified by what the artifact would do to an "
         "experiment rather than by which comparator code fired — several codes describe the same "
         "underlying mistake. Where a replicate raised more than one code the most severe class is "
         "reported, since nothing downstream of a crash was observed.\n"]

    by_class = collections.Counter(r["class"] for r in rows)
    L.append("\n## Summary\n")
    L.append("| class | failures | what it means |")
    L.append("|---|---|---|")
    for cls, k in by_class.most_common():
        L.append("| `%s` | %d | %s |" % (cls, k, MEANING.get(cls, "")))
    L.append("| **total** | **%d** | |" % len(rows))

    L.append("\n## By series\n")
    L.append("| series | failures | classes |")
    L.append("|---|---|---|")
    per = collections.defaultdict(list)
    for r in rows:
        per[r["series"]].append(r)
    for s in sorted(per):
        c = collections.Counter(r["class"] for r in per[s])
        L.append("| %s | %d | %s |" % (s, len(per[s]),
                                       ", ".join("%s x%d" % (k, v) for k, v in c.most_common())))

    L.append("\n## Every failure\n")
    L.append("| series | rep | class | what went wrong | codes |")
    L.append("|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: (x["series"], x["replicate"])):
        L.append("| %s | %s | `%s` | %s | %s |"
                 % (r["series"], r["replicate"], r["class"], r["detail"],
                    ", ".join("`%s`" % c for c in r["codes"]) or "—"))

    pathlib.Path(args.out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:14]))
    print("\nwrote %s (%d failures)" % (args.out, len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
