"""
Knowledge graph storage: file-based JSON graph with optional Neo4j integration.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config.settings import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER, STORAGE_DIR

logger = logging.getLogger(__name__)

KG_GRAPH_PATH = STORAGE_DIR / "kg_graph.json"


def _load_graph() -> dict[str, Any]:
    if KG_GRAPH_PATH.is_file():
        with open(KG_GRAPH_PATH) as f:
            return json.load(f)
    return {"triples": [], "nodes": {}, "edges": []}


def _save_graph(graph: dict[str, Any]):
    KG_GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(KG_GRAPH_PATH, "w") as f:
        json.dump(graph, f, indent=2)


def save_triples(triples: list[dict[str, Any]], *, merge: bool = True):
    """Save extracted triples to the file-based graph store."""
    if merge:
        graph = _load_graph()
    else:
        graph = {"triples": [], "nodes": {}, "edges": []}

    for t in triples:
        graph["triples"].append(t)

        subj_key = f"{t['subject_type']}::{t['subject']}"
        obj_key = f"{t['object_type']}::{t['object']}"

        if subj_key not in graph["nodes"]:
            graph["nodes"][subj_key] = {
                "name": t["subject"],
                "type": t["subject_type"],
                "company_ids": [],
            }
        if t.get("company_id") and t["company_id"] not in graph["nodes"][subj_key]["company_ids"]:
            graph["nodes"][subj_key]["company_ids"].append(t["company_id"])

        if obj_key not in graph["nodes"]:
            graph["nodes"][obj_key] = {
                "name": t["object"],
                "type": t["object_type"],
                "company_ids": [],
            }
        if t.get("company_id") and t["company_id"] not in graph["nodes"][obj_key]["company_ids"]:
            graph["nodes"][obj_key]["company_ids"].append(t["company_id"])

        graph["edges"].append({
            "source": subj_key,
            "target": obj_key,
            "relation": t["relation"],
            "properties": t.get("properties", {}),
            "source_file": t.get("source_file", ""),
            "page_label": t.get("page_label", ""),
        })

    _save_graph(graph)
    logger.info("KG saved: %d triples, %d nodes, %d edges",
                len(graph["triples"]), len(graph["nodes"]), len(graph["edges"]))


def load_graph() -> dict[str, Any]:
    return _load_graph()


def graph_exists() -> bool:
    return KG_GRAPH_PATH.is_file()


def get_triples_for_company(company_id: str, limit: int = 100) -> list[dict]:
    graph = _load_graph()
    triples = [t for t in graph["triples"] if t.get("company_id") == company_id]
    return triples[:limit]


def get_graph_nodes() -> list[dict]:
    graph = _load_graph()
    return [{"key": k, **v} for k, v in graph.get("nodes", {}).items()]


def get_graph_edges() -> list[dict]:
    graph = _load_graph()
    return graph.get("edges", [])


def get_graph_for_company(company_id: str) -> dict:
    """Get subgraph for a specific company (for visualization)."""
    graph = _load_graph()
    company_triples = [t for t in graph["triples"] if t.get("company_id") == company_id]

    relevant_node_keys = set()
    for t in company_triples:
        relevant_node_keys.add(f"{t['subject_type']}::{t['subject']}")
        relevant_node_keys.add(f"{t['object_type']}::{t['object']}")

    nodes = []
    for key in relevant_node_keys:
        if key in graph.get("nodes", {}):
            node_data = graph["nodes"][key]
            nodes.append({"id": key, **node_data})

    edges = [
        e for e in graph.get("edges", [])
        if e["source"] in relevant_node_keys or e["target"] in relevant_node_keys
    ]

    return {
        "company_id": company_id,
        "nodes": nodes,
        "edges": edges,
        "triple_count": len(company_triples),
    }


def query_triples(
    company_id: str,
    subject_contains: str | None = None,
    predicate_contains: str | None = None,
    object_contains: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Filter triples by optional substring matches."""
    graph = _load_graph()
    results = []
    for t in graph["triples"]:
        if t.get("company_id") != company_id:
            continue
        if subject_contains and subject_contains.lower() not in t["subject"].lower():
            continue
        if predicate_contains and predicate_contains.lower() not in t["relation"].lower():
            continue
        if object_contains and object_contains.lower() not in t["object"].lower():
            continue
        results.append(t)
        if len(results) >= limit:
            break
    return results


def try_load_neo4j():
    """Attempt to connect to Neo4j and return driver, or None."""
    if not NEO4J_PASSWORD:
        return None
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        return driver
    except Exception as e:
        logger.debug("Neo4j not available: %s", e)
        return None


def push_to_neo4j(triples: list[dict[str, Any]]):
    """Push triples to Neo4j if available (same model as scripts/load_kg_json_to_neo4j.py)."""
    driver = try_load_neo4j()
    if not driver:
        logger.info("Neo4j not configured — skipping graph push")
        return

    try:
        from pipelines.p2_graph.neo4j_import import import_triples_to_neo4j

        import_triples_to_neo4j(driver, triples, clear_first=False)
    finally:
        driver.close()
