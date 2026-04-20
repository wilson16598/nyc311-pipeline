"""
Main pipeline runner — executes all 3 stages in order.

Usage:
    uv run python scripts/run_pipeline.py              # full pipeline
    uv run python scripts/run_pipeline.py --stage raw  # raw only
    uv run python scripts/run_pipeline.py --stage clean
    uv run python scripts/run_pipeline.py --stage agg
    uv run python scripts/run_pipeline.py --max-rows 100000  # test run
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Make sure src/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ingestion.fetcher import ingest_raw, get_raw_row_count, show_raw_schema
from src.cleaning.cleaner import run_cleaning, get_clean_row_count
from src.aggregation.aggregator import run_aggregations
from src.utils.logger import get_logger, setup_logging

setup_logging("INFO", "logs/pipeline.log")
logger = get_logger("pipeline")


def run_raw_stage(max_rows: int | None) -> None:
    logger.info("=" * 60)
    logger.info("STAGE 1: RAW INGESTION")
    logger.info("=" * 60)
    start = time.time()
    inserted = ingest_raw(max_rows=max_rows)
    elapsed = time.time() - start

    total_in_db = get_raw_row_count()
    show_raw_schema()

    logger.info("Raw stage complete in %.1fs", elapsed)
    logger.info("  Inserted this run : %d", inserted)
    logger.info("  Total rows in DB  : %d", total_in_db)


def run_clean_stage() -> None:
    logger.info("=" * 60)
    logger.info("STAGE 2: CLEAN LAYER")
    logger.info("=" * 60)
    start = time.time()
    stats = run_cleaning()
    elapsed = time.time() - start

    clean_count = get_clean_row_count()
    logger.info("Clean stage complete in %.1fs", elapsed)
    logger.info("  Stats     : %s", stats)
    logger.info("  Clean rows: %d", clean_count)


def run_agg_stage() -> None:
    logger.info("=" * 60)
    logger.info("STAGE 3: AGGREGATIONS")
    logger.info("=" * 60)
    start = time.time()
    stats = run_aggregations()
    elapsed = time.time() - start

    logger.info("Aggregation stage complete in %.1fs", elapsed)
    logger.info("  Stats: %s", stats)


def main() -> None:
    parser = argparse.ArgumentParser(description="NYC 311 Big Data Pipeline")
    parser.add_argument(
        "--stage",
        choices=["raw", "clean", "agg", "all"],
        default="all",
        help="Which stage to run (default: all)",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Limit rows ingested (for testing). Example: --max-rows 100000",
    )
    args = parser.parse_args()

    logger.info("NYC 311 Pipeline starting | stage=%s | max_rows=%s", args.stage, args.max_rows)

    if args.stage in ("raw", "all"):
        run_raw_stage(args.max_rows)

    if args.stage in ("clean", "all"):
        run_clean_stage()

    if args.stage in ("agg", "all"):
        run_agg_stage()

    logger.info("Pipeline finished.")


if __name__ == "__main__":
    main()
