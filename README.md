# InsightOS POC 2 — Large-Scale Financial Document AI

Company-scoped RAG over financial PDFs with hierarchical chunking, hybrid retrieval,
knowledge graph extraction, and citations. Built on **LlamaIndex + Chroma + Neo4j** (optional).

Implements the architecture from `insightos_system_design.md` and the evaluation
criteria from `JIRA_KB/TICKET_7.md` (IN-7) / `JIRA_KB/SUB_TICKET_9.md` (IN-9).

This repository is meant to ship **source code + sample PDFs** under `documents/`.
All heavy artifacts live under `storage/` and are **gitignored** — you either build them locally (Path A) or copy in a bundle someone shares with you (Path B).

---

## Path A — Build everything from the PDFs (full pipeline)

Use this when you only have the repo and the annual reports in `documents/` (as listed in `manifest.yaml`).

### 1. Prerequisites

- Python 3.11+ (3.14 supported with this stack)
- A Gemini or OpenAI API key for LLM calls (ingestion embeddings use local Hugging Face models)

### 2. Install and configure

```bash
cd llamaIndexPOC_2
pip install -r requirements.txt
cp .env.example .env
# Edit .env — set GOOGLE_API_KEY (Gemini) or OPENAI_API_KEY / LLM_PROVIDER as needed
```

### 3. Ingest PDFs (Pipeline 1)

This parses every file in `manifest.yaml`, builds hierarchical chunks, writes **Chroma** under `storage/chroma/`, and saves **`storage/docstore.json`**, **`storage/bm25_corpus.json`**, and **`storage/registry.db`**.

```bash
python scripts/ingest.py
```

- **First run / new documents:** incremental ingest processes new or changed PDFs.
- **Wipe and rebuild from scratch:** `python scripts/ingest.py --reset` (deletes local docstore, BM25 file, Chroma collection for this project, then re-parses all PDFs).

### 4. Extract the knowledge graph (Pipeline 2)

Reads the same PDFs (and manifest); produces **`storage/kg_graph.json`**. Does not require Neo4j; optional push to Neo4j if `NEO4J_PASSWORD` is set.

```bash
python scripts/extract_kg.py
```

You can also trigger ingestion / KG from the web UI or `POST /api/ingest` and `POST /api/kg/extract`.

### 5. Run the app

```bash
uvicorn api.main:app --reload --port 8000
```

Open **http://localhost:8000** — chat (hybrid RAG), citations, and Knowledge Graph tab use the files under `storage/` you just created.

### 6. Neo4j (optional)

If you use Docker: `cd docker && docker compose up` (set `GOOGLE_API_KEY`). For query-time graph context, configure `NEO4J_*` and `USE_NEO4J_IN_QUERY` in `.env` after importing the graph (see `scripts/load_kg_json_to_neo4j.py`).

---

## Path B — You received the three JSON files separately

Someone may give you a bundle so you can skip long PDF parsing and KG extraction. The usual trio is:

| File | Role |
|------|------|
| **`storage/docstore.json`** | Full hierarchical node store (required to rebuild vectors without re-parsing PDFs) |
| **`storage/bm25_corpus.json`** | Keyword (BM25) index input used by hybrid retrieval |
| **`storage/kg_graph.json`** | Knowledge graph for the Graph tab and graph context in chat |

**Chroma** (`storage/chroma/`) is never shipped as a folder you copy; it is always built on your machine from the docstore (embeddings are environment-specific).

### 1. Clone the repo and install

Same as Path A: `pip install -r requirements.txt`, copy `.env.example` → `.env`, set API keys.

### 2. Keep PDFs and manifest aligned

The repo includes sample PDFs under `documents/` matching `manifest.yaml`. **Use the same PDF set (and paths) the bundle was built from** so citations and metadata (file name, page labels) line up. If your bundle was built on different files, replace `documents/` and update `manifest.yaml` accordingly.

### 3. Copy the JSON files into `storage/`

Create `storage/` if needed, then place:

```text
storage/docstore.json
storage/bm25_corpus.json
storage/kg_graph.json
```

(Optional but useful) If the provider also sends **`storage/registry.db`**, copy it too. That records which PDFs were already ingested so a normal `ingest` run will not re-parse them.

### 4. Build Chroma (vectors) locally — required for chat

You must create **`storage/chroma/`** once on your machine.

