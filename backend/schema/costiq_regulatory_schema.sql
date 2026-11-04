-- MM CostIQ regulatory core: the reference registers the shared engines read, and their computed outputs.
-- Shared by every tenant; each tenant pack adds its own registers (tenants/<tenant>/schema.sql). Registers are
-- maintained by the client's finance, regulatory and compliance teams; the sample ships generated rows.
-- allocated_cost and filing_evidence are computed by costiq/allocation.py and costiq/prudence.py, never edited by hand.

-- Legal entities in the group: the shared-services entity, operating companies, the research entity, affiliates.
CREATE TABLE IF NOT EXISTS ref_legal_entity (
    LegalEntity   VARCHAR,
    Parent        VARCHAR,
    EntityType    VARCHAR,   -- service_company | opco | research | affiliate
    Regulated     BOOLEAN,   -- regulated entity (the tenant decides what regulated means)
    Jurisdictions VARCHAR    -- comma-separated jurisdictions
);

-- Which entity owns each cost center's work (cross-charging checks compare this with where cost lands).
CREATE TABLE IF NOT EXISTS ref_cost_center (
    CostCenter   VARCHAR,
    OwningEntity VARCHAR,
    Regulated    BOOLEAN
);

-- Statistical allocation keys by entity and month (transfer-pricing recharge bases).
CREATE TABLE IF NOT EXISTS ref_allocation_driver (
    DriverName   VARCHAR,   -- allocation key, e.g. Employees | Workstations (tenant-specific keys)
    PeriodStart  TIMESTAMP,
    LegalEntity  VARCHAR,
    DriverValue  DECIMAL(38,10)
);

-- Versioned, effective-dated allocation policies: which charges go to which entity, and by what key.
-- Only Status = 'approved' policies are applied; lower Priority runs first; MatchValue '*' matches everything.
CREATE TABLE IF NOT EXISTS ref_allocation_policy (
    PolicyId      VARCHAR,
    Version       INTEGER,
    Step          VARCHAR,   -- opco
    Name          VARCHAR,
    MatchColumn   VARCHAR,   -- CostCenter | ServiceName | ServiceProviderName | SubAccountId | Application | *
    MatchValue    VARCHAR,
    DriverName    VARCHAR,   -- split by this driver's shares, or
    FixedEntity   VARCHAR,   -- send 100% to this entity
    Priority      INTEGER,
    EffectiveFrom TIMESTAMP,
    EffectiveTo   TIMESTAMP,
    Status        VARCHAR,   -- submitted | approved | returned | superseded
    SubmittedBy   VARCHAR,
    SubmittedAt   TIMESTAMP,
    ApprovedBy    VARCHAR,
    ApprovedAt    TIMESTAMP
);

