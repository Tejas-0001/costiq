-- MM CostIQ utility tenant registers (tenants/utility/pack.yaml `schema`), on top of schema/costiq_regulatory_schema.sql.
-- Maintained by the client's regulatory accounting, service-company accounting, rates, Cloud CoE and NERC CIP teams; the
-- tenant ships generated rows (tenants/utility/sample.py). Account numbers are text (VARCHAR) everywhere.

-- ------------------------------------------------------------------------------------------- core registers, utility columns
-- Added here (nullable) so the other tenants' tables keep their shape.
ALTER TABLE ref_cost_center ADD COLUMN IF NOT EXISTS BusinessUnit VARCHAR;     -- tag value: TRANSMISSION | DISTRIBUTION | CORPORATE_IT | SHARED_SERVICES | NON_UTILITY
ALTER TABLE ref_cost_center ADD COLUMN IF NOT EXISTS FercFunction VARCHAR;     -- tag value: T | D | CUST | AG | GENERAL | NONUTIL
ALTER TABLE ref_cost_center ADD COLUMN IF NOT EXISTS DefaultFactor VARCHAR;    -- allocation factor when not tagged to one company
ALTER TABLE ref_legal_entity ADD COLUMN IF NOT EXISTS EntityCode VARCHAR;      -- opco tag value (SVC, NOR, ...)
ALTER TABLE ref_legal_entity ADD COLUMN IF NOT EXISTS AccountsPart VARCHAR;    -- 18 CFR Part 101 | Part 367 | none
ALTER TABLE ref_legal_entity ADD COLUMN IF NOT EXISTS Grid VARCHAR;
ALTER TABLE ref_gl_account_map ADD COLUMN IF NOT EXISTS RuleId VARCHAR;        -- ref_ferc_rule
ALTER TABLE ref_gl_account_map ADD COLUMN IF NOT EXISTS Citation VARCHAR;
ALTER TABLE ref_gl_account_map ADD COLUMN IF NOT EXISTS PolicyFlag VARCHAR;    -- Regulation | POLICY (the controller signs POLICY rows off)
ALTER TABLE ref_gl_account_map ADD COLUMN IF NOT EXISTS FunctionCode VARCHAR;  -- T | D | CUST | AG | GENERAL | NONUTIL; NULL = any function
ALTER TABLE ai_seat_assignment ADD COLUMN IF NOT EXISTS SkuId VARCHAR;
ALTER TABLE ai_seat_assignment ADD COLUMN IF NOT EXISTS AssignedByGroupId VARCHAR;   -- NULL = assigned directly
ALTER TABLE ai_seat_assignment ADD COLUMN IF NOT EXISTS AssignmentState VARCHAR;     -- Active | Suspended

-- The FERC ledger (pack.yaml allocation.ledger_columns, written by costiq/allocation.py): the charge's identifiers and the
-- rule that placed each allocated line. Appended so the shared columns keep their positions.
ALTER TABLE allocated_cost ADD COLUMN IF NOT EXISTS SubAccountId VARCHAR;      -- the charge's account or subscription
ALTER TABLE allocated_cost ADD COLUMN IF NOT EXISTS InvoiceId VARCHAR;
ALTER TABLE allocated_cost ADD COLUMN IF NOT EXISTS InvoiceDetailId VARCHAR;   -- invoice line (invoice-to-ledger drill)
ALTER TABLE allocated_cost ADD COLUMN IF NOT EXISTS ChargeDescription VARCHAR;
ALTER TABLE allocated_cost ADD COLUMN IF NOT EXISTS FunctionCode VARCHAR;      -- T | D | CUST | AG | GENERAL | NONUTIL; NULL = held at the service company
ALTER TABLE allocated_cost ADD COLUMN IF NOT EXISTS RuleId VARCHAR;            -- ref_ferc_rule (R1..R17) that placed the line
ALTER TABLE allocated_cost ADD COLUMN IF NOT EXISTS Citation VARCHAR;          -- ref_ferc_rule.Citation
ALTER TABLE allocated_cost ADD COLUMN IF NOT EXISTS PolicyFlag VARCHAR;        -- Regulation | POLICY (ref_ferc_rule.Basis)
ALTER TABLE allocated_cost ADD COLUMN IF NOT EXISTS WorkOrderId VARCHAR;       -- valid work order on 107 lines; the tagged id on held lines
ALTER TABLE allocated_cost ADD COLUMN IF NOT EXISTS BillingClass VARCHAR;      -- 457.1 direct | 457.2 by factor | NULL retained at the service company

