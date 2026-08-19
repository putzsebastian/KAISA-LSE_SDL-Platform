#!/usr/bin/env python3
"""Switch the LLM model on an agent workflow's chat nodes, with a restorable backup.

The model is set directly on each node rather than through a Config/Set node and expressions - n8n
silently ignores an expression in that field, so the run would quietly use whatever was baked in and
the comparison would be meaningless.

    python set_model.py --agent device_protocol --model gpt-5.5
    python set_model.py --agent device_protocol --restore
"""
from __future__ import annotations

import argparse
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKUPS = ROOT / "config" / "workflows"


def load_cfg():
    sec = {k.strip(): v.strip() for k, v in
           (line.split("=", 1) for line in
            (ROOT / "config" / "secrets.env").read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.startswith("#"))}
    cfg = json.loads((ROOT / "config" / "agents.json").read_text(encoding="utf-8"))
    return cfg, {"X-N8N-API-KEY": sec["N8N_API_KEY"], "Content-Type": "application/json"}, \
        cfg.get("n8n_base_url", "http://localhost:5678")


def call(base, headers, method, path, body=None):
    req = urllib.request.Request(base + path, headers=headers, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    return json.loads(urllib.request.urlopen(req, timeout=180).read() or b"{}")


def is_chat(node) -> bool:
    return "lmChat" in node.get("type", "")


def current_model(node):
    m = node.get("parameters", {}).get("model")
    return m.get("value") if isinstance(m, dict) else m


def set_model(node, model):
    p = node.setdefault("parameters", {})
    m = p.get("model")
    if isinstance(m, dict):
        m["value"] = model
        if "cachedResultName" in m:
            m["cachedResultName"] = model
    else:
        p["model"] = model


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--model")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()
    if not args.model and not args.restore:
        ap.error("give --model or --restore")

    cfg, H, BASE = load_cfg()
    wid = cfg["agents"][args.agent]["workflow_id"]
    backup = BACKUPS / ("%s_MODEL_BACKUP.json" % args.agent)

    if args.restore:
        if not backup.exists():
            print("no backup at %s" % backup)
            return 1
        saved = json.loads(backup.read_text(encoding="utf-8"))
        wf = call(BASE, H, "GET", "/api/v1/workflows/" + wid)
        by_name = {n["name"]: n for n in saved["nodes"]}
        for n in wf["nodes"]:
            if is_chat(n) and n["name"] in by_name:
                set_model(n, current_model(by_name[n["name"]]))
        call(BASE, H, "PUT", "/api/v1/workflows/" + wid,
             {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"],
              "settings": wf.get("settings", {})})
        print("restored models on %s:" % args.agent)
    else:
        wf = call(BASE, H, "GET", "/api/v1/workflows/" + wid)
        if not backup.exists():
            backup.write_text(json.dumps(wf, indent=2) + "\n", encoding="utf-8", newline="\n")
            print("backed up original workflow to %s" % backup.name)
        for n in wf["nodes"]:
            if is_chat(n):
                set_model(n, args.model)
        call(BASE, H, "PUT", "/api/v1/workflows/" + wid,
             {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"],
              "settings": wf.get("settings", {})})

    after = call(BASE, H, "GET", "/api/v1/workflows/" + wid)
    if not after.get("active"):
        call(BASE, H, "POST", "/api/v1/workflows/%s/activate" % wid)
        after = call(BASE, H, "GET", "/api/v1/workflows/" + wid)
    for n in after["nodes"]:
        if is_chat(n):
            print("   %-24s model=%s" % (n["name"], current_model(n)))
    print("   active: %s" % after.get("active"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
