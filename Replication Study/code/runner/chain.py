#!/usr/bin/env python3
"""M4 slice 4a - run the full agent chain for one replicate.

Each agent consumes the previous agent's output from this same replicate. The chain is not linear;
what each agent actually consumes gives this graph:

    device_protocol_1..5 ---+   (independent of one another)
                            +--> orchestration   (needs this replicate's protocols/ on disk)
    analysis ---------------+
                            +--> report          (selected_plots scanned from THIS analysis script)
                            +--> database        (analysis_script IS this analysis output)

No agent is ever retried. A failure is recorded and everything downstream is marked not_run, so a
failed protocol 3 blocks orchestration but leaves analysis -> report/database intact: partial
information is kept rather than discarding the replicate.

The replicate's working directory lives under the app container's mounted data volume, because the
agents' own save tools write into it over HTTP from inside that container.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

import templates

ROOT = pathlib.Path(__file__).resolve().parent.parent

# agent -> (payload stem, dependencies). Defined per template in templates.py, because the FPLC
# experiment has no device_protocol steps at all - its ÄKTA protocol is assembled deterministically
# by the app rather than by an agent.
CHAIN = templates.get("isotherm")["chain"]


def chain_for(template_name: str) -> list:
    return templates.get(template_name)["chain"]
AGENT_OF = {  # which entry in config/agents.json each chain step uses
    **{f"device_protocol_{i}": "device_protocol" for i in range(1, 6)},
    "analysis": "analysis", "orchestration": "orchestration",
    "report": "report", "database": "database",
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def post_json(url: str, payload: dict, timeout: int):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json", "User-Agent": "SDL-Frontend/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def n8n_get(cfg: dict, path: str):
    url = cfg["n8n_base_url"].rstrip("/") + path
    req = urllib.request.Request(url, headers={
        "X-N8N-API-KEY": cfg["_secrets"].get("N8N_API_KEY", ""), "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def find_execution(cfg, workflow_id, job_id, after, tries=20, delay=6):
    for _ in range(tries):
        try:
            listing = n8n_get(cfg, f"/api/v1/executions?workflowId={workflow_id}&limit=20")
        except Exception:  # noqa: BLE001
            time.sleep(delay)
            continue
        for e in listing.get("data", []):
            started = e.get("startedAt")
            if not started:
                continue
            ts = dt.datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
            if ts + 2 < after:
                continue
            try:
                full = n8n_get(cfg, f"/api/v1/executions/{e['id']}?includeData=true")
            except Exception:  # noqa: BLE001
                continue
            if job_id and job_id not in json.dumps(full)[:400000]:
                continue
            return full
        time.sleep(delay)
    return None


def extract_telemetry(execution: dict, tool_nodes: list[str]):
    run_data = (execution.get("data", {}).get("resultData", {}) or {}).get("runData", {})
    calls, tokens = [], {}
    for node, runs in run_data.items():
        for i, run in enumerate(runs or []):
            if node in tool_nodes:
                calls.append({"node": node, "run_index": i, "start_ms": run.get("startTime"),
                              "duration_ms": run.get("executionTime"),
                              "status": run.get("executionStatus")})
            for chan, groups in (run.get("data") or {}).items():
                if "languageModel" not in chan:
                    continue
                for g in groups or []:
                    for item in g or []:
                        # Three spellings, all seen on real executions. The Gemini node reports
                        # `tokenUsageEstimate` where the OpenAI node reports `tokenUsage`, so
                        # reading only the latter silently drops every Gemini call - 6,002 prompt
                        # tokens on the first report run. Which spelling supplied the figure is
                        # recorded, since an estimate is weaker evidence than a reported count.
                        j = (item or {}).get("json", {}) or {}
                        usage, kind = j.get("tokenUsage"), "reported"
                        if not usage:
                            usage, kind = j.get("tokenUsageEstimate"), "estimated"
                        if not usage:
                            usage, kind = (j.get("response") or {}).get("tokenUsage"), "reported"
                        if usage:
                            t = tokens.setdefault(node, {"calls": 0, "prompt": 0, "completion": 0,
                                                         "total": 0, "source": kind})
                            t["calls"] += 1
                            t["prompt"] += usage.get("promptTokens", 0)
                            t["completion"] += usage.get("completionTokens", 0)
                            t["total"] += usage.get("totalTokens", 0)
                            if t["source"] != kind:
                                t["source"] = "mixed"
    calls.sort(key=lambda c: c["start_ms"] or 0)
    totals = {k: sum(t[k] for t in tokens.values())
              for k in ("calls", "prompt", "completion", "total")}
    totals["estimated_nodes"] = sorted(n for n, t in tokens.items() if t["source"] != "reported")
    if not tokens:
        totals["note"] = ("no token usage found on any ai_languageModel output - "
                          "inspect execution.raw.json and adjust the extractor")
    timing = {"started_at": execution.get("startedAt"), "stopped_at": execution.get("stoppedAt"),
              "status": execution.get("status"),
              "node_run_counts": {n: len(r or []) for n, r in run_data.items()}}
    return {"calls": calls, "count": len(calls)}, {"by_node": tokens, "totals": totals}, timing


def artifact_from_execution(execution: dict, node: str, key: str = "query"):
    runs = (execution.get("data", {}).get("resultData", {}) or {}).get("runData", {}).get(node)
    try:
        return runs[0]["data"]["main"][0][0]["json"][key]
    except (KeyError, IndexError, TypeError):
        return None


# --------------------------------------------------------------------------- payload assembly
def build_payload(step: str, base: dict, rep) -> dict:
    """Fill runtime fields, and substitute this replicate's upstream outputs."""
    p = json.loads(json.dumps(base))
    container = rep.container_dir
    if "process_folder_path" in p:
        p["process_folder_path"] = container
    if "protocols_folder" in p:
        p["protocols_folder"] = f"{container}/protocols"
    if "analysis_folder" in p:
        p["analysis_folder"] = f"{container}/analysis"
    if "expected_save_path" in p:
        p["expected_save_path"] = f"{container}/analysis/analysis_script_{p.get('process_id')}.py"
    if "timestamp" in p:
        p["timestamp"] = now()

    if step == "database":
        # the analysis script this replicate produced, not the reference one
        produced = rep.template_dir / "analysis" / f"analysis_script_{p['process_id']}.py"
        if produced.exists():
            p["analysis_script"] = produced.read_text(encoding="utf-8", errors="replace")
    if step == "report":
        plots = rep.scan_plots()
        if plots is not None:
            p["selected_plots"] = plots
            p["prompt"] = rep.rebuild_report_prompt(p, plots)
    return p


