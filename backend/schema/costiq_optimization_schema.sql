-- MM CostIQ monitoring and optimization tables. FOCUS billing data says what was spent; these tables say how well it
-- was used (provider recommendations, utilization, LLM application telemetry, provisioned AI capacity) and track what
-- was done about it (optimization lifecycle, alerts). Loaded by adapters in costiq/adapters/; the sample ships synthetic
-- Meridian Energy rows (data/optimization.py).

-- Provider-native recommendations: AWS Cost Optimization Hub data export, Google Recommender BigQuery export,
-- Azure Advisor API.
CREATE TABLE IF NOT EXISTS optimization_recommendation (
    RecommendationId        VARCHAR,
    Source                  VARCHAR,   -- aws_cost_optimization_hub | gcp_recommender | azure_advisor
    ServiceProviderName     VARCHAR,
    SubAccountId            VARCHAR,
    ResourceId              VARCHAR,
    ResourceName            VARCHAR,
    ActionType              VARCHAR,   -- rightsize | stop_idle | delete_unattached | schedule_offhours | upgrade_generation | storage_tier | purchase_commitment
    CurrentResourceSummary  VARCHAR,
    RecommendedResourceSummary VARCHAR,
    CurrentMonthlyCost      DECIMAL(38,10),
    EstimatedMonthlySavings DECIMAL(38,10),
    Effort                  VARCHAR,   -- low | medium | high
    RestartNeeded           BOOLEAN,
    RefreshedAt             TIMESTAMP
);

-- Daily utilization from monitoring exports (CloudWatch, Azure Monitor, Cloud Monitoring), by resource.
CREATE TABLE IF NOT EXISTS resource_utilization_daily (
    UsageDate    DATE,
    ResourceId   VARCHAR,
    ResourceName VARCHAR,
    CpuP95       DECIMAL(38,10),   -- percent
    MemP95       DECIMAL(38,10),   -- percent
    GpuUtilAvg   DECIMAL(38,10),   -- percent, GPU resources only
    Requests     BIGINT,
    RunningHours DECIMAL(38,10)
);

-- LLM application telemetry from the gateway or the app (one row per application, model and day).
CREATE TABLE IF NOT EXISTS ai_app_telemetry_daily (
    UsageDate          DATE,
    Application        VARCHAR,
    ModelName          VARCHAR,
    Requests           BIGINT,
    FailedRequests     BIGINT,
    RetriedRequests    BIGINT,
    AvgInputTokens     DECIMAL(38,10),
    AvgOutputTokens    DECIMAL(38,10),
    MaxTokensSetting   BIGINT,
    TruncatedResponses BIGINT,
    AcceptedOutcomes   BIGINT,   -- answers/tasks the user or workflow accepted
    LatencyP95Ms       BIGINT
);

-- Provisioned AI capacity (Azure OpenAI PTU, Bedrock provisioned throughput, Vertex provisioned throughput).
CREATE TABLE IF NOT EXISTS ai_provisioned_capacity (
    UsageDate      DATE,
    Deployment     VARCHAR,
    Application    VARCHAR,
    ModelName      VARCHAR,
    Units          BIGINT,
    DailyCost      DECIMAL(38,10),
    UtilizationPct DECIMAL(38,10)
);

-- Optimization lifecycle: identified -> approved -> implemented -> verified (or rejected). Verified savings compare the
-- scope's run-rate after implementation with its baseline before it.
CREATE TABLE IF NOT EXISTS optimization_action (
    OptimizationId          VARCHAR,
    Origin                  VARCHAR,   -- use case id or provider recommendation id
    Lever                   VARCHAR,   -- rightsizing | idle | scheduling | storage | egress | commitment | ai_cache | ai_batch | model_routing | seats | ptu | gpu_idle | context
    Domain                  VARCHAR,   -- cloud | ai
    Title                   VARCHAR,
    ScopeType               VARCHAR,   -- resource | application | seat_pool | account
    ScopeKey                VARCHAR,
    CostCenter              VARCHAR,
    Owner                   VARCHAR,
    Confidence              VARCHAR,   -- provider_recommended | costiq_estimated
    Status                  VARCHAR,   -- identified | approved | implemented | verified | rejected
    EstimatedMonthlySavings DECIMAL(38,10),
    BaselineMonthlyCost     DECIMAL(38,10),
    VerifiedMonthlySavings  DECIMAL(38,10),
    VerificationBasis       VARCHAR,   -- measured (post-change run-rate) | provisional (too little post-change data)
    IdentifiedOn            DATE,
    ApprovedOn              DATE,
    ImplementedOn           DATE,
    VerifiedOn              DATE,
    ImplementedBy           VARCHAR
);

-- Alert rules and the events they raise.
CREATE TABLE IF NOT EXISTS cost_alert_rule (
    RuleId       VARCHAR,
    Name         VARCHAR,
    Kind         VARCHAR,   -- budget_threshold | forecast_overrun | anomaly | token_budget | commitment_expiry
    Target       VARCHAR,   -- cost center, product or '*'
    ThresholdPct DECIMAL(38,10),
    Channel      VARCHAR,   -- in_app | webhook
    Enabled      BOOLEAN
);

CREATE TABLE IF NOT EXISTS cost_alert_event (
    EventId      VARCHAR,
    RuleId       VARCHAR,
    FiredAt      TIMESTAMP,
    Severity     VARCHAR,   -- high | medium | low
    Subject      VARCHAR,
    Message      VARCHAR,
    Value        DECIMAL(38,10),
    Acknowledged BOOLEAN,
    AcknowledgedBy VARCHAR,
    AcknowledgedAt TIMESTAMP
);
