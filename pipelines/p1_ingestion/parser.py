"""
PDF parsing with table-aware extraction.
Uses PyMuPDF for text and pdfplumber for structured table extraction.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import fitz
import pdfplumber
from llama_index.core import Document

from config.settings import LOW_TEXT_CHAR_THRESHOLD

logger = logging.getLogger(__name__)


def _page_display_label(page: Any) -> str:
    try:
        lab = page.get_label()
        if lab is not None and str(lab).strip():
            return str(lab).strip()
    except Exception:
        pass
    return str(page.number + 1)


def _tables_to_markdown(tables: list) -> str:
    parts = []
    for table in tables:
        if not table or not table[0]:
            continue
        header = "| " + " | ".join(str(c or "") for c in table[0]) + " |"
        separator = "| " + " | ".join("---" for _ in table[0]) + " |"
        rows = [
            "| " + " | ".join(str(c or "") for c in row) + " |"
            for row in table[1:]
        ]
        parts.append("\n".join([header, separator] + rows))
    return "\n\n".join(parts)


def parse_financial_pdf(
    pdf_path: str | Path,
    company_id: str,
    doc_type: str,
    fiscal_year: str = "",
    company_name: str = "",
    sector: str = "",
) -> list[Document]:
    """
    Parse a financial PDF into one Document per page.
    Tables are extracted as Markdown and appended to the page text.
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    documents: list[Document] = []
    skip_count = 0
    fitz_doc = fitz.open(str(pdf_path))

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_num, (fitz_page, plumber_page) in enumerate(
                zip(fitz_doc, pdf.pages), start=1
            ):
                text = fitz_page.get_text("text") or ""
                page_label = _page_display_label(fitz_page)

                table_md = ""
                try:
                    tables = plumber_page.extract_tables()
                    if tables:
                        table_md = _tables_to_markdown(tables)
                except Exception as e:
                    logger.debug("Table extraction failed on page %s: %s", page_num, e)

                combined_text = text.strip()
                if table_md:
                    combined_text += "\n\n### Tables\n\n" + table_md

                meta: dict[str, Any] = {
                    "company_id": company_id,
                    "company_name": company_name,
                    "doc_type": doc_type,
                    "fiscal_year": fiscal_year,
                    "sector": sector,
                    "source_file": pdf_path.name,
                    "page_label": page_label,
                    "page_number": page_num,
                    "has_tables": bool(table_md),
                }

                stripped = text.strip()
                if len(stripped) < LOW_TEXT_CHAR_THRESHOLD:
                    meta["content_type"] = "figure_skipped"
                    meta["low_text_chars"] = len(stripped)
                    skip_count += 1

                documents.append(Document(text=combined_text, metadata=meta))
    finally:
        fitz_doc.close()

    if skip_count:
        logger.info(
            "%s: %d/%d pages tagged figure_skipped (text < %d chars)",
            pdf_path.name, skip_count, len(documents), LOW_TEXT_CHAR_THRESHOLD,
        )

    return documents


def load_manifest_documents(manifest: dict, root: Path) -> list[Document]:
    """Load all documents declared in manifest.yaml."""
    entries = manifest.get("documents", [])
    all_docs: list[Document] = []

    for entry in entries:
        rel_path = entry.get("path", "")
        pdf_path = (root / rel_path).resolve()
        if not pdf_path.is_file():
            logger.warning("Document not found, skipping: %s", pdf_path)
            continue

        docs = parse_financial_pdf(
            pdf_path=pdf_path,
            company_id=entry.get("company_id", ""),
            company_name=entry.get("company_name", ""),
            doc_type=entry.get("doc_type", ""),
            fiscal_year=entry.get("fiscal_year", ""),
            sector=entry.get("sector", ""),
        )
        all_docs.extend(docs)
        logger.info("Parsed %s: %d pages", pdf_path.name, len(docs))

    return all_docs
