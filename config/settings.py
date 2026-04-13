"""
Environment-based configuration for InsightOS POC 2.
Supports Gemini (default) and OpenAI LLM backends.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

DOCUMENTS_DIR = ROOT / "documents"
STORAGE_DIR = ROOT / "storage"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# --- Vector store (Chroma — default; matches llama-index-core 0.14+ on Python 3.14) ---
CHROMA_DIR = STORAGE_DIR / "chroma"
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "insightos_poc2")

# Optional Qdrant (Docker / Python 3.11–3.13 + llama-index-vector-stores-qdrant only)
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "insightos_chunks")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

# Query-time graph: live Cypher against Neo4j when driver connects (else JSON kg_graph.json)
USE_NEO4J_IN_QUERY = os.getenv("USE_NEO4J_IN_QUERY", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
NEO4J_QUERY_LIMIT = int(os.getenv("NEO4J_QUERY_LIMIT", "50"))
NEO4J_TEXT_TO_CYPHER = os.getenv("NEO4J_TEXT_TO_CYPHER", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)

REGISTRY_DB = os.getenv("REGISTRY_DB", str(STORAGE_DIR / "registry.db"))

# --- LLM ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
GEMINI_LLM_MODEL = os.getenv("GEMINI_LLM_MODEL", "gemini-2.0-flash")
OPENAI_LLM_MODEL = os.getenv("OPENAI_LLM_MODEL", "gpt-4o-mini")

# --- Embedding ---
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "").strip() or None
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))

# --- Chunking ---
CHUNK_SIZES = [int(x) for x in os.getenv("CHUNK_SIZES", "2048,512,128").split(",")]
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "64"))

# --- Retrieval ---
SIMILARITY_TOP_K = int(os.getenv("SIMILARITY_TOP_K", "15"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "5"))
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-2-v2")
BM25_TOP_K = int(os.getenv("BM25_TOP_K", "10"))

# --- KG ---
KG_MAX_TRIPLETS_PER_CHUNK = int(os.getenv("KG_MAX_TRIPLETS_PER_CHUNK", "15"))
KG_NUM_WORKERS = int(os.getenv("KG_NUM_WORKERS", "4"))

# --- Ingestion ---
INGEST_NUM_WORKERS = int(os.getenv("INGEST_NUM_WORKERS", "4"))
INGEST_SEMAPHORE = int(os.getenv("INGEST_SEMAPHORE", "5"))

LOW_TEXT_CHAR_THRESHOLD = int(os.getenv("LOW_TEXT_CHAR_THRESHOLD", "40"))


def _google_key() -> str:
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "Set GOOGLE_API_KEY or GEMINI_API_KEY in .env for the Gemini LLM."
        )
    return key


def get_llm():
    """Return configured LLM instance."""
    if LLM_PROVIDER == "openai":
        from llama_index.llms.openai import OpenAI
        return OpenAI(model=OPENAI_LLM_MODEL, temperature=0)
    else:
        from llama_index.llms.google_genai import GoogleGenAI
        return GoogleGenAI(model=GEMINI_LLM_MODEL, temperature=0, api_key=_google_key())


def get_embed_model():
    """Return configured embedding model."""
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    kwargs: dict[str, Any] = {
        "model_name": LOCAL_EMBEDDING_MODEL,
        "embed_batch_size": EMBEDDING_BATCH_SIZE,
    }
    if EMBEDDING_DEVICE:
        kwargs["device"] = EMBEDDING_DEVICE
    return HuggingFaceEmbedding(**kwargs)


def configure_global_settings():
    """Set LlamaIndex global Settings."""
    from llama_index.core import Settings
    Settings.llm = get_llm()
    Settings.embed_model = get_embed_model()
