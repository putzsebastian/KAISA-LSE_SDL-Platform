#!/usr/bin/env python3
"""
Create a Qdrant collection snapshot and download it for shipping.

After you have ingested the collection (build_corpus.py -> ingest_to_qdrant.py) into a
running Qdrant, run this to produce the snapshot that the main `docker-compose.yml`
restores on startup. Commit the resulting file so end users get a pre-ingested
collection with no OpenAI key / ingest step.

Stdlib only (urllib).

Usage:
    docker compose -f docker-compose.qdrant.yml up -d
    python ingest_to_qdrant.py --corpus corpus.jsonl --collection opentrons_docs
    python make_snapshot.py --collection opentrons_docs --out snapshots/opentrons_docs.snapshot

Then PIN the qdrant image tag in BOTH compose files to the version you used here
(`docker exec <qdrant> ./qdrant --version`, or check the image tag) — snapshots are
version-specific.
"""
import argparse
import json
import os
import sys
import urllib.request


def _req(method: str, url: str) -> bytes:
    r = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(r, timeout=300) as resp:
        return resp.read()


def main() -> int:
    ap = argparse.ArgumentParser(description="Create + download a Qdrant collection snapshot.")
    ap.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    ap.add_argument("--collection", default="opentrons_docs")
    ap.add_argument("--out", default="snapshots/opentrons_docs.snapshot")
    args = ap.parse_args()
    base = args.qdrant_url.rstrip("/")

    print(f"Creating snapshot of '{args.collection}' on {base} ...")
    try:
        created = json.loads(_req("POST", f"{base}/collections/{args.collection}/snapshots"))
    except Exception as e:
        print(f"ERROR creating snapshot (is the collection ingested?): {e}", file=sys.stderr)
        return 1
    name = created["result"]["name"]
    print(f"  snapshot name: {name}")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    data = _req("GET", f"{base}/collections/{args.collection}/snapshots/{name}")
    with open(args.out, "wb") as fh:
        fh.write(data)

    print(f"Saved {args.out} ({len(data) / 1e6:.1f} MB)")
    print("Next: commit this file, and pin the qdrant image tag in both compose files to "
          "the Qdrant version used here. The main stack will then restore it on `docker compose up`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
