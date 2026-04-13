"""Ingestion endpoints: trigger document parsing and indexing."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.models import IngestRequest, KGExtractRequest

router = APIRouter(prefix="/api", tags=["ingestion"])


@router.post("/ingest")
def run_ingest(body: IngestRequest):
    try:
        from pipelines.p1_ingestion.pipeline import run_ingestion
        result = run_ingestion(reset=body.reset)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/ingest/status")
def ingest_status():
    from pipelines.p1_ingestion.registry import registry_summary
    return registry_summary()


@router.post("/kg/extract")
def run_kg_extraction(body: KGExtractRequest):
    """Run knowledge graph extraction on ingested documents."""
    try:
        from config.settings import configure_global_settings, ROOT
        configure_global_settings()

        import yaml
        with open(ROOT / "manifest.yaml") as f:
            manifest = yaml.safe_load(f) or {}

        from pipelines.p1_ingestion.parser import load_manifest_documents
        from pipelines.p2_graph.extractor import (
            extract_triples_from_nodes,
            select_pages_for_kg_extraction,
        )

        docs = load_manifest_documents(manifest, ROOT)
        selected = select_pages_for_kg_extraction(
            docs,
            company_id=body.company_id,
            max_pages=body.max_pages,
        )
        triples = extract_triples_from_nodes(selected, max_pages=len(selected))

        from pipelines.p2_graph.loader import push_to_neo4j, save_triples
        save_triples(triples, merge=True)
        push_to_neo4j(triples)

        return {
            "triples_extracted": len(triples),
            "pages_processed": len(selected),
            "company_id": body.company_id,
            "mode": "single_company" if body.company_id else "all_companies_balanced",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
