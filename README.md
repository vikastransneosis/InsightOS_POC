# InsightOS POC 2 — Large-Scale Financial Document AI

Company-scoped RAG over financial PDFs with hierarchical chunking, hybrid retrieval,
knowledge graph extraction, and citations. Built on **LlamaIndex + Chroma + Neo4j** (optional).

Implements the architecture from `insightos_system_design.md` and the evaluation
criteria from `JIRA_KB/TICKET_7.md` (IN-7) / `JIRA_KB/SUB_TICKET_9.md` (IN-9).

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env — set GOOGLE_API_KEY (for Gemini) or OPENAI_API_KEY

# 3. Place PDFs in documents/ (or use the symlinked ones)

# 4. Run ingestion
python scripts/ingest.py

# 5. Start the API + UI
uvicorn api.main:app --reload --port 8000

# 6. Open http://localhost:8000 in your browser
```

## Architecture

```
Pipeline 1 — Ingestion
  PDF (pdfplumber + PyMuPDF) → HierarchicalNodeParser → Chroma + BM25

Pipeline 2 — Knowledge Graph
  LLM + Financial Schema → Entity Resolution → File-based / Neo4j

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
│   ├── ingest.py           # CLI: run ingestion
│   └── extract_kg.py       # CLI: run KG extraction
├── docker/
│   ├── docker-compose.yml  # Neo4j + API (Chroma on volume)
│   └── Dockerfile
├── documents/              # PDFs (symlinked from POC 1)
├── storage/                # Persisted indexes and graph
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

## Docker (Full Stack)

```bash
cd docker
GOOGLE_API_KEY=your_key docker compose up
```

This starts Neo4j and the API. The vector index uses **Chroma** (persisted under `storage/chroma` via the mounted volume).
