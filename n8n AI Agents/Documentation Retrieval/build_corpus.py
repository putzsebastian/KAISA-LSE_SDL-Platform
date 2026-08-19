#!/usr/bin/env python3
"""
Build the documentation corpus from the Opentrons Apache-2.0 docs source.

Downloads the Python API 2.19 markdown docs from the Opentrons monorepo
(Apache License 2.0) and chunks them into a portable JSONL corpus:

    {"id": "...", "text": "<chunk>", "metadata": {"source": "<url>", "file": "...", "section": "..."}}

Source (Apache-2.0): https://github.com/Opentrons/opentrons  ->  opentrons-ai-server/api/data
This is the license-clean, reproducible way to (re)create the corpus — no web
scraping, no Pinecone account. Attribute Opentrons and keep the Apache-2.0 notice
when redistributing.

Stdlib only (urllib) — no extra dependencies.

Usage:
    python build_corpus.py --out corpus.jsonl
    python build_corpus.py --ref v8.5.0 --out corpus.jsonl     # pin a tag/commit for reproducibility
"""
import argparse
import json
import re
import sys
import urllib.request

DEFAULT_FILES = ["python_api_219_docs.md", "python_api_219_reference.md"]
RAW = "https://raw.githubusercontent.com/Opentrons/opentrons/{ref}/opentrons-ai-server/api/data/{name}"
BLOB = "https://github.com/Opentrons/opentrons/blob/{ref}/opentrons-ai-server/api/data/{name}"


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read().decode("utf-8")


def chunk_markdown(text: str, max_chars: int = 1200, overlap_paras: int = 1):
    """Greedy paragraph-packing chunker that tracks the nearest markdown heading."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur, cur_len, section = [], [], 0, ""
    for p in paras:
        m = re.match(r"^#{1,6}\s+(.*)", p)
        if m:
            section = m.group(1).strip()
        if cur and cur_len + len(p) > max_chars:
            chunks.append(("\n\n".join(cur).strip(), section))
            cur = cur[-overlap_paras:] if overlap_paras else []
            cur_len = sum(len(x) for x in cur)
        cur.append(p)
        cur_len += len(p)
    if cur:
        chunks.append(("\n\n".join(cur).strip(), section))
    return chunks


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Opentrons-docs corpus (Apache-2.0 source).")
    ap.add_argument("--ref", default="edge",
                    help="Git ref (branch/tag/commit) of Opentrons/opentrons. "
                         "Pin a tag or commit SHA for reproducibility (default: edge)")
    ap.add_argument("--files", nargs="+", default=DEFAULT_FILES,
                    help="Markdown filenames under opentrons-ai-server/api/data/")
    ap.add_argument("--out", default="corpus.jsonl", help="Output JSONL path")
    ap.add_argument("--max-chars", type=int, default=1200, help="Approx max characters per chunk")
    args = ap.parse_args()

    total = 0
    with open(args.out, "w", encoding="utf-8") as fh:
        for name in args.files:
            url = RAW.format(ref=args.ref, name=name)
            blob = BLOB.format(ref=args.ref, name=name)
            print(f"Fetching {url}")
            try:
                md = fetch(url)
            except Exception as e:
                print(f"ERROR fetching {name}: {e}", file=sys.stderr)
                return 1
            chunks = chunk_markdown(md, max_chars=args.max_chars)
            for i, (chunk_text, section) in enumerate(chunks):
                row = {
                    "id": f"{name}-{i:04d}",
                    "text": chunk_text,
                    "metadata": {"source": blob, "file": name, "section": section,
                                 "license": "Apache-2.0", "attribution": "Opentrons Labworks Inc."},
                }
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                total += 1
            print(f"  {name}: {len(chunks)} chunks")

    print(f"Wrote {total} chunks to {args.out}")
    print("Source: Opentrons/opentrons (Apache-2.0). Attribute Opentrons when redistributing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