**Recommended (explicit):**

```bash
python scripts/ingest.py --rebuild-vectors
```

This loads `docstore.json`, recomputes leaf nodes, embeds them into Chroma, and refreshes `bm25_corpus.json` from those leaves (so BM25 stays consistent with the docstore).

**Alternative:** If you also have **`registry.db`** from the provider and your PDFs match the registry, you can run:

```bash
python scripts/ingest.py
```

With no Chroma on disk, the pipeline detects “everything already registered, but index missing” and performs the same vector rebuild without re-parsing PDFs.

**Do not** run `python scripts/ingest.py --reset` after dropping in a provided docstore unless you intend to throw away that bundle and re-parse PDFs from scratch.

### 5. What works with partial bundles?

- **`kg_graph.json` only** — Knowledge Graph tab and JSON-backed graph context in chat can work; **hybrid vector + BM25 chat** still needs docstore + Chroma (Path A ingest or full Path B).
- **Docstore + BM25 + KG, no `registry.db`** — Prefer **`--rebuild-vectors`** so a full ingest does not overwrite your docstore by re-parsing.

### 6. Run the server

```bash
uvicorn api.main:app --reload --port 8000
```

---

## Architecture (summary)

```
Pipeline 1 — Ingestion
  PDF (pdfplumber + PyMuPDF) → HierarchicalNodeParser → Chroma + BM25

Pipeline 2 — Knowledge Graph
  LLM + Financial Schema → Entity Resolution → kg_graph.json (+ optional Neo4j)

Pipeline 3 — RAG + Chat
  Hybrid Retrieval (Vector + BM25) → Cross-Encoder Rerank → Graph Context → LLM Synthesis
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve UI |
| GET | `/api/health` | System health check |
| GET | `/api/companies` | List companies from manifest |
| GET | `/api/documents` | Document registry |
| POST | `/api/query` | RAG query with citations |
| POST | `/api/ingest` | Run ingestion pipeline |
| GET | `/api/ingest/status` | Ingestion registry status |
| POST | `/api/kg/extract` | Run KG extraction |
| GET | `/api/kg/status` | KG graph stats |
| GET | `/api/kg/graph/{company_id}` | Company subgraph |
| GET | `/api/kg/triples` | List triples |
| POST | `/api/kg/query` | Filter triples |
| GET | `/api/kg/aliases` | Entity aliases |

## Project Structure

```
llamaIndexPOC_2/
├── api/                    # FastAPI application
│   ├── main.py             # App + middleware + static mount
│   ├── models.py           # Pydantic schemas
│   └── routes/             # Query, ingest, graph endpoints
├── config/
│   └── settings.py         # Environment-based configuration
├── pipelines/
│   ├── p1_ingestion/       # PDF parsing, chunking, Chroma indexing
│   ├── p2_graph/           # Schema, LLM extraction, entity resolution
│   └── p3_rag/             # Hybrid retrieval, reranking, synthesis
├── frontend/
│   └── index.html          # Chat UI with graph panel
├── scripts/
│   ├── ingest.py           # CLI: ingest / --reset / --rebuild-vectors
│   ├── extract_kg.py       # CLI: KG extraction
│   └── load_kg_json_to_neo4j.py
├── docker/
│   ├── docker-compose.yml  # Neo4j + API (Chroma on volume)
│   └── Dockerfile
├── documents/              # Sample PDFs (tracked in git)
├── storage/                # Local only: chroma/, *.json, registry.db (see Path A / B)
├── manifest.yaml           # Document manifest
├── requirements.txt
└── insightos_system_design.md
```

## Key Features

- **Hierarchical chunking** (2048/512/128 tokens) for AutoMerging retrieval
- **Table-aware parsing** with pdfplumber for structured Markdown tables
- **Hybrid retrieval** combining vector search + BM25 with RRF
- **Cross-encoder reranking** for precision
- **Schema-constrained KG extraction** (10 entity types, 11 relation types)
- **Entity resolution** for Indian financial entity co-references
- **Source citations** with file, page, and passage references
- **Modern chat UI** with company selector, source chips, and graph panel

## Docker (full stack)

```bash
cd docker
GOOGLE_API_KEY=your_key docker compose up
```

This starts Neo4j and the API. Chroma still persists under **`storage/chroma`** (e.g. via a bind mount if you add one in compose).
