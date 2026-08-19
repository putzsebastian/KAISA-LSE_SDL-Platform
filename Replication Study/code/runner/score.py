#!/usr/bin/env python3
"""M4 slice 4c - instantiate a replicate and score it against the reference.

Instantiation and every comparator run inside the app container (they need the application, the
opentrons SDK and the experiment database), so this drives them by docker exec. Only agents that
produced an artifact are scored; anything not_run is reported as such rather than counted as a
failure, so a blocked agent is never confused with a wrong one.
"""
from __future__ import annotations

import json
import pathlib
import subprocess

import templates

SUCCESS = {"completed", "completed_artifact_from_execution"}
HARNESS = "/tmp/harness"

# comparator -> the chain steps whose artifacts it judges
NEEDS = {
    "device_protocol": [f"device_protocol_{i}" for i in range(1, 6)],
    "device_protocol_spec": [f"device_protocol_{i}" for i in range(1, 6)],
    "orchestration": ["orchestration"],
    "analysis": ["analysis"],
    "report": ["report"],
    "database": ["database"],
}

# Comparators that are recorded but do not decide convergence. `device_protocol` diffs the
# candidate against the PUBLISHED protocol, and across this study that reference was wrong five
# times - a method that no longer exists, a deck layout contradicting its own prompt, a transfer
# volume of 200 where the prompt said 300, and a reservoir three prompts never declared. Each
# scored a correct agent as failing. `device_protocol_spec` judges the same artifacts against the
# prompt the agent was actually given, which is what the manuscript claims, so that one counts and
# the reference diff is kept alongside it as a diagnostic.
ADVISORY = {"device_protocol"}


def dexec(cfg, args, timeout=1800):
    return subprocess.run(
        ["docker", "exec", "-e", "HOME=/tmp", "-w", "/tmp", cfg["app_container"],
         cfg["container_python"], *args],
        capture_output=True, text=True, timeout=timeout)