-- ------------------------------------------------------------------------------------------- FERC accounting
-- Uniform System of Accounts reference (18 CFR Part 101 for public utilities, Part 367 for the centralized service company).
-- Order No. 898 software accounts apply from 2025-01-01; 303/404 software placement before then.
CREATE TABLE IF NOT EXISTS ref_ferc_account (
    FercAccount      VARCHAR,
    Title            VARCHAR,
    Part             VARCHAR,   -- 101 | 367 | 101/367
    AccountType      VARCHAR,   -- Plant | Contra-plant | Balance sheet | Expense | Below the line | Billing | O&M | O&M (labor only) | Revenue
    FercFunction     VARCHAR,   -- T | D | CUST | AG | GENERAL | SERVICE_COMPANY | ANY
    Rules            VARCHAR,   -- ref_ferc_rule ids that place cost here
    EffectiveFrom    DATE,
    EffectiveTo      DATE,
    Citation         VARCHAR,
    PolicyFlag       VARCHAR,   -- Regulation | POLICY
    ClientSubaccount VARCHAR,   -- the client's five-digit subaccount (optional)
    LaborOnly        BOOLEAN,   -- true: compensation only (920); a non-labor line here is exception labor_account_on_non_labor
    Note             VARCHAR
);

-- Mapping rules R1-R17 in order of evaluation, with the citation behind each and the controller's sign-off status.
CREATE TABLE IF NOT EXISTS ref_ferc_rule (
    RuleId             VARCHAR,
    EvaluationStep     INTEGER,   -- 1 tax, credits, corrections; 2 commitments; 3 capital; 4 maintenance; 5 functional O&M; 6 other
    ChargePattern      VARCHAR,
    Treatment          VARCHAR,   -- debit / credit
    TransmissionAccount VARCHAR,
    DistributionAccount VARCHAR,
    GeneralAccount     VARCHAR,   -- A&G, General plant or multi-function
    ServiceCompanyAccount VARCHAR,
    Condition          VARCHAR,
    Citation           VARCHAR,
    Basis              VARCHAR,   -- Regulation | POLICY
    PolicyElement      VARCHAR,   -- the part of a Regulation rule the company chooses (NULL when none)
    Status             VARCHAR,   -- approved | awaiting sign-off
    SignedOffBy        VARCHAR,
    SignedOffAt        TIMESTAMP
);

-- Exception codes: what the exception queue, tag enforcement and the readiness score report.
CREATE TABLE IF NOT EXISTS ref_exception_code (
    ExceptionCode VARCHAR,
    Meaning       VARCHAR,
    Owner         VARCHAR,
    Resolution    VARCHAR
);

-- Mapping-rule change requests, including proposals the account guards blocked.
CREATE TABLE IF NOT EXISTS ref_rule_change (
    ChangeId        VARCHAR,
    RuleId          VARCHAR,
    MatchColumn     VARCHAR,
    MatchValue      VARCHAR,
    ProposedAccount VARCHAR,
    Description     VARCHAR,
    ProposedBy      VARCHAR,
    ProposedAt      TIMESTAMP,
    Status          VARCHAR,   -- blocked | submitted | approved
    BlockedBy       VARCHAR,   -- the guard that stopped it
    ExceptionCode   VARCHAR
);