-- Jurisdiction factors per entity (share of an entity's cost that belongs to each jurisdiction).
CREATE TABLE IF NOT EXISTS ref_jurisdiction_factor (
    LegalEntity  VARCHAR,
    Jurisdiction VARCHAR,
    PeriodStart  TIMESTAMP,
    Factor       DECIMAL(38,10)
);

-- GL account and P&L function map for operating cost (first match by Priority; EntityType NULL = any entity).
-- Illustrative: the client's controllership team owns this map.
CREATE TABLE IF NOT EXISTS ref_gl_account_map (
    Priority     INTEGER,
    MatchColumn  VARCHAR,   -- ChargeCategory | ServiceCategory | ServiceName
    MatchValue   VARCHAR,
    EntityType   VARCHAR,
    PnlFunction  VARCHAR,   -- Research & Development | SG&A | COGS
    GlAccount    VARCHAR,
    GlTitle      VARCHAR
);

-- Per jurisdiction: how recurring cloud and SaaS fees are treated (tenant pack: CreditStatus, CreditClaimed).
CREATE TABLE IF NOT EXISTS ref_jurisdiction_policy (
    Jurisdiction  VARCHAR,
    CreditStatus  VARCHAR,   -- claimed | eligible | ineligible | not_applicable
    CreditClaimed BOOLEAN,
    Reference     VARCHAR
);

-- Filings: the review period that is tested, the forecast year for the budget bridge, and the filing's rate.
CREATE TABLE IF NOT EXISTS ref_tax_filing (
    FilingId          VARCHAR,
    Jurisdiction      VARCHAR,
    LegalEntity       VARCHAR,
    ReviewPeriodStart TIMESTAMP,
    ReviewPeriodEnd   TIMESTAMP,
    ForecastYearStart TIMESTAMP,
    ForecastYearEnd   TIMESTAMP,
    FilingDate        TIMESTAMP,
    Status            VARCHAR,
    CreditRate        DECIMAL(38,10),   -- percent of qualifying expenditure
    Authority         VARCHAR
);

-- Allocation output: one row per charge × entity × jurisdiction share (computed; lineage at every step).
CREATE TABLE IF NOT EXISTS allocated_cost (
    ChargeKey           VARCHAR,
    PeriodStart         TIMESTAMP,
    ChargePeriodStart   TIMESTAMP,
    ServiceProviderName VARCHAR,
    ServiceName         VARCHAR,
    ServiceCategory     VARCHAR,
    ChargeCategory      VARCHAR,
    ChargeFrequency     VARCHAR,
    Application         VARCHAR,
    CostCenter          VARCHAR,
    PoolMethod          VARCHAR,
    LegalEntity         VARCHAR,
    Regulated           BOOLEAN,
    OpcoPolicy          VARCHAR,
    OpcoPolicyVersion   INTEGER,
    DriverName          VARCHAR,
    OpcoShare           DECIMAL(38,10),
    Jurisdiction        VARCHAR,
    JurisdictionFactor  DECIMAL(38,10),
    PnlFunction         VARCHAR,
    GlAccount           VARCHAR,
    GlTitle             VARCHAR,
    CostClass           VARCHAR,   -- from the tenant pack's allocation.classes (pharma: capital | opex | qre)
    Share               DECIMAL(38,10),
    SourceCost          DECIMAL(38,10),
    AllocatedCost       DECIMAL(38,10),
    Exception           VARCHAR    -- NULL | no_opco_policy | no_gl_account
);

-- Filing evidence: one row per allocated charge line in a filing's review period, with the evidence a reviewer
-- would test (computed by costiq/prudence.py; never edited by hand).
CREATE TABLE IF NOT EXISTS filing_evidence (
    FilingId            VARCHAR,
    ChargeKey           VARCHAR,
    PeriodStart         TIMESTAMP,
    ChargeDay           DATE,
    LegalEntity         VARCHAR,
    Jurisdiction        VARCHAR,
    ServiceProviderName VARCHAR,
    ServiceName         VARCHAR,
    ResourceName        VARCHAR,
    Application         VARCHAR,
    CostCenter          VARCHAR,
    GlAccount           VARCHAR,
    CostClass           VARCHAR,
    AllocatedCost       DECIMAL(38,10),
    Reconciled          BOOLEAN,
    PolicyApproved      BOOLEAN,
    GlMapped            BOOLEAN,
    BenefitShown        BOOLEAN,
    Remediated          BOOLEAN,
    SavingsActioned     BOOLEAN,
    RiskReason          VARCHAR,   -- no_benefit | unreconciled | no_approved_policy | unmapped_gl | unactioned_savings
    RiskAmount          DECIMAL(38,10),
    EvidenceScore       DECIMAL(38,10)   -- 0..1 weighted share of evidence present
);

-- Data requests on a filing (examiner or regulator requests), with the drafted and approved response.
CREATE TABLE IF NOT EXISTS data_request (
    RequestId       VARCHAR,
    FilingId        VARCHAR,
    Requester       VARCHAR,
    RequestText     VARCHAR,
    MatchedUseCases VARCHAR,
    DraftJson       VARCHAR,
    Status          VARCHAR,   -- draft | approved | returned
    PreparedBy      VARCHAR,
    PreparedAt      TIMESTAMP,
    ApprovedBy      VARCHAR,
    ApprovedAt      TIMESTAMP
);
