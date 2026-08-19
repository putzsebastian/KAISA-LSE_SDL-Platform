#!/usr/bin/env python3
"""Per-replicate working directory: seeding, artifact placement, state.

The replicate's template is seeded with ONLY the files no agent produces - the script generator,
the Tecan protocol, the custom labware definition, the workflow JSON. The agent-produced files are
deliberately absent, so if an agent fails there is no reference file lying around for the scorer to
compare against itself and report a spurious pass.

The directory lives under the app container's mounted data volume, because the agents' own save
tools write into it over HTTP from inside that container. Finished replicates are copied out to
replication/runs/ as the durable record.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import templates

# Files the reference template contributes because no agent generates them, and where each agent's
# artifact belongs. Both are per template (see templates.py); the module-level names are kept as the
# isotherm defaults so existing helpers that import them keep working.
SEED = templates.get("isotherm")["seed"]
SUBDIRS = ["protocols", "analysis", "db", "reports", "data", "results", "config"]
ARTIFACT_TARGET = templates.get("isotherm")["artifact_target"]


class Replicate:
    def __init__(self, run_id: str, index: int, cfg: dict, reference_template: pathlib.Path,
                 template_name: str = "isotherm"):
        self.run_id = run_id
        self.index = index
        self.cfg = cfg
        self.template_name = template_name
        self.reference_template = reference_template
        rel = f"runs/{run_id}/replicate_{index:03d}"
        self.host_dir = (pathlib.Path(cfg["_repo"]) / cfg["host_scratch_root"]).parent / rel
        self.container_dir = f"{cfg['container_scratch_root'].rsplit('/', 1)[0]}/{rel}"
        self.template_dir = self.host_dir

    # ------------------------------------------------------------------ layout
    def agent_dir(self, step: str) -> pathlib.Path:
        return self.host_dir / "agents" / step

    @property
    def stub_port(self) -> int:
        """Per-replicate device-stub port. Replicates score in parallel, so a fixed 8000 would
        collide; the scripts' own default still lands on replicate 0's port."""
        return 8000 + self.index

    @property
    def scratch_db(self) -> str:
        return f"sdl_repl_{self.run_id}_{self.index:03d}".lower().replace("-", "_")

    @property
    def state_path(self) -> pathlib.Path:
        return self.host_dir / "state.json"

    def seed(self):
        for sub in SUBDIRS:
            (self.host_dir / sub).mkdir(parents=True, exist_ok=True)
        (self.host_dir / "agents").mkdir(parents=True, exist_ok=True)
        for rel in templates.get(self.template_name)["seed"]:
            src = self.reference_template / rel
            if src.exists():
                dst = self.host_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    shutil.copy2(src, dst)

    # ------------------------------------------------------------------ state
    def load_state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"run_id": self.run_id, "index": self.index, "agents": {}}

    def save_state(self, state: dict):
        self.state_path.write_text(json.dumps(state, indent=2) + "\n",
                                   encoding="utf-8", newline="\n")

    # ------------------------------------------------------------------ artifacts
    def place_artifact(self, step: str, artifact: str, response: dict):
        """Write the agent's output where the template expects it."""
        target = self.host_dir / templates.get(self.template_name)["artifact_target"][step]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact, encoding="utf-8", newline="\n")

        if step == "database":
            # The application's save_db_schema splits the agent's structured reply into
            # schema.json and field_mapping.json alongside the commit script.
            schema = {k: response[k] for k in
                      ("parameters_schema", "results_schema",
                       "expected_data_files", "expected_plot_files") if k in response}
            if schema:
                (self.host_dir / "db" / "schema.json").write_text(
                    json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")
            if "field_mapping" in response:
                (self.host_dir / "db" / "field_mapping.json").write_text(
                    json.dumps(response["field_mapping"], indent=2) + "\n",
                    encoding="utf-8", newline="\n")

    # ------------------------------------------------------------------ chain substitutions
    def scan_plots(self):
        """Plot candidates from THIS replicate's analysis script, via the application's own scan."""
        script_dir = self.container_dir + "/analysis"
        proc = subprocess.run(
            ["docker", "exec", "-e", "HOME=/tmp", "-w", "/app", self.cfg["app_container"],
             self.cfg["container_python"], "/tmp/harness/scan_plots.py", script_dir],
            capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            return None
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return None

    def rebuild_report_prompt(self, payload: dict, plots: list) -> str:
        """Reproduce api.py:2079-2101 with this replicate's plots."""
        workflow = json.loads((self.host_dir / "workflow.json").read_text(encoding="utf-8"))
        workflow = workflow.get("workflow", workflow)
        prompt = ("WORKFLOW CONTEXT:\n"
                  "The following workflow describes the experimental process:\n"
                  + json.dumps(workflow, indent=2)
                  + "\n\nUSER REQUEST:\n" + payload.get("original_prompt", ""))
        if plots:
            info = "\n\nSELECTED PLOTS TO INCLUDE:\n"
            for i, p in enumerate(plots, 1):
                info += f"{i}. {p['display_name']} ({p['filename']}) - {p.get('type', 'plot')}\n"
            info += ("\nPlease create specific sections in the HTML template for each selected "
                     "plot. Use placeholders like [[PLOT_1]], [[PLOT_2]], etc. for the plot "
                     "images. Include descriptive headings and proper styling for each plot "
                     "section. IMPORTANT: The plots will be uploaded to eLabFTW and need proper "
                     "download URLs.")
            prompt += info
        return prompt

    # ------------------------------------------------------------------ record
    def publish(self, runs_root: pathlib.Path):
        """Copy the replicate record out of the scratch volume into replication/runs/."""
        dst = runs_root / self.run_id / f"replicate_{self.index:03d}"
        dst.mkdir(parents=True, exist_ok=True)
        for item in self.host_dir.iterdir():
            if item.name == "__pycache__":
                continue
            target = dst / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        return dst
