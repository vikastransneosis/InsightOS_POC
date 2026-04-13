"""Knowledge graph endpoints: query triples and graph visualization data."""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.models import KGQueryRequest

router = APIRouter(prefix="/api", tags=["graph"])


@router.get("/kg/status")
def kg_status():
    from pipelines.p2_graph.loader import graph_exists, load_graph
    if not graph_exists():
        return {"exists": False, "triples": 0, "nodes": 0}
    g = load_graph()
    return {
        "exists": True,
        "triples": len(g.get("triples", [])),
        "nodes": len(g.get("nodes", {})),
        "edges": len(g.get("edges", [])),
    }


@router.get("/kg/graph/{company_id}")
def get_company_graph(company_id: str):
    from pipelines.p2_graph.loader import get_graph_for_company
    return get_graph_for_company(company_id)


@router.get("/kg/triples")
def list_triples(
    company_id: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
):
    from pipelines.p2_graph.loader import get_triples_for_company
    return {
        "company_id": company_id,
        "triples": get_triples_for_company(company_id, limit),
    }


@router.post("/kg/query")
def query_triples(body: KGQueryRequest):
    from pipelines.p2_graph.loader import query_triples as qt
    return {
        "company_id": body.company_id,
        "triples": qt(
            company_id=body.company_id,
            subject_contains=body.subject_contains,
            predicate_contains=body.predicate_contains,
            object_contains=body.object_contains,
            limit=body.limit,
        ),
    }


@router.get("/kg/aliases")
def list_aliases():
    from pipelines.p2_graph.resolver import get_all_aliases
    return get_all_aliases()
