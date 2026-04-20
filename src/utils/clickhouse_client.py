"""
ClickHouse client wrapper with retry logic and connection pooling.
"""

from __future__ import annotations

import time
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_client: Client | None = None


def get_client() -> Client:
    """Return a singleton ClickHouse client, creating it if needed."""
    global _client
    if _client is None:
        logger.info(
            "Connecting to ClickHouse at %s:%s",
            settings.clickhouse.host,
            settings.clickhouse.port,
        )
        _client = clickhouse_connect.get_client(
            host=settings.clickhouse.host,
            port=settings.clickhouse.port,
            database=settings.clickhouse.database,
            username=settings.clickhouse.user,
            password=settings.clickhouse.password,
            connect_timeout=30,
            send_receive_timeout=300,
        )
        logger.info("ClickHouse connection established.")
    return _client


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def execute_query(query: str, parameters: dict[str, Any] | None = None) -> Any:
    """Execute a query with automatic retry on transient failures."""
    client = get_client()
    try:
        return client.query(query, parameters=parameters or {})
    except Exception as exc:
        logger.warning("Query failed, will retry. Error: %s", exc)
        raise


def ping() -> bool:
    """Check if ClickHouse is reachable."""
    try:
        get_client().ping()
        return True
    except Exception as exc:
        logger.error("ClickHouse ping failed: %s", exc)
        return False


def wait_for_clickhouse(max_wait_seconds: int = 60) -> None:
    """Block until ClickHouse is ready or timeout is reached."""
    logger.info("Waiting for ClickHouse to be ready...")
    start = time.time()
    while time.time() - start < max_wait_seconds:
        if ping():
            logger.info("ClickHouse is ready.")
            return
        time.sleep(3)
    raise TimeoutError(
        f"ClickHouse not ready after {max_wait_seconds}s. "
        "Is Docker running? Try: docker compose up -d"
    )
