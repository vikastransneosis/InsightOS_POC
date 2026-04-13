"""
Query-time knowledge graph context from Neo4j (live Cypher), with JSON fallback in query_engine.

Default: safe parameterized subgraph query scoped by relationship.company_id.
Optional NEO4J_TEXT_TO_CYPHER=1: LLM proposes MATCH...RETURN; validated before execution.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from config.settings import (
    NEO4J_QUERY_LIMIT,
    NEO4J_TEXT_TO_CYPHER,
    USE_NEO4J_IN_QUERY,
)
from pipelines.p2_graph.loader import try_load_neo4j

logger = logging.getLogger(__name__)

_FORBIDDEN = re.compile(
    r"\b(CREATE|MERGE|DELETE|DROP|DETACH|SET\s|REMOVE\s|LOAD\s+CSV|FOREACH|"
    r"CALL\s+dbms|CALL\s+apoc\.|GRANT|DENY|REVOKE|ALTER|USING\s+PERIODIC)\b",
    re.IGNORECASE,
)


def _validate_readonly_cypher(cypher: str) -> bool:
    s = (cypher or "").strip()
    if not s.lower().startswith("match"):
        return False
    if ";" in s.rstrip().rstrip(";"):
        return False
    if _FORBIDDEN.search(s):
        return False
    return True


def _strip_llm_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:cypher)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _template_subgraph_cypher(limit: int) -> str:
    return f"""
MATCH (a:InsightEntity)-[r]->(b:InsightEntity)
WHERE coalesce(r.company_id, '') = $company_id
RETURN a.name AS subject,
       a.entity_type AS subject_type,
       type(r) AS relation,
       b.name AS object,
       b.entity_type AS object_type,
       coalesce(r.page_label, '') AS page_label,
       coalesce(r.source_file, '') AS source_file
LIMIT {int(limit)}
""".strip()


def _rows_to_context(rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    lines = []
    out_rows = []
    for row in rows:
        subj = row.get("subject") or ""
        obj = row.get("object") or ""
        rel = row.get("relation") or ""
        st = row.get("subject_type") or ""
        ot = row.get("object_type") or ""
        line = f"- {subj} ({st}) —[{rel}]→ {obj} ({ot})"
        pg = row.get("page_label") or ""
        sf = row.get("source_file") or ""
        if pg or sf:
            line += f"  [{sf} p.{pg}]" if pg else f"  [{sf}]"
        lines.append(line)
        out_rows.append(
            {
                "subject": subj,
                "subject_type": st,
                "relation": rel,
                "object": obj,
                "object_type": ot,
                "page_label": pg,
                "source_file": sf,
            }
        )
    header = "Knowledge graph (Neo4j, company-scoped Cypher):"
    text = "\n\n" + header + "\n" + "\n".join(lines) if lines else ""
    return text, out_rows


def _run_cypher(driver: Any, cypher: str, company_id: str, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    params: dict[str, Any] = {"company_id": company_id}
    if re.search(r"\$limit\b", cypher):
        params["limit"] = int(limit)
    with driver.session() as session:
        result = session.run(cypher, **params)
        for record in result:
            rows.append(dict(record))
    return rows


def _llm_cypher(question: str, company_id: str) -> str | None:
    from config.settings import get_llm

    prompt = f"""You write a single read-only Neo4j 5 Cypher query for an InsightOS financial knowledge graph.

Schema:
- Nodes: (:InsightEntity {{uid, name, entity_type}})
- Relationships connect InsightEntity nodes; types include OFFICER_OF, REGULATED_BY, SUPPLIES_TO, AUDITED_BY, etc.
- Many relationships have property company_id (NSE symbol string) for the filing issuer.

Rules:
- First keyword must be MATCH (no CREATE/MERGE/DELETE/SET/REMOVE/DROP/CALL/LOAD).
- Use parameter $company_id to filter: e.g. WHERE coalesce(r.company_id,'') = $company_id
- Return human-readable columns: subject, subject_type, relation, object, object_type, page_label, source_file where available from nodes r and a,b.
- End with LIMIT 40 or less. No semicolons. No markdown.

company_id parameter value will be: {company_id!r}
User question: {question}

Output only the Cypher statement, nothing else."""

    try:
        llm = get_llm()
        resp = llm.complete(prompt)
        raw = _strip_llm_fences(str(resp))
        if not _validate_readonly_cypher(raw):
            logger.warning("LLM Cypher failed validation")
            return None
        return raw
    except Exception as e:
        logger.warning("LLM Cypher generation failed: %s", e)
        return None


def fetch_graph_context_from_neo4j(
    company_id: str,
    question: str,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Returns dict: text (for LLM), rows (structured), source ('neo4j_template'|'neo4j_llm'|'').
    Empty source if Neo4j disabled, unavailable, or no rows.
    """
    if not USE_NEO4J_IN_QUERY:
        return {"text": "", "rows": [], "source": ""}

    driver = try_load_neo4j()
    if not driver:
        return {"text": "", "rows": [], "source": ""}

    lim = limit if limit is not None else NEO4J_QUERY_LIMIT
    rows: list[dict[str, Any]] = []
    source = ""

    try:
        if NEO4J_TEXT_TO_CYPHER:
            cy = _llm_cypher(question, company_id)
            if cy:
                try:
                    rows = _run_cypher(driver, cy, company_id, lim)
                    source = "neo4j_llm" if rows else ""
                except Exception as e:
                    logger.debug("LLM Cypher execution failed: %s", e)
                    rows = []

        if not rows:
            cy = _template_subgraph_cypher(lim)
            try:
                rows = _run_cypher(driver, cy, company_id, lim)
                source = "neo4j_template" if rows else ""
            except Exception as e:
                logger.debug("Template Cypher failed: %s", e)
                rows = []
    finally:
        driver.close()

    text, structured = _rows_to_context(rows)
    return {"text": text, "rows": structured, "source": source}
