-- FOCUS 1.4 datasets for DuckDB, generated from FOCUS_1.4_Data_Model.xlsx (Columns sheet).
-- One table per dataset; table name = Dataset ID. Comments show Feature Level / Allowed Values.

-- ---------------------------------------------------------------- Cost and Usage
CREATE TABLE IF NOT EXISTS CostAndUsage (
    AllocatedMethodDetails JSON,  -- Recommended; Supports data generator-calculated split cost allocation
    AllocatedMethodId VARCHAR,  -- Conditional; Supports data generator-calculated split cost allocation
    AllocatedResourceId VARCHAR,  -- Conditional; Supports data generator-calculated split cost allocation
    AllocatedResourceName VARCHAR,  -- Conditional; Supports data generator-calculated split cost allocation
    AllocatedTags JSON,  -- Conditional; Supports data generator-calculated split cost allocation
    AvailabilityZone VARCHAR,  -- Recommended; Supports deploying resources or services within an availability zone
    BilledCost DECIMAL(38,10),  -- Mandatory
    BillingAccountId VARCHAR,  -- Mandatory
    BillingAccountName VARCHAR,  -- Mandatory
    BillingAccountType VARCHAR,  -- Conditional; Supports more than one possible BillingAccountType value
    BillingCurrency VARCHAR,  -- Mandatory
    BillingPeriodEnd TIMESTAMP,  -- Mandatory
    BillingPeriodStart TIMESTAMP,  -- Mandatory
    CapacityReservationId VARCHAR,  -- Conditional; Supports capacity reservations
    CapacityReservationStatus VARCHAR,  -- Conditional; Supports capacity reservations; values: Used, Unused
    ChargeCategory VARCHAR,  -- Mandatory; values: Usage, Purchase, Tax, Credit, Adjustment
    ChargeClass VARCHAR,  -- Mandatory; values: Correction
    ChargeDescription VARCHAR,  -- Mandatory
    ChargeFrequency VARCHAR,  -- Recommended; values: One-Time, Recurring, Usage-Based
    ChargePeriodEnd TIMESTAMP,  -- Mandatory
    ChargePeriodStart TIMESTAMP,  -- Mandatory
    CommitmentDiscountCategory VARCHAR,  -- Conditional; Supports commitment discounts; values: Spend, Usage
    CommitmentDiscountId VARCHAR,  -- Conditional; Supports commitment discounts
    CommitmentDiscountName VARCHAR,  -- Conditional; Supports commitment discounts
    CommitmentDiscountQuantity DECIMAL(38,10),  -- Conditional; Supports commitment discounts
    CommitmentDiscountStatus VARCHAR,  -- Conditional; Supports commitment discounts; values: Used, Unused
    CommitmentDiscountType VARCHAR,  -- Conditional; Supports commitment discounts
    CommitmentDiscountUnit VARCHAR,  -- Conditional; Supports commitment discounts
    CommitmentProgramEligibilityDetails JSON,  -- Conditional; Supports commitment programs
    ConsumedQuantity DECIMAL(38,10),  -- Conditional; Supports the measurement of usage
    ConsumedUnit VARCHAR,  -- Conditional; Supports the measurement of usage
    ContractApplied JSON,  -- Conditional; Supports contract commitments
    ContractedCost DECIMAL(38,10),  -- Mandatory
    ContractedUnitPrice DECIMAL(38,10),  -- Conditional; Supports negotiated pricing concepts
    EffectiveCost DECIMAL(38,10),  -- Mandatory
    HostProviderName VARCHAR,  -- Mandatory
    InvoiceDetailId VARCHAR,  -- Conditional; Supports payable invoices
    InvoiceId VARCHAR,  -- Conditional; Supports payable invoices
    InvoiceIssuerName VARCHAR,  -- Mandatory
    ListCost DECIMAL(38,10),  -- Mandatory
    ListUnitPrice DECIMAL(38,10),  -- Conditional; Publishes unit prices exclusive of discounts
    PricingCategory VARCHAR,  -- Conditional; Supports more than one pricing category across all SKUs; values: Standard, Dynamic, Committed, Other
    PricingCurrency VARCHAR,  -- Conditional; Supports pricing and billing in different currencies
    PricingCurrencyContractedUnitPrice DECIMAL(38,10),  -- Conditional; Supports prices in virtual currency and publishes unit prices exclusive of discounts
    PricingCurrencyEffectiveCost DECIMAL(38,10),  -- Conditional; Supports prices in virtual currency and publishes unit prices exclusive of discounts
    PricingCurrencyListUnitPrice DECIMAL(38,10),  -- Conditional; Supports prices in virtual currency and publishes unit prices exclusive of discounts
    PricingQuantity DECIMAL(38,10),  -- Mandatory
    PricingUnit VARCHAR,  -- Mandatory
    RegionId VARCHAR,  -- Conditional; Supports deploying resources or services within a region
    RegionName VARCHAR,  -- Conditional; Supports deploying resources or services within a region
    ResourceId VARCHAR,  -- Conditional; Supports billing based on provisioned resources
    ResourceName VARCHAR,  -- Conditional; Supports billing based on provisioned resources
    ResourceType VARCHAR,  -- Conditional; Supports billing based on provisioned resources and supports assigning types to resources
    ServiceCategory VARCHAR,  -- Mandatory
    ServiceName VARCHAR,  -- Mandatory
    ServiceProviderName VARCHAR,  -- Mandatory
    ServiceSubcategory VARCHAR,  -- Recommended; values: See here for full list of ~150 values
    SkuId VARCHAR,  -- Conditional; Supports unit pricing concepts and publishes price lists, publicly or as part of contracting
    SkuMeter VARCHAR,  -- Conditional; Supports unit pricing concepts and publishes price lists, publicly or as part of contracting
    SkuPriceDetails JSON,  -- Conditional; Supports unit pricing concepts and publishes price lists, publicly or as part of contracting
    SkuPriceId VARCHAR,  -- Conditional; Supports unit pricing concepts and publishes price lists, publicly or as part of contracting
    SubAccountId VARCHAR,  -- Conditional; Supports a sub account construct
    SubAccountName VARCHAR,  -- Conditional; Supports a sub account construct
    SubAccountType VARCHAR,  -- Conditional; Supports more than one possible SubAccountType value
    Tags JSON,  -- Conditional; Supports setting user or provider-defined tags
    x_AIProduct VARCHAR,  -- AI extension (custom column): Normalized AI product: m365_copilot, copilot_studio, github_copilot, azure_openai, claude_api, claude_enterprise, claude_code, claude_bedrock, claude_vertex, claude_foundry
    x_ModelName VARCHAR,  -- AI extension (custom column): Model identifier as reported by the source (e.g. claude-sonnet-5-5, gpt-5.4)
    x_ModelVendor VARCHAR,  -- AI extension (custom column): Vendor that built the model (Anthropic, OpenAI, Microsoft). Differs from ServiceProviderName when a model is resold (Bedrock, Foundry, GitHub)
    x_TokenType VARCHAR,  -- AI extension (custom column): Token meter of the row: input, output, cache_read, cache_write, mixed (row carries several token types, e.g. GitHub AI credits), n/a (seats, credits by action)
    x_InputTokens BIGINT,  -- AI extension (custom column): Uncached input tokens on this row
    x_OutputTokens BIGINT,  -- AI extension (custom column): Output tokens on this row
    x_CacheReadTokens BIGINT,  -- AI extension (custom column): Cache read (cache hit) input tokens on this row
    x_CacheWriteTokens BIGINT,  -- AI extension (custom column): Cache creation/write input tokens on this row
    x_ServiceTier VARCHAR,  -- AI extension (custom column): standard, batch, priority, provisioned
    x_Speed VARCHAR,  -- AI extension (custom column): standard or fast (Claude fast mode)
    x_InferenceGeo VARCHAR,  -- AI extension (custom column): Where inference ran / data-residency routing: us, global, eu, not_available
    x_ContextWindow VARCHAR,  -- AI extension (custom column): Context-window pricing band, e.g. 0-200k, 200k-1M
    x_UserPrincipal VARCHAR,  -- AI extension (custom column): User identity (UPN / email / GitHub username) when the source attributes usage to a person
    x_ApiKeyId VARCHAR,  -- AI extension (custom column): API key identifier (Claude API, Azure deployment key)
    x_ApiKeyName VARCHAR,  -- AI extension (custom column): API key display name
    x_WorkspaceId VARCHAR,  -- AI extension (custom column): Claude workspace / Azure AI Foundry project / Copilot Studio environment
    x_WorkspaceName VARCHAR,  -- AI extension (custom column): Display name of the workspace/project/environment
    x_AgentId VARCHAR,  -- AI extension (custom column): Copilot Studio / M365 agent identifier
    x_AgentName VARCHAR,  -- AI extension (custom column): Agent display name
    x_ActionType VARCHAR,  -- AI extension (custom column): Billable action for credit-metered products: classic_answer, generative_answer, agent_action, graph_grounding, agent_flow, ai_prompt
    x_CreditQuantity DECIMAL(38,10),  -- AI extension (custom column): Credits consumed (Copilot Credits, GitHub AI credits) when the product meters in credits
    x_CreditUnit VARCHAR,  -- AI extension (custom column): Copilot Credits, GitHub AI Credits
    x_RequestCount BIGINT,  -- AI extension (custom column): Requests / messages / answers on this row
    x_CostSource VARCHAR,  -- AI extension (custom column): invoiced (from a billing export) or estimated (from an analytics API such as Claude Code Analytics)
    x_SourceSystem VARCHAR  -- AI extension (custom column): Ingestion source: azure_focus_export, aws_focus_export, gcp_focus_export, anthropic_cost_report, anthropic_usage_report, anthropic_enterprise_analytics, claude_code_analytics, github_ai_usage_report, m365_admin_copilot_cost, graph_copilot_usage
);

