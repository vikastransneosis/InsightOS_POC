#!/usr/bin/env python3
"""
CLI script to run the ingestion pipeline.
Usage:
    python scripts/ingest.py                  # incremental ingest (rebuilds Chroma if docstore present)
    python scripts/ingest.py --reset          # full re-ingestion from PDFs
    python scripts/ingest.py --rebuild-vectors # Chroma + BM25 only from docstore.json
"""
import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main():
    parser = argparse.ArgumentParser(description="InsightOS ingestion pipeline")
    parser.add_argument("--reset", action="store_true", help="Reset and re-ingest everything")
    parser.add_argument(
        "--rebuild-vectors",
        action="store_true",
        help="Rebuild Chroma + BM25 from storage/docstore.json only (no PDF parsing)",
    )
    args = parser.parse_args()
    if args.reset and args.rebuild_vectors:
        parser.error("Use either --reset or --rebuild-vectors, not both")

    from pipelines.p1_ingestion.pipeline import rebuild_chroma_from_docstore, run_ingestion

    print(f"\n{'='*60}")
    print("  InsightOS POC 2 — Ingestion Pipeline")
    if args.rebuild_vectors:
        print("  Mode: rebuild vectors from docstore (no PDFs)")
    else:
        print(f"  Mode: {'RESET + full re-ingest' if args.reset else 'incremental'}")
    print(f"{'='*60}\n")

    if args.rebuild_vectors:
        result = rebuild_chroma_from_docstore()
    else:
        result = run_ingestion(reset=args.reset)
    print("\n--- Result ---")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
