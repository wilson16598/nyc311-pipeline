"""
Clean Layer: reads from requests_raw, transforms, validates with Pydantic,
writes to requests_clean.

Cleaning steps applied:
  1. Deduplicate on unique_key
  2. Parse and standardize datetime fields
  3. Normalize text fields
  4. Clean ZIP codes
  5. Validate NYC lat/lng bounds
  6. Handle missing values
  7. Derive resolution metrics + categories
  8. Filter invalid resolution values
  9. Pydantic validation
  10. Log bad records
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

import pandas as pd
from pydantic import ValidationError

from config.settings import settings
from src.models.request import CleanRequest
from src.utils.clickhouse_client import get_client, wait_for_clickhouse
from src.utils.logger import get_logger

logger = get_logger(__name__)

BAD_CLEAN_FILE = Path("logs/bad_records_clean.jsonl")

# ---------------------------------------------------------------------------
# Load raw data
# ---------------------------------------------------------------------------

def _load_raw_data() -> pd.DataFrame:
    client = get_client()

    query = f"""
        SELECT
            unique_key,
            created_date,
            closed_date,
            agency,
            agency_name,
            complaint_type,
            descriptor,
            location_type,
            incident_zip,
            city,
            borough,
            status,
            latitude,
            longitude
        FROM {settings.clickhouse.database}.{settings.raw_table}
    """

    result = client.query(query)
    df = pd.DataFrame(result.named_results())

    logger.info("Loaded %d raw rows from ClickHouse.", len(df))
    return df


# ---------------------------------------------------------------------------
# Pandas cleaning
# ---------------------------------------------------------------------------

def _clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    original = len(df)

    # 1. Deduplicate
    df = df.drop_duplicates(subset=["unique_key"])
    logger.info("After dedup: %d rows (removed %d)", len(df), original - len(df))

    # 2. Datetime parsing
    df["created_date"] = pd.to_datetime(df["created_date"], errors="coerce", utc=True)
    df["closed_date"] = pd.to_datetime(df["closed_date"], errors="coerce", utc=True)

    df = df.dropna(subset=["created_date"])
    logger.info("After dropping null created_date: %d rows", len(df))

    # 3. Normalize text
    text_cols = [
        "agency", "agency_name", "complaint_type", "descriptor",
        "location_type", "city", "borough", "status"
    ]

    for col in text_cols:
        df[col] = (
            df[col]
            .fillna("UNKNOWN")
            .astype(str)
            .str.strip()
            .str.upper()
        )

    df["complaint_type"] = df["complaint_type"].str.title()
    df["descriptor"] = df["descriptor"].str.title()

    # 4. Borough cleanup
    valid_boroughs = {
        "BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"
    }
    df["borough"] = df["borough"].where(df["borough"].isin(valid_boroughs), "UNSPECIFIED")

    # 5. ZIP cleanup
    df["incident_zip"] = (
        df["incident_zip"]
        .astype(str)
        .str.extract(r"(\d{5})", expand=False)
    )

    # 6. Coordinates
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    df.loc[~df["latitude"].between(40.4, 41.0), "latitude"] = None
    df.loc[~df["longitude"].between(-74.3, -73.7), "longitude"] = None

    # 7. Defaults
    df["agency"] = df["agency"].replace("UNKNOWN", "N/A")
    df["status"] = df["status"].replace("UNKNOWN", "OPEN")

    # 8. Resolution hours
    df["resolution_hours"] = None
    mask = df["closed_date"].notna()

    df.loc[mask, "resolution_hours"] = (
        (df.loc[mask, "closed_date"] - df.loc[mask, "created_date"])
        .dt.total_seconds() / 3600
    )

    # Remove invalid resolution
    before = len(df)
    df = df[~(df["resolution_hours"].notna() & (df["resolution_hours"] < 0))]
    logger.info("Removed %d negative resolution rows", before - len(df))

    df.loc[df["resolution_hours"] > 8760, "resolution_hours"] = None

    # 9. Derived columns
    df["is_resolved"] = df["closed_date"].notna().astype(int)
    df["year_month"] = df["created_date"].dt.strftime("%Y-%m")

    def categorize(ct: str) -> str:
        ct = ct.upper()
        if "NOISE" in ct or "SOUND" in ct:
            return "NOISE"
        if "HEAT" in ct or "HOT WATER" in ct or "PLUMBING" in ct:
            return "HOUSING"
        if any(x in ct for x in ["STREET", "POTHOLE", "SIDEWALK"]):
            return "INFRASTRUCTURE"
        if any(x in ct for x in ["TRASH", "GARBAGE", "LITTER"]):
            return "SANITATION"
        if any(x in ct for x in ["PARK", "TREE"]):
            return "PARKS"
        if any(x in ct for x in ["TAXI", "VEHICLE", "PARKING"]):
            return "TRANSPORTATION"
        return "OTHER"

    df["complaint_category"] = df["complaint_type"].apply(categorize)

    # remove timezone for ClickHouse
    df["created_date"] = df["created_date"].dt.tz_localize(None)
    df["closed_date"] = df["closed_date"].apply(
        lambda x: x.tz_localize(None) if pd.notna(x) else None
    )

    logger.info("Final cleaned rows: %d", len(df))
    return df


# ---------------------------------------------------------------------------
# Pydantic validation + ClickHouse normalization
# ---------------------------------------------------------------------------

def _validate_and_filter(df: pd.DataFrame) -> tuple[list[dict], int]:
    valid: list[dict] = []
    bad_count = 0

    BAD_CLEAN_FILE.parent.mkdir(parents=True, exist_ok=True)

    def normalize_dt(value):
        if value is None:
            return None
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        if hasattr(value, "to_pydatetime"):
            return value.to_pydatetime()
        if isinstance(value, datetime):
            return value
        return None

    for record in df.to_dict(orient="records"):
        try:
            clean = CleanRequest(**record)
            row = clean.model_dump()

            row["created_date"] = normalize_dt(clean.created_date)
            row["closed_date"] = normalize_dt(clean.closed_date)

            valid.append(row)

        except ValidationError as exc:
            bad_count += 1
            with BAD_CLEAN_FILE.open("a") as f:
                f.write(json.dumps({"record": str(record), "error": str(exc)}) + "\n")

    logger.info("Pydantic validation: %d valid, %d invalid", len(valid), bad_count)
    return valid, bad_count


# ---------------------------------------------------------------------------
# Write to ClickHouse
# ---------------------------------------------------------------------------

CLEAN_COLUMNS = [
    "unique_key", "created_date", "closed_date", "agency", "agency_name",
    "complaint_type", "descriptor", "location_type", "incident_zip", "city",
    "borough", "status", "latitude", "longitude", "resolution_hours",
    "is_resolved", "complaint_category", "year_month",
]


def _write_clean(records: list[dict]) -> None:
    if not records:
        logger.warning("No valid records to write.")
        return

    df = pd.DataFrame(records)

    for col in CLEAN_COLUMNS:
        if col not in df.columns:
            df[col] = None

    df = df[CLEAN_COLUMNS]

    client = get_client()
    client.insert_df(
        f"{settings.clickhouse.database}.{settings.clean_table}",
        df
    )

    logger.info("Wrote %d rows to clean table", len(df))


# ---------------------------------------------------------------------------
# Pipeline entry
# ---------------------------------------------------------------------------

def run_cleaning() -> dict[str, int]:
    wait_for_clickhouse()
    logger.info("=== CLEAN PIPELINE START ===")
    client = get_client()
    client.command(f"TRUNCATE TABLE {settings.clickhouse.database}.{settings.clean_table}")

    raw_df = _load_raw_data()
    raw_count = len(raw_df)

    clean_df = _clean_dataframe(raw_df)
    after_clean = len(clean_df)

    valid, bad = _validate_and_filter(clean_df)

    _write_clean(valid)

    stats = {
        "raw_count": raw_count,
        "after_clean": after_clean,
        "valid": len(valid),
        "invalid": bad,
        "removed": raw_count - len(valid),
    }

    logger.info("=== CLEAN PIPELINE DONE ===")
    logger.info("Stats: %s", stats)

    return stats


def get_clean_row_count() -> int:
    client = get_client()
    result = client.query(
        f"SELECT count() FROM {settings.clickhouse.database}.{settings.clean_table}"
    )
    return int(result.first_row[0])
