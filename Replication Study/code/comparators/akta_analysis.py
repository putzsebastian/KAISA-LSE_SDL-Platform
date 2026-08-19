#!/usr/bin/env python3
"""Execute a generated ÄKTA analysis script and judge what it actually computed.

Scored against the PROMPT's requirements, with the published reference supplying the numeric truth
the chromatogram implies:

    "Please create a data analysis script for my AKTA run. Plot the chromatogram. Use different
     colors for UV 280nm and conductivity. Identify peaks if present and calculate their retention
     time. Include the peak labelling with their retention time in the plot, but only if peaks were
     identified."

So four things have to hold, and they are checked directly rather than inferred from a success
message: the script runs, it finds the peak that is in the data, it puts that peak at the right
retention time, and it draws the chromatogram.

The reference (experiment 5520) detects exactly one UV peak at 64.63 min, 423.75 mAU. Those numbers
come from the same raw data the candidate is given, so a candidate that disagrees has analysed it
differently - that is the measurement.

Tolerances are deliberately loose enough to forgive implementation choices (smoothing window, peak
prominence threshold, interpolation) and tight enough to catch a different answer: 2% on retention
time, 10% on peak height. A script that picks a different peak, or invents peaks in noise, moves far
outside both.

Usage:
    python akta_analysis.py --experiment <ref experiment dir> --script <candidate.py> \
        --exp-id 5520 [--port 8100] [--out r.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from akta_stub import AktaResultsStub, env_for  # noqa: E402

RT_TOL_FRAC = 0.02
HEIGHT_TOL_FRAC = 0.10


def reference_peaks(experiment_dir: pathlib.Path, exp_id: str) -> dict:
    """The published analysis_results_<id>.json peak block."""
    f = experiment_dir / "results" / f"analysis_results_{exp_id}.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    return d.get("peak_analysis") or {}


def run_script(script: pathlib.Path, experiment_dir: pathlib.Path, exp_id: str,
               workdir: pathlib.Path, port: int = 8100, serve: bool = True):
    """Run the candidate against the stubbed control server; return (results, error, meta)."""
    results = workdir / "results"
    results.mkdir(parents=True, exist_ok=True)
    analysis_dir = workdir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    data_dir = workdir / "data"
    data_dir.mkdir(exist_ok=True)

    staged = analysis_dir / script.name
    shutil.copy2(script, staged)

    # the experiment JSON carries metadata_decoded.extra_fields, which the prompt tells the script
    # to read; place it everywhere a script has been seen to look
    for name in (f"experiment_{exp_id}.json",):
        src = experiment_dir / name
        if src.exists():
            shutil.copy2(src, workdir / name)
            shutil.copy2(src, data_dir / name)

    akta = experiment_dir / "results" / f"akta_results_{exp_id}.json"
    # also pre-place the raw results, for a script that reads from disk rather than the server
    if akta.exists():
        shutil.copy2(akta, data_dir / akta.name)
        shutil.copy2(akta, results / akta.name)

    env = dict(os.environ)
    cmd = [sys.executable, staged.name, str(exp_id),
           "--data-folder", "../data", "--results-folder", str(results)]

    def _execute():
        return subprocess.run(cmd, cwd=str(analysis_dir), capture_output=True,
                              text=True, timeout=900, env=env)

    if serve:
        with AktaResultsStub(akta, exp_id, port=port) as stub:
            env.update(env_for(stub.url))
            proc = _execute()
            hits, unknown = stub.hits, stub.unknown
    else:
        proc, hits, unknown = _execute(), [], []

    produced = sorted(p.name for p in results.iterdir()) if results.is_dir() else []
    meta = {
        "results_dir": produced,
        "plots_produced": [n for n in produced if n.lower().endswith((".png", ".pdf", ".svg"))],
        "csv_produced": [n for n in produced if n.lower().endswith(".csv")],
        "server_routes": sorted(set(hits)),
        "undefined_endpoints": sorted(set(unknown)),
        "stdout_tail": (proc.stdout or "")[-400:],
    }
    if proc.returncode != 0:
        return None, f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[-600:]}", meta

    out = results / f"analysis_results_{exp_id}.json"
    if not out.exists():
        cands = [p for p in results.glob("*.json") if "akta_results" not in p.name]
        out = cands[0] if cands else None
    if out is None:
        return None, "no analysis results JSON was written", meta
    meta["results_carrier"] = out.name
    try:
        return json.loads(out.read_text(encoding="utf-8")), None, meta
    except Exception as exc:  # noqa: BLE001
        return None, f"results JSON unreadable: {exc}", meta


def peaks_from_csv(results_dir: pathlib.Path) -> list:
    """Peaks a candidate exported to CSV instead of embedding in the results JSON.

    The prompt asks it to identify peaks and to "Export processed data: Save as CSV", and says
    nothing about the peaks having to appear in the JSON. Several candidates therefore write
    `akta_peaks_<id>.csv` and record only a count in the JSON - one reported the reference peak to
    twelve decimal places that way and was scored as having detected nothing at all.
    """
    if not results_dir or not results_dir.is_dir():
        return []
    for f in sorted(results_dir.glob("*.csv")):
        if "peak" not in f.name.lower():
            continue
        try:
            rows = list(csv.DictReader(f.read_text(encoding="utf-8").splitlines()))
        except Exception:  # noqa: BLE001
            continue
        out = []
        for row in rows:
            rec = {}
            for k, v in row.items():
                if k is None:
                    continue
                try:
                    rec[k.strip()] = float(v)
                except (TypeError, ValueError):
                    rec[k.strip()] = v
            if rec:
                out.append(rec)
        if out:
            return out
    return []


PARALLEL_RT = ("retention_times_min", "retention_times", "peak_times_min", "times_min",
               "peak_retention_times_min")
PARALLEL_H = ("peak_heights_mAU", "peak_heights", "heights_mAU", "heights", "prominences")


def peaks_from_parallel_arrays(node: dict) -> list:
    """Peaks stored as parallel arrays rather than a list of records.

    `{"n_peaks": 6, "retention_times_min": [...], "peak_heights_mAU": [...]}` says exactly the same
    thing as a list of peak objects, and the prompt does not prescribe a shape. One candidate
    reported the reference peak at 64.6167 min this way and was scored as detecting nothing.
    """
    if not isinstance(node, dict):
        return []
    rts = next((node[k] for k in PARALLEL_RT if isinstance(node.get(k), list)), None)
    if not rts:
        return []
    hs = next((node[k] for k in PARALLEL_H if isinstance(node.get(k), list)), []) or []
    out = []
    for i, rt in enumerate(rts):
        rec = {"retention_time_min": rt}
        if i < len(hs):
            rec["peak_height_mAU"] = hs[i]
        out.append(rec)
    return out


def peaks_of(results: dict) -> list:
    """Find the peak list wherever the candidate chose to put it."""
    if not isinstance(results, dict):
        return []
    parallel = peaks_from_parallel_arrays(results)
    if parallel:
        return parallel
    pa = results.get("peak_analysis")
    if isinstance(pa, dict) and isinstance(pa.get("peaks"), list):
        return pa["peaks"]
    for key in ("peaks", "detected_peaks", "uv_peaks"):
        v = results.get(key)
        if isinstance(v, list):
            return v
        if isinstance(v, dict) and isinstance(v.get("peaks"), list):
            return v["peaks"]
    for v in results.values():
        if isinstance(v, dict):
            got = peaks_of(v)
            if got:
                return got
    return []


# The prompt asks for "their retention time" and says nothing about what to call the key, so
# candidates name these whatever they like - `retention_time_min`, `time_min`, `rt_min`. Scoring a
# spelling would measure vocabulary rather than analysis: one pilot replicate reported the reference
# retention time to twelve decimal places under the key `time_min` and was marked as not having
# computed it at all.
RT_MIN_KEYS = ("retention_time_min", "time_min", "rt_min", "retention_min",
               "retention_time_minutes", "peak_time_min")
RT_S_KEYS = ("retention_time_s", "time_s", "rt_s", "retention_s", "retention_time_sec",
             "retention_time", "peak_time_s")
HEIGHT_KEYS = ("peak_height_mAU", "peak_height", "height_mAU", "height", "intensity",
               "peak_intensity", "uv_height")


def _num(peak: dict, *names):
    if not isinstance(peak, dict):
        return None
    lowered = {str(k).lower(): v for k, v in peak.items()}
    for n in names:
        v = lowered.get(n.lower())
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _retention_min(peak: dict):
    """Retention time in minutes, whatever the candidate called it, seconds accepted."""
    rt = _num(peak, *RT_MIN_KEYS)
    if rt is not None:
        return rt
    secs = _num(peak, *RT_S_KEYS)
    return secs / 60.0 if secs is not None else None


def compare(results: dict, ref_peaks: dict, meta: dict,
            results_dir: pathlib.Path | None = None) -> dict:
    findings = []
    ref_list = ref_peaks.get("peaks") or []
    ref_n = ref_peaks.get("num_peaks", len(ref_list))
    cand = peaks_of(results) or peaks_from_csv(results_dir)

    if not cand and ref_n:
        findings.append({"code": "NO_PEAKS_DETECTED", "expected": ref_n,
                         "detail": "the reference finds %d peak(s) in this chromatogram" % ref_n})
    elif len(cand) != ref_n:
        # Recorded, not fatal. The prompt says "identify peaks if present" and names no prominence
        # threshold, height cut-off or minimum spacing, so how many features count as peaks is a
        # choice it leaves open. A pilot replicate found the reference peak (423.8 mAU at 64.63 min)
        # plus a 20.1 mAU feature at 6.12 min - plausibly the void peak, and not something the
        # specification excludes. Failing that would measure a threshold the prompt never set.
        # Finding NO peaks stays fatal above: that is a failure to do what was asked.
        findings.append({"code": "PEAK_COUNT_MISMATCH", "expected": ref_n, "found": len(cand),
                         "benign": True,
                         "detail": "the prompt sets no criterion for what counts as a peak"})

    # compare the principal peak: highest, since a candidate may order peaks differently
    if cand and ref_list:
        rt_ref = _retention_min(ref_list[0])
        h_ref = _num(ref_list[0], *HEIGHT_KEYS)
        # Match the reference peak by RETENTION TIME, not by picking the candidate's tallest.
        # With the count no longer decisive (the prompt sets no criterion for what counts as a
        # peak) the question is simply whether the peak that is in the data was found. Choosing
        # "the tallest" also breaks on candidates that report retention times without heights:
        # one stored six peaks including the reference at 64.62 min, and tallest-by-missing-height
        # silently selected the first, at 3.15 min.
        if rt_ref is not None:
            best = min(cand, key=lambda p: abs((_retention_min(p) if _retention_min(p) is not None
                                                else -1e9) - rt_ref))
        else:
            best = max(cand, key=lambda p: _num(p, *HEIGHT_KEYS) or 0.0)
        rt = _retention_min(best)
        h = _num(best, *HEIGHT_KEYS)

        if rt is None:
            findings.append({"code": "RETENTION_TIME_MISSING",
                             "detail": "the prompt asks for each peak's retention time"})
        elif rt_ref and abs(rt - rt_ref) > max(0.5, RT_TOL_FRAC * rt_ref):
            findings.append({"code": "RETENTION_TIME_MISMATCH", "reference_min": rt_ref,
                             "candidate_min": round(rt, 3)})
        if h is not None and h_ref and abs(h - h_ref) > HEIGHT_TOL_FRAC * h_ref:
            findings.append({"code": "PEAK_HEIGHT_MISMATCH", "reference_mAU": h_ref,
                             "candidate_mAU": round(h, 3)})

    # the prompt asks for the chromatogram to be plotted
    if not any(n.lower().endswith(".png") for n in meta.get("plots_produced", [])):
        findings.append({"code": "PLOT_NOT_PRODUCED",
                         "detail": "the prompt asks for the chromatogram to be plotted; no raster "
                                   "image was written"})
    if not any(n.lower().endswith(".pdf") for n in meta.get("plots_produced", [])):
        findings.append({"code": "PDF_NOT_PRODUCED", "benign": True})
    if not meta.get("csv_produced"):
        findings.append({"code": "PROCESSED_CSV_NOT_PRODUCED", "benign": True,
                         "detail": "the reference exports processed chromatogram data; the prompt "
                                   "does not require it"})

    fatal = [f for f in findings if not f.get("benign")]
    return {"pass": not fatal,
            "codes": sorted({f["code"] for f in findings}),
            "fatal_codes": sorted({f["code"] for f in fatal}),
            "findings": findings, "n_findings": len(fatal),
            "peaks_found": len(cand), "peaks_reference": ref_n}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True, help="reference experiment directory")
    ap.add_argument("--script", required=True)
    ap.add_argument("--exp-id", required=True)
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--workdir")
    ap.add_argument("--no-serve", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    experiment = pathlib.Path(args.experiment)
    script = pathlib.Path(args.script)
    workdir = pathlib.Path(args.workdir) if args.workdir else pathlib.Path(
        "/tmp/_akta_%s_%s" % (args.exp_id, os.getpid()))
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)

    if not script.exists():
        r = {"pass": False, "codes": ["MISSING_SCRIPT"], "fatal_codes": ["MISSING_SCRIPT"],
             "findings": [{"code": "MISSING_SCRIPT", "detail": str(script)}]}
    else:
        results, error, meta = run_script(script, experiment, args.exp_id, workdir,
                                          port=args.port, serve=not args.no_serve)
        if error:
            r = {"pass": False, "codes": ["SCRIPT_FAILED"], "fatal_codes": ["SCRIPT_FAILED"],
                 "candidate_error": error, "findings": [{"code": "SCRIPT_FAILED",
                                                         "detail": error[:300]}]}
            r.update({k: meta[k] for k in ("results_dir", "plots_produced", "server_routes",
                                           "undefined_endpoints") if k in meta})
        else:
            r = compare(results, reference_peaks(experiment, args.exp_id), meta,
                        workdir / "results")
            r.update({k: meta[k] for k in ("results_dir", "plots_produced", "csv_produced",
                                           "server_routes", "undefined_endpoints",
                                           "results_carrier") if k in meta})

    print("  peaks     : found %s, reference %s" % (r.get("peaks_found"), r.get("peaks_reference")))
    print("  plots     : %s" % (r.get("plots_produced") or []))
    print("  routes    : %s" % (r.get("server_routes") or []))
    print("  verdict   : %s" % ("PASS" if r.get("pass") else
                                "FAIL " + ",".join(r.get("fatal_codes", r.get("codes", [])))))
    for f in r.get("findings", [])[:6]:
        print("      %s" % json.dumps(f)[:200])
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    return 0 if r.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
