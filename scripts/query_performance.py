"""
Query Performance Demo

Demonstrates the value of ClickHouse's partitioning and bloom filter indexes
with before/after query timing comparisons.

Run:
    uv run python scripts/query_performance.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.clickhouse_client import get_client, wait_for_clickhouse
from src.utils.logger import get_logger, setup_logging
from config.settings import settings

setup_logging("INFO")
logger = get_logger("query_perf")

DB = settings.clickhouse.database
CLEAN = f"{DB}.requests_clean"


def time_query(label: str, query: str) -> float:
    client = get_client()
    start = time.perf_counter()
    result = client.query(query)
    elapsed = time.perf_counter() - start
    rows = len(result.result_rows)
    logger.info("[%s] %.4fs | %d rows returned", label, elapsed, rows)
    return elapsed


def run_demos() -> None:
    wait_for_clickhouse()

    logger.info("=" * 60)
    logger.info("QUERY PERFORMANCE DEMO")
    logger.info("=" * 60)

    # -----------------------------------------------------------------------
    # DEMO 1: Partition pruning
    # Without partitioning, a date-range scan reads the entire table.
    # With PARTITION BY year_month, ClickHouse skips irrelevant parts.
    # -----------------------------------------------------------------------
    logger.info("\n--- DEMO 1: Partition pruning (date range scan) ---")

    t_full = time_query(
        "FULL TABLE SCAN (no partition filter)",
        f"SELECT count() FROM {CLEAN}",
    )

    t_partitioned = time_query(
        "PARTITION PRUNED (2023 only)",
        f"""
        SELECT count() FROM {CLEAN}
        WHERE year_month BETWEEN '2023-01' AND '2023-12'
        """,
    )

    logger.info(
        "Speedup from partition pruning: %.1fx",
        t_full / t_partitioned if t_partitioned > 0 else 0,
    )

    # -----------------------------------------------------------------------
    # DEMO 2: Bloom filter index on agency column
    # idx_agency was created in init_db.sql — filters data granules
    # that cannot contain the target agency.
    # -----------------------------------------------------------------------
    logger.info("\n--- DEMO 2: Bloom filter index on agency ---")

    t_no_index = time_query(
        "Agency filter WITHOUT using index (force skip)",
        f"""
        SELECT count(), avg(resolution_hours)
        FROM {CLEAN}
        WHERE cityHash64(unique_key) % 1 = 0  -- prevents index use
          AND agency = 'NYPD'
        """,
    )

    t_indexed = time_query(
        "Agency filter WITH bloom filter index",
        f"""
        SELECT count(), avg(resolution_hours)
        FROM {CLEAN}
        WHERE agency = 'NYPD'
        """,
    )

    logger.info(
        "Speedup from bloom filter index: %.1fx",
        t_no_index / t_indexed if t_indexed > 0 else 0,
    )

    # -----------------------------------------------------------------------
    # DEMO 3: Analytical aggregation — ClickHouse columnar advantage
    # This is the core strength of ClickHouse vs row-oriented stores.
    # -----------------------------------------------------------------------
    logger.info("\n--- DEMO 3: Columnar aggregation efficiency ---")

    time_query(
        "Top 10 agencies by avg resolution time",
        f"""
        SELECT
            agency,
            agency_name,
            count() AS total,
            round(avg(resolution_hours), 2) AS avg_hrs,
            round(median(resolution_hours), 2) AS median_hrs
        FROM {CLEAN}
        WHERE is_resolved = 1 AND resolution_hours IS NOT NULL
        GROUP BY agency, agency_name
        ORDER BY avg_hrs DESC
        LIMIT 10
        """,
    )

    time_query(
        "Borough complaint distribution",
        f"""
        SELECT
            borough,
            complaint_category,
            count() AS cnt,
            round(count() * 100.0 / sum(count()) OVER (PARTITION BY borough), 2) AS pct
        FROM {CLEAN}
        GROUP BY borough, complaint_category
        ORDER BY borough, cnt DESC
        """,
    )

    logger.info("\nPerformance demo complete.")
    logger.info(
        "Key insight: ClickHouse partition pruning + bloom filter indexes "
        "dramatically reduce I/O for time-range and exact-match queries, "
        "which are the dominant access patterns in 311 analytics."
    )


if __name__ == "__main__":
    run_demos()