-- ---------------------------------------------------------------- Billing Period
CREATE TABLE IF NOT EXISTS BillingPeriod (
    BillingPeriodCreated TIMESTAMP,  -- Mandatory
    BillingPeriodEnd TIMESTAMP,  -- Mandatory
    BillingPeriodLastUpdated TIMESTAMP,  -- Mandatory
    BillingPeriodStart TIMESTAMP,  -- Mandatory
    BillingPeriodStatus VARCHAR,  -- Mandatory; values: Open, Closed
    InvoiceIssuerName VARCHAR  -- Mandatory
);

-- ---------------------------------------------------------------- Contract Commitment
CREATE TABLE IF NOT EXISTS ContractCommitment (
    BillingCurrency VARCHAR,  -- Mandatory
    ContractCommitmentApplicability JSON,  -- Mandatory
    ContractCommitmentBenefitCategory VARCHAR,  -- Mandatory; values: Discount, Entitlement, Availability, Other
    ContractCommitmentCategory VARCHAR,  -- Mandatory; values: Spend, Usage
    ContractCommitmentCost DECIMAL(38,10),  -- Mandatory
    ContractCommitmentCreated TIMESTAMP,  -- Mandatory
    ContractCommitmentDescription VARCHAR,  -- Mandatory
    ContractCommitmentDiscountPercentage DECIMAL(38,10),  -- Mandatory; values: 0.0 to 1.0
    ContractCommitmentDurationType VARCHAR,  -- Mandatory; values: [Numeric Value] [Unit]
    ContractCommitmentFulfillmentInterval VARCHAR,  -- Mandatory; values: Hourly, Daily, Weekly, Monthly, Quarterly, Semi-Annual, Annual, Full Period, Transactional, Custom
    ContractCommitmentId VARCHAR,  -- Mandatory
    ContractCommitmentLastUpdated TIMESTAMP,  -- Mandatory
    ContractCommitmentLifecycleStatus VARCHAR,  -- Mandatory; values: Proposed, Pending, Active, Exhausted, Expired, Canceled, Superseded
    ContractCommitmentModel VARCHAR,  -- Mandatory; values: Continuous, Discontinuous
    ContractCommitmentOfferCategory VARCHAR,  -- Mandatory; values: Negotiated, Public
    ContractCommitmentPaymentInterval VARCHAR,  -- Mandatory; values: One-Time, Monthly, Quarterly, Semi-Annual, Annual, Custom
    ContractCommitmentPaymentModel VARCHAR,  -- Mandatory; values: No Upfront, Partial Upfront, All Upfront
    ContractCommitmentPaymentUpfrontPercentage DECIMAL(38,10),  -- Conditional; Offers "Partial Upfront" payment models; values: 0.0 to 1.0
    ContractCommitmentPeriodEnd TIMESTAMP,  -- Mandatory
    ContractCommitmentPeriodStart TIMESTAMP,  -- Mandatory
    ContractCommitmentQuantity DECIMAL(38,10),  -- Mandatory
    ContractCommitmentType VARCHAR,  -- Mandatory
    ContractCommitmentUnit VARCHAR,  -- Mandatory
    ContractId VARCHAR,  -- Mandatory
    ContractPeriodEnd TIMESTAMP,  -- Mandatory
    ContractPeriodStart TIMESTAMP,  -- Mandatory
    InvoiceIssuerName VARCHAR,  -- Mandatory
    PricingCurrency VARCHAR,  -- Conditional; Supports pricing and billing in different currencies
    PricingCurrencyContractCommitmentCost DECIMAL(38,10),  -- Conditional; Supports pricing and billing in different currencies
    ServiceProviderName VARCHAR  -- Mandatory
);

