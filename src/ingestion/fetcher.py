"""
Raw data ingestion from NYC Open Data API into ClickHouse requests_raw table.

Strategy:
  - Fetch in paginated batches via Socrata REST API
  - Validate each record with RawRequest Pydantic model
  - Bad records are logged to bad_records.jsonl, never silently dropped
  - Checkpointing: tracks last ingested offset in a local file
  - Retry logic via tenacity on transient HTTP failures
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Generator

import pandas as pd
import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings
from src.models.request import RawRequest
from src.utils.clickhouse_client import get_client, wait_for_clickhouse
from src.utils.logger import get_logger

logger = get_logger(__name__)

CHECKPOINT_FILE = Path(".pipeline_checkpoint.json")
BAD_RECORDS_FILE = Path("logs/bad_records_raw.jsonl")
FIELDS = [
    "unique_key", "created_date", "closed_date", "agency", "agency_name",
    "complaint_type", "descriptor", "location_type", "incident_zip",
    "city", "borough", "status", "resolution_description", "latitude", "longitude",
]


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def load_checkpoint() -> int:
    if CHECKPOINT_FILE.exists():
        data = json.loads(CHECKPOINT_FILE.read_text())
        offset = data.get("raw_offset", 0)
        logger.info("Resuming from checkpoint offset %d", offset)
        return offset
    return 0


def save_checkpoint(offset: int) -> None:
    data: dict[str, int] = {}
    if CHECKPOINT_FILE.exists():
        data = json.loads(CHECKPOINT_FILE.read_text())
    data["raw_offset"] = offset
    CHECKPOINT_FILE.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# Fetch from API
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(settings.max_retries),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def _fetch_batch(offset: int, limit: int) -> list[dict]:  # type: ignore[return]
    url = f"{settings.nyc_api.base_url}/{settings.nyc_api.dataset_id}.json"
    params: dict[str, str | int] = {
        "$limit": limit,
        "$offset": offset,
        "$order": "unique_key ASC",
        "$select": ",".join(FIELDS),
    }
    headers: dict[str, str] = {}
    if settings.nyc_api.app_token:
        headers["X-App-Token"] = settings.nyc_api.app_token

    resp = requests.get(url, params=params, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Validate + split good/bad records
# ---------------------------------------------------------------------------

def _validate_batch(
    raw_records: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Returns (valid_records, bad_records)."""
    valid: list[dict] = []
    bad: list[dict] = []

    for record in raw_records:
        try:
            validated = RawRequest(**record)
            valid.append(validated.model_dump())
        except Exception as exc:
            logger.warning("Validation failed for record %s: %s", record.get("unique_key"), exc)
            bad.append({"record": record, "error": str(exc)})

    return valid, bad


def _log_bad_records(bad: list[dict]) -> None:
    if not bad:
        return
    BAD_RECORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with BAD_RECORDS_FILE.open("a") as f:
        for entry in bad:
            f.write(json.dumps(entry) + "\n")
    logger.warning("Logged %d bad records to %s", len(bad), BAD_RECORDS_FILE)


# ---------------------------------------------------------------------------
# Insert into ClickHouse
# ---------------------------------------------------------------------------

def _insert_batch(records: list[dict]) -> None:
    if not records:
        return
    df = pd.DataFrame(records)
    # Ensure all expected columns present
    for col in FIELDS:
        if col not in df.columns:
            df[col] = None
    df = df[FIELDS]
    client = get_client()
    client.insert_df(settings.raw_table, df)


# ---------------------------------------------------------------------------
# Main ingestion function
# ---------------------------------------------------------------------------

def ingest_raw(
    max_rows: int | None = None,
    batch_size: int | None = None,
) -> int:
    """
    Ingest raw NYC 311 data into ClickHouse.

    Args:
        max_rows: Cap total rows (useful for testing). None = full dataset.
        batch_size: Rows per API call. Defaults to settings.batch_size.

    Returns:
        Total rows successfully inserted.
    """
    wait_for_clickhouse()

    limit = batch_size or settings.batch_size
    offset = load_checkpoint()
    total_inserted = 0
    total_bad = 0

    logger.info(
        "Starting raw ingestion | batch_size=%d | max_rows=%s | start_offset=%d",
        limit, max_rows, offset,
    )

    while True:
        if max_rows and total_inserted >= max_rows:
            logger.info("Reached max_rows=%d, stopping.", max_rows)
            break

        logger.info("Fetching batch offset=%d ...", offset)
        try:
            raw_batch = _fetch_batch(offset, limit)
        except Exception as exc:
            logger.error("Fatal fetch error at offset %d: %s", offset, exc)
            break

        if not raw_batch:
            logger.info("API returned empty batch — ingestion complete.")
            break

        valid, bad = _validate_batch(raw_batch)
        _log_bad_records(bad)
        total_bad += len(bad)

        try:
            _insert_batch(valid)
        except Exception as exc:
            logger.error("Insert failed at offset %d: %s", offset, exc)
            # Don't advance checkpoint — allow retry from this point
            break

        total_inserted += len(valid)
        offset += limit
        save_checkpoint(offset)

        logger.info(
            "Progress: inserted=%d | bad=%d | offset=%d",
            total_inserted, total_bad, offset,
        )

        # Polite rate limiting
        time.sleep(0.2)

    logger.info(
        "Raw ingestion finished. Total inserted=%d | Total bad=%d",
        total_inserted, total_bad,
    )
    return total_inserted


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def get_raw_row_count() -> int:
    client = get_client()
    result = client.query(f"SELECT count() FROM {settings.clickhouse.database}.{settings.raw_table}")
    return int(result.first_row[0])


def show_raw_schema() -> None:
    client = get_client()
    result = client.query(
        f"DESCRIBE TABLE {settings.clickhouse.database}.{settings.raw_table}"
    )
    logger.info("Raw table schema:")
    for row in result.named_results():
        logger.info("  %-30s %s", row["name"], row["type"])
