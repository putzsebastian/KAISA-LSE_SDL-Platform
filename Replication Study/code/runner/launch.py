#!/usr/bin/env python3
"""Launch one run: N replicates of the agent chain, in parallel, resumable.

Runs N replicates of the full agent chain, then instantiates and scores each one. Replicates are
independent, so they run concurrently; within a replicate the chain honours its dependency graph.

Resume: state is written after every agent. Re-running the same run id skips replicates that
already finished and picks a partial one up at its first incomplete agent. Nothing already
generated is regenerated, which is what makes an interrupted long run cheap to continue rather
than a total loss.

Concurrency defaults to 3. The device-protocol agent is the slowest and most token-hungry of the
five, and firing too many at once risks provider rate limits and n8n contention - which would
surface as spurious agent failures in exactly the numbers this study reports.

(This paragraph previously asserted that the device-protocol agent runs at high reasoning effort.
The workflow snapshots taken at measurement time set no `reasoningEffort` on that agent's chat
node, and the executions record no effort at all, so the claim was not supported by anything in
the harness and has been removed rather than restated.)

Usage:
    python launch.py --run-id pilot --replicates 5 [--concurrency 3] [--skip-scoring]
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import chain  # noqa: E402
import templates  # noqa: E402
import score as scoring  # noqa: E402
from callback_hub import CallbackHub  # noqa: E402
from replicate import Replicate  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = ROOT.parent
RUNS = ROOT / "runs"
PRINT_LOCK = threading.Lock()


def load_config() -> dict:
    cfg = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))
    secrets = {}
    sf = ROOT / "config" / "secrets.env"
    if sf.exists():
        for line in sf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                secrets[k.strip()] = v.strip()
    cfg["_secrets"] = secrets
    cfg["_repo"] = str(REPO)
    cfg.setdefault("app_container", "sdl-app")
    cfg.setdefault("container_python", "/opt/conda/envs/sdl-app/bin/python")
    cfg.setdefault("process_id", 15)
    return cfg


def stage_harness(cfg, reference_template: pathlib.Path, reference_experiment: pathlib.Path,
                  exp_id: str, template_name: str = "isotherm"):
    """Put the comparators, instantiation and reference material where the container can see them."""
    c = cfg["app_container"]
    pay = ROOT / "payloads" / template_name
    subprocess.run(["docker", "exec", c, "mkdir", "-p", "/tmp/harness"], check=True,
                   capture_output=True)
    for f in sorted((ROOT / "comparators").glob("*.py")):
        subprocess.run(["docker", "cp", str(f), f"{c}:/tmp/harness/{f.name}"], check=True,
                       capture_output=True)
    subprocess.run(["docker", "cp", str(ROOT / "runner" / "instantiate.py"),
                    f"{c}:/tmp/harness/instantiate.py"], check=True, capture_output=True)
    subprocess.run(["docker", "cp", str(ROOT / "runner" / "helpers" / "scan_plots.py"),
                    f"{c}:/tmp/harness/scan_plots.py"], check=True, capture_output=True)
    # the ELN field list the report comparator checks against
    payload = json.loads((pay / "analysis.payload.json").read_text(encoding="utf-8"))
    fields = ROOT / "runs" / "_fields.json"
    fields.parent.mkdir(parents=True, exist_ok=True)
    fields.write_text(json.dumps(payload["template_fields"], indent=2), encoding="utf-8")
    subprocess.run(["docker", "cp", str(fields), f"{c}:/tmp/harness/fields.json"], check=True,
                   capture_output=True)
    # the report comparator derives its requirements from the prompt, so the payload goes too
    subprocess.run(["docker", "cp", str(pay / "report.payload.json"),
                    f"{c}:/tmp/harness/report_payload.json"], check=True, capture_output=True)
    # ...and so does protocol_spec, which reads the deck layout out of the device-protocol payloads.
    # File by file rather than by directory: `docker cp` of a directory onto an existing directory
    # nests it instead of replacing it, so re-staging would build payloads_isotherm/isotherm/...
    subprocess.run(["docker", "exec", c, "mkdir", "-p", "/tmp/harness/payloads_isotherm"],
                   check=True, capture_output=True)
    for f in sorted(pay.glob("device_protocol_*.payload.json")):
        subprocess.run(["docker", "cp", str(f), f"{c}:/tmp/harness/payloads_isotherm/{f.name}"],
                       check=True, capture_output=True)

    ref_t, ref_e = "/tmp/harness/reference_template", "/tmp/harness/reference_experiment"
    for host, cpath in ((reference_template, ref_t), (reference_experiment, ref_e)):
        subprocess.run(["docker", "exec", c, "mkdir", "-p", cpath], check=True, capture_output=True)
        subprocess.run(["docker", "cp", f"{host}{chr(92)}.", f"{c}:{cpath}"], check=True,
                       capture_output=True)
    return ref_t, ref_e


def run_replicate(index, cfg, hub, args, ref_template_c, ref_experiment_c,
                  reference_template) -> dict:
    def log(msg):
        with PRINT_LOCK:
            print(f"[rep {index:03d}] {msg}", flush=True)

    rep = Replicate(args.run_id, index, cfg, reference_template, args.template)
    rep.seed()
    started = time.time()
    log("chain start")
    state = chain.run_chain(rep, cfg, hub, args.timeout, log=log,
                            retry_failed=args.retry_failed,
                            agent_retries=args.agent_retries,
                            only=set(args.only.split(",")) if args.only else None)

    summary = None
    if not args.skip_scoring:
        try:
            summary = scoring.score(rep, cfg, ref_experiment_c, ref_template_c,
                                    args.exp_id, state, log=log)
        except Exception as exc:  # noqa: BLE001
            log(f"    scoring failed: {type(exc).__name__}: {exc}")
            summary = {"error": f"{type(exc).__name__}: {exc}"}

    tokens = sum((a.get("tokens_total") or 0) for a in state["agents"].values())
    tools = sum((a.get("tool_calls") or 0) for a in state["agents"].values())
    # Generation time is the SUM of the agents' own wall clocks, not the span of the run that
    # launched them. Within a replicate the chain executes serially, so the two agree up to the
    # harness's own overhead - but only the sum survives a resume, where a replicate is assembled
    # across several runs and the span of any one of them means nothing. The measured span is kept
    # alongside it rather than discarded.
    measured = round(time.time() - started, 1)
    executed = state.get("agents_executed_this_run", 0)
    agent_time = round(sum((a.get("wall_clock_s") or 0) for a in state["agents"].values()), 1)
    # First-attempt convergence is the claim the manuscript actually makes ("success on the first
    # attempt"), so it is recorded separately from convergence after retries rather than being
    # recoverable only by digging through per-agent attempt lists.
    ran = [a for a in state["agents"].values() if a.get("attempts")]
    first_ok = [a for a in ran if a.get("converged_first_attempt")]
    record = {
        "index": index,
        "generation_complete": state.get("generation_complete"),
        "generation_complete_first_attempt": bool(ran) and len(first_ok) == len(ran)
        and state.get("generation_complete"),
        "agents_succeeded": state.get("agents_succeeded"),
        "agents_succeeded_first_attempt": len(first_ok),
        "agents_total": state.get("agents_total"),
        "agent_attempts_total": sum(a.get("attempt_count") or 1 for a in ran),
        "infrastructure_retries": sum(a.get("infrastructure_retries") or 0 for a in ran),
        "semantic_convergence": (summary or {}).get("semantic_convergence"),
        "codes": (summary or {}).get("codes", []),
        "tokens_total": tokens,
        "tool_calls_total": tools,
        "wall_clock_s": agent_time,
        "run_wall_clock_s": measured,
        "agents_executed_this_run": executed,
        "resumed": executed < (state.get("agents_total") or 0),
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    state["record"] = record
    rep.save_state(state)
    (rep.host_dir / "telemetry.json").write_text(
        json.dumps({"per_agent": {k: {kk: v.get(kk) for kk in
                                      ("status", "wall_clock_s", "tool_calls", "tokens_total")}
                                  for k, v in state["agents"].items()},
                    "totals": record}, indent=2) + "\n", encoding="utf-8", newline="\n")
    rep.publish(RUNS)
    log(f"done  generation={record['generation_complete']} "
        f"semantic={record['semantic_convergence']} "
        f"{record['wall_clock_s']}s agent time {tokens} tokens")
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--replicates", type=int, required=True)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=90 * 60)
    ap.add_argument("--exp-id", default="5016")
    ap.add_argument("--template", default="isotherm")
    ap.add_argument("--skip-scoring", action="store_true")
    ap.add_argument("--retry-failed", action="store_true",
                    help="on resume, re-attempt agents that previously failed "
                         "(off by default: a resumed run continues, it does not "
                         "give failures a second chance)")
    ap.add_argument("--only",
                    help="comma-separated agents or chain steps to run (e.g. 'report' or "
                         "'report,orchestration'). Unselected steps are left untouched, not "
                         "marked as failed - for working one agent at a time.")
    ap.add_argument("--agent-retries", type=int, default=2,
                    help="extra attempts after a genuine agent failure, within one run. Each is "
                         "RECORDED as an attempt, so first-attempt and eventual convergence stay "
                         "separable. Provider rate limits and transport errors are retried "
                         "regardless and are never counted as attempts. 0 = never retry.")
    ap.add_argument("--reference-repo",
                    default=os.environ.get("SDL_REFERENCE_REPO",
                                     r"C:\Promotion\Paper\Paper 3 - SDL App\Repo"))
    args = ap.parse_args()

    cfg = load_config()
    ref_repo = pathlib.Path(args.reference_repo)
    tpl = templates.get(args.template)
    # --exp-id defaults to the isotherm's; follow the template unless it was given explicitly
    if args.exp_id == "5016" and tpl["exp_id"] != "5016":
        args.exp_id = tpl["exp_id"]
    reference_template = ref_repo.joinpath(*tpl["reference_template"])
    reference_experiment = ref_repo.joinpath(*tpl["reference_experiment"])
    for p in (reference_template, reference_experiment):
        if not p.is_dir():
            print(f"reference missing: {p}")
            return 1

    print(f"run {args.run_id}: {args.replicates} replicates, "
          f"concurrency {args.concurrency}, timeout {args.timeout}s")
    ref_template_c, ref_experiment_c = stage_harness(cfg, reference_template,
                                                     reference_experiment, args.exp_id,
                                                     args.template)

    run_dir = RUNS / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": args.run_id, "replicates": args.replicates,
        "concurrency": args.concurrency, "template": args.template, "exp_id": args.exp_id,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }, indent=2) + "\n", encoding="utf-8", newline="\n")

    records = []
    started = time.time()
    with CallbackHub(port=cfg["callback_port"]) as hub:
        with futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            jobs = {pool.submit(run_replicate, i, cfg, hub, args, ref_template_c,
                                ref_experiment_c, reference_template): i
                    for i in range(1, args.replicates + 1)}
            for fut in futures.as_completed(jobs):
                i = jobs[fut]
                try:
                    records.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    with PRINT_LOCK:
                        print(f"[rep {i:03d}] CRASHED: {type(exc).__name__}: {exc}", flush=True)
                    records.append({"index": i, "crashed": f"{type(exc).__name__}: {exc}"})
        if hub.orphans:
            print(f"warning: {len(hub.orphans)} callback(s) arrived with no matching job_id")

    records.sort(key=lambda r: r["index"])
    gen = sum(1 for r in records if r.get("generation_complete"))
    gen1 = sum(1 for r in records if r.get("generation_complete_first_attempt"))
    sem = sum(1 for r in records if r.get("semantic_convergence"))
    summary = {
        "run_id": args.run_id, "replicates": len(records),
        "agent_retries_allowed": args.agent_retries,
        "generation_complete": gen,
        "generation_complete_first_attempt": gen1,
        "semantic_convergence": sem,
        "agent_attempts_total": sum(r.get("agent_attempts_total") or 0 for r in records),
        "infrastructure_retries": sum(r.get("infrastructure_retries") or 0 for r in records),
        "wall_clock_s": round(time.time() - started, 1),
        "tokens_total": sum(r.get("tokens_total") or 0 for r in records),
        "records": records,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n",
                                            encoding="utf-8", newline="\n")
    print(f"\nrun {args.run_id}: generation {gen}/{len(records)} "
          f"({gen1}/{len(records)} on the first attempt), "
          f"semantic {sem}/{len(records)}, {summary['wall_clock_s']}s, "
          f"{summary['tokens_total']} tokens, "
          f"{summary['infrastructure_retries']} infrastructure retries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
