"""
RAG query engine: orchestrates retrieval → reranking → synthesis with citations.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.core.response_synthesizers import get_response_synthesizer
from llama_index.core.schema import NodeWithScore, QueryBundle

from config.settings import RERANK_TOP_N, SIMILARITY_TOP_K
from pipelines.p1_ingestion.pipeline import get_docstore, get_vector_store
from pipelines.p2_graph.loader import get_triples_for_company
from pipelines.p3_rag.reranker import rerank_nodes
from pipelines.p3_rag.retriever import build_company_retriever

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    answer: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    graph_context: list[dict[str, Any]] = field(default_factory=list)
    retrieval_mode: str = ""
    query: str = ""
    company_id: str = ""


def _build_index() -> VectorStoreIndex:
    vector_store = get_vector_store()
    docstore = get_docstore()

    if docstore:
        storage_context = StorageContext.from_defaults(
            vector_store=vector_store,
            docstore=docstore,
        )
    else:
        storage_context = StorageContext.from_defaults(vector_store=vector_store)

    return VectorStoreIndex.from_vector_store(
        vector_store,
        storage_context=storage_context,
        embed_model=Settings.embed_model,
    )


def query_company(
    company_id: str,
    question: str,
    *,
    similarity_top_k: int | None = None,
    use_hybrid: bool = True,
    use_rerank: bool = True,
    include_graph: bool = True,
) -> dict[str, Any]:
    """
    Full RAG query pipeline:
    1. Hybrid retrieval (vector + BM25)
    2. Cross-encoder reranking
    3. Graph context enrichment
    4. LLM synthesis with citations
    """
    top_k = similarity_top_k or SIMILARITY_TOP_K

    index = _build_index()
    retriever = build_company_retriever(
        company_id, index,
        similarity_top_k=top_k,
        use_hybrid=use_hybrid,
    )

    nodes = retriever.retrieve(QueryBundle(question))
    retrieval_mode = "hybrid" if use_hybrid else "vector"

    if use_rerank and nodes:
        nodes = rerank_nodes(question, nodes, top_n=RERANK_TOP_N)
        retrieval_mode += "+rerank"
    elif len(nodes) > RERANK_TOP_N:
        nodes = nodes[:RERANK_TOP_N]

    # Graph context: prefer live Neo4j Cypher; fall back to kg_graph.json
    graph_context: list[dict[str, Any]] = []
    graph_text = ""
    graph_source = ""
    if include_graph:
        try:
            from pipelines.p3_rag.neo4j_graph_context import fetch_graph_context_from_neo4j

            neo = fetch_graph_context_from_neo4j(company_id, question)
            if neo.get("source") and neo.get("text"):
                graph_text = neo["text"]
                graph_context = neo["rows"][:20]
                graph_source = neo["source"]
        except Exception as e:
            logger.debug("Neo4j graph context failed: %s", e)

        if not graph_text:
            try:
                triples = get_triples_for_company(company_id, limit=20)
                if triples:
                    graph_context = triples
                    graph_lines = [
                        f"- {t['subject']} ({t['subject_type']}) —[{t['relation']}]→ "
                        f"{t['object']} ({t['object_type']})"
                        for t in triples[:10]
                    ]
                    graph_text = (
                        "\n\nRelevant entity relationships (from file cache):\n"
                        + "\n".join(graph_lines)
                    )
                    graph_source = "json_file"
            except Exception as e:
                logger.debug("JSON graph context unavailable: %s", e)

    # Inject graph context for the synthesizer (even if vector retrieval returned nothing)
    if graph_text:
        from llama_index.core.schema import TextNode

        graph_node = TextNode(
            text=graph_text,
            metadata={"source": "knowledge_graph", "company_id": company_id},
        )
        gns = NodeWithScore(node=graph_node, score=1.0)
        if nodes:
            nodes.insert(0, gns)
        else:
            nodes = [gns]

    # Synthesize answer
    synth = get_response_synthesizer(response_mode="compact")
    response = synth.synthesize(question, nodes)

    # Build source citations
    sources = []
    for sn in response.source_nodes:
        node = sn.node
        meta = node.metadata or {}
        if meta.get("source") == "knowledge_graph":
            continue
        page_num = meta.get("page_number")
        try:
            page_number = int(page_num) if page_num is not None else None
        except (TypeError, ValueError):
            page_number = None

        sources.append({
            "source_file": meta.get("source_file", ""),
            "page_label": meta.get("page_label", ""),
            "page_number": page_number,
            "company_id": meta.get("company_id", ""),
            "doc_type": meta.get("doc_type", ""),
            "passage": (node.get_content() or "")[:2000],
            "score": round(float(sn.score), 4) if sn.score is not None else None,
            "has_tables": meta.get("has_tables", False),
        })

    return {
        "answer": str(response),
        "sources": sources,
        "graph_context": graph_context[:10],
        "graph_source": graph_source,
        "retrieval_mode": retrieval_mode,
        "query": question,
        "company_id": company_id,
    }
