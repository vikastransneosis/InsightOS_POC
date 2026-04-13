"""
Hierarchical chunking: 3-level node hierarchy for AutoMerging retrieval.
Leaf nodes go into the vector store; all levels go into the docstore.
"""
from __future__ import annotations

import logging
from typing import Any

from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.node_parser.relational.hierarchical import (
    HierarchicalNodeParser,
    get_leaf_nodes,
)
from llama_index.core.schema import BaseNode, Document

from config.settings import CHUNK_OVERLAP, CHUNK_SIZES

logger = logging.getLogger(__name__)


def build_hierarchical_nodes(
    documents: list[Document],
    *,
    chunk_sizes: list[int] | None = None,
    chunk_overlap: int | None = None,
    show_progress: bool = True,
) -> tuple[list[BaseNode], list[BaseNode]]:
    """
    Parse documents into hierarchical nodes.

    Returns:
        (all_nodes, leaf_nodes) — all_nodes for docstore, leaf_nodes for vector index.
    """
    sizes = chunk_sizes or CHUNK_SIZES
    overlap = chunk_overlap if chunk_overlap is not None else CHUNK_OVERLAP

    min_size = min(sizes)
    if overlap >= min_size:
        raise ValueError(
            f"chunk_overlap ({overlap}) must be smaller than the smallest "
            f"chunk_size ({min_size})."
        )

    parser = HierarchicalNodeParser.from_defaults(
        chunk_sizes=sizes,
        chunk_overlap=overlap,
    )

    all_nodes = parser.get_nodes_from_documents(
        documents, show_progress=show_progress
    )
    leaf_nodes = get_leaf_nodes(all_nodes)

    logger.info(
        "Hierarchical chunking: sizes=%s overlap=%d → %d total nodes, %d leaf nodes",
        sizes, overlap, len(all_nodes), len(leaf_nodes),
    )

    return all_nodes, leaf_nodes


def build_flat_nodes(
    documents: list[Document],
    *,
    chunk_size: int = 1024,
    chunk_overlap: int = 128,
) -> list[BaseNode]:
    """Fallback: simple sentence splitting for smaller documents."""
    parser = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    nodes = parser.get_nodes_from_documents(documents)
    logger.info(
        "Flat chunking: size=%d overlap=%d → %d nodes",
        chunk_size, chunk_overlap, len(nodes),
    )
    return nodes
