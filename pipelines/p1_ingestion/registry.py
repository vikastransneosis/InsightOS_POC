"""
Document registry using SQLite for tracking ingestion state.
Lightweight alternative to PostgreSQL for POC usage.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import REGISTRY_DB

_DDL = """
CREATE TABLE IF NOT EXISTS document_registry (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    company_name    TEXT NOT NULL DEFAULT '',
    doc_type        TEXT NOT NULL DEFAULT '',
    fiscal_year     TEXT DEFAULT '',
    source_file     TEXT NOT NULL,
    page_count      INTEGER DEFAULT 0,
    content_hash    TEXT UNIQUE,
    ingestion_status TEXT DEFAULT 'pending',
    chunks_created  INTEGER DEFAULT 0,
    p1_completed_at TEXT,
    p2_completed_at TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_registry_company ON document_registry(company_id);
CREATE INDEX IF NOT EXISTS idx_registry_status  ON document_registry(ingestion_status);
"""


def _conn() -> sqlite3.Connection:
    Path(REGISTRY_DB).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(REGISTRY_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    return conn


def compute_content_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def register_document(
    company_id: str,
    source_file: str,
    *,
    company_name: str = "",
    doc_type: str = "",
    fiscal_year: str = "",
    page_count: int = 0,
    content_hash: str = "",
) -> str:
    """Insert or update a document in the registry. Returns the document ID."""
    doc_id = hashlib.md5(f"{company_id}::{source_file}".encode()).hexdigest()
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO document_registry
               (id, company_id, company_name, doc_type, fiscal_year,
                source_file, page_count, content_hash, ingestion_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
               ON CONFLICT(id) DO UPDATE SET
                 page_count=excluded.page_count,
                 content_hash=excluded.content_hash,
                 updated_at=datetime('now')
            """,
            (doc_id, company_id, company_name, doc_type, fiscal_year,
             source_file, page_count, content_hash),
        )
        conn.commit()
    finally:
        conn.close()
    return doc_id


def update_status(doc_id: str, status: str, chunks: int = 0):
    conn = _conn()
    now = datetime.now(timezone.utc).isoformat()
    try:
        sets = "ingestion_status=?, updated_at=?"
        params: list[Any] = [status, now]
        if chunks:
            sets += ", chunks_created=?"
            params.append(chunks)
        if status == "p1_done":
            sets += ", p1_completed_at=?"
            params.append(now)
        elif status == "p2_done":
            sets += ", p2_completed_at=?"
            params.append(now)
        params.append(doc_id)
        conn.execute(f"UPDATE document_registry SET {sets} WHERE id=?", params)
        conn.commit()
    finally:
        conn.close()


def get_all_documents() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM document_registry ORDER BY company_id, source_file").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_company_documents(company_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM document_registry WHERE company_id=?", (company_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def has_document(content_hash: str) -> bool:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM document_registry WHERE content_hash=?", (content_hash,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def registry_summary() -> dict:
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM document_registry").fetchone()[0]
        by_status = conn.execute(
            "SELECT ingestion_status, COUNT(*) FROM document_registry GROUP BY ingestion_status"
        ).fetchall()
        companies = conn.execute(
            "SELECT DISTINCT company_id FROM document_registry"
        ).fetchall()
        return {
            "total_documents": total,
            "by_status": {r[0]: r[1] for r in by_status},
            "companies": [r[0] for r in companies],
        }
    finally:
        conn.close()
