"""
LlamaIndex Workflow for the full InsightOS query pipeline.
Async, event-driven orchestration of retrieval → reranking → graph merge → synthesis.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from llama_index.core.response_synthesizers import get_response_synthesizer
from llama_index.core import Settings

from config.settings import RERANK_TOP_N, SIMILARITY_TOP_K

logger = logging.getLogger(__name__)


@dataclass
class WorkflowResult:
    answer: str
    citations: list[dict[str, Any]]
    graph_context: list[dict[str, Any]]
    retrieval_mode: str


async def run_query_workflow(
    query: str,
    company_id: str,
    *,
    similarity_top_k: int | None = None,
    use_hybrid: bool = True,
    use_rerank: bool = True,
    include_graph: bool = True,
) -> WorkflowResult:
    """
    Async query workflow:
    Step 1: Hybrid retrieval (vector + BM25)
    Step 2: Cross-encoder reranking
    Step 3: Graph context enrichment
    Step 4: LLM answer synthesis with citations
    """
    from pipelines.p1_ingestion.pipeline import get_docstore, get_vector_store
    from pipelines.p3_rag.retriever import build_company_retriever
    from pipelines.p3_rag.reranker import rerank_nodes
    from pipelines.p2_graph.loader import get_triples_for_company
    from llama_index.core import StorageContext, VectorStoreIndex

    top_k = similarity_top_k or SIMILARITY_TOP_K

    # Build index
    vector_store = get_vector_store()
    docstore = get_docstore()
    if docstore:
        sc = StorageContext.from_defaults(vector_store=vector_store, docstore=docstore)
    else:
        sc = StorageContext.from_defaults(vector_store=vector_store)

    index = VectorStoreIndex.from_vector_store(
        vector_store, storage_context=sc, embed_model=Settings.embed_model
    )

    # Step 1: Retrieve
    retriever = build_company_retriever(
        company_id, index, similarity_top_k=top_k, use_hybrid=use_hybrid,
    )
    nodes = retriever.retrieve(QueryBundle(query))
    retrieval_mode = "hybrid" if use_hybrid else "vector"

    # Step 2: Rerank
    if use_rerank and nodes:
        nodes = rerank_nodes(query, nodes, top_n=RERANK_TOP_N)
        retrieval_mode += "+rerank"
    elif len(nodes) > RERANK_TOP_N:
        nodes = nodes[:RERANK_TOP_N]

    # Step 3: Graph enrichment (Neo4j Cypher first, else JSON)
    graph_context: list[dict] = []
    graph_text = ""
    if include_graph:
        try:
            from pipelines.p3_rag.neo4j_graph_context import fetch_graph_context_from_neo4j

            neo = fetch_graph_context_from_neo4j(company_id, query)
            if neo.get("source") and neo.get("text"):
                graph_text = neo["text"]
                graph_context = neo["rows"][:15]
        except Exception:
            pass
        if not graph_text:
            try:
                triples = get_triples_for_company(company_id, limit=15)
                graph_context = triples
                if triples:
                    graph_lines = [
                        f"- {t['subject']} —[{t['relation']}]→ {t['object']}"
                        for t in triples[:8]
                    ]
                    graph_text = "\n\nEntity relationships (file cache):\n" + "\n".join(
                        graph_lines
                    )
            except Exception:
                pass
        if graph_text:
            graph_node = TextNode(
                text=graph_text,
                metadata={"source": "knowledge_graph"},
            )
            gns = NodeWithScore(node=graph_node, score=1.0)
            if nodes:
                nodes.insert(0, gns)
            else:
                nodes = [gns]

    # Step 4: Synthesize
    synth = get_response_synthesizer(response_mode="compact")
    response = synth.synthesize(query, nodes)

    citations = []
    for sn in response.source_nodes:
        meta = sn.node.metadata or {}
        if meta.get("source") == "knowledge_graph":
            continue
        citations.append({
            "source_file": meta.get("source_file", ""),
            "page_label": meta.get("page_label", ""),
            "score": round(float(sn.score), 4) if sn.score is not None else None,
            "passage": (sn.node.get_content() or "")[:500],
        })

    return WorkflowResult(
        answer=str(response),
        citations=citations,
        graph_context=graph_context[:10],
        retrieval_mode=retrieval_mode,
    )