def read_json(path: pathlib.Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def instantiate(rep, cfg, ref_experiment_c: str, exp_id: str, log=print):
    """Run the application's own instantiation over this replicate's template.

    Deliberately WITHOUT --reference. A replicate's protocols are expected to differ from the
    published ones - that is the thing being measured - so instantiation is judged only on whether
    it produced parametrized scripts. The device-protocol comparator does the comparing.
    """
    out_c = f"{rep.container_dir}/instantiated"
    proc = dexec(cfg, [f"{HARNESS}/instantiate.py",
                       "--template", rep.container_dir,
                       "--experiment-json", f"{ref_experiment_c}/experiment_{exp_id}.json",
                       "--exp-id", str(exp_id),
                       "--out", out_c])
    report = read_json(rep.host_dir / "instantiated" / "instantiate_report.json", {})
    produced = sorted((rep.host_dir / "instantiated" / f"experiment_{exp_id}" / "protocols")
                      .glob("Template_*_OT_Protocol_*.py")) \
        if (rep.host_dir / "instantiated" / f"experiment_{exp_id}" / "protocols").is_dir() else []
    ok = proc.returncode == 0 and len(produced) > 0
    report["ok"] = ok
    report["protocols_produced"] = len(produced)
    if not ok:
        report.setdefault("error", (proc.stderr or proc.stdout)[-400:])
    log(f"    instantiate           {'ok' if ok else 'FAILED'}  "
        f"{len(produced)} parametrized protocol(s)")
    return report


def score(rep, cfg, ref_experiment_c: str, ref_template_c: str, exp_id: str, state: dict,
          log=print, only: set | None = None) -> dict:
    scores_dir = rep.host_dir / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    def unavailable(name):
        missing = [s for s in NEEDS[name] if state["agents"].get(s, {}).get("status")
                   not in SUCCESS]
        return missing

    tpl = templates.get(getattr(rep, "template_name", "isotherm"))
    tpl_steps = {s for s, _p, _d in tpl["chain"]}
    # Instantiation exists to parametrize agent-generated Opentrons protocols for the
    # device-protocol comparators. A template with no device_protocol steps has nothing to
    # instantiate, and running it anyway reports a spurious FAILED / "0 parametrized protocols".
    needs_instantiation = any(s.startswith("device_protocol") for s in tpl_steps)
    inst = (instantiate(rep, cfg, ref_experiment_c, exp_id, log)
            if needs_instantiation and (not only or "device_protocol" in only) else {})
    (scores_dir / "instantiation.json").write_text(json.dumps(inst, indent=2) + "\n",
                                                   encoding="utf-8", newline="\n")

    inst_c = f"{rep.container_dir}/instantiated/experiment_{exp_id}"

    plan = [
        ("device_protocol", [f"{HARNESS}/device_protocol.py",
                             "--reference-dir", f"{ref_experiment_c}/protocols",
                             "--candidate-dir", f"{inst_c}/protocols",
                             "--labware", f"{ref_experiment_c}/protocols"]),
        ("device_protocol_spec", [f"{HARNESS}/protocol_spec.py",
                                  "--prompt-dir", f"{HARNESS}/payloads_isotherm",
                                  "--candidate-dir", f"{inst_c}/protocols",
                                  # --labware supplies the custom definitions AND turns on the
                                  # simulation, without which a protocol that crashes still passes
                                  "--labware", f"{ref_experiment_c}/protocols",
                                  "--protocols", "2,3,4,5"]),
        # The reference is the TEMPLATE's main.py, not the experiment's. They are byte-identical
        # for the isotherm, but the FPLC experiment copy was hand-refined after generation and
        # gained two akta.set_valve calls the agent never wrote - comparing against it scored every
        # candidate down for failing to reproduce a later manual edit. The template folder holds
        # what the orchestration agent actually produced, which is what is being measured.
        ("orchestration", [f"{HARNESS}/orchestration.py",
                           "--workflow", f"{ref_experiment_c}/workflow.json",
                           "--reference", f"{ref_template_c}/main.py",
                           "--candidate", f"{rep.container_dir}/main.py"]),
        ("analysis", [f"{HARNESS}/analysis.py",
                      "--experiment", ref_experiment_c,
                      "--script", f"{rep.container_dir}/analysis/"
                                  f"analysis_script_{cfg['process_id']}.py",
                      "--tecan", f"{ref_experiment_c}/data/tecan_data_{exp_id}.xlsx",
                      "--reference-results",
                      f"{ref_experiment_c}/results/analysis_results_{exp_id}.json",
                      "--exp-id", str(exp_id), "--port", str(rep.stub_port)]),
        ("report", [f"{HARNESS}/report.py",
                    "--candidate", f"{rep.container_dir}/{tpl['artifact_target']['report']}",
                    "--fields", f"{HARNESS}/fields.json",
                    # requirements come from the prompt; the reference only supplies the
                    # non-fatal "fields the published template used and this one did not"
                    "--prompt", f"{HARNESS}/report_payload.json",
                    "--reference",
                    f"{ref_experiment_c}/reports/report_template_{tpl['template_id']}.html"]),
        ("database", [f"{HARNESS}/database.py",
                      "--experiment", ref_experiment_c,
                      "--reference-script", f"{ref_template_c}/db/commit_to_db.py",
                      "--candidate-script", f"{rep.container_dir}/db/commit_to_db.py",
                      "--elab-id", str(exp_id),
                      "--scratch-db", rep.scratch_db]),
    ]

    # The FPLC experiment has no device_protocol steps and judges its analysis against a
    # chromatogram rather than a Tecan workbook, so the plan is filtered to what this template
    # actually produces and its analysis entry swapped for the ÄKTA comparator.
    if tpl["analysis_comparator"] == "akta":
        plan = [(n, a) for n, a in plan
                if n not in ("device_protocol", "device_protocol_spec", "analysis")]
        plan.insert(0, ("analysis", [f"{HARNESS}/akta_analysis.py",
                                     "--experiment", ref_experiment_c,
                                     "--script", f"{rep.container_dir}/analysis/"
                                                 f"analysis_script_{tpl['process_id']}.py",
                                     "--exp-id", str(exp_id),
                                     "--port", str(rep.stub_port + 200)]))
    plan = [(n, a) for n, a in plan if not set(NEEDS[n]).isdisjoint(tpl_steps)]

    for name, args in plan:
        # `only` scores a subset. Used when upstream artifacts were seeded from another
        # replicate: re-judging them would duplicate the donor's verdict, not add a data point.
        if only and name not in only:
            continue
        missing = unavailable(name)
        if missing:
            results[name] = {"pass": None, "codes": ["NOT_RUN"], "blocked_by": missing}
            log(f"    {name:<21} not scored (upstream {', '.join(missing)})")
        else:
            out_c = f"{rep.container_dir}/scores/{name}.json"
            proc = dexec(cfg, args + ["--out", out_c])
            r = read_json(scores_dir / f"{name}.json",
                          {"pass": False, "codes": ["COMPARATOR_ERROR"],
                           "error": (proc.stderr or proc.stdout)[-400:]})
            results[name] = r
            verdict = "PASS" if r.get("pass") else "FAIL " + ",".join(r.get("codes", []))
            log(f"    {name:<21} {verdict}"
                f"{'   (advisory)' if name in ADVISORY else ''}")
        (scores_dir / f"{name}.json").write_text(json.dumps(results[name], indent=2) + "\n",
                                                 encoding="utf-8", newline="\n")

    deciding = {k: v for k, v in results.items() if k not in ADVISORY}
    scored = [v for v in deciding.values() if v.get("pass") is not None]
    summary = {
        "instantiation": inst.get("acceptance"),
        "instantiation_ok": inst.get("ok"),
        "comparators": {k: v.get("pass") for k, v in results.items()},
        "advisory": sorted(ADVISORY & set(results)),
        "codes": sorted({c for v in deciding.values() for c in v.get("codes", [])
                         if c != "NOT_RUN"}),
        "advisory_codes": sorted({c for k, v in results.items() if k in ADVISORY
                                  for c in v.get("codes", []) if c != "NOT_RUN"}),
        "scored": len(scored),
        "passed": sum(1 for v in scored if v.get("pass")),
        # convergence is over the comparators THIS template actually runs, not the full registry:
        # the FPLC experiment has no device-protocol comparators, so requiring them would make
        # every FPLC replicate non-convergent by construction.
        "semantic_convergence": bool(scored) and all(v.get("pass") for v in scored)
        and len(scored) == len([n for n, _a in plan if n not in ADVISORY]),
    }
    (scores_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n",
                                             encoding="utf-8", newline="\n")
    return summary
