"""
Import KG triples (from kg_graph.json or in-memory list) into Neo4j.

Uses label :InsightEntity with stable `uid` = "{entity_type}::{name}" and typed
relationships where the predicate is in the financial schema; otherwise :RELATED.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ALLOWED_REL = frozenset(
    {
        "SUBSIDIARY_OF",
        "PROMOTED_BY",
        "HOLDS_STAKE_IN",
        "OFFICER_OF",
        "AUDITED_BY",
        "RATED_BY",
        "RELATED_PARTY_OF",
        "BORROWS_FROM",
        "JV_PARTNER_OF",
        "REGULATED_BY",
        "SUPPLIES_TO",
    }
)
_REL_SAFE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def entity_uid(entity_type: str, name: str) -> str:
    return f"{entity_type}::{name}"


def flatten_edge_props(raw: Any) -> dict[str, Any]:
    """Neo4j relationship properties must be JSON-serializable scalars or flat strings."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {"detail": raw[:4000]} if raw.strip() else {}
    if isinstance(raw, list):
        return {"items": json.dumps(raw, ensure_ascii=False)[:4000]}
    if not isinstance(raw, dict):
        return {"value": str(raw)[:4000]}

    out: dict[str, Any] = {}
    for k, v in raw.items():
        if v is None:
            continue
        key = str(k)
        if isinstance(v, (dict, list)):
            out[key] = json.dumps(v, ensure_ascii=False)[:4000]
        elif isinstance(v, (str, int, float, bool)):
            out[key] = v
        else:
            out[key] = str(v)[:4000]
    return out


def safe_relation_type(rel: str) -> str:
    if rel in _ALLOWED_REL and _REL_SAFE.match(rel):
        return rel
    return "RELATED"


def ensure_schema(session) -> None:
    session.run(
        """
        CREATE CONSTRAINT insightos_entity_uid IF NOT EXISTS
        FOR (e:InsightEntity) REQUIRE e.uid IS UNIQUE
        """
    )


def import_triples_to_neo4j(
    driver: Any,
    triples: list[dict[str, Any]],
    *,
    clear_first: bool = False,
) -> dict[str, int]:
    """
    Upsert triples into Neo4j. Each triple: subject, subject_type, relation, object, object_type,
    optional properties, source_file, page_label, company_id.
    """
    if clear_first:
        logger.info("Clearing all InsightEntity nodes...")
        with driver.session() as session:
            session.run("MATCH (n:InsightEntity) DETACH DELETE n")

    written = 0
    with driver.session() as session:
        ensure_schema(session)
        for t in triples:
            suid = entity_uid(t["subject_type"], t["subject"])
            ouid = entity_uid(t["object_type"], t["object"])
            rel = safe_relation_type(str(t.get("relation", "RELATED")))
            edge_props = flatten_edge_props(t.get("properties"))
            edge_props.setdefault("source_file", t.get("source_file", ""))
            edge_props.setdefault("page_label", str(t.get("page_label", "")))
            edge_props.setdefault("company_id", t.get("company_id", ""))
            if rel == "RELATED" and t.get("relation"):
                edge_props.setdefault("original_predicate", str(t["relation"]))

            # Dynamic rel type must be validated (safe_relation_type)
            cypher = (
                "MERGE (s:InsightEntity {uid: $suid}) "
                "SET s.name = $sname, s.entity_type = $stype "
                "MERGE (o:InsightEntity {uid: $ouid}) "
                "SET o.name = $oname, o.entity_type = $otype "
                f"MERGE (s)-[r:`{rel}`]->(o) "
                "SET r += $eprops"
            )
            session.run(
                cypher,
                {
                    "suid": suid,
                    "sname": t["subject"],
                    "stype": t["subject_type"],
                    "ouid": ouid,
                    "oname": t["object"],
                    "otype": t["object_type"],
                    "eprops": edge_props,
                },
            )
            written += 1
            if written % 500 == 0:
                logger.info("Imported %s triples...", written)

    logger.info("Neo4j import finished: %s relationships written", written)
    return {"triples_written": written, "input_triples": len(triples)}


def import_kg_json_file(
    path: Path,
    driver: Any,
    *,
    clear_first: bool = False,
) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    triples = data.get("triples") or []
    if not triples:
        return {"error": "No triples in JSON", "path": str(path)}
    stats = import_triples_to_neo4j(driver, triples, clear_first=clear_first)
    stats["path"] = str(path)
    return stats
