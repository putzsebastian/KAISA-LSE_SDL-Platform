#!/usr/bin/env python3
"""In-container helper: the application's own plot scan over a replicate's analysis folder.

Emits the selected plots as a single JSON line on stdout. Uses the app's function rather than a
reimplementation so a replicate's report payload is built exactly as the wizard would build it.
"""
import json
import pathlib
import sys

sys.path.insert(0, "/app")
from blueprints.api import scan_evaluation_script_for_plots  # noqa: E402

folder = pathlib.Path(sys.argv[1])
candidates = scan_evaluation_script_for_plots(folder, sys.argv[2] if len(sys.argv) > 2 else "0")

seen, unique = set(), []
for c in candidates:
    if c["filename"] not in seen:
        seen.add(c["filename"])
        unique.append(c)

print(json.dumps([c for c in unique if c["filename"].endswith(".png")]))
