#!/usr/bin/env python3
"""M3 slice 3c -- analysis comparator.

Executes the candidate analysis script against the reference raw data and compares its fitted
outputs to the published values within tolerance.

Runs INSIDE the sdl-app container. Two things about the published script that matter for staging,
both found by running it rather than reading it:

  * it reads the Tecan workbook from the RESULTS folder, not the data folder
    (`tecan_data_path = results_folder_path / f'tecan_data_{id}.xlsx'`), and its device-server
    fetch is commented out -- so the workbook must be placed in the results folder
  * it resolves the experiment JSON from the experiment root (`../experiment_<id>.json`)

Tolerances are deliberately loose: the question is whether a replicate's script does substantively
the same thing, not whether it reproduces the same floating-point noise. Defaults are 1 % relative
on qmax and K and 0.01 absolute on r2; override with --rel-tol / --r2-tol.

Taxonomy: EXEC_FAILED, MISSING_OUTPUT, FIT_OUT_OF_TOLERANCE, CONDITION_MISMATCH

Usage:
    python analysis.py --experiment <staged exp dir> --script <analysis.py> \
                       --tecan <xlsx> --reference-results <analysis_results_<id>.json> --exp-id 5016
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

from device_stub import DeviceDataStub, classify_route, env_for

# Column/key spellings seen for each fitted quantity. The published script writes a `fits_by_salt`
# block into the results JSON; a replicate may compute exactly the same fits and serialise them
# only as a CSV table. Which file carries them is a presentation choice, so both are accepted.
FIT_ALIASES = {
    "salt": ("salt_concentration", "salt", "salt_conc", "salt_mm", "nacl", "nacl_concentration",
             "c_salt", "cs", "c_s"),
    "qmax": ("qmax", "q_max", "qm", "q_maximum"),
    # k_a and k_d turn up as often as k; a Langmuir affinity constant gets many names.
    "K": ("k", "k_l", "kl", "k_langmuir", "k_a", "ka", "k_d", "kd", "k_ads", "b", "affinity"),
    "r2": ("r2", "r_squared", "rsq", "r2_score", "rsquared", "coefficient_of_determination"),
    "n_points": ("n_points", "npoints", "n_conc", "n"),
}
# Never treat these as the salt: they are positional, or describe the salt rather than being it.
SALT_EXCLUDE = ("unit", "stock", "name", "index", "idx", "label", "id")


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _isnum(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _pick(cols, key):
    wanted = {_norm(a) for a in FIT_ALIASES[key]}
    for c in cols:
        if _norm(c) in wanted:
            return c
    if key == "salt":
        # Generated scripts name this column almost anything - salt_conc, Salt_conc,
        # salt_concentration, c_salt, cs - and an exact alias list silently loses whole fit tables,
        # reporting MISSING_OUTPUT for a script that computed them correctly. Any column naming
        # salt is accepted, except ones that are positional or describe it rather than being it.
        for c in cols:
            n = _norm(c)
            if "salt" in n and not any(x in n for x in SALT_EXCLUDE):
                return c
    return None


def _salt_of(entry: dict, exclude: set):
    """The salt value of a fit record whose column name we could not recognise.

    Once qmax, K and r2 have been identified, a record that still has exactly one unclaimed numeric
    field is unambiguous: that field is the condition the fit belongs to. This is what stops the
    comparator from needing an ever-growing list of names for the same quantity.
    """
    named = _pick(list(entry), "salt")
    if named:
        return entry.get(named)
    spare = [k for k, v in entry.items()
             if k not in exclude and _isnum(v)
             and not any(x in _norm(k) for x in SALT_EXCLUDE)]
    return entry[spare[0]] if len(spare) == 1 else None


def salt_key(value) -> str:
    """One spelling for a salt concentration, so "0" and "0.0" are the same condition."""
    return f"{float(value):.1f}"


def _entry_from(obj: dict):
    """(fit entry, claimed column names), or (None, set()) if all three quantities are not present.

    Requiring qmax, K and r2 together is what keeps this permissive matching safe: a table of
    aggregated points or per-well values cannot satisfy it, so only real fit tables are accepted.
    """
    cols = list(obj)
    cq, ck, cr = (_pick(cols, k) for k in ("qmax", "K", "r2"))
    if not (cq and ck and cr):
        return None, set()
    try:
        out = {"qmax": float(obj[cq]), "K": float(obj[ck]), "r2": float(obj[cr])}
    except (TypeError, ValueError):
        return None, set()
    claimed = {cq, ck, cr}
    cn = _pick(cols, "n_points")
    if cn and obj.get(cn) is not None:
        claimed.add(cn)
        try:
            out["n_points"] = int(float(obj[cn]))
        except (TypeError, ValueError):
            pass
    return out, claimed


def fits_from_results(results: dict):
    """Find a salt-keyed fit table anywhere in the results document.

    `fits_by_salt` is the published script's name for it; generated scripts also use `fit_results`,
    `fit_parameters` and others, either as a mapping keyed by salt or as a list of per-salt records.
    Only the name varies, so every top-level value is examined rather than a fixed key being
    required - otherwise a script that computed the fits correctly is reported as producing none.
    """
    def as_fits(value):
        if isinstance(value, dict) and value:
            got = {}
            for salt, entry in value.items():
                if not isinstance(entry, dict):
                    return None
                e, claimed = _entry_from(entry)
                if not e:
                    return None
                # The mapping key is usually the salt concentration, but scripts also key by
                # position - "salt_0", "salt_1" - and carry the concentration inside the entry.
                try:
                    got[salt_key(salt)] = e
                    continue
                except (TypeError, ValueError):
                    pass
                sv = _salt_of(entry, claimed)
                try:
                    got[salt_key(sv)] = e
                except (TypeError, ValueError):
                    return None
            return got or None
        if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
            got = {}
            for entry in value:
                e, claimed = _entry_from(entry)
                if not e:
                    return None
                try:
                    got[salt_key(_salt_of(entry, claimed))] = e
                except (TypeError, ValueError):
                    return None
            return got or None
        return None

    # The document may BE the table - a langmuir_fit_params_<id>.json whose top level is already
    # keyed by salt. Checked first, because scanning its values instead would look at individual
    # fit records and find nothing. Safe for the main results file, whose top level carries status
    # and message rather than fit records.
    whole = as_fits(results)
    if whole:
        return whole, "results_json"

    # One level of nesting is searched as well: scripts commonly file the fit table under
    # data_outputs or results rather than at the top level.
    for key, value in (results or {}).items():
        got = as_fits(value)
        if got:
            return got, f"results_json:{key}"
    for key, value in (results or {}).items():
        if isinstance(value, dict):
            for sub, subval in value.items():
                got = as_fits(subval)
                if got:
                    return got, f"results_json:{key}.{sub}"
    return None, None


def recover_fits(results_dir: pathlib.Path):
    """Find the Langmuir fits wherever the script wrote them.

    Returns (fits_by_salt, carrier filename) or (None, None). Only tables that carry all three
    fitted quantities are accepted, so an intermediate file of aggregated points is not mistaken
    for a fit table.
    """
    for path in sorted(results_dir.glob("*.csv")):
        try:
            with path.open(encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except Exception:  # noqa: BLE001
            continue
        if not rows:
            continue
        fits = {}
        for row in rows:
            entry, claimed = _entry_from(row)
            if not entry:
                break
            try:
                fits[salt_key(_salt_of(row, claimed))] = entry
            except (TypeError, ValueError):
                continue
        if fits:
            return fits, path.name

    # Scripts also write the fit table as its own JSON file beside the results - a
    # langmuir_fit_params_<id>.json sitting next to analysis_results_<id>.json. Same document
    # shapes as the results file, so the same reader handles it.
    for path in sorted(results_dir.glob("*.json")):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(blob, (dict, list)):
            continue
        got, _ = fits_from_results(blob if isinstance(blob, dict) else {"_": blob})
        if got:
            return got, path.name
    return None, None


def run_script(script: pathlib.Path, experiment_dir: pathlib.Path, tecan: pathlib.Path,
               exp_id: str, workdir: pathlib.Path, serve: bool = True, port: int = 8000):
    """Run the analysis script and return (results_dict, error, route).

    A generated script is expected to FETCH its workbook from the device control server; that is
    correct behaviour, and off the lab network it would fail and the script would skip the
    analysis. So the reference workbook is offered by every route a script has been seen to use:
    over the device-server HTTP contract (see device_stub.py), and pre-placed in both the data and
    results folders. Whichever route the candidate takes, it gets the same reference data, and the
    route it chose is recorded.
    """
    results = workdir / "results"
    results.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tecan, results / f"tecan_data_{exp_id}.xlsx")

    analysis_dir = workdir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    staged = analysis_dir / script.name
    shutil.copy2(script, staged)
    for name in (f"experiment_{exp_id}.json",):
        src = experiment_dir / name
        if src.exists():
            shutil.copy2(src, workdir / name)
    data_dir = workdir / "data"
    data_dir.mkdir(exist_ok=True)
    if (experiment_dir / "data").is_dir():
        for f in (experiment_dir / "data").glob("*"):
            if f.is_file():
                shutil.copy2(f, data_dir / f.name)
    shutil.copy2(tecan, data_dir / f"tecan_data_{exp_id}.xlsx")

    env = dict(os.environ)
    cmd = [sys.executable, staged.name, str(exp_id),
           "--data-folder", "../data", "--results-folder", str(results)]

    def _execute():
        return subprocess.run(cmd, cwd=str(analysis_dir), capture_output=True,
                              text=True, timeout=900, env=env)

    if serve:
        with DeviceDataStub(tecan, port=port) as stub:
            env.update(env_for(stub.url))
            proc = _execute()
            hits, unknown = stub.hits, stub.unknown
    else:
        proc, hits, unknown = _execute(), [], []

    produced = sorted(p.name for p in results.iterdir()) if results.is_dir() else []
    # The prompt's step 5 asks for a plot of all isotherms. Whether one was produced is checked
    # directly rather than inferred, since a script can report success and draw nothing.
    meta = {"undefined_endpoints": sorted(set(unknown)), "fits_carrier": None,
            "results_carrier": None, "results_dir": produced,
            "plots_produced": [n for n in produced
                               if n.lower().endswith((".png", ".pdf", ".svg", ".jpg"))]}
    if proc.returncode != 0:
        return None, f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[-600:]}", "none", meta

    out = results / f"analysis_results_{exp_id}.json"
    if out.exists():
        meta["results_carrier"] = out.name
    else:
        # Exit 0 without the expected filename. The name is a convention, not the analysis, so any
        # other JSON carrying results is accepted and the deviation recorded - the same treatment
        # the fits themselves get below.
        for cand in sorted(results.glob("*.json")):
            try:
                blob = json.loads(cand.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if isinstance(blob, dict) and (blob.get("fits_by_salt") or blob.get("status")):
                out, meta["results_carrier"] = cand, cand.name
                break
    if meta["results_carrier"] is None:
        return None, ("no analysis results JSON produced; exit 0. "
                      f"results dir holds {produced}. "
                      f"stdout tail: {(proc.stdout or '')[-500:]}"), "none", meta
    parsed = json.loads(out.read_text(encoding="utf-8"))
    meta["fits_carrier"] = "results_json"
    if not parsed.get("fits_by_salt"):
        # Look inside the results document first - a differently-named key is the commonest case -
        # then fall back to any fit table written beside it.
        recovered, carrier = fits_from_results(parsed)
        if not recovered:
            recovered, carrier = recover_fits(results)
        if recovered:
            parsed["fits_by_salt"] = recovered
            meta["fits_carrier"] = carrier
        else:
            meta["fits_carrier"] = None
    meta["results_dir"] = sorted(p.name for p in results.iterdir())
    meta["plots_produced"] = [n for n in meta["results_dir"]
                              if n.lower().endswith((".png", ".pdf", ".svg", ".jpg"))]
    return parsed, None, classify_route(hits, parsed), meta


def compare_fits(ref: dict, cand: dict, rel_tol: float, r2_tol: float,
                 abs_floor: float = 1e-6) -> dict:
    """Compare fitted outputs.

    `abs_floor` guards against a relative tolerance on a physically-zero quantity. Where a
    condition shows no measurable binding - 500 mM ovalbumin, for instance - the Langmuir fit is
    degenerate: qmax comes out at ~1e-12 and r2 goes negative, meaning the fit is worse than a
    horizontal line. Two such fits can differ by 99 % relatively while being physically identical,
    so a condition that is degenerate on BOTH sides is recorded as such and excluded from the
    verdict rather than failed. A condition degenerate on only one side is still a real difference
    and is reported.
    """
    findings, degenerate = [], []
    # Salt keys are normalised on both sides, so "0" and "0.0" are one condition rather than two.
    rf = {salt_key(k): v for k, v in (ref.get("fits_by_salt") or {}).items()}
    cf = {salt_key(k): v for k, v in (cand.get("fits_by_salt") or {}).items()}

    if not cf:
        findings.append({"code": "MISSING_OUTPUT",
                         "detail": "candidate produced no Langmuir fits, in the results JSON or "
                                   "any results-folder table",
                         "candidate_status": cand.get("status"),
                         "candidate_message": cand.get("message")})
        return {"pass": False, "codes": ["MISSING_OUTPUT"], "findings": findings}

    missing = sorted(set(rf) - set(cf))
    extra = sorted(set(cf) - set(rf))
    if missing or extra:
        findings.append({"code": "CONDITION_MISMATCH",
                         "salt_conditions_missing": missing, "unexpected": extra})

    for salt in sorted(set(rf) & set(cf), key=float):
        r, c = rf[salt], cf[salt]

        ref_flat = abs(r.get("qmax") or 0.0) <= abs_floor
        cand_flat = abs(c.get("qmax") or 0.0) <= abs_floor
        if ref_flat and cand_flat:
            degenerate.append({"salt": salt, "reference_qmax": r.get("qmax"),
                               "candidate_qmax": c.get("qmax"),
                               "reference_r2": r.get("r2"), "candidate_r2": c.get("r2"),
                               "note": "no measurable binding on either side; "
                                       "fit parameters carry no information"})
            continue
        if ref_flat != cand_flat:
            findings.append({"code": "FIT_OUT_OF_TOLERANCE", "salt": salt, "key": "qmax",
                             "reference": r.get("qmax"), "candidate": c.get("qmax"),
                             "detail": "one side fits a curve, the other is flat"})
            continue

        for key, tol, kind in (("qmax", rel_tol, "rel"), ("K", rel_tol, "rel"),
                               ("r2", r2_tol, "abs")):
            rv, cv = r.get(key), c.get(key)
            if rv is None or cv is None:
                findings.append({"code": "MISSING_OUTPUT", "salt": salt, "key": key})
                continue
            if abs(rv) <= abs_floor and abs(cv) <= abs_floor:
                continue  # both physically zero
            if kind == "rel":
                denom = abs(rv) if abs(rv) > 1e-12 else 1.0
                off = abs(cv - rv) / denom
                bad = off > tol
            else:
                off = abs(cv - rv)
                bad = off > tol
            if bad:
                findings.append({"code": "FIT_OUT_OF_TOLERANCE", "salt": salt, "key": key,
                                 "reference": rv, "candidate": cv,
                                 ("rel_error" if kind == "rel" else "abs_error"): round(off, 8),
                                 "tolerance": tol})
        # Only checked when the candidate reports it: a CSV fit table need not carry the point
        # count, and not recording a number is not evidence of a different one.
        if c.get("n_points") is not None and r.get("n_points") != c.get("n_points"):
            findings.append({"code": "FIT_OUT_OF_TOLERANCE", "salt": salt, "key": "n_points",
                             "reference": r.get("n_points"), "candidate": c.get("n_points")})

    return {"pass": not findings, "codes": sorted({f["code"] for f in findings}),
            "findings": findings[:20], "conditions": sorted(rf, key=float),
            "degenerate_conditions": degenerate}


def apply_prompt_checks(r: dict, meta: dict) -> dict:
    """Checks that come from the prompt rather than from the reference's numbers.

    Step 5 of the analysis prompt is "Plot all isotherms (actual data points + fits) in one graph."
    A script that computes every value correctly and draws nothing has not done what was asked, and
    no amount of comparing fitted parameters would notice.
    """
    if not meta.get("plots_produced"):
        r["findings"] = (r.get("findings") or []) + [
            {"code": "PLOT_NOT_PRODUCED",
             "detail": "the prompt asks for a plot of all isotherms; none was written",
             "results_dir": meta.get("results_dir")}]
        r["codes"] = sorted(set(r.get("codes", [])) | {"PLOT_NOT_PRODUCED"})
        r["pass"] = False
    r["plots_produced"] = meta.get("plots_produced")
    r["results_carrier"] = meta.get("results_carrier")
    r["fits_carrier"] = meta.get("fits_carrier")
    r["undefined_endpoints"] = meta.get("undefined_endpoints", [])
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--tecan", required=True)
    ap.add_argument("--reference-results", required=True)
    ap.add_argument("--exp-id", required=True)
    ap.add_argument("--rel-tol", type=float, default=0.01)
    ap.add_argument("--r2-tol", type=float, default=0.01)
    ap.add_argument("--abs-floor", type=float, default=1e-6,
                    help="below this magnitude a fitted value is treated as physically zero")
    ap.add_argument("--port", type=int, default=8000,
                    help="device-stub port; must differ between concurrently scored replicates")
    ap.add_argument("--out")
    args = ap.parse_args()

    ref = json.loads(pathlib.Path(args.reference_results).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        cand, err, route, meta = run_script(
            pathlib.Path(args.script), pathlib.Path(args.experiment),
            pathlib.Path(args.tecan), args.exp_id, pathlib.Path(tmp), port=args.port)
    if err:
        r = {"pass": False, "codes": ["EXEC_FAILED"], "findings": [{"error": err}]}
        r = apply_prompt_checks(r, {**meta, "plots_produced": ["n/a"]})
    else:
        r = apply_prompt_checks(compare_fits(ref, cand, args.rel_tol, args.r2_tol, args.abs_floor),
                                meta)
    r["data_route"] = route
    # Recorded, not scored. `fits_carrier` and `results_carrier` name the files the numbers came
    # from - a replicate that writes fits only as CSV is doing the same analysis in a different
    # container, and counting how often that happens is more informative than failing it.
    # `undefined_endpoints` lists device-server paths outside the lab contract, which the stub
    # answered around and would otherwise leave no trace.
    if meta.get("results_dir"):
        r["results_dir"] = meta["results_dir"]

    print(f"  tolerances : {args.rel_tol:.3%} relative on qmax/K, {args.r2_tol} absolute on r2")
    print(f"  conditions : {r.get('conditions')}")
    print(f"  data route : {r['data_route']}   (how the script obtained the workbook)")
    print(f"  fits from  : {r['fits_carrier']}   results from: {r['results_carrier']}")
    print(f"  plots      : {r['plots_produced']}")
    if r["undefined_endpoints"]:
        print(f"  undefined  : {r['undefined_endpoints'][:4]}")
    print(f"  verdict    : {'PASS' if r['pass'] else 'FAIL ' + ','.join(r['codes'])}")
    for f in r["findings"][:6]:
        print(f"      {json.dumps(f)[:190]}")
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
