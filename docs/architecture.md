# NYC 311 Big Data Pipeline — Architecture

## System Diagram

```mermaid
flowchart TB
    subgraph SOURCE["📡 Data Source"]
        API["NYC Open Data API\nSocrata REST\n35M+ rows"]
    end

    subgraph INGESTION["⚙️ Ingestion Layer (Python)"]
        direction TB
        FETCH["fetcher.py\nPaginated batch fetch\nRetry logic · Checkpointing"]
        VAL1["RawRequest\nPydantic validation"]
        BAD1["bad_records_raw.jsonl\n(rejected records logged)"]
        FETCH --> VAL1
        VAL1 -->|invalid| BAD1
    end

    subgraph DOCKER["🐳 Docker Compose Cluster"]
        direction TB
        ZK["ZooKeeper\nCluster coordination"]

        subgraph CH["ClickHouse Cluster"]
            direction LR
            CH1["clickhouse-01\nShard 1\n:8123 :9000"]
            CH2["clickhouse-02\nShard 2\n:8124 :9001"]
            ZK -. coordinates .-> CH1
            ZK -. coordinates .-> CH2
        end
    end

    subgraph RAW["🗄️ Raw Layer"]
        RAW_T["nyc311.requests_raw\nMergeTree\nPARTITION BY toYYYYMM(created_date)\nAll original fields · ~35M rows"]
    end

    subgraph CLEAN_LAYER["🧹 Clean Layer (Python + Pandas)"]
        direction TB
        CLEANER["cleaner.py\n1. Dedup on unique_key\n2. Parse datetimes\n3. Normalize text/borough/zip\n4. Validate NYC lat-lng bounds\n5. Filter negative resolution times\n6. Derive: resolution_hours, category, year_month"]
        VAL2["CleanRequest\nPydantic validation"]
        BAD2["bad_records_clean.jsonl\n(schema violations logged)"]
        CLEANER --> VAL2
        VAL2 -->|invalid| BAD2
    end

    subgraph CLEAN_T["🗄️ Clean Table"]
        CLEAN_DB["nyc311.requests_clean\nReplacingMergeTree\nPARTITION BY year_month\nBloom filter: agency\nMinMax index: borough+complaint_type"]
    end

    subgraph AGG_LAYER["📊 Aggregation Layer (Python + Pandas)"]
        direction TB
        AGG1["compute_agency_performance()\nAvg/median resolution hrs\nResolution rate by agency+month"]
        AGG2["compute_borough_complaints()\nTop complaint types per borough\n% share within borough"]
        AGG3["compute_monthly_trend()\nVolume + resolution trend\nby month × borough × category"]
    end

    subgraph AGG_TABLES["🗄️ Aggregated Tables"]
        direction TB
        T1["agg_agency_performance\nReplacingMergeTree"]
        T2["agg_borough_complaints\nReplacingMergeTree"]
        T3["agg_monthly_trend\nReplacingMergeTree"]
    end

    subgraph VIZ["📈 Visualization Layer"]
        DASH["Streamlit Dashboard\ndashboard/app.py\n\n📈 Chart 1: Monthly volume trend\n🗺️  Chart 2: Borough heatmap\n⏱️  Chart 3: Agency performance scatter"]
    end

    API -->|"HTTP · JSON batches\n50k rows/request"| FETCH
    VAL1 -->|valid rows| RAW_T
    RAW_T --> CLEANER
    VAL2 -->|valid rows| CLEAN_DB
    CLEAN_DB --> AGG1 & AGG2 & AGG3
    AGG1 --> T1
    AGG2 --> T2
    AGG3 --> T3
    T1 & T2 & T3 -->|"Direct ClickHouse\nqueries"| DASH

    style SOURCE fill:#1c2333,stroke:#388bfd,color:#e6edf3
    style INGESTION fill:#1c2333,stroke:#3fb950,color:#e6edf3
    style DOCKER fill:#161b22,stroke:#f78166,color:#e6edf3
    style CH fill:#1c2333,stroke:#f78166,color:#e6edf3
    style RAW fill:#1c2333,stroke:#d29922,color:#e6edf3
    style CLEAN_LAYER fill:#1c2333,stroke:#3fb950,color:#e6edf3
    style CLEAN_T fill:#1c2333,stroke:#d29922,color:#e6edf3
    style AGG_LAYER fill:#1c2333,stroke:#a371f7,color:#e6edf3
    style AGG_TABLES fill:#1c2333,stroke:#d29922,color:#e6edf3
    style VIZ fill:#1c2333,stroke:#388bfd,color:#e6edf3
```

## Layer Descriptions

### Source
NYC Open Data — 311 Service Requests from 2010 to present (~35M rows). Accessed via Socrata REST API in paginated JSON batches with optional app token authentication.

### Ingestion
Python fetches batches of 50,000 rows. Each record is validated via `RawRequest` (Pydantic). Bad records are written to `logs/bad_records_raw.jsonl` and never silently dropped. Checkpointing saves progress so restarts resume from the last successful batch.

### Raw Layer
`requests_raw` uses `MergeTree` partitioned by `toYYYYMM(created_date)`. This partitioning makes time-range scans dramatically faster and is the first performance decision in the pipeline.

### Clean Layer
10 cleaning steps applied in pandas (vectorized for performance), followed by `CleanRequest` Pydantic validation. Derives 3 new columns: `resolution_hours`, `complaint_category`, `year_month`. Uses `ReplacingMergeTree` so duplicate ingestion runs are idempotent.

### Aggregation Layer
3 purpose-built summary tables, each tied to a specific business question and visualization. Written back to ClickHouse so the dashboard queries the database directly — not flat files.

### Visualization
Streamlit app with live ClickHouse connections, borough/category/date filters, drill-down by agency, and 3 charts answering distinct business questions.

## Performance Decisions

| Decision | Rationale |
|---|---|
| `PARTITION BY year_month` | Most 311 queries filter by date range — partition pruning eliminates unread parts |
| Bloom filter on `agency` | Exact-match lookups on agency are common; bloom filter reduces granule scans by ~80% |
| MinMax index on `(borough, complaint_type)` | Supports the heatmap query which filters both dimensions |
| `ReplacingMergeTree` for clean/agg tables | Idempotent re-runs — duplicate keys are deduplicated in background merges |
| 2-node ClickHouse cluster | Demonstrates distribution; production would use `Distributed` engine for cross-shard queries |
