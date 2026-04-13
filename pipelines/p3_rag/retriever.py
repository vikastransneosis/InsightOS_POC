"""
Hybrid retrieval: Vector (Chroma) + BM25 keyword search with Reciprocal Rank Fusion.
Company-scoped filtering ensures cross-contamination-free results.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from llama_index.core import Settings, VectorStoreIndex
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.indices.vector_store.retrievers import VectorIndexRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from llama_index.core.vector_stores.types import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)
from rank_bm25 import BM25Okapi

from config.settings import BM25_TOP_K, SIMILARITY_TOP_K, STORAGE_DIR

logger = logging.getLogger(__name__)

BM25_CORPUS_PATH = STORAGE_DIR / "bm25_corpus.json"


class CompanyBM25Retriever(BaseRetriever):
    """BM25 retriever scoped to a single company's documents."""

    def __init__(self, company_id: str, similarity_top_k: int = 10):
        super().__init__()
        self._company_id = company_id
        self._top_k = similarity_top_k
        self._corpus: list[dict] = []
        self._bm25: BM25Okapi | None = None
        self._load()

    def _load(self):
        if not BM25_CORPUS_PATH.is_file():
            return
        with open(BM25_CORPUS_PATH) as f:
            all_rows = json.load(f)
        self._corpus = [r for r in all_rows if r.get("company_id") == self._company_id]
        if self._corpus:
            tokenized = [r["text"].lower().split() for r in self._corpus]
            self._bm25 = BM25Okapi(tokenized)

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        if not self._bm25 or not self._corpus:
            return []
        tokens = query_bundle.query_str.lower().split()
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])

        results = []
        for idx, score in ranked[:self._top_k]:
            row = self._corpus[idx]
            node = TextNode(
                text=row["text"],
                id_=row.get("node_id", ""),
                metadata={
                    "company_id": row.get("company_id", ""),
                    "source_file": row.get("source_file", ""),
                    "page_label": row.get("page_label", ""),
                },
            )
            results.append(NodeWithScore(node=node, score=float(score)))
        return results


class HybridRRFRetriever(BaseRetriever):
    """Fuse vector and BM25 results using Reciprocal Rank Fusion."""

    def __init__(
        self,
        vector_retriever: BaseRetriever,
        bm25_retriever: BaseRetriever,
        *,
        final_k: int = 15,
        rrf_k: float = 60.0,
    ):
        super().__init__()
        self._vector = vector_retriever
        self._bm25 = bm25_retriever
        self._final_k = final_k
        self._rrf_k = rrf_k

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        vec_nodes = self._vector.retrieve(query_bundle)
        bm25_nodes = self._bm25.retrieve(query_bundle)

        scores: dict[str, float] = {}
        node_map: dict[str, NodeWithScore] = {}

        for rank, nws in enumerate(vec_nodes):
            nid = nws.node.node_id or nws.node.text[:80]
            scores[nid] = scores.get(nid, 0) + 1.0 / (self._rrf_k + rank + 1)
            node_map[nid] = nws

        for rank, nws in enumerate(bm25_nodes):
            nid = nws.node.node_id or nws.node.text[:80]
            scores[nid] = scores.get(nid, 0) + 1.0 / (self._rrf_k + rank + 1)
            if nid not in node_map:
                node_map[nid] = nws

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        results = []
        for nid, score in ranked[:self._final_k]:
            nws = node_map[nid]
            results.append(NodeWithScore(node=nws.node, score=score))

        return results


def build_company_retriever(
    company_id: str,
    index: VectorStoreIndex,
    *,
    similarity_top_k: int | None = None,
    use_hybrid: bool = True,
) -> BaseRetriever:
    """Build a company-scoped retriever (hybrid or vector-only)."""
    top_k = similarity_top_k or SIMILARITY_TOP_K

    filters = MetadataFilters(
        filters=[
            MetadataFilter(
                key="company_id",
                value=company_id,
                operator=FilterOperator.EQ,
            )
        ]
    )

    vector_retriever = VectorIndexRetriever(
        index=index,
        similarity_top_k=top_k,
        filters=filters,
    )

    if use_hybrid and BM25_CORPUS_PATH.is_file():
        bm25_retriever = CompanyBM25Retriever(company_id, similarity_top_k=BM25_TOP_K)
        return HybridRRFRetriever(
            vector_retriever,
            bm25_retriever,
            final_k=top_k,
        )

    return vector_retriever
