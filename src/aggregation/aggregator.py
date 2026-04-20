"""
Aggregated Layer: computes 3 business-purpose summaries from requests_clean
and writes them back into ClickHouse aggregation tables.

Aggregations:
  1. agg_agency_performance  — Which agencies resolve tickets fastest/slowest?
  2. agg_borough_complaints  — What complaint types dominate each borough?
  3. agg_monthly_trend       — How does 311 call volume change over time?
"""

from __future__ import annotations

import pandas as pd

from config.settings import settings
from src.utils.clickhouse_client import get_client, wait_for_clickhouse
from src.utils.logger import get_logger

logger = get_logger(__name__)

DB = settings.clickhouse.database
CLEAN = f"{DB}.{settings.clean_table}"


# ---------------------------------------------------------------------------
# Helper: load clean data into pandas
# ---------------------------------------------------------------------------

def _load_clean(columns: list[str]) -> pd.DataFrame:
    col_str = ", ".join(columns)
    client = get_client()
    result = client.query(f"SELECT {col_str} FROM {CLEAN}")
    return pd.DataFrame(result.named_results())


# ---------------------------------------------------------------------------
# AGG 1: Agency performance
# Business question: Which agencies take longest to resolve requests,
#                   and how does that change month over month?
# ---------------------------------------------------------------------------

def compute_agency_performance() -> pd.DataFrame:
    logger.info("Computing agency performance aggregation...")

    cols = ["agency", "agency_name", "year_month", "is_resolved", "resolution_hours"]
    df = _load_clean(cols)

    df["resolution_hours"] = pd.to_numeric(df["resolution_hours"], errors="coerce")
    df["is_resolved"] = df["is_resolved"].astype(int)

    agg = (
        df.groupby(["agency", "agency_name", "year_month"])
        .agg(
            total_requests=("agency", "count"),
            resolved_count=("is_resolved", "sum"),
            avg_resolution_hrs=("resolution_hours", "mean"),
            median_resolution_hrs=("resolution_hours", "median"),
        )
        .reset_index()
    )

    agg["resolution_rate"] = agg["resolved_count"] / agg["total_requests"]
    agg["avg_resolution_hrs"] = agg["avg_resolution_hrs"].fillna(0.0).round(2)
    agg["median_resolution_hrs"] = agg["median_resolution_hrs"].fillna(0.0).round(2)
    agg["resolution_rate"] = agg["resolution_rate"].round(4)

    logger.info("Agency performance: %d rows", len(agg))
    return agg


# ---------------------------------------------------------------------------
# AGG 2: Borough complaint heatmap
# Business question: Which complaint categories are highest in each borough?
#                   What % of that borough's total do they represent?
# ---------------------------------------------------------------------------

def compute_borough_complaints() -> pd.DataFrame:
    logger.info("Computing borough complaint heatmap...")

    cols = ["borough", "complaint_category", "complaint_type", "year_month"]
    df = _load_clean(cols)

    agg = (
        df.groupby(["borough", "complaint_category", "complaint_type", "year_month"])
        .size()
        .reset_index(name="request_count")
    )

    # Compute % of total per borough per month
    borough_totals = (
        agg.groupby(["borough", "year_month"])["request_count"]
        .transform("sum")
    )
    agg["pct_of_borough"] = (agg["request_count"] / borough_totals).round(4)

    logger.info("Borough complaints: %d rows", len(agg))
    return agg


# ---------------------------------------------------------------------------
# AGG 3: Monthly volume trend
# Business question: Is 311 usage increasing? Which categories drive spikes?
# ---------------------------------------------------------------------------

def compute_monthly_trend() -> pd.DataFrame:
    logger.info("Computing monthly trend aggregation...")

    cols = ["year_month", "borough", "complaint_category", "is_resolved", "resolution_hours"]
    df = _load_clean(cols)

    df["resolution_hours"] = pd.to_numeric(df["resolution_hours"], errors="coerce")
    df["is_resolved"] = df["is_resolved"].astype(int)

    agg = (
        df.groupby(["year_month", "borough", "complaint_category"])
        .agg(
            request_count=("borough", "count"),
            resolved_count=("is_resolved", "sum"),
            avg_resolution_hrs=("resolution_hours", "mean"),
        )
        .reset_index()
    )

    agg["avg_resolution_hrs"] = agg["avg_resolution_hrs"].round(2)
    agg = agg.sort_values(["year_month", "borough", "complaint_category"])

    logger.info("Monthly trend: %d rows", len(agg))
    return agg


# ---------------------------------------------------------------------------
# Write aggregations to ClickHouse
# ---------------------------------------------------------------------------

def _write_agg(df: pd.DataFrame, table: str) -> None:
    if df.empty:
        logger.warning("Empty DataFrame, skipping write to %s", table)
        return
    client = get_client()
    client.insert_df(f"{DB}.{table}", df)
    logger.info("Wrote %d rows to %s.%s", len(df), DB, table)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_aggregations() -> dict[str, int]:
    """
    Compute all 3 aggregations and write to ClickHouse.
    Returns row counts per aggregation table.
    """
    wait_for_clickhouse()
    logger.info("=== AGGREGATION LAYER STARTED ===")

    agency_df = compute_agency_performance()
    _write_agg(agency_df, "agg_agency_performance")

    borough_df = compute_borough_complaints()
    _write_agg(borough_df, "agg_borough_complaints")

    trend_df = compute_monthly_trend()
    _write_agg(trend_df, "agg_monthly_trend")

    stats = {
        "agg_agency_performance": len(agency_df),
        "agg_borough_complaints": len(borough_df),
        "agg_monthly_trend": len(trend_df),
    }

    logger.info("=== AGGREGATION LAYER COMPLETE === %s", stats)
    return stats
