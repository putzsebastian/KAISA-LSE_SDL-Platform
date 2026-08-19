#!/usr/bin/env python3
"""Create replicates whose upstream artifacts are copied in rather than generated.

Why this exists. Measuring one agent across many replicates does not require regenerating the
agents above it in the chain when that agent does not consume their output. The orchestration
payload carries `protocol_files` — filenames, devices and methods — and never protocol content, so
its input is identical no matter what the device-protocol agent produced. Regenerating five
protocols per replicate to obtain one orchestration draw would cost roughly five times the tokens
and measure nothing extra.

Copied agents are recorded with status `seeded` and a `seeded_from` field. That satisfies the
dependency gate but is deliberately NOT a success: seeded steps are excluded from
`agents_succeeded`, from token and wall-clock totals, and from first-attempt convergence, because no
agent was called. Anything derived from them stays honest.

Sources are cycled, so N new replicates spread evenly over the available donors instead of all
inheriting one.

Usage:
    python seed_upstream.py --run <run> --from 1,2,3,4,5 --new 6-20 \
                            --agents device_protocol_1,...,analysis
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import launch as launch_mod  # noqa: E402
import chain  # noqa: E402

# what each seeded step puts on disk, relative to the replicate directory
ARTIFACTS = {
    "analysis": ["analysis"],
    **{f"device_protocol_{i}": ["protocols"] for i in range(1, 6)},
}


def parse_range(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--from", dest="sources", required=True, help="donor replicate indices")
    ap.add_argument("--new", required=True, help="indices to create, e.g. 6-20")
    ap.add_argument("--agents", required=True, help="chain steps to seed")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = launch_mod.load_config()
    root = (pathlib.Path(cfg["_repo"]) / cfg["host_scratch_root"]).parent / "runs" / args.run
    sources, new = parse_range(args.sources), parse_range(args.new)
    steps = [s.strip() for s in args.agents.split(",") if s.strip()]

    missing = [i for i in sources if not (root / f"replicate_{i:03d}" / "state.json").exists()]
    if missing:
        print(f"donor replicate(s) not found: {missing}")
        return 1

    for n, index in enumerate(new):
        src = root / f"replicate_{sources[n % len(sources)]:03d}"
        dst = root / f"replicate_{index:03d}"
        src_state = json.loads((src / "state.json").read_text(encoding="utf-8"))
        absent = [s for s in steps if src_state.get("agents", {}).get(s, {}).get("status")
                  not in chain.SUCCESS]
        if absent:
            print(f"replicate_{index:03d}: donor {src.name} has no successful {absent}; skipped")
            continue

        print(f"replicate_{index:03d}  <- {src.name}"
              f"{'  (dry run)' if args.dry_run else ''}")
        if args.dry_run:
            continue

        dst.mkdir(parents=True, exist_ok=True)
        for step in steps:
            for rel in ARTIFACTS.get(step, []):
                s, d = src / rel, dst / rel
                if s.is_dir() and not d.exists():
                    shutil.copytree(s, d)
        state = {"agents": {}}
        sp = dst / "state.json"
        if sp.exists():
            state = json.loads(sp.read_text(encoding="utf-8"))
        for step in steps:
            state.setdefault("agents", {})[step] = {
                "step": step, "status": chain.SEEDED, "seeded_from": src.name,
                "wall_clock_s": 0, "tool_calls": 0, "tokens_total": 0,
                "note": "artifact copied from another replicate; no agent was called",
            }
        sp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"\nseeded {len(new)} replicate(s) from {len(sources)} donor(s): "
          f"{steps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
