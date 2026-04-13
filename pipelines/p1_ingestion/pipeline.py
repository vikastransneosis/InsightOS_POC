"""
Full ingestion pipeline: parse → chunk → embed → store in Chroma.
Uses persistent Chroma under storage/chroma (compatible with llama-index-core 0.14+ / Python 3.14).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import chromadb
import yaml
from llama_index.core import Document, StorageContext, VectorStoreIndex
from llama_index.core.schema import MetadataMode
from llama_index.core.storage.docstore import SimpleDocumentStore
from llama_index.vector_stores.chroma import ChromaVectorStore

from config.settings import CHROMA_COLLECTION, CHROMA_DIR, ROOT, STORAGE_DIR
from pipelines.p1_ingestion.chunker import build_hierarchical_nodes
from pipelines.p1_ingestion.parser import load_manifest_documents, parse_financial_pdf
from pipelines.p1_ingestion.registry import (
    compute_content_hash,
    has_document,
    register_document,
    update_status,
)

logger = logging.getLogger(__name__)

BM25_CORPUS_PATH = STORAGE_DIR / "bm25_corpus.json"
DOCSTORE_PATH = STORAGE_DIR / "docstore.json"


def _get_chroma_vector_store() -> ChromaVectorStore:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(CHROMA_COLLECTION)
    return ChromaVectorStore(chroma_collection=collection)


def _save_bm25_corpus(nodes: list) -> int:
    """Persist node texts for BM25 retrieval."""
    rows = []
    for n in nodes:
        text = (n.get_content(metadata_mode=MetadataMode.EMBED) or "").strip()
        if not text:
            continue
        meta = n.metadata or {}
        rows.append({
            "node_id": n.node_id,
            "company_id": meta.get("company_id", ""),
            "text": text,
            "source_file": meta.get("source_file", ""),
            "page_label": meta.get("page_label", ""),
        })

    BM25_CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BM25_CORPUS_PATH, "w") as f:
        json.dump(rows, f)

    logger.info("BM25 corpus: %d rows saved to %s", len(rows), BM25_CORPUS_PATH)
    return len(rows)


def rebuild_chroma_from_docstore() -> dict[str, Any]:
    """
    Re-embed leaf nodes into Chroma from persisted docstore (no PDF parsing).
    Used after clone when repo ships docstore.json but not storage/chroma/.
    """
    from llama_index.core.node_parser.relational.hierarchical import get_leaf_nodes

    from config.settings import configure_global_settings

    configure_global_settings()

    global _vector_store, _docstore

    if not DOCSTORE_PATH.is_file():
        return {"error": "docstore.json not found; run a full ingest with PDFs first"}

    docstore = SimpleDocumentStore.from_persist_path(str(DOCSTORE_PATH))
    all_nodes = list(docstore.docs.values())
    if not all_nodes:
        return {"error": "docstore is empty"}

    leaf_nodes = get_leaf_nodes(all_nodes)
    leaf_nodes = [
        n for n in leaf_nodes
        if (n.get_content(metadata_mode=MetadataMode.EMBED) or "").strip()
    ]

    try:
        if CHROMA_DIR.is_dir():
            c = chromadb.PersistentClient(path=str(CHROMA_DIR))
            try:
                c.delete_collection(CHROMA_COLLECTION)
            except Exception:
                pass
    except Exception as e:
        logger.warning("Chroma cleanup before rebuild: %s", e)

    vector_store = _get_chroma_vector_store()
    storage_context = StorageContext.from_defaults(
        vector_store=vector_store,
        docstore=docstore,
    )
    VectorStoreIndex(
        leaf_nodes,
        storage_context=storage_context,
        show_progress=True,
    )
    bm25_rows = _save_bm25_corpus(leaf_nodes)

    _vector_store = None
    _docstore = None

    return {
        "status": "rebuilt_chroma_from_docstore",
        "leaf_nodes_indexed": len(leaf_nodes),
        "bm25_corpus_rows": bm25_rows,
    }


def run_ingestion(
    root: Path | None = None,
    *,
    reset: bool = False,
) -> dict[str, Any]:
    """
    Run the full ingestion pipeline:
    1. Parse PDFs from manifest
    2. Hierarchical chunking
    3. Embed and store in Chroma
    4. Save BM25 corpus
    5. Update document registry
    """
    from config.settings import configure_global_settings
    configure_global_settings()

    root = root or ROOT
    manifest_path = root / "manifest.yaml"
    if not manifest_path.is_file():
        return {"error": "manifest.yaml not found"}

    with open(manifest_path) as f:
        manifest = yaml.safe_load(f) or {}

    entries = manifest.get("documents", [])
    if not entries:
        return {"error": "No documents in manifest.yaml"}

    if reset:
        for p in [BM25_CORPUS_PATH, DOCSTORE_PATH]:
            p.unlink(missing_ok=True)
        try:
            if CHROMA_DIR.is_dir():
                c = chromadb.PersistentClient(path=str(CHROMA_DIR))
                c.delete_collection(CHROMA_COLLECTION)
        except Exception:
            pass

    # Parse all documents
    all_docs: list[Document] = []
    skipped_pages: list[dict] = []

    for entry in entries:
        rel_path = entry.get("path", "")
        pdf_path = (root / rel_path).resolve()
        if not pdf_path.is_file():
            logger.warning("Document not found: %s", pdf_path)
            continue

        content_hash = compute_content_hash(pdf_path)
        if not reset and has_document(content_hash):
            logger.info("Skipping already-ingested: %s", pdf_path.name)
            continue

        docs = parse_financial_pdf(
            pdf_path=pdf_path,
            company_id=entry.get("company_id", ""),
            company_name=entry.get("company_name", ""),
            doc_type=entry.get("doc_type", ""),
            fiscal_year=entry.get("fiscal_year", ""),
            sector=entry.get("sector", ""),
        )

        doc_id = register_document(
            company_id=entry.get("company_id", ""),
            source_file=pdf_path.name,
            company_name=entry.get("company_name", ""),
            doc_type=entry.get("doc_type", ""),
            fiscal_year=entry.get("fiscal_year", ""),
            page_count=len(docs),
            content_hash=content_hash,
        )

        for d in docs:
            if (d.metadata or {}).get("content_type") == "figure_skipped":
                skipped_pages.append(d.metadata)
            else:
                all_docs.append(d)

        update_status(doc_id, "parsing_done")

    if not all_docs:
        if not reset and not index_exists() and DOCSTORE_PATH.is_file():
            logger.info(
                "Bundled docstore present but Chroma missing — rebuilding vector index only."
            )
            return rebuild_chroma_from_docstore()
        if index_exists():
            return {
                "status": "already_indexed",
                "skipped_pages": len(skipped_pages),
            }
        return {"error": "No new documents to ingest", "skipped_pages": len(skipped_pages)}

    # Hierarchical chunking
    all_nodes, leaf_nodes = build_hierarchical_nodes(all_docs, show_progress=True)

    # Filter empty chunks
    leaf_nodes = [
        n for n in leaf_nodes
        if (n.get_content(metadata_mode=MetadataMode.EMBED) or "").strip()
    ]

    # Save docstore (all hierarchy levels for AutoMerging)
    docstore = SimpleDocumentStore()
    docstore.add_documents(all_nodes)
    DOCSTORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    docstore.persist(str(DOCSTORE_PATH))

    # Vector store setup
    vector_store = _get_chroma_vector_store()
    storage_context = StorageContext.from_defaults(
        vector_store=vector_store,
        docstore=docstore,
    )

    # Build index with leaf nodes only
    index = VectorStoreIndex(
        leaf_nodes,
        storage_context=storage_context,
        show_progress=True,
    )

    # Save BM25 corpus
    bm25_rows = _save_bm25_corpus(leaf_nodes)

    # Update registry status
    for entry in entries:
        rel_path = entry.get("path", "")
        pdf_path = (root / rel_path).resolve()
        if pdf_path.is_file():
            import hashlib
            doc_id = hashlib.md5(
                f"{entry.get('company_id', '')}::{pdf_path.name}".encode()
            ).hexdigest()
            update_status(doc_id, "p1_done", chunks=len(leaf_nodes))

    companies = list({d.metadata.get("company_id") for d in all_docs if d.metadata.get("company_id")})

    return {
        "documents_parsed": len(entries),
        "pages_total": len(all_docs) + len(skipped_pages),
        "pages_indexed": len(all_docs),
        "pages_skipped": len(skipped_pages),
        "total_nodes": len(all_nodes),
        "leaf_nodes_indexed": len(leaf_nodes),
        "bm25_corpus_rows": bm25_rows,
        "companies": companies,
    }


# Global references for the query layer
_vector_store = None
_docstore = None


def get_vector_store() -> ChromaVectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = _get_chroma_vector_store()
    return _vector_store


def get_docstore() -> SimpleDocumentStore | None:
    global _docstore
    if _docstore is None and DOCSTORE_PATH.is_file():
        _docstore = SimpleDocumentStore.from_persist_path(str(DOCSTORE_PATH))
    return _docstore


def index_exists() -> bool:
    """True when a full ingest has completed (vectors + docstore + BM25)."""
    if not BM25_CORPUS_PATH.is_file() or not DOCSTORE_PATH.is_file():
        return False
    if not CHROMA_DIR.is_dir():
        return False
    try:
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        coll = client.get_collection(CHROMA_COLLECTION)
        return coll.count() > 0
    except Exception:
        return False
