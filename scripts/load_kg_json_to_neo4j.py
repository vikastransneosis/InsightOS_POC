#!/usr/bin/env python3
"""
Load storage/kg_graph.json into Neo4j (no LLM calls).

Prerequisites:
  pip install neo4j
  Neo4j running; set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD in .env

Usage:
  cd llamaIndexPOC_2
  source ../.venv/bin/activate
  python scripts/load_kg_json_to_neo4j.py
  python scripts/load_kg_json_to_neo4j.py --json /path/to/kg_graph.json
  python scripts/load_kg_json_to_neo4j.py --clear   # wipe InsightEntity graph first
  python scripts/load_kg_json_to_neo4j.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_JSON = ROOT / "storage" / "kg_graph.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import kg_graph.json into Neo4j")
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON,
        help=f"Path to kg_graph.json (default: {DEFAULT_JSON})",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete all :InsightEntity nodes before import",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print triple count and exit",
    )
    args = parser.parse_args()

    path: Path = args.json
    if not path.is_file():
        logger.error("File not found: %s", path)
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8"))
    triples = data.get("triples") or []
    logger.info("Found %s triples in %s", len(triples), path)

    if args.dry_run:
        return

    try:
        from neo4j import GraphDatabase
    except ImportError:
        logger.error("Install the driver: pip install neo4j")
        sys.exit(1)

    from config.settings import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

    if not NEO4J_PASSWORD:
        logger.error("Set NEO4J_PASSWORD (and URI/user) in .env")
        sys.exit(1)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
    except Exception as e:
        logger.error("Cannot connect to Neo4j at %s: %s", NEO4J_URI, e)
        sys.exit(1)

    from pipelines.p2_graph.neo4j_import import import_kg_json_file

    stats = import_kg_json_file(path, driver, clear_first=args.clear)
    driver.close()
    logger.info("Done: %s", stats)


if __name__ == "__main__":
    main()
