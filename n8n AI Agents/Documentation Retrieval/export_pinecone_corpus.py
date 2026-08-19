#!/usr/bin/env python3
"""
Export a Pinecone index to a portable JSONL corpus.

Run by the data owner with their own Pinecone credentials. Produces one JSON
object per line:

    {"id": "<vec-id>", "text": "<chunk text>", "metadata": {...}}

The exported text is the document chunk stored in the vector's metadata (this is
how LangChain / the n8n Pinecone node persist the source text). Embeddings are
NOT exported by default — they are model-specific and large; re-embed the text
with `ingest_to_qdrant.py` instead. Pass --include-vectors to keep them anyway.

No secrets are hardcoded. Reads PINECONE_API_KEY from the environment.

Usage:
    set PINECONE_API_KEY=...                      (PowerShell: $env:PINECONE_API_KEY="...")
    python export_pinecone_corpus.py --index opentrons-test --out corpus.jsonl

Note on licensing: the chunk text is derived from the Opentrons documentation
(© Opentrons). Only redistribute a corpus you have the right to share — prefer
building it from the Apache-2.0 docs source. See README.md.
"""
import argparse
import json
import os
import sys

# metadata keys commonly used to store the chunk text, tried in order
DEFAULT_TEXT_KEYS = ["text", "page_content", "content", "_node_content", "chunk", "pageContent"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Export a Pinecone index to JSONL.")
    ap.add_argument("--index", required=True, help="Pinecone index name")
    ap.add_argument("--namespace", default="", help="Namespace (default: '')")
    ap.add_argument("--out", default="corpus.jsonl", help="Output JSONL path")
    ap.add_argument("--text-key", default=None,
                    help="Metadata key holding the chunk text (auto-detected if omitted)")
    ap.add_argument("--include-vectors", action="store_true",
                    help="Also store the raw embedding under 'values' (large, model-specific)")
    args = ap.parse_args()

    api_key = os.environ.get("PINECONE_API_KEY")
    if not api_key:
        print("ERROR: set PINECONE_API_KEY in the environment.", file=sys.stderr)
        return 1

    try:
        from pinecone import Pinecone
    except ImportError:
        print("ERROR: pip install pinecone>=5.0", file=sys.stderr)
        return 1

    pc = Pinecone(api_key=api_key)
    index = pc.Index(args.index)
    text_keys = [args.text_key] if args.text_key else DEFAULT_TEXT_KEYS

    written = 0
    missing_text = 0
    with open(args.out, "w", encoding="utf-8") as fh:
        # index.list() paginates IDs (serverless indexes). For pod-based indexes
        # that don't support list(), supply IDs another way.
        for id_batch in index.list(namespace=args.namespace):
            resp = index.fetch(ids=id_batch, namespace=args.namespace)
            for vid, rec in resp.vectors.items():
                md = dict(rec.metadata or {})
                text = next((md[k] for k in text_keys if k and k in md), None)
                if text is None:
                    missing_text += 1
                rest = {k: v for k, v in md.items() if k not in text_keys}
                row = {"id": vid, "text": text, "metadata": rest}
                if args.include_vectors:
                    row["values"] = list(rec.values)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1

    print(f"Wrote {written} records to {args.out}")
    if missing_text:
        print(f"WARNING: {missing_text} records had no recognizable text field "
              f"(tried {text_keys}). Use --text-key to set it explicitly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
