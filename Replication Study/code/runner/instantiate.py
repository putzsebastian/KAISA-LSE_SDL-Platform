#!/usr/bin/env python3
"""M3 slice 3a — instantiate an experiment from a template via the application's own code path.

Runs INSIDE the sdl-app container. Drives the real functions rather than reimplementing them:

    services.experiment_runner.extract_experiment_parameters   -> extracted_parameters.json
    services.experiment_runner.generate_experiment_protocols   -> runs Template_*_Script_Generator.py

`import_experiment_metadata` (step 1 of the app's chain) is deliberately not called: it fetches the
experiment from a live eLabFTW and begins by rmtree-ing the target folder. Its only other job is to
copy the process folder in, which this script does from the published template — so the staged
folder is what step 1 would have produced, without the network or the destructive delete.

Acceptance check: the protocols this produces must match the published parametrized protocols.
Trailing whitespace is normalised and comment-only differences are reported as warnings, since
neither changes what the robot does.

Usage (inside the container):
    python instantiate.py --template <dir> --experiment-json <file> --exp-id 5016 \
                          --out /tmp/m3a/out [--reference <dir>]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

sys.path.insert(0, "/app")


def unwrap(result):
    """The app's functions return Flask responses (sometimes (response, status) tuples)."""
    if isinstance(result, tuple):
        resp, code = result[0], result[1]
    else:
        resp, code = result, 200
    try:
        body = resp.get_json()
    except Exception:  # noqa: BLE001
        body = {"_raw": str(resp)}
    return code, body


def strip_trailing_ws(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines()]


def code_only(lines: list[str]) -> list[str]:
    """Drop whole-line comments and strip trailing inline comments (naive but adequate: these
    protocols contain no '#' inside string literals on commented lines)."""
    out = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("#"):
            continue
        if "#" in ln and ln.count("'") % 2 == 0 and ln.count('"') % 2 == 0:
            ln = ln.split("#", 1)[0].rstrip()
        if ln.strip():
            out.append(ln)
    return out


def classify(generated: pathlib.Path, reference: pathlib.Path) -> dict:
    g_raw = generated.read_text(encoding="utf-8", errors="replace")
    r_raw = reference.read_text(encoding="utf-8", errors="replace")
    if g_raw == r_raw:
        return {"verdict": "IDENTICAL", "detail": ""}
    g, r = strip_trailing_ws(g_raw), strip_trailing_ws(r_raw)
    if g == r:
        return {"verdict": "IDENTICAL_MODULO_WHITESPACE", "detail": ""}
    if code_only(g) == code_only(r):
        import difflib
        d = [x for x in difflib.unified_diff(r, g, "reference", "generated", lineterm="", n=0)
             if x.startswith(("+", "-")) and not x.startswith(("+++", "---"))]
        return {"verdict": "COMMENT_ONLY", "detail": d[:6]}
    import difflib
    d = [x for x in difflib.unified_diff(r, g, "reference", "generated", lineterm="", n=0)
         if x.startswith(("+", "-")) and not x.startswith(("+++", "---"))]
    return {"verdict": "DIFFERS", "detail": d[:12]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True, help="published template process folder")
    ap.add_argument("--experiment-json", required=True)
    ap.add_argument("--exp-id", type=int, required=True)
    ap.add_argument("--out", required=True, help="becomes OUTPUT_BASE_FOLDER")
    ap.add_argument("--reference", help="published parametrized protocols dir")
    ap.add_argument("--user-id", type=int, default=1)
    args = ap.parse_args()

    out_root = pathlib.Path(args.out)
    exp_dir = out_root / f"experiment_{args.exp_id}"
    if exp_dir.exists():
        shutil.rmtree(exp_dir)
    exp_dir.mkdir(parents=True)

    # what import_experiment_metadata would have staged. Harness bookkeeping is excluded: when the
    # template IS a replicate working directory, `instantiated` is the output directory itself and
    # copying it would recurse.
    skip = {"__pycache__", "agents", "scores", "instantiated",
            "state.json", "telemetry.json", "instantiate_report.json"}
    tpl = pathlib.Path(args.template)
    for item in tpl.iterdir():
        if item.name in skip:
            continue
        (shutil.copy2 if item.is_file() else shutil.copytree)(item, exp_dir / item.name)
    shutil.copy2(args.experiment_json, exp_dir / f"experiment_{args.exp_id}.json")
    print(f"staged {exp_dir}")
    print(f"   {sorted(p.name for p in exp_dir.iterdir())}")

    from app import create_app  # noqa: E402
    from services.experiment_runner import (  # noqa: E402
        extract_experiment_parameters,
        generate_experiment_protocols,
    )

    app = create_app()
    app.config["OUTPUT_BASE_FOLDER"] = str(out_root)
    report: dict = {"exp_id": args.exp_id, "steps": {}, "protocols": {}}

    with app.app_context():
        code, body = unwrap(extract_experiment_parameters(args.exp_id))
        n = len(body.get("parameters", {})) if isinstance(body, dict) else 0
        print(f"\nextract_experiment_parameters -> HTTP {code}, {n} parameters")
        report["steps"]["extract_parameters"] = {"http": code, "count": n,
                                                 "message": (body or {}).get("message")}
        if code != 200:
            print(f"   {body}")
            (out_root / "instantiate_report.json").write_text(json.dumps(report, indent=2))
            return 1

        code, body = unwrap(generate_experiment_protocols(args.exp_id, user_id=args.user_id))
        print(f"generate_experiment_protocols -> HTTP {code}")
        report["steps"]["generate_protocols"] = {"http": code,
                                                 "message": (body or {}).get("message"),
                                                 "generator": (body or {}).get("generator_used")}
        if code != 200:
            print(f"   {body}")
            (out_root / "instantiate_report.json").write_text(json.dumps(report, indent=2))
            return 1
        for line in ((body or {}).get("generator_output") or "").splitlines()[-6:]:
            print(f"   | {line}")

    log = exp_dir / "protocol_generation_log.json"
    if log.exists():
        gen_log = json.loads(log.read_text(encoding="utf-8"))
        report["replacements_made"] = gen_log.get("replacements_made", {})
        print(f"\nprotocol_generation_log.json: {len(report['replacements_made'])} replacements")

    if args.reference:
        ref = pathlib.Path(args.reference)
        print("\nacceptance check — generated vs published parametrized protocols")
        worst = "IDENTICAL"
        order = ["IDENTICAL", "IDENTICAL_MODULO_WHITESPACE", "COMMENT_ONLY", "DIFFERS", "MISSING"]
        for rf in sorted(ref.glob("Template_*_OT_Protocol_*.py")):
            gf = exp_dir / "protocols" / rf.name
            if not gf.exists():
                res = {"verdict": "MISSING", "detail": ""}
            else:
                res = classify(gf, rf)
            report["protocols"][rf.name] = res
            if order.index(res["verdict"]) > order.index(worst):
                worst = res["verdict"]
            print(f"   {rf.name:<32} {res['verdict']}")
            for d in (res["detail"] or [])[:4]:
                print(f"        {d[:110]}")
        report["acceptance"] = worst
        print(f"\nworst verdict: {worst}")

    (out_root / "instantiate_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if report.get("acceptance", "IDENTICAL") in (
        "IDENTICAL", "IDENTICAL_MODULO_WHITESPACE", "COMMENT_ONLY") else 1


if __name__ == "__main__":
    raise SystemExit(main())