-- ---------------------------------------------------------------- Invoice Detail
CREATE TABLE IF NOT EXISTS InvoiceDetail (
    BilledCost DECIMAL(38,10),  -- Mandatory
    BillingAccountId VARCHAR,  -- Mandatory
    BillingCurrency VARCHAR,  -- Mandatory
    BillingPeriodEnd TIMESTAMP,  -- Mandatory
    BillingPeriodStart TIMESTAMP,  -- Mandatory
    ChargeCategory VARCHAR,  -- Mandatory; values: Usage, Purchase, Tax, Credit, Adjustment
    InvoiceDetailCreated TIMESTAMP,  -- Mandatory
    InvoiceDetailDescription VARCHAR,  -- Mandatory
    InvoiceDetailGrain JSON,  -- Mandatory
    InvoiceDetailId VARCHAR,  -- Mandatory
    InvoiceDetailLastUpdated TIMESTAMP,  -- Mandatory
    InvoiceId VARCHAR,  -- Mandatory
    InvoiceIssueDate TIMESTAMP,  -- Mandatory
    InvoiceIssueStatus VARCHAR,  -- Mandatory; values: Open, Issued, Voided
    InvoiceIssuerName VARCHAR,  -- Mandatory
    PaymentCurrency VARCHAR,  -- Conditional; Supports billing and payment in different currencies
    PaymentCurrencyBilledCost DECIMAL(38,10),  -- Conditional; Supports billing and payment in different currencies
    PaymentCurrencyInvoiceDetailId VARCHAR,  -- Conditional; Supports billing currency and payment currency at different aggregation levels on payable invoices
    PaymentDueDate TIMESTAMP,  -- Mandatory
    PaymentTerms VARCHAR,  -- Mandatory
    PurchaseOrderNumber VARCHAR,  -- Conditional; Supports customer input of purchase order number
    ReferenceInvoiceId VARCHAR  -- Mandatory
);