-- Work orders (capital projects): construction work in progress (107) until ready for use, then functional software plant.
CREATE TABLE IF NOT EXISTS ref_work_order (
    WorkOrderId             VARCHAR,
    Module                  VARCHAR,
    Description             VARCHAR,
    Application             VARCHAR,   -- application tag of the build and run environments
    CostCenter              VARCHAR,
    BenefitingEntities      VARCHAR,   -- comma-separated entity codes (benefiting location)
    BenefitingBasis         VARCHAR,   -- allocation driver across the benefiting entities
    FercFunction            VARCHAR,   -- T | D | GENERAL
    TargetPlantAccount      VARCHAR,   -- 351.2 | 363.2 | 397.2
    CapitalizationStartDate DATE,      -- probable-to-complete threshold (ASU 2025-06); NULL = not set
    ReadyForUseDate         DATE,      -- NULL = not yet in service
    ModuleShare             DECIMAL(38,10),   -- share of the work order's cost in this module
    HostingTermMonths       INTEGER,
    DepreciationRate        DECIMAL(38,10),   -- annual, percent (existing rate until the next depreciation case)
    AfudcEligible           BOOLEAN,
    Status                  VARCHAR,   -- open | in_service | partly_in_service
    CwipBeforeWindow        DECIMAL(38,10),   -- 107 balance accumulated before 2026-07-01
    Owner                   VARCHAR
);

-- Allocation factors (Form 60 Schedule XXI style): the driver behind each policy, how often it refreshes and how it bills.
CREATE TABLE IF NOT EXISTS ref_allocation_factor (
    FactorCode   VARCHAR,
    DriverName   VARCHAR,   -- ref_allocation_driver.DriverName
    Refresh      VARCHAR,
    UsedFor      VARCHAR,
    BillingClass VARCHAR,   -- 457.1 direct | 457.2 indirect
    Status       VARCHAR,   -- approved | awaiting approval
    Note         VARCHAR
);

-- ------------------------------------------------------------------------------------------- tags and accounts
CREATE TABLE IF NOT EXISTS ref_required_tag (
    TagKey        VARCHAR,
    AllowedValues VARCHAR,
    Requirement   VARCHAR,   -- required | conditional | optional
    RequiredOn    VARCHAR,
    ClientField   VARCHAR,
    FallbackOrder VARCHAR
);

-- Tag keys dropped at ingest (they can describe protected systems) and how keys are normalized.
CREATE TABLE IF NOT EXISTS ref_tag_denylist (
    KeyPattern VARCHAR,
    Action     VARCHAR,
    Reason     VARCHAR
);

-- Known invalid tag values and what happens to them (normalized, or sent to the exception queue).
CREATE TABLE IF NOT EXISTS ref_tag_value_map (
    TagKey          VARCHAR,
    RawValue        VARCHAR,
    NormalizedValue VARCHAR,   -- NULL = cannot be normalized; goes to the queue
    Action          VARCHAR,   -- normalize | queue
    Reason          VARCHAR
);

-- Cloud accounts and subscriptions: account-level defaults (the tag fallback), owner, function and sensitivity flag.
CREATE TABLE IF NOT EXISTS ref_cloud_account (
    SubAccountId        VARCHAR,
    SubAccountName      VARCHAR,
    ServiceProviderName VARCHAR,
    BillingAccountId    VARCHAR,
    Workload            VARCHAR,
    DefaultCostCenter   VARCHAR,   -- NULL = none (untagged spend goes to suspense)
    OwningEntity        VARCHAR,   -- legal entity that owns the account's work
    FercFunction        VARCHAR,   -- NULL = not set (multi-function default applies)
    Environment         VARCHAR,
    SensitivityFlag     VARCHAR,   -- ceii | cip_related_workload | potential_bcsi_context | NULL
    NamesMasked         BOOLEAN,   -- resource names pseudonymized at ingest pending the client's CIP review
    Owner               VARCHAR,
    Application         VARCHAR    -- primary application tag of the account
);

-- ------------------------------------------------------------------------------------------- invoices
-- Labeled reconciling items: why an invoice and its cost rows differ, and when the difference clears.
CREATE TABLE IF NOT EXISTS ref_invoice_reconciling_item (
    InvoiceId          VARCHAR,
    InvoiceIssuerName  VARCHAR,
    BillingPeriodStart TIMESTAMP,
    Item               VARCHAR,
    Amount             DECIMAL(38,10),
    Category           VARCHAR,   -- tax_not_in_cost_data | late_charge | overbilling | correction_next_period | rounding
    Explained          BOOLEAN,
    Treatment          VARCHAR,
    ExpectedResolution VARCHAR,
    InvoiceDetailId    VARCHAR    -- the invoice line the item explains (InvoiceDetail.InvoiceDetailId)
);
ALTER TABLE ref_invoice_reconciling_item ADD COLUMN IF NOT EXISTS InvoiceDetailId VARCHAR;

