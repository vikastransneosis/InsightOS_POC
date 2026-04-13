"""Query endpoint: company-scoped RAG with citations."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.models import QueryRequest

router = APIRouter(prefix="/api", tags=["query"])

_llm_configured = False


def _ensure_llm():
    global _llm_configured
    if _llm_configured:
        return
    from config.settings import configure_global_settings
    configure_global_settings()
    _llm_configured = True


@router.post("/query")
def run_query(body: QueryRequest):
    from pipelines.p1_ingestion.pipeline import index_exists
    if not index_exists():
        raise HTTPException(
            status_code=400,
            detail="No vector index found. Run: python scripts/ingest.py first.",
        )

    _ensure_llm()
    try:
        from pipelines.p3_rag.query_engine import query_company
        return query_company(
            body.company_id,
            body.question,
            similarity_top_k=body.similarity_top_k,
            use_hybrid=body.use_hybrid,
            use_rerank=body.use_rerank,
            include_graph=body.include_graph,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
