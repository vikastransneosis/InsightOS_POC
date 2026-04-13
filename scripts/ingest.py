#!/usr/bin/env python3
"""
CLI script to run the ingestion pipeline.
Usage:
    python scripts/ingest.py          # incremental ingestion
    python scripts/ingest.py --reset  # full re-ingestion
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
    args = parser.parse_args()

    from pipelines.p1_ingestion.pipeline import run_ingestion

    print(f"\n{'='*60}")
    print("  InsightOS POC 2 — Ingestion Pipeline")
    print(f"  Mode: {'RESET + full re-ingest' if args.reset else 'incremental'}")
    print(f"{'='*60}\n")

    result = run_ingestion(reset=args.reset)
    print("\n--- Result ---")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
