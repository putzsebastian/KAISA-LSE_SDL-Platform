# Documentation retrieval (Opentrons API docs vector store)

The device-protocol agents query a vector store (`Get_Opentrons_API_Info` tool) of the
**Opentrons Python API documentation** to ground protocol generation. In the authors'
deployment this is a **Pinecone** index; this folder lets you reproduce it on a
self-hostable **Qdrant** instance so no external SaaS account is required.

Pipeline: **corpus (JSONL) → embed (OpenAI) → Qdrant collection → point the n8n node at it.**

---

## Licensing

The corpus is built from the Opentrons **Python API 2.19 markdown docs** in the
`Opentrons/opentrons` monorepo (`opentrons-ai-server/api/data/` — files
`python_api_219_docs.md`, `python_api_219_reference.md`), which is licensed
**Apache-2.0**. It is therefore redistributable **with attribution + the Apache-2.0
notice** (`build_corpus.py` stamps `license` / `attribution` into each chunk's metadata).

You may either commit a built `corpus.jsonl` (keeping the attribution) or — cleaner and
fully reproducible — ship only the build script and let users regenerate it. By default
`corpus*.jsonl` is git-ignored here; delete that rule in `.gitignore` if you choose to
commit the corpus.

Do **not** scrape the rendered docs site (`docs.opentrons.com`) — that content is marked
"all rights reserved". Use the Apache-2.0 markdown source above.

---

## Corpus format

One JSON object per line:

```json
{"id": "chunk-001", "text": "<documentation chunk>", "metadata": {"source": "https://docs.opentrons.com/v2/...", "section": "..."}}
```

Only `text` is required by the ingester (`id`/`metadata` optional).

---

## Step 1 — get a corpus

**Recommended — build from the Apache-2.0 docs source** (no Pinecone account, reproducible):
```powershell
pip install -r requirements.txt          # build_corpus.py itself needs no extra deps
python build_corpus.py --ref edge --out corpus.jsonl
# For a reproducible build, pin a tag or commit instead of the moving 'edge' branch:
#   python build_corpus.py --ref v8.5.0 --out corpus.jsonl
```
Downloads `python_api_219_docs.md` + `python_api_219_reference.md` from
`Opentrons/opentrons` and chunks them (heading-aware, ~1200 chars).

**Alternative — export your existing Pinecone index:**
```powershell
$env:PINECONE_API_KEY="..."
python export_pinecone_corpus.py --index opentrons-test --out corpus.jsonl
```

## Step 2 — start Qdrant
```powershell
docker compose -f docker-compose.qdrant.yml up -d
curl.exe http://localhost:6333/healthz
```

## Step 3 — ingest
```powershell
$env:OPENAI_API_KEY="..."
python ingest_to_qdrant.py --corpus corpus.jsonl --collection opentrons_docs
```
The embedding model (`--model`, default `text-embedding-3-small`, 1536-dim) **must match**
the model on the n8n *Embeddings OpenAI* node that queries this collection.

## Step 4 — point n8n at Qdrant

Qdrant is already part of `../docker-compose.yml` (service `qdrant`, pre-loaded from the
shipped snapshot — see below), so n8n reaches it on the `sdl` network. In each
device-protocol agent workflow:

1. **Create a Qdrant credential** — n8n **Credentials → New → "Qdrant API"**:
   - **Qdrant URL**: `http://qdrant:6333` (service name on the shared network; use
     `http://localhost:6333` only if n8n runs outside the compose stack)
   - **API Key**: leave **empty** (local Qdrant has no auth)
2. Replace the **Pinecone Vector Store** node feeding `Get_Opentrons_API_Info` with a
   **Qdrant Vector Store** node → select the credential above, collection `opentrons_docs`.
3. Keep the existing **Embeddings OpenAI** node, and set its model **explicitly** to the
   same one used at ingest (`text-embedding-3-small`).

The agent then retrieves Opentrons docs from the local Qdrant instead of Pinecone.

> The Qdrant credential is **not exported with the workflows** — credentials are
> instance-specific, and the workflow JSON references them only by a scrubbed
> `<CREDENTIAL_ID>`. Create it once as above (it holds no secret beyond the local URL).

---

## (Recommended) Ship the collection pre-ingested — Qdrant snapshot

So end users need **no OpenAI key and no ingest step**, ship a Qdrant *snapshot*. The main
`../docker-compose.yml` restores it into the `opentrons_docs` collection on startup
(`--snapshot .../opentrons_docs.snapshot:opentrons_docs --force-snapshot`).

Produce it once (you, with your OpenAI key):
```powershell
docker compose -f docker-compose.qdrant.yml up -d
$env:OPENAI_API_KEY="..."
python build_corpus.py --ref edge --out corpus.jsonl          # if not already built
python ingest_to_qdrant.py --corpus corpus.jsonl --collection opentrons_docs
python make_snapshot.py --collection opentrons_docs --out snapshots/opentrons_docs.snapshot
```
Then:
1. **Commit** `snapshots/opentrons_docs.snapshot` (Apache-2.0 text + your OpenAI embeddings — keep the Opentrons attribution).
2. **Pin** the `qdrant/qdrant:` image tag in **both** compose files to the version you used here (snapshots are version-specific).

After that, `docker compose up` in `n8n AI Agents/` brings up Qdrant **already populated** —
the `Get_Opentrons_API_Info` tool works out of the box once its node points at Qdrant
(`http://qdrant:6333`, collection `opentrons_docs`).

---

## Files
| File | Purpose |
|---|---|
| `build_corpus.py` | Build `corpus.jsonl` from the Apache-2.0 Opentrons docs source (recommended) |
| `export_pinecone_corpus.py` | Alternative: dump an existing Pinecone index → `corpus.jsonl` |
| `ingest_to_qdrant.py` | Embed `corpus.jsonl` and upsert into a Qdrant collection |
| `make_snapshot.py` | Create + download a Qdrant snapshot to ship a pre-ingested collection |
| `docker-compose.qdrant.yml` | Run Qdrant locally (for building/ingesting/snapshotting) |
| `requirements.txt` | Python deps (`build_corpus.py` / `make_snapshot.py` need none beyond stdlib) |

Secrets (`PINECONE_API_KEY`, `OPENAI_API_KEY`) are read from environment variables — never hardcode them.
