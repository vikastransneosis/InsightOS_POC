#!/usr/bin/env python3
"""
CLI script to run knowledge graph extraction.
Usage:
    python scripts/extract_kg.py                         # all companies
    python scripts/extract_kg.py --company FEDERALBNK    # one company
    python scripts/extract_kg.py --max-pages 20          # limit pages
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main():
    parser = argparse.ArgumentParser(description="InsightOS KG extraction")
    parser.add_argument("--company", type=str, default=None, help="Filter to one company_id")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=120,
        help="With --company: cap pages for that company. Without --company: total budget split evenly.",
    )
    args = parser.parse_args()

    from config.settings import configure_global_settings
    configure_global_settings()

    print(f"\n{'='*60}")
    print("  InsightOS POC 2 — Knowledge Graph Extraction")
    print(f"  Company: {args.company or 'ALL'}")
    print(f"  Max pages: {args.max_pages}")
    print(f"{'='*60}\n")

    with open(ROOT / "manifest.yaml") as f:
        manifest = yaml.safe_load(f) or {}

    from pipelines.p1_ingestion.parser import load_manifest_documents
    from pipelines.p2_graph.extractor import (
        extract_triples_from_nodes,
        select_pages_for_kg_extraction,
    )

    docs = load_manifest_documents(manifest, ROOT)
    selected = select_pages_for_kg_extraction(
        docs,
        company_id=args.company,
        max_pages=args.max_pages,
    )
    print(f"Processing {len(selected)} document pages (after per-company selection)...")

    triples = extract_triples_from_nodes(selected, max_pages=len(selected))

    from pipelines.p2_graph.loader import push_to_neo4j, save_triples
    save_triples(triples, merge=True)
    push_to_neo4j(triples)

    print(f"\n--- Result ---")
    print(f"Triples extracted: {len(triples)}")
    print(f"Pages processed: {len(selected)}")

    if triples:
        print("\nSample triples:")
        for t in triples[:5]:
            print(f"  ({t['subject']}) —[{t['relation']}]→ ({t['object']})")


if __name__ == "__main__":
    main()