-- ============================================================================
-- Supporting AI tables (not FOCUS datasets; joined to FOCUS by user/product/date)
-- ============================================================================

-- License / seat roster. Sources: Microsoft Graph licenseDetails, GitHub Copilot seats API,
-- Claude Enterprise members. Used for unused-seat and seat-vs-usage analysis.
CREATE TABLE IF NOT EXISTS ai_seat_assignment (
    SnapshotDate        DATE,
    x_AIProduct         VARCHAR,
    ServiceProviderName VARCHAR,
    UserPrincipal       VARCHAR,
    Department          VARCHAR,
    CostCenter          VARCHAR,
    LicenseSku          VARCHAR,
    AssignedDate        DATE,
    MonthlySeatCost     DECIMAL(38,10),
    BillingCurrency     VARCHAR
);

-- Daily per-user engagement/productivity. Sources: Graph getMicrosoft365CopilotUsageUserDetail (v2:
-- prompts submitted, active usage days), Claude Enterprise Analytics user activity, Claude Code
-- Analytics API (sessions, LOC, commits, PRs, tool accept/reject), GitHub Copilot metrics API.
CREATE TABLE IF NOT EXISTS ai_user_activity (
    ActivityDate     DATE,
    x_AIProduct      VARCHAR,
    UserPrincipal    VARCHAR,
    Surface          VARCHAR,   -- teams, word, chat, vscode, cli, cowork ...
    IsActive         BOOLEAN,
    Prompts          BIGINT,
    Conversations    BIGINT,
    Sessions         BIGINT,
    LinesAdded       BIGINT,
    LinesRemoved     BIGINT,
    Commits          BIGINT,
    PullRequests     BIGINT,
    ToolAccepted     BIGINT,
    ToolRejected     BIGINT,
    -- Per-user token and estimated cost (Claude Code Analytics model_breakdown; GitHub AI usage report)
    ModelName        VARCHAR,
    InputTokens      BIGINT,
    OutputTokens     BIGINT,
    CacheReadTokens  BIGINT,
    CacheWriteTokens BIGINT,
    EstimatedCost    DECIMAL(38,10),
    BillingCurrency  VARCHAR
);

-- Model catalog / governance list with list prices per 1M tokens (maintained by the FinOps team).
CREATE TABLE IF NOT EXISTS ai_model_catalog (
    ModelName              VARCHAR,
    ModelVendor            VARCHAR,
    ModelTier              VARCHAR,   -- frontier, balanced, fast
    Approved               BOOLEAN,
    InputPricePerM         DECIMAL(38,10),
    OutputPricePerM        DECIMAL(38,10),
    CacheReadPricePerM     DECIMAL(38,10),
    CacheWritePricePerM    DECIMAL(38,10),
    PricingCurrency        VARCHAR
);

-- User directory (Entra ID / HRIS) used to map users to departments and cost centers.
CREATE TABLE IF NOT EXISTS ai_user_directory (
    UserPrincipal    VARCHAR,
    DisplayName      VARCHAR,
    Department       VARCHAR,
    CostCenter       VARCHAR,
    IsActiveEmployee BOOLEAN
);

-- Monthly AI budgets by cost center.
CREATE TABLE IF NOT EXISTS ai_budget (
    BudgetPeriodStart TIMESTAMP,
    CostCenter        VARCHAR,
    BudgetAmount      DECIMAL(38,10),
    BillingCurrency   VARCHAR
);