-- Prepayments (165) and their monthly amortization to the account of the usage that consumes them (R9).
CREATE TABLE IF NOT EXISTS ref_prepayment (
    PrepaymentId        VARCHAR,
    Vendor              VARCHAR,
    InvoiceId           VARCHAR,
    IssueDate           DATE,
    Description         VARCHAR,
    Amount              DECIMAL(38,10),
    Account             VARCHAR,
    AmortizationStart   DATE,
    Months              INTEGER,
    MonthlyAmortization DECIMAL(38,10),
    ExpenseRule         VARCHAR,
    CommitmentDiscountId VARCHAR
);

-- ------------------------------------------------------------------------------------------- rates and regulatory
-- Rate cases (ref_tax_filing holds the docket, its test period and rate year): authorized returns per docket.
CREATE TABLE IF NOT EXISTS ref_rate_case (
    FilingId      VARCHAR,
    TestYearType  VARCHAR,   -- historical | forecast | formula rate
    AuthorizedRoe DECIMAL(38,10),   -- percent
    EquityRatio   DECIMAL(38,10),   -- fraction of the capital structure
    CostOfDebt    DECIMAL(38,10),   -- percent
    PreTaxRor     DECIMAL(38,10),   -- percent, pre-tax weighted return (carrying return on a regulatory asset)
    RateYears     VARCHAR
);

-- Vendors: CIP-013 supply-chain clauses, renewals and price escalators.
CREATE TABLE IF NOT EXISTS ref_vendor (
    Vendor              VARCHAR,   -- matches ServiceProviderName
    CipScoped           BOOLEAN,
    Cip013Clauses       JSON,      -- {"vulnerability_disclosure", "incident_notification", "remote_access_controls", "software_integrity"}
    ContractEnd         TIMESTAMP,
    RenewalDate         TIMESTAMP,
    EscalatorPct        DECIMAL(38,10),
    AnnualContractValue DECIMAL(38,10)
);

-- Cloud workloads the client's CIP program is asked to review. CostIQ flags and masks; the client's CIP-011 R1.1 method
-- decides whether anything is BES Cyber System Information (HoldsBcsi NULL = not yet classified).
CREATE TABLE IF NOT EXISTS ref_cip_review (
    Application        VARCHAR,
    SubAccountId       VARCHAR,
    SystemName         VARCHAR,
    SensitivityFlag    VARCHAR,   -- ceii | cip_related_workload | potential_bcsi_context
    HoldsBcsi          BOOLEAN,
    KeyOwnership       VARCHAR,   -- customer | provider
    ReviewStatus       VARCHAR,   -- pending | classified
    ReviewRequestedOn  DATE,
    Owner              VARCHAR
);

-- Major storm events (cost recovery is filed per event).
CREATE TABLE IF NOT EXISTS ref_storm_event (
    StormEventId  VARCHAR,
    Name          VARCHAR,
    StartDate     DATE,
    EndDate       DATE,
    Jurisdictions VARCHAR
);

-- Volume drivers for unit costs (sample counts, not benchmarks).
CREATE TABLE IF NOT EXISTS ref_volume (
    PeriodStart          TIMESTAMP,
    LegalEntity          VARCHAR,
    RetailCustomers      BIGINT,
    CustomerContacts     BIGINT,
    WorkOrdersCompleted  BIGINT,
    FieldCrewReports     BIGINT
);

-- AI use register, flagging grid-operations contexts.
CREATE TABLE IF NOT EXISTS ref_ai_use (
    Application    VARCHAR,
    UseCase        VARCHAR,
    GridOpsContext BOOLEAN,
    ReviewStatus   VARCHAR,   -- Approved | Pending | Rejected
    ApprovedOn     DATE,
    Owner          VARCHAR
);
