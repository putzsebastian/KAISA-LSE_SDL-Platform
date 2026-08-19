#!/usr/bin/env python3
"""M3 slice 3c — orchestration comparator.

Two checks, per the milestone definition:

  * every device endpoint named in the workflow JSON is actually driven by the generated main.py
  * the step sequence matches the reference

The device call surface is data, not logic, so a second lab (168 / AKTA) is a table entry rather
than a code change. Calls are collected from the AST in source order; the isotherm main.py keeps
its device work linear inside one function, so source order is execution order up to loops, and
loops are compared identically on both sides.

Connection lifecycle calls are deliberately NOT part of the ordered sequence. Whether a script tears
a device down once, or twice because it repeats the block in an error path, or once behind an
`if dev is not None:` guard, says nothing about the experiment it runs - and comparing those as a
positioned multiset made every replicate in the pilot fail for that reason alone. What matters is
coverage: a device the reference disconnects must be disconnected. Extra teardowns of devices this
workflow never uses are recorded, not failed, since generated scripts commonly pre-initialise the
whole lab roster to None and guard each call.

Taxonomy: ENDPOINT_MISSING, SEQUENCE_MISMATCH, TEARDOWN_MISSING, EXEC_FAILED (unparseable main.py)
Recorded but not fatal: TEARDOWN_EXTRA

Usage:
    python orchestration.py --workflow <workflow.json> --reference <main.py> --candidate <main.py>
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys

# device in workflow.json -> how it appears in the generated script.
# ("attr", "<object>")  -> any call on that object, e.g. ot2.Run_Protocol(...)
# ("func", "<name>")    -> a bare function call, e.g. Grab(...)
CALL_SURFACE = {
    "Opentrons OT-2": [("attr", "ot2"), ("attr", "opentrons")],
    "Opentrons Flex": [("attr", "ot2"), ("attr", "flex"), ("attr", "opentrons")],
    "Tecan Spark": [("attr", "tecan")],
    "VacuuPump": [("attr", "vacuupump"), ("attr", "vacuu")],
    "UR5": [("attr", "ur5"), ("func", "Grab"), ("func", "Place"),
            ("func", "Place_Vacuum_Manifold"), ("func", "Remove_Vacuum_Manifold")],
    "UR10": [("attr", "ur10")],
    "AKTA Pure": [("attr", "akta")],
    "Zetasizer": [("attr", "zs"), ("attr", "zetasizer")],
    "Utility": [("func", "wait"), ("func", "pause_for_user")],
}

# Connection setup/teardown. Compared as coverage, never as an ordered multiset (see module
# docstring). Measurement-scoped open/close - tecan.open_device / close_device around a read - is
# deliberately NOT here: that is part of what the experiment does, and stays in the ordered sequence.
LIFECYCLE_METHODS = {"disconnect", "Disconnect_Robot", "Disconnect_Pump", "Reconnect_Robot",
                     "connect", "load_positions"}

NON_FATAL = {"TEARDOWN_EXTRA"}


def split_lifecycle(tokens: list[str]):
    """(operational tokens in order, {object: {lifecycle methods}})."""
    operational, lifecycle = [], {}
    for tok in tokens:
        obj, _, meth = tok.partition(".")
        if meth in LIFECYCLE_METHODS:
            lifecycle.setdefault(obj, set()).add(meth)
        else:
            operational.append(tok)
    return operational, lifecycle


def call_sequence(path: pathlib.Path):
    """Ordered call tokens, e.g. 'ot2.Run_Protocol' or 'Grab'.

    Tokens are the object/function actually called, not a device label: several devices share a
    call object (`ot2` serves both OT-2 and Flex), so labelling here would be ambiguous. Device
    coverage is resolved separately, against the workflow's own device list.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    attrs = {n for entries in CALL_SURFACE.values() for k, n in entries if k == "attr"}
    funcs = {n for entries in CALL_SURFACE.values() for k, n in entries if k == "func"}

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id in attrs:
            found.append((node.lineno, f"{fn.value.id}.{fn.attr}"))
        elif isinstance(fn, ast.Name) and fn.id in funcs:
            found.append((node.lineno, fn.id))
    found.sort(key=lambda t: t[0])
    return [tok for _, tok in found]


