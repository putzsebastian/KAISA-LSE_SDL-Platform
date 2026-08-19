#!/usr/bin/env python3
"""
Ingest a JSONL corpus into a Qdrant collection, embedding the text with OpenAI.

Input: a JSONL file (see export_pinecone_corpus.py or build your own) where each
line is:

    {"id": "...", "text": "<chunk text>", "metadata": {...}}

Each point is stored with a payload compatible with the n8n / LangChain Qdrant
vector-store node:

    { "page_content": "<chunk text>", "metadata": { ... } }

so the agent's `Get_Opentrons_API_Info` tool can read the retrieved content.

IMPORTANT: the embedding model here MUST match the model configured on the n8n
"Embeddings OpenAI" node that queries this collection (default: text-embedding-3-small,
1536 dimensions). If they differ, retrieval quality collapses.

No secrets are hardcoded. Reads OPENAI_API_KEY from the environment.

Usage:
    $env:OPENAI_API_KEY="..."
    docker compose -f docker-compose.qdrant.yml up -d
    python ingest_to_qdrant.py --corpus corpus.jsonl --collection opentrons_docs
"""
import argparse
import json
import os
import sys
import uuid

CONTENT_KEY = "page_content"   # payload key the n8n/LangChain Qdrant node reads
METADATA_KEY = "metadata"


def _point_id(raw):
    """Qdrant requires an unsigned int or a UUID. Coerce string IDs deterministically."""
    if raw is None:
        return str(uuid.uuid4())
    try:
        return int(raw)
    except (TypeError, ValueError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, str(raw)))


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest a JSONL corpus into Qdrant.")
    ap.add_argument("--corpus", default="corpus.jsonl", help="Input JSONL path")
    ap.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    ap.add_argument("--collection", default="opentrons_docs", help="Qdrant collection name")
    ap.add_argument("--model", default="text-embedding-3-small",
                    help="OpenAI embedding model (MUST match the n8n query node)")
    ap.add_argument("--dim", type=int, default=1536, help="Embedding dimension (3-small/ada-002=1536, 3-large=3072)")
    ap.add_argument("--batch", type=int, default=64, help="Embedding/upsert batch size")
    ap.add_argument("--recreate", action="store_true", help="Drop and recreate the collection first")
    args = ap.parse_args()

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("ERROR: set OPENAI_API_KEY in the environment.", file=sys.stderr)
        return 1

    try:
        from openai import OpenAI
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams, PointStruct
    except ImportError:
        print("ERROR: pip install -r requirements.txt", file=sys.stderr)
        return 1

    # Load corpus
    rows = []
    with open(args.corpus, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("text"):
                rows.append(r)
    if not rows:
        print(f"ERROR: no rows with a 'text' field in {args.corpus}", file=sys.stderr)
        return 1
    print(f"Loaded {len(rows)} chunks from {args.corpus}")

    oc = OpenAI(api_key=key)
    qc = QdrantClient(url=args.qdrant_url)

    if args.recreate and qc.collection_exists(args.collection):
        qc.delete_collection(args.collection)
    if not qc.collection_exists(args.collection):
        qc.create_collection(
            args.collection,
            vectors_config=VectorParams(size=args.dim, distance=Distance.COSINE),
        )
        print(f"Created collection '{args.collection}' (dim={args.dim}, cosine)")

    total = 0
    for i in range(0, len(rows), args.batch):
        chunk = rows[i:i + args.batch]
        resp = oc.embeddings.create(model=args.model, input=[r["text"] for r in chunk])
        vectors = [d.embedding for d in resp.data]
        if len(vectors[0]) != args.dim:
            print(f"ERROR: model '{args.model}' returns {len(vectors[0])}-dim vectors, "
                  f"but --dim is {args.dim}. Fix --dim to match.", file=sys.stderr)
            return 1
        points = [
            PointStruct(
                id=_point_id(r.get("id")),
                vector=v,
                payload={CONTENT_KEY: r["text"], METADATA_KEY: (r.get("metadata") or {})},
            )
            for r, v in zip(chunk, vectors)
        ]
        qc.upsert(collection_name=args.collection, points=points)
        total += len(points)
        print(f"  upserted {total}/{len(rows)}")

    print(f"Done. {total} points in Qdrant collection '{args.collection}' at {args.qdrant_url}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
