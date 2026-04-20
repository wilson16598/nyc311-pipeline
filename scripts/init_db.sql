-- =============================================================================
-- NYC 311 Pipeline — ClickHouse Schema
-- =============================================================================
-- Run automatically by Docker on first start via docker-entrypoint-initdb.d
-- =============================================================================

CREATE DATABASE IF NOT EXISTS nyc311;

-- ---------------------------------------------------------------------------
-- RAW LAYER
-- MergeTree engine — partitioned by month for fast time-range scans
-- No deduplication at this layer; duplicates caught in clean layer
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nyc311.requests_raw
(
    unique_key       String,
    created_date     Datatime,
    closed_date      Nullable(String),
    agency           Nullable(String),
    agency_name      Nullable(String),
    complaint_type   Nullable(String),
    descriptor       Nullable(String),
    location_type    Nullable(String),
    incident_zip     Nullable(String),
    city             Nullable(String),
    borough          Nullable(String),
    status           Nullable(String),
    resolution_description Nullable(String),
    latitude         Nullable(Float64),
    longitude        Nullable(Float64),
    ingested_at      DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(created_date)
ORDER BY unique_key
SETTINGS index_granularity = 8192;


-- ---------------------------------------------------------------------------
-- CLEAN LAYER
-- ReplacingMergeTree — deduplicates on unique_key during background merges
-- Partitioned by year_month for fast range queries
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nyc311.requests_clean
(
    unique_key          String,
    created_date        DateTime,
    closed_date         Nullable(DateTime),
    agency              String,
    agency_name         String,
    complaint_type      String,
    descriptor          String,
    location_type       String,
    incident_zip        Nullable(String),
    city                String,
    borough             String,
    status              String,
    latitude            Nullable(Float64),
    longitude           Nullable(Float64),
    resolution_hours    Nullable(Float64),
    is_resolved         UInt8,
    complaint_category  String,
    year_month          String,
    cleaned_at          DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(cleaned_at)
PARTITION BY year_month
ORDER BY (unique_key)
SETTINGS index_granularity = 8192;

-- Index for fast agency lookups
ALTER TABLE nyc311.requests_clean
    ADD INDEX idx_agency (agency) TYPE bloom_filter(0.01) GRANULARITY 1;

-- Index for fast borough + complaint_type queries
ALTER TABLE nyc311.requests_clean
    ADD INDEX idx_borough_complaint (borough, complaint_type) TYPE minmax GRANULARITY 1;


-- ---------------------------------------------------------------------------
-- AGGREGATED LAYER — 3 business-purpose summary tables
-- ---------------------------------------------------------------------------

-- AGG 1: Agency performance — avg resolution time, volume, SLA compliance
CREATE TABLE IF NOT EXISTS nyc311.agg_agency_performance
(
    agency              String,
    agency_name         String,
    year_month          String,
    total_requests      UInt64,
    resolved_count      UInt64,
    avg_resolution_hrs  Float64,
    median_resolution_hrs Float64,
    resolution_rate     Float64,   -- 0.0–1.0
    computed_at         DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (agency, year_month);


-- AGG 2: Borough complaint heatmap — top complaint types by borough
CREATE TABLE IF NOT EXISTS nyc311.agg_borough_complaints
(
    borough             String,
    complaint_category  String,
    complaint_type      String,
    year_month          String,
    request_count       UInt64,
    pct_of_borough      Float64,   -- % within that borough
    computed_at         DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (borough, complaint_category, year_month);


-- AGG 3: Monthly volume trend — total requests over time
CREATE TABLE IF NOT EXISTS nyc311.agg_monthly_trend
(
    year_month          String,
    borough             String,
    complaint_category  String,
    request_count       UInt64,
    resolved_count      UInt64,
    avg_resolution_hrs  Nullable(Float64),
    computed_at         DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (year_month, borough, complaint_category);
