"""
Central configuration using Pydantic Settings.
All values loaded from environment / .env file — never hardcoded.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClickHouseSettings(BaseSettings):
    host: str = Field(default="localhost", alias="CLICKHOUSE_HOST")
    port: int = Field(default=8123, alias="CLICKHOUSE_PORT")
    database: str = Field(default="nyc311", alias="CLICKHOUSE_DATABASE")
    user: str = Field(default="pipeline_user", alias="CLICKHOUSE_USER")
    password: str = Field(default="pipeline_pass_2024", alias="CLICKHOUSE_PASSWORD")


class NYCApiSettings(BaseSettings):
    app_token: str = Field(default="", alias="NYC_APP_TOKEN")
    dataset_id: str = Field(default="erm2-nwe9", alias="NYC_DATASET_ID")
    base_url: str = "https://data.cityofnewyork.us/resource"


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    batch_size: int = Field(default=50_000, alias="BATCH_SIZE")
    max_retries: int = Field(default=3, alias="MAX_RETRIES")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    raw_table: str = Field(default="requests_raw", alias="RAW_TABLE")
    clean_table: str = Field(default="requests_clean", alias="CLEAN_TABLE")
    agg_table_prefix: str = Field(default="agg_", alias="AGG_TABLE_PREFIX")

    clickhouse: ClickHouseSettings = ClickHouseSettings()
    nyc_api: NYCApiSettings = NYCApiSettings()


# Singleton — import this everywhere
settings = PipelineSettings()
