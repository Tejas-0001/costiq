-- MM CostIQ application tables (DuckDB dialect; translated per database by `costiq migrate`).

-- Total technology budget (cloud + AI) by cost center and month.
CREATE TABLE IF NOT EXISTS cost_budget (
    BudgetPeriodStart TIMESTAMP,
    CostCenter        VARCHAR,
    BudgetAmount      DECIMAL(38,10),
    BillingCurrency   VARCHAR
);

-- One row per source file / API pull and billing period. Written by the ingestion adapters.
CREATE TABLE IF NOT EXISTS ingest_manifest (
    ManifestId          VARCHAR,
    SourceSystem        VARCHAR,
    BillingPeriodStart  TIMESTAMP,
    Maturity            VARCHAR,     -- implemented | representative
    RecordCount         BIGINT,
    Digest              VARCHAR,     -- sha256 of the normalized rows
    ReceivedAt          TIMESTAMP,
    Checks              JSON,        -- [{"check": "...", "status": "pass|warn|fail", "detail": "..."}]
    Status              VARCHAR      -- promoted | quarantined
);

-- Approved, versioned, effective-dated allocation rules (Finance Controller owns them).
CREATE TABLE IF NOT EXISTS allocation_rule (
    RuleId        VARCHAR,
    Version       INTEGER,
    Name          VARCHAR,
    Priority      INTEGER,      -- lower runs first
    MatchColumn   VARCHAR,      -- SubAccountId | ServiceName | ChargeCategory | x_WorkspaceName | ServiceProviderName
    MatchValue    VARCHAR,
    CostCenter    VARCHAR,
    EffectiveFrom TIMESTAMP,
    EffectiveTo   TIMESTAMP,
    ApprovedBy    VARCHAR,
    ApprovedAt    TIMESTAMP
);

-- Append-only, hash-chained decision ledger.
CREATE TABLE IF NOT EXISTS decision_ledger (
    Seq        BIGINT,
    EntryAt    TIMESTAMP,
    Actor      VARCHAR,
    Persona    VARCHAR,
    Action     VARCHAR,       -- approve | return | submit | note | seed
    Target     VARCHAR,       -- e.g. finding:UO-05, forecast:2026-10:Engineering
    Detail     VARCHAR,
    PrevHash   VARCHAR,
    Hash       VARCHAR
);