# --------------------------------------------------------------------------- failure class
# Provider-side and transport failures. These say nothing about the agent - a 429 on a free-tier
# quota measures our billing plan - so they are retried and never counted as an attempt.
INFRA_SIGNATURES = re.compile(
    r"\b(429|502|503|504)\b|too many requests|rate.?limit|quota|resource[ _]exhausted|"
    r"overloaded|service unavailable|bad gateway|gateway time-?out|internal server error|"
    r"connection (?:reset|refused|aborted|closed)|econnreset|econnrefused|etimedout|"
    r"socket hang up|temporarily unavailable|please try again later",
    re.I)


def classify_failure(execution: dict | None, code: int, text: str) -> tuple[str, str | None]:
    """('infrastructure'|'agent', matched signature).

    Only ERROR channels are scanned. A successful body is the model's own output and may legitimately
    contain the word "quota" - reading that as a rate limit would silently convert an agent failure
    into an untracked retry, which is the one mistake this classification exists to prevent.

    A non-200 from the webhook with NO execution behind it means the request never reached a
    workflow at all - n8n down, workflow inactive, webhook not re-registered after an edit. The
    agent was never asked, so counting it as an attempt would measure our deployment. When there IS
    an execution the workflow did run, and a 500 out of it can be the agent's own failure, so the
    normal signature scan applies.
    """
    if code != 200 and not execution:
        return "infrastructure", f"webhook returned {code} with no execution"

    # An execution that did not reach a verdict was interrupted rather than answered: n8n restarted
    # or was killed under it, the container died, the run was cancelled. `error` is excluded on
    # purpose - that is the workflow running to a genuine failure, which can be the agent's own.
    status = str((execution or {}).get("status") or "").lower()
    if status in {"crashed", "running", "waiting", "new", "canceled", "cancelled"}:
        return "infrastructure", f"execution {status} - interrupted, not answered"

    haystack = []
    if code != 200:
        haystack.append(text or "")
    result_data = ((execution or {}).get("data", {}) or {}).get("resultData", {}) or {}
    err = result_data.get("error") or {}
    haystack += [str(err.get("message", "")), str(err.get("description", ""))]
    for runs in (result_data.get("runData") or {}).values():
        for run in runs or []:
            e = run.get("error") or {}
            haystack += [str(e.get("message", "")), str(e.get("description", "")),
                         str((e.get("context") or {}).get("data", ""))[:500]]
    m = INFRA_SIGNATURES.search("\n".join(h for h in haystack if h))
    return ("infrastructure", m.group(0)) if m else ("agent", None)


