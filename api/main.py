"""
InsightOS POC 2 — FastAPI application.
Full-featured document AI for financial documents.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT.parent / ".env")
    yield


app = FastAPI(
    title="InsightOS POC 2",
    description="Large-scale financial document AI with LlamaIndex, Chroma vector store, and Knowledge Graphs",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Mount frontend ---
FRONTEND_DIR = ROOT / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")

# --- Register routers ---
from api.routes.query import router as query_router
from api.routes.ingest import router as ingest_router
from api.routes.graph import router as graph_router

app.include_router(query_router)
app.include_router(ingest_router)
app.include_router(graph_router)


@app.get("/")
def root():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return {
        "service": "insightos-poc-2",
        "docs": "/docs",
        "ui": "/ui/" if FRONTEND_DIR.is_dir() else None,
    }


@app.get("/api/health")
def health():
    from pipelines.p1_ingestion.pipeline import index_exists, BM25_CORPUS_PATH, DOCSTORE_PATH
    from pipelines.p1_ingestion.registry import registry_summary
    from pipelines.p2_graph.loader import graph_exists, try_load_neo4j

    neo4j_ok = False
    drv = try_load_neo4j()
    if drv:
        neo4j_ok = True
        try:
            drv.close()
        except Exception:
            pass

    return {
        "ok": True,
        "vector_index_present": index_exists(),
        "bm25_corpus_present": BM25_CORPUS_PATH.is_file(),
        "docstore_present": DOCSTORE_PATH.is_file(),
        "kg_graph_present": graph_exists(),
        "neo4j_reachable": neo4j_ok,
        "registry": registry_summary(),
    }


@app.get("/api/companies")
def list_companies():
    manifest_path = ROOT / "manifest.yaml"
    if not manifest_path.is_file():
        return {"companies": []}

    with open(manifest_path) as f:
        manifest = yaml.safe_load(f) or {}

    companies = []
    seen = set()
    for entry in manifest.get("documents", []):
        cid = entry.get("company_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            companies.append({
                "company_id": cid,
                "company_name": entry.get("company_name", cid),
                "sector": entry.get("sector", ""),
                "doc_type": entry.get("doc_type", ""),
                "fiscal_year": entry.get("fiscal_year", ""),
            })

    return {"companies": companies}


@app.get("/api/documents")
def list_documents():
    from pipelines.p1_ingestion.registry import get_all_documents
    return {"documents": get_all_documents()}
