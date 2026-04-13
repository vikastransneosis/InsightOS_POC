"""Pydantic request/response schemas for the InsightOS API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    company_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    similarity_top_k: int = Field(5, ge=1, le=30)
    use_hybrid: bool = True
    use_rerank: bool = True
    include_graph: bool = True


class IngestRequest(BaseModel):
    reset: bool = False


class KGQueryRequest(BaseModel):
    company_id: str = Field(..., min_length=1)
    subject_contains: str | None = None
    predicate_contains: str | None = None
    object_contains: str | None = None
    limit: int = Field(100, ge=1, le=500)


class KGExtractRequest(BaseModel):
    company_id: str | None = None
    max_pages: int = Field(
        120,
        ge=1,
        le=500,
        description="Total LLM page budget; split evenly across companies when company_id is null.",
    )
