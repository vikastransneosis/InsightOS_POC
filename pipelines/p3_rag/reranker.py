"""
Cross-encoder reranking for precision improvement.
Reranks the hybrid retrieval results using a sentence-transformer model.
"""
from __future__ import annotations

import logging

from llama_index.core.schema import NodeWithScore, QueryBundle

from config.settings import RERANKER_MODEL, RERANK_TOP_N

logger = logging.getLogger(__name__)

_reranker = None


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(RERANKER_MODEL)
        logger.info("Loaded reranker model: %s", RERANKER_MODEL)
    return _reranker


def rerank_nodes(
    query: str,
    nodes: list[NodeWithScore],
    *,
    top_n: int | None = None,
) -> list[NodeWithScore]:
    """Re-score and re-order nodes using a cross-encoder model."""
    if not nodes:
        return []

    top_n = top_n or RERANK_TOP_N
    reranker = _get_reranker()

    pairs = [(query, n.node.get_content() or "") for n in nodes]
    scores = reranker.predict(pairs)

    scored = list(zip(nodes, scores))
    scored.sort(key=lambda x: -x[1])

    results = []
    for nws, score in scored[:top_n]:
        results.append(NodeWithScore(node=nws.node, score=float(score)))

    return results
