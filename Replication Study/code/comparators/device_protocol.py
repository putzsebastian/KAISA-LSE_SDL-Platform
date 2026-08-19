#!/usr/bin/env python3
"""M3 slice 3b — device-protocol comparator.

Simulates a parametrized Opentrons protocol, reduces the run log to a transfer ledger of
(source labware, source well, destination labware, destination well, volume), derives the
per-well net volume map, and compares both against a reference protocol.

Equivalence: the net volume map agrees within 0.5 uL AND the transfer multiset is equal.
Pipetting order, tip handling and mixing are recorded as benign variation, not failure.

Runs INSIDE the sdl-app container (needs opentrons 8.5.0).

Two things the run log makes non-obvious, both established by probing rather than assumed:

* Entries are {level, logs, payload} with NO commandType. Aspirate/dispense are identified by
  the payload signature (instrument + volume + location + rate) plus the text verb.
* A multi-channel command addresses the well's whole COLUMN, so its physical footprint depends on
  the labware: 8 wells on a 96-well plate, but a single trough on a 12-well reservoir (1 row).
  Volume per physical well is therefore volume * channels / len(column) — which yields the
  per-channel volume on a plate and the full 8-channel total in a trough.

Usage:
    python device_protocol.py --reference <ref.py> --candidate <cand.py> --labware <dir>
    python device_protocol.py --reference-dir <d> --candidate-dir <d> --labware <dir>
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import signal
import sys

TOL_UL = 0.5

TAXONOMY = {
    "SIM_FAILED": "the protocol did not simulate to completion",
    "WELL_MISMATCH": "a well appears in one net-volume map but not the other",
    "VOLUME_MISMATCH": "a well's net volume differs by more than the tolerance",
    "TRANSFER_MISSING": "a reference transfer has no counterpart in the candidate",
    "TRANSFER_EXTRA": "the candidate performs a transfer the reference does not",
    "MODULE_ACTION_MISSING": "a reference module action has no counterpart in the candidate",
    "MODULE_ACTION_EXTRA": "the candidate performs a module action the reference does not",
    "MODULE_PARAMETER_MISMATCH": "a module action is performed with a different parameter "
                                 "(speed, temperature or duration)",
}

# Module commands, unlike pipetting, reach the run log only as free text with no structured
# payload - so they are recognised by the exact phrasings the Opentrons simulator emits, which were
# read off a probe protocol rather than assumed. Without this a protocol that only shakes and
# incubates (isotherm protocol 4) has an empty transfer ledger on both sides and passes vacuously,
# no matter what speed or duration the candidate chose.
MODULE_PATTERNS = [
    (re.compile(r"^Latching labware on "), "latch_close", None),
    (re.compile(r"^Unlatching labware on "), "latch_open", None),
    (re.compile(r"^Setting Target Temperature of Heater-Shaker to ([\d.]+)"),
     "heater_shaker_temperature", "celsius"),
    (re.compile(r"^Waiting for Heater-Shaker to reach target temperature"),
     "heater_shaker_temperature_wait", None),
    (re.compile(r"^Setting Heater-Shaker to Shake at ([\d.]+) RPM"), "shake", "rpm"),
    (re.compile(r"^Deactivating Shaker"), "shake_off", None),
    (re.compile(r"^Deactivating Heater\b"), "heater_off", None),
    (re.compile(r"^Setting Temperature Module temperature to ([\d.]+)"),
     "temperature_module", "celsius"),
    (re.compile(r"^Deactivating Temperature Module"), "temperature_module_off", None),
    (re.compile(r"^Delaying for "), "delay", "seconds"),
    (re.compile(r"^Pausing robot operation"), "pause", None),
]

# Absolute tolerance per parameter; `delay` additionally allows 1 % of the reference duration, so a
# two-hour incubation is not failed over a rounding difference.
MODULE_TOL = {"rpm": 1.0, "celsius": 0.5, "seconds": 1.0}

# Idempotent module actions: doing one twice is indistinguishable from doing it once, because a
# latch that is already open does not become more open and a deactivated heater does not become
# more deactivated. These are compared as COVERAGE - each must appear at least once on both sides -
# never as a multiset. The published reference opens the latch defensively before it starts and
# closes it twice; a protocol that performs the minimal correct sequence was being failed for it.
# Parameterised actions (shake, delay, temperature) stay matched with tolerance: those carry meaning.
IDEMPOTENT = {"latch_open", "latch_close", "shake_off", "heater_off", "temperature_module_off",
              "heater_shaker_temperature_wait"}

# Latch position is enforced by the simulator wherever it physically matters: the Heater-Shaker
# refuses to shake with the latch open and refuses to open it while shaking, so a protocol that
# SIMULATES cleanly has every latch operation it actually needs. A reference that additionally
# opens the latch defensively at the start is doing something with no observable consequence, and
# a candidate that omits it is not wrong. Recorded, not fatal.
# Deactivations are deliberately NOT in this set: leaving a heater or shaker running at the end has
# a real consequence the simulator will not object to, so those stay fatal.
NON_FATAL_MODULE = {"latch_open", "latch_close"}

# Switching something off that was never switched on changes nothing, so an EXTRA deactivation is
# forgiven. A MISSING one is not: it leaves a heater or shaker running past the end of the protocol.
DEACTIVATIONS = {"shake_off", "heater_off", "temperature_module_off"}


def module_action(payload: dict):
    """(kind, parameter) for a module command, or None if the entry is not one.

    Protocol comments are indistinguishable from module commands in the log's structure, so the
    phrasing is the only signal available; every pattern here is one the simulator itself writes.
    """
    text = str(payload.get("text", ""))
    for pattern, kind, unit in MODULE_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        if unit is None:
            return kind, None
        if kind == "delay":  # the one command with a structured payload
            try:
                value = float(payload.get("minutes", 0)) * 60.0 + float(payload.get("seconds", 0))
            except (TypeError, ValueError):
                return kind, None
        elif payload.get("celsius") is not None and unit == "celsius":
            value = float(payload["celsius"])
        else:
            value = float(m.group(1))
        return kind, round(value, 3)
    return None


# --------------------------------------------------------------------------- run log
class SimulationTimeout(RuntimeError):
    """The protocol did not finish simulating inside the wall-clock bound."""


def simulate_protocol(path: pathlib.Path, labware_dir: pathlib.Path, timeout_s: int = 120):
    """Simulate, with a hard wall-clock bound.

    `protocol.delay()` is an instruction the simulator steps over instantly. `time.sleep()` is not:
    it blocks the interpreter, so a protocol that sleeps out a 60-minute incubation takes 60 real
    minutes to "simulate" and the same again on the robot. Three gpt-5-nano protocol-4 replicates do
    exactly that, and without a bound they stall scoring indefinitely rather than being scored.

    Timing out is therefore a genuine verdict about the protocol, not a harness inconvenience, and
    it is reported as such rather than being retried or skipped.
    """
    from opentrons.simulate import simulate

    # Refuse BEFORE simulating. A wall-clock sleep is a defect that inspection settles outright:
    # nothing needs to be executed to know that `time.sleep(3600)` stops the robot for an hour.
    # Detecting it statically is also the only thing that works - SIGALRM does not interrupt the
    # sleep, because the simulator runs the protocol off the main thread, so a timeout simply
    # never fires and scoring stalls indefinitely.
    src = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?m)^\s*(?:time\.)?sleep\s*\(", src) and "import time" in src:
        raise SimulationTimeout(
            "protocol calls time.sleep(): it blocks real time instead of using protocol.delay(), "
            "so the robot and the simulator both wait out the full duration")

    def _run():
        with open(path) as fh:
            runlog, _ = simulate(fh, str(path), custom_labware_paths=[str(labware_dir)])
        return runlog

    if not hasattr(signal, "SIGALRM"):          # not POSIX; run unbounded
        return _run()

    # kept as a backstop for anything else pathological, though it cannot interrupt a sleep
    def _fire(_signum, _frame):
        raise SimulationTimeout("simulation exceeded %ds" % timeout_s)

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.alarm(timeout_s)
    try:
        return _run()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def labware_key(well) -> str:
    """Stable labware identity: load name plus deck slot.

    Deliberately excludes the human-readable display name, so a reference and a candidate that load
    equivalent labware do not mismatch on cosmetics.

    The parent chain has to be walked, not sampled one level up. Labware on a module may sit
    directly on it (`module.load_labware(...)`) or on an adapter between the two
    (`module.load_adapter(...).load_labware(...)`), which is the physically real arrangement for the
    Heater-Shaker and what Opentrons documents. Both put the plate in the same deck slot, but a
    single-step lookup finds the adapter instead of the module, fails to read a slot, and makes
    every well of an otherwise identical protocol mismatch.
    """
    import re
    lw = well.parent
    node, slot = lw, None
    for _ in range(6):                      # labware -> adapter -> module -> deck
        parent = getattr(node, "parent", None)
        if parent is None:
            break
        if isinstance(parent, str):
            slot = parent
            break
        m = re.search(r"\bon (\w+?)(?: lw |$)|\bslot (\w+)", str(parent))
        if m:
            slot = m.group(1) or m.group(2)
            break
        node = parent
    return f"{getattr(lw, 'load_name', str(lw))}@{slot or '?'}"


def footprint(well, channels: int):
    """Physical wells touched, and the volume multiplier per well."""
    if channels <= 1:
        return [well], 1.0
    try:
        for column in well.parent.columns():
            if well in column:
                touched = column[:channels]
                if touched:
                    return touched, channels / len(touched)
    except Exception:  # noqa: BLE001
        pass
    return [well], float(channels)


def parse_runlog(runlog) -> dict:
    """Reduce a run log to a ledger, a net volume map, module actions and benign counters."""
    ledger: list[tuple] = []
    modules: list[tuple] = []
    net: dict[tuple[str, str], float] = collections.defaultdict(float)
    benign = collections.Counter()
    carry: dict[int, list] = collections.defaultdict(list)  # instrument id -> pending aspirates

    for entry in runlog:
        p = entry.get("payload") or {}
        text = str(p.get("text", ""))
        mod = module_action(p)
        if mod is not None:
            modules.append(mod)
            continue
        instr = p.get("instrument")
        if instr is None:
            continue
        channels = int(getattr(instr, "channels", 1) or 1)
        iid = id(instr)

        if text.startswith("Picking up tip"):
            benign["tip_pickup"] += 1
            continue
        if text.startswith("Dropping tip") or text.startswith("Returning tip"):
            benign["tip_drop"] += 1
            continue
        if text.startswith("Mixing"):
            benign["mix"] += 1
            continue
        if text.startswith("Blowing out") or text.startswith("Touching tip") or \
                text.startswith("Air gap"):
            benign["other_tip_action"] += 1
            continue
        if text.startswith("Transferring") or text.startswith("Distributing") or \
                text.startswith("Consolidating"):
            continue  # aggregate; the constituent aspirates/dispenses are logged separately

        vol = p.get("volume")
        loc = p.get("location")
        if vol is None or loc is None:
            continue
        well = getattr(loc, "labware", loc)
        well = getattr(well, "as_well", lambda: well)() if hasattr(well, "as_well") else well
        if not hasattr(well, "well_name"):
            continue

        wells, mult = footprint(well, channels)
        per_well = float(vol) * mult

        if text.startswith("Aspirating"):
            carry[iid].append((wells, per_well))
            for w in wells:
                net[(labware_key(w), w.well_name)] -= per_well
        elif text.startswith("Dispensing"):
            for w in wells:
                net[(labware_key(w), w.well_name)] += per_well
            if carry[iid]:
                src_wells, src_vol = carry[iid][-1]
                if abs(src_vol - per_well) < 1e-6:
                    carry[iid].pop()
                for sw, dw in zip(src_wells, wells) if len(src_wells) == len(wells) else \
                        [(src_wells[0], w) for w in wells]:
                    if labware_key(sw) == labware_key(dw) and sw.well_name == dw.well_name:
                        benign["self_transfer"] += 1
                        continue
                    ledger.append((labware_key(sw), sw.well_name,
                                   labware_key(dw), dw.well_name, round(per_well, 3)))
            else:
                benign["dispense_without_matching_aspirate"] += 1

    return {"ledger": ledger,
            "modules": modules,
            "net": {f"{k[0]}|{k[1]}": round(v, 4) for k, v in net.items() if abs(v) > 1e-9},
            "benign": dict(benign)}


def compare_modules(ref: list, cand: list) -> list:
    """Match module actions as a multiset, within tolerance, kind by kind.

    Ordering is treated as benign, as pipetting order is: what the protocol does to the modules is
    the substance, the sequence in which two deactivations are issued is not. Where both sides
    perform a kind the same number of times but with different parameters, that is reported as a
    parameter mismatch rather than as a missing plus an extra action, since it is the more
    informative statement of the same difference.
    """
    findings = []
    by_kind = lambda seq: {k: [p for kk, p in seq if kk == k]  # noqa: E731
                           for k in {kk for kk, _ in seq}}
    rk, ck = by_kind(ref), by_kind(cand)

    for kind in sorted(set(rk) | set(ck)):
        rvals, cvals = list(rk.get(kind, [])), list(ck.get(kind, []))
        unit = next((u for _, k, u in MODULE_PATTERNS if k == kind), None)
        tol = MODULE_TOL.get(unit, 0.0) if unit else 0.0

        if kind in IDEMPOTENT:
            # Coverage, not count: performed at least once on both sides is a match.
            if rvals and not cvals:
                findings.append({"code": "MODULE_ACTION_MISSING", "action": kind, "unit": unit,
                                 "detail": "the reference performs this and the candidate never "
                                           "does"})
            elif cvals and not rvals:
                # Deliberately asymmetric. FAILING to deactivate leaves a heater or shaker running
                # after the protocol ends, which the simulator will not object to and which matters
                # on real hardware - so a missing deactivation stays fatal above. Deactivating
                # something that was never switched on is a no-op, so an EXTRA one is recorded and
                # forgiven.
                findings.append({"code": "MODULE_ACTION_EXTRA", "action": kind, "unit": unit,
                                 "benign": kind in DEACTIVATIONS,
                                 "detail": "the candidate performs this and the reference never "
                                           "does"})
            continue

        unmatched_c = list(cvals)
        leftover_r = []
        for rv in rvals:
            hit = None
            for i, cv in enumerate(unmatched_c):
                if rv is None and cv is None:
                    hit = i
                    break
                if rv is not None and cv is not None:
                    allow = max(tol, abs(rv) * 0.01) if unit == "seconds" else tol
                    if abs(rv - cv) <= allow:
                        hit = i
                        break
            if hit is None:
                leftover_r.append(rv)
            else:
                unmatched_c.pop(hit)

        if leftover_r and unmatched_c and len(leftover_r) == len(unmatched_c):
            for rv, cv in zip(sorted(leftover_r, key=lambda x: (x is None, x)),
                              sorted(unmatched_c, key=lambda x: (x is None, x))):
                findings.append({"code": "MODULE_PARAMETER_MISMATCH", "action": kind,
                                 "unit": unit, "reference": rv, "candidate": cv})
            continue
        for rv in leftover_r:
            findings.append({"code": "MODULE_ACTION_MISSING", "action": kind,
                             "unit": unit, "reference": rv})
        for cv in unmatched_c:
            findings.append({"code": "MODULE_ACTION_EXTRA", "action": kind,
                             "unit": unit, "candidate": cv})
    return findings


# --------------------------------------------------------------------------- compare
def compare(ref: dict, cand: dict) -> dict:
    findings: list[dict] = []

    rn, cn = ref["net"], cand["net"]
    for key in sorted(set(rn) | set(cn)):
        rv, cv = rn.get(key), cn.get(key)
        if rv is None or cv is None:
            findings.append({"code": "WELL_MISMATCH", "well": key,
                             "reference": rv, "candidate": cv})
        elif abs(rv - cv) > TOL_UL:
            findings.append({"code": "VOLUME_MISMATCH", "well": key,
                             "reference": rv, "candidate": cv,
                             "delta": round(cv - rv, 4)})

    rc = collections.Counter(tuple(t) for t in ref["ledger"])
    cc = collections.Counter(tuple(t) for t in cand["ledger"])
    for t, n in (rc - cc).items():
        findings.append({"code": "TRANSFER_MISSING", "transfer": list(t), "count": n})
    for t, n in (cc - rc).items():
        findings.append({"code": "TRANSFER_EXTRA", "transfer": list(t), "count": n})

    findings.extend(compare_modules(ref.get("modules", []), cand.get("modules", [])))

    benign_delta = {k: cand["benign"].get(k, 0) - ref["benign"].get(k, 0)
                    for k in set(ref["benign"]) | set(cand["benign"])}
    fatal = [f for f in findings
             if f.get("action") not in NON_FATAL_MODULE and not f.get("benign")]
    return {
        "pass": not fatal,
        "codes": sorted({f["code"] for f in findings}),
        "fatal_codes": sorted({f["code"] for f in fatal}),
        "findings": findings[:40],
        "n_findings": len(findings),
        "benign_variation": {k: v for k, v in benign_delta.items() if v},
        "reference_transfers": len(ref["ledger"]),
        "candidate_transfers": len(cand["ledger"]),
        "reference_wells": len(ref["net"]),
        "candidate_wells": len(cand["net"]),
        "reference_modules": [[k, v] for k, v in ref.get("modules", [])],
        "candidate_modules": [[k, v] for k, v in cand.get("modules", [])],
    }


def analyse(path: pathlib.Path, labware: pathlib.Path):
    try:
        return parse_runlog(simulate_protocol(path, labware)), None
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        for marker in ("detail='", 'detail="'):
            if marker in detail:
                detail = detail.split(marker, 1)[1].split("'")[0]
                break
        return None, detail[:200]


def run_pair(ref_path, cand_path, labware):
    ref, ref_err = analyse(ref_path, labware)
    cand, cand_err = analyse(cand_path, labware)
    if ref_err or cand_err:
        return {"pass": False, "codes": ["SIM_FAILED"],
                "reference_error": ref_err, "candidate_error": cand_err}
    return compare(ref, cand)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference")
    ap.add_argument("--candidate")
    ap.add_argument("--reference-dir")
    ap.add_argument("--candidate-dir")
    ap.add_argument("--labware", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    labware = pathlib.Path(args.labware)

    results = {}
    if args.reference_dir:
        rd, cd = pathlib.Path(args.reference_dir), pathlib.Path(args.candidate_dir)
        pairs = [(f, cd / f.name) for f in sorted(rd.glob("Template_*_OT_Protocol_*.py"))]
    else:
        pairs = [(pathlib.Path(args.reference), pathlib.Path(args.candidate))]

    worst_pass = True
    for ref_path, cand_path in pairs:
        if not cand_path.exists():
            results[ref_path.name] = {"pass": False, "codes": ["SIM_FAILED"],
                                      "candidate_error": "candidate file missing"}
            worst_pass = False
            continue
        r = run_pair(ref_path, cand_path, labware)
        results[ref_path.name] = r
        worst_pass &= r["pass"]
        status = "PASS" if r["pass"] else "FAIL " + ",".join(r["codes"])
        extra = ""
        if r.get("reference_transfers") is not None:
            extra = (f"  transfers ref={r['reference_transfers']} cand={r['candidate_transfers']}"
                     f"  wells ref={r['reference_wells']} cand={r['candidate_wells']}"
                     f"  modules ref={len(r['reference_modules'])} "
                     f"cand={len(r['candidate_modules'])}")
        print(f"  {ref_path.name:<32} {status}{extra}")
        for f in (r.get("findings") or [])[:4]:
            print(f"       {json.dumps(f)[:150]}")
        if r.get("benign_variation"):
            print(f"       benign: {r['benign_variation']}")

    print(f"\noverall: {'PASS' if worst_pass else 'FAIL'}")
    if args.out:
        codes = sorted({c for r in results.values() for c in r.get("codes", [])})
        pathlib.Path(args.out).write_text(
            json.dumps({"pass": worst_pass, "codes": codes,
                        "protocols": results}, indent=2) + "\n",
            encoding="utf-8")
    return 0 if worst_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