def is_driven(device: str, tokens: list[str]) -> bool:
    """Did the script exercise this workflow device at all?"""
    for kind, name in CALL_SURFACE.get(device, []):
        prefix = f"{name}." if kind == "attr" else None
        for tok in tokens:
            if prefix and tok.startswith(prefix):
                return True
            if prefix is None and tok == name:
                return True
    return False


def workflow_devices(path: pathlib.Path):
    wf = json.loads(path.read_text(encoding="utf-8"))
    content = wf.get("workflow", wf).get("content", [])
    devices, steps = [], []
    for s in content:
        if not isinstance(s, dict) or not s.get("device"):
            continue
        steps.append((s["device"], s.get("method", "")))
        if s["device"] not in devices:
            devices.append(s["device"])
    return devices, steps


def compare(workflow: pathlib.Path, reference: pathlib.Path, candidate: pathlib.Path) -> dict:
    devices, wf_steps = workflow_devices(workflow)
    try:
        ref_seq = call_sequence(reference)
    except SyntaxError as e:
        return {"pass": False, "codes": ["EXEC_FAILED"], "findings": [{"reference": str(e)}]}
    try:
        cand_seq = call_sequence(candidate)
    except SyntaxError as e:
        return {"pass": False, "codes": ["EXEC_FAILED"], "findings": [{"candidate": str(e)}]}

    findings = []
    driven = [d for d in devices if is_driven(d, cand_seq)]
    for device in devices:
        if device not in driven:
            findings.append({"code": "ENDPOINT_MISSING", "device": device,
                             "detail": "named in workflow.json but never driven by the script"})

    ref_ops, ref_life = split_lifecycle(ref_seq)
    cand_ops, cand_life = split_lifecycle(cand_seq)

    if ref_ops != cand_ops:
        import difflib
        diff = [x for x in difflib.unified_diff(ref_ops, cand_ops, "reference", "candidate",
                                                lineterm="", n=0)
                if x.startswith(("+", "-")) and not x.startswith(("+++", "---"))]
        findings.append({"code": "SEQUENCE_MISMATCH",
                         "reference_calls": len(ref_ops), "candidate_calls": len(cand_ops),
                         "diff": diff[:12]})

    for obj in sorted(ref_life):
        missing = ref_life[obj] - cand_life.get(obj, set())
        if missing:
            findings.append({"code": "TEARDOWN_MISSING", "object": obj,
                             "methods": sorted(missing),
                             "detail": "the reference closes this connection and the candidate "
                                       "never does"})
    extra = sorted(set(cand_life) - set(ref_life))
    if extra:
        findings.append({"code": "TEARDOWN_EXTRA", "objects": extra,
                         "detail": "teardown for devices this workflow does not use; generated "
                                   "scripts commonly guard these with `if dev is not None`"})

    fatal = [f for f in findings if f["code"] not in NON_FATAL]
    return {
        "pass": not fatal,
        "codes": sorted({f["code"] for f in findings}),
        "fatal_codes": sorted({f["code"] for f in fatal}),
        "findings": findings,
        "workflow_devices": devices,
        "workflow_steps": len(wf_steps),
        "reference_calls": len(ref_ops),
        "candidate_calls": len(cand_ops),
        "reference_lifecycle": {k: sorted(v) for k, v in sorted(ref_life.items())},
        "candidate_lifecycle": {k: sorted(v) for k, v in sorted(cand_life.items())},
        "devices_driven": sorted(driven),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    r = compare(pathlib.Path(args.workflow), pathlib.Path(args.reference),
                pathlib.Path(args.candidate))
    print(f"  workflow devices : {r.get('workflow_devices')}")
    print(f"  driven by script : {r.get('devices_driven')}")
    print(f"  operational calls: reference {r.get('reference_calls')}, "
          f"candidate {r.get('candidate_calls')}")
    print(f"  teardown  ref    : {r.get('reference_lifecycle')}")
    print(f"  teardown  cand   : {r.get('candidate_lifecycle')}")
    print(f"  verdict          : "
          f"{'PASS' if r['pass'] else 'FAIL ' + ','.join(r.get('fatal_codes', []))}")
    for f in r["findings"][:4]:
        print(f"      {json.dumps(f)[:220]}")
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
