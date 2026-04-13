"""
Knowledge graph extraction using LLM with the financial schema.
Uses a structured prompt to extract entity-relation triples from document chunks.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from config.settings import KG_MAX_TRIPLETS_PER_CHUNK, get_llm
from pipelines.p2_graph.resolver import resolve_entity
from pipelines.p2_graph.schema import ENTITY_TYPE_DESCRIPTIONS, FINANCIAL_SCHEMA

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are an expert financial analyst. Extract entities and relationships from the following text.

ENTITY TYPES (use ONLY these):
{entity_types}

RELATIONSHIP TYPES (use ONLY these):
{relation_types}

RULES:
1. Extract up to {max_triplets} entity-relationship triplets.
2. Each triplet must be: (subject_name, subject_type, relation, object_name, object_type)
3. Only use entity types and relation types from the lists above.
4. Include properties like stake percentages, designations, dates when mentioned.
5. Be precise — use exact names as they appear in the text.

TEXT:
{text}

Return a JSON array of objects with keys: subject, subject_type, relation, object, object_type, properties
If no entities are found, return an empty array [].
Return ONLY valid JSON, no other text."""


def extract_triples_from_text(
    text: str,
    company_id: str = "",
    source_file: str = "",
    page_label: str = "",
) -> list[dict[str, Any]]:
    """Extract entity-relation triples from a text chunk using the LLM."""
    if not text.strip():
        return []

    entity_types_str = "\n".join(
        f"- {k}: {v}" for k, v in ENTITY_TYPE_DESCRIPTIONS.items()
    )
    relation_types_str = "\n".join(
        f"- {rel}" for rels in FINANCIAL_SCHEMA.values() for rel in rels
    )
    # Deduplicate relations
    seen_rels = set()
    unique_rels = []
    for rels in FINANCIAL_SCHEMA.values():
        for rel in rels:
            if rel not in seen_rels:
                seen_rels.add(rel)
                unique_rels.append(f"- {rel}")
    relation_types_str = "\n".join(unique_rels)

    prompt = EXTRACTION_PROMPT.format(
        entity_types=entity_types_str,
        relation_types=relation_types_str,
        max_triplets=KG_MAX_TRIPLETS_PER_CHUNK,
        text=text[:8000],
    )

    try:
        llm = get_llm()
        response = llm.complete(prompt)
        raw = response.text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        triples = json.loads(raw)
        if not isinstance(triples, list):
            triples = []
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("KG extraction failed for chunk: %s", e)
        return []

    # Resolve entities and add provenance
    resolved = []
    for t in triples:
        if not isinstance(t, dict):
            continue
        subj = t.get("subject", "")
        obj = t.get("object", "")
        if not subj or not obj:
            continue

        props = t.get("properties", {})
        if isinstance(props, str):
            props = {"detail": props}
        elif isinstance(props, list):
            props = {"items": props}
        elif not isinstance(props, dict):
            props = {"value": props}

        resolved.append({
            "subject": resolve_entity(subj, company_id),
            "subject_type": t.get("subject_type", "COMPANY"),
            "relation": t.get("relation", "RELATED_PARTY_OF"),
            "object": resolve_entity(obj, company_id),
            "object_type": t.get("object_type", "COMPANY"),
            "properties": props,
            "source_file": source_file,
            "page_label": page_label,
            "company_id": company_id,
        })

    return resolved


def select_pages_for_kg_extraction(
    docs: list,
    *,
    company_id: str | None = None,
    max_pages: int = 120,
) -> list:
    """
    Choose which page-documents to run KG extraction on.

    - If ``company_id`` is set: only that company's pages, first ``max_pages`` pages.
    - If ``company_id`` is None: split ``max_pages`` **evenly across all companies**
      (so manifest order no longer starves later companies).
    """
    usable = [
        d
        for d in docs
        if (d.metadata or {}).get("content_type") != "figure_skipped"
    ]
    if company_id:
        sub = [d for d in usable if (d.metadata or {}).get("company_id") == company_id]
        return sub[:max_pages]

    by_cid: dict[str, list] = defaultdict(list)
    for d in usable:
        cid = (d.metadata or {}).get("company_id")
        if cid:
            by_cid[str(cid)].append(d)
    if not by_cid:
        return []

    n = len(by_cid)
    per = max(1, max_pages // n)
    selected: list = []
    for cid in sorted(by_cid.keys()):
        selected.extend(by_cid[cid][:per])
    logger.info(
        "KG page selection: %s companies, up to %s pages each (total cap %s) → %s pages",
        n,
        per,
        max_pages,
        len(selected),
    )
    return selected


def extract_triples_from_nodes(
    nodes: list,
    *,
    section_filter: list[str] | None = None,
    max_pages: int = 60,
) -> list[dict[str, Any]]:
    """
    Extract triples from a list of document nodes.
    Optionally filter to entity-rich sections.
    """
    entity_rich_sections = section_filter or [
        "related_parties", "directors_report", "shareholding_pattern",
        "management_discussion", "key_managerial_personnel",
        "corporate_governance", "auditors_report",
    ]

    all_triples: list[dict[str, Any]] = []
    processed = 0

    for node in nodes:
        if processed >= max_pages:
            break

        meta = node.metadata or {}
        text = node.get_content() or ""
        if not text.strip():
            continue

        triples = extract_triples_from_text(
            text=text,
            company_id=meta.get("company_id", ""),
            source_file=meta.get("source_file", ""),
            page_label=meta.get("page_label", ""),
        )
        all_triples.extend(triples)
        processed += 1

    logger.info("KG extraction: %d triples from %d pages", len(all_triples), processed)
    return all_triples