# --------------------------------------------------------------------------- one agent
def attempt_once(step: str, rep, cfg, hub, timeout: int, out: pathlib.Path) -> dict:
    # Some agents are per lab (orchestration knows the instrument set), so the template may point a
    # step at a different entry in agents.json than the default.
    tpl = templates.get(getattr(rep, "template_name", "isotherm"))
    agent_key = tpl.get("agent_overrides", {}).get(step, AGENT_OF[step])
    spec = cfg["agents"][agent_key]
    base = json.loads((ROOT / "payloads" / rep.template_name /
                       f"{step}.payload.json").read_text(encoding="utf-8"))
    payload = build_payload(step, base, rep)

    out.mkdir(parents=True, exist_ok=True)

    job_id = None
    if spec["mode"] == "callback":
        job_id = f"{payload['process_id']}_{payload['device_type']}_{payload['step_id']}_" \
                 f"{rep.index:03d}_{int(time.time() * 1000) % 10**9}"
        payload["job_id"] = job_id
        payload["callback_url"] = (f"http://{cfg['callback_host']}:{cfg['callback_port']}"
                                   f"/api/protocol_callback")
        hub.expect(job_id)

    (out / "request.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8", newline="\n")

    url = f"{cfg['n8n_base_url'].rstrip('/')}/webhook/{spec['webhook']}"
    t0 = time.time()
    code, text = post_json(url, payload, timeout=timeout)
    (out / "response.json").write_text(text + "\n", encoding="utf-8", newline="\n")

    artifact, status = None, "unknown"
    if code != 200:
        status = "webhook_error"
    elif spec["mode"] == "callback":
        got = hub.wait(job_id, timeout - (time.time() - t0))
        if got:
            (out / "callback.json").write_text(
                json.dumps(got["body"], indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8", newline="\n")
            artifact = got["body"].get("protocol")
            status = "completed" if artifact else "callback_without_artifact"
        else:
            status = "timeout"
        hub.release(job_id)
    else:
        try:
            artifact = json.loads(text).get(spec["artifact_key"])
            status = "completed" if artifact else "no_artifact_in_response"
        except json.JSONDecodeError:
            status = "non_json_response"

    wall = time.time() - t0
    execution = find_execution(cfg, spec["workflow_id"], job_id, t0)
    tool, tokens, timing = ({"count": None}, {"totals": {}}, {})
    if execution:
        (out / "execution.raw.json").write_text(
            json.dumps(execution, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")
        tool, tokens, timing = extract_telemetry(execution, spec.get("tool_nodes", []))
        if artifact is None and spec.get("artifact_node"):
            artifact = artifact_from_execution(execution, spec["artifact_node"])
            if artifact:
                status = "completed_artifact_from_execution"
        for name, obj in (("toolcalls", tool), ("tokens", tokens), ("timing", timing)):
            (out / f"{name}.json").write_text(json.dumps(obj, indent=2) + "\n",
                                              encoding="utf-8", newline="\n")

    if artifact:
        (out / f"artifact{spec['artifact_ext']}").write_text(artifact, encoding="utf-8",
                                                             newline="\n")
        rep.place_artifact(step, artifact, json.loads(text) if code == 200 else {})

    failure_class, signature = (None, None)
    if status not in SUCCESS:
        failure_class, signature = classify_failure(execution, code, text)

    result = {"step": step, "status": status, "wall_clock_s": round(wall, 1), "job_id": job_id,
              "artifact_chars": len(artifact) if artifact else 0,
              "execution_id": (execution or {}).get("id"),
              "execution_status": (execution or {}).get("status"),
              "tool_calls": tool.get("count"),
              "tokens_total": tokens.get("totals", {}).get("total"),
              "failure_class": failure_class,
              "failure_signature": signature,
              "finished_at": now()}
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n",
                                     encoding="utf-8", newline="\n")
    return result


# --------------------------------------------------------------------------- retry wrapper
INFRA_BACKOFF_S = [30, 60, 120, 240]


def invoke(step: str, rep, cfg, hub, timeout: int, agent_retries: int = 0, log=print) -> dict:
    """Run one agent, retrying infrastructure failures freely and agent failures on request.

    The two are deliberately not the same thing. A provider rate limit or a dropped connection is a
    property of our billing plan and our network; retrying it costs nothing in interpretation and
    it is not recorded as an attempt. A malformed or empty model response is the phenomenon being
    measured; retrying it is legitimate - a lab user would press the button again - but it MUST be
    counted, because "succeeded on the first attempt" and "succeeded eventually" are different
    claims and the manuscript makes the first one.

    So `attempts` records every agent attempt, `first_attempt_status` is preserved separately, and
    tokens and wall clock are summed across everything actually spent. Superseded attempts keep
    their full evidence under agents/<step>/superseded/attempt_<n>/.
    """
    base_out = rep.agent_dir(step)
    attempts, infra_retries = [], 0
    attempt_no, result = 0, None

    while True:
        attempt_no += 1
        result = attempt_once(step, rep, cfg, hub, timeout, base_out)
        if result["status"] in SUCCESS:
            attempts.append(_attempt_record(attempt_no, result))
            break

        if result["failure_class"] == "infrastructure" and infra_retries < len(INFRA_BACKOFF_S):
            delay = INFRA_BACKOFF_S[infra_retries]
            infra_retries += 1
            attempt_no -= 1  # not an attempt: the agent never got a fair run
            _supersede(base_out, f"infra_{infra_retries}")
            log(f"    {step:<20} infrastructure failure ({result['failure_signature']}), "
                f"retry {infra_retries}/{len(INFRA_BACKOFF_S)} in {delay}s")
            time.sleep(delay)
            continue

        attempts.append(_attempt_record(attempt_no, result))
        if len(attempts) > agent_retries:
            break
        _supersede(base_out, f"attempt_{attempt_no}")
        log(f"    {step:<20} {result['status']}, agent retry "
            f"{len(attempts)}/{agent_retries}")

    result["attempts"] = attempts
    result["attempt_count"] = len(attempts)
    result["first_attempt_status"] = attempts[0]["status"]
    result["converged_first_attempt"] = attempts[0]["status"] in SUCCESS
    result["infrastructure_retries"] = infra_retries
    result["tokens_successful_attempt"] = result["tokens_total"]
    result["wall_clock_successful_attempt_s"] = result["wall_clock_s"]
    result["tokens_total"] = sum(a.get("tokens_total") or 0 for a in attempts)
    result["wall_clock_s"] = round(sum(a.get("wall_clock_s") or 0 for a in attempts), 1)
    (base_out / "result.json").write_text(json.dumps(result, indent=2) + "\n",
                                          encoding="utf-8", newline="\n")
    return result


def _attempt_record(n: int, r: dict) -> dict:
    return {"attempt": n, "status": r["status"], "wall_clock_s": r["wall_clock_s"],
            "tool_calls": r["tool_calls"], "tokens_total": r["tokens_total"],
            "execution_id": r["execution_id"], "failure_class": r["failure_class"],
            "failure_signature": r["failure_signature"]}


def _supersede(out: pathlib.Path, label: str):
    """Move a superseded attempt's evidence aside so the retry starts from a clean directory."""
    dest = out / "superseded" / label
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for item in list(out.iterdir()):
        if item.name == "superseded":
            continue
        shutil.move(str(item), str(dest / item.name))


SUCCESS = {"completed", "completed_artifact_from_execution"}

# An upstream artifact copied in from another replicate rather than generated here. It satisfies a
# dependency but is NEVER a success: it is excluded from agents_succeeded, from the token and
# wall-clock totals, and from first-attempt convergence, because no agent was called for it. Used to
# measure one agent across many replicates without regenerating upstream agents whose output that
# agent does not actually consume - orchestration, for instance, receives protocol FILENAMES, not
# protocol content.
SEEDED = "seeded"
DEP_SATISFIED = SUCCESS | {SEEDED}


TERMINAL = SUCCESS | {"webhook_error", "timeout", "no_artifact_in_response",
                      "non_json_response", "callback_without_artifact", "not_run"}


def run_chain(rep, cfg, hub, timeout: int, log=print, retry_failed: bool = False,
              agent_retries: int = 0, only: set | None = None) -> dict:
    """Execute the chain for one replicate, honouring dependencies.

    Resume treats any agent with a recorded TERMINAL status as done, not just a successful one.
    Re-running a run is meant to continue an interrupted one, not to give a failed agent a second
    attempt - that would quietly inflate the convergence rate this study reports. --retry-failed
    opts into the other behaviour explicitly.

    `agent_retries` is a different thing entirely: extra attempts WITHIN a single run, recorded as
    attempts so first-attempt and eventual convergence stay separable. Infrastructure failures are
    retried regardless and are not attempts at all (see `invoke`).
    """
    state = rep.load_state()
    done_states = SUCCESS if retry_failed else TERMINAL
    executed = 0
    # the chain is per template: the FPLC experiment has no device_protocol steps
    chain_steps = chain_for(getattr(rep, "template_name", "isotherm"))
    for step, _payload, deps in chain_steps:
        # `only` runs a subset of the chain, for working one agent at a time. An unselected step is
        # left exactly as it is - not run, and NOT marked not_run - so nothing is destroyed and a
        # later run picks it up normally.
        if only and step not in only and AGENT_OF[step] not in only:
            log(f"    {step:<20} skip (not selected)")
            continue
        prior = state["agents"].get(step, {})
        if prior.get("status") in done_states:
            log(f"    {step:<20} skip (already {prior['status']})")
            continue
        blocked = [d for d in deps
                   if state["agents"].get(d, {}).get("status") not in DEP_SATISFIED]
        if blocked:
            state["agents"][step] = {"status": "not_run", "blocked_by": blocked,
                                     "finished_at": now()}
            log(f"    {step:<20} not_run (blocked by {', '.join(blocked)})")
            rep.save_state(state)
            continue
        log(f"    {step:<20} running ...")
        result = invoke(step, rep, cfg, hub, timeout, agent_retries=agent_retries, log=log)
        state["agents"][step] = result
        executed += 1
        rep.save_state(state)
        extra = ""
        if result["attempt_count"] > 1 or result["infrastructure_retries"]:
            extra = (f"  [attempts={result['attempt_count']}"
                     f" first={result['first_attempt_status']}"
                     f" infra_retries={result['infrastructure_retries']}]")
        log(f"    {step:<20} {result['status']}  {result['wall_clock_s']}s  "
            f"tools={result['tool_calls']} tokens={result['tokens_total']}{extra}")

    wanted = {s for s, _p, _d in chain_steps}
    done = [s for s, a in state["agents"].items()
            if a.get("status") in SUCCESS and s in wanted]
    state["generation_complete"] = len(done) == len(chain_steps)
    state["agents_succeeded"] = len(done)
    state["agents_total"] = len(chain_steps)
    state["agents_executed_this_run"] = executed
    rep.save_state(state)
    return state
