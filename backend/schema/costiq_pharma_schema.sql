-- MM CostIQ pharma reference registers and allocation output.
-- Registers are maintained by the client's Tax, Transfer Pricing, GxP Quality and Data Privacy teams; the sample ships
-- sample rows (data/pharma.py). allocated_cost is computed by costiq/allocation.py and never edited
-- by hand.

-- Legal entities in the group: the shared-services entity, operating companies, the research entity, affiliates.
CREATE TABLE IF NOT EXISTS ref_legal_entity (
    LegalEntity   VARCHAR,
    Parent        VARCHAR,
    EntityType    VARCHAR,   -- service_company | opco | research | affiliate
    Regulated     BOOLEAN,   -- GxP-regulated entity
    Jurisdictions VARCHAR    -- comma-separated tax jurisdictions
);

-- Which entity owns each cost center's work (cross-charging checks compare this with where cost lands).
CREATE TABLE IF NOT EXISTS ref_cost_center (
    CostCenter   VARCHAR,
    OwningEntity VARCHAR,
    Regulated    BOOLEAN
);

-- Statistical allocation keys by entity and month (transfer-pricing recharge bases).
CREATE TABLE IF NOT EXISTS ref_allocation_driver (
    DriverName   VARCHAR,   -- EnrolledPatients | TrialSites | ActiveStudies | Employees | Workstations
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

-- Jurisdiction factors per entity (share of an entity's cost that belongs to each tax jurisdiction).
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

-- Per jurisdiction: do recurring cloud and SaaS fees count as qualifying R&D expenditure, and are they claimed?
CREATE TABLE IF NOT EXISTS ref_jurisdiction_policy (
    Jurisdiction  VARCHAR,
    CreditStatus  VARCHAR,   -- claimed | eligible | ineligible | not_applicable
    CreditClaimed BOOLEAN,
    Reference     VARCHAR
);

-- Tax filings: the review period an examiner tests, the forecast year for the budget bridge, and the R&D credit rate.
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

-- GxP and patient-data register: cloud workloads, their GxP impact and whether they hold patient data.
CREATE TABLE IF NOT EXISTS ref_gxp_system (
    Application      VARCHAR,
    SystemName       VARCHAR,
    GxpImpact        VARCHAR,   -- High | Medium | Low | None
    HoldsPatientData BOOLEAN,
    KeyOwnership     VARCHAR,   -- customer | provider
    Owner            VARCHAR,
    NextReviewDate   DATE       -- next periodic validation review (NULL when not a validated system)
);

-- Vendors: quality-agreement clauses, renewals and price escalators.
CREATE TABLE IF NOT EXISTS ref_vendor (
    Vendor              VARCHAR,   -- matches ServiceProviderName
    GxpScoped           BOOLEAN,
    QualityClauses      JSON,      -- {"quality_agreement": bool, "audit_rights": bool, "change_notification": bool, "data_integrity": bool}
    ContractEnd         TIMESTAMP,
    RenewalDate         TIMESTAMP,
    EscalatorPct        DECIMAL(38,10),
    AnnualContractValue DECIMAL(38,10)
);

-- Study events that drive surges (database lock, interim analysis).
CREATE TABLE IF NOT EXISTS ref_study_event (
    StudyEventId  VARCHAR,
    Name          VARCHAR,
    StartDate     DATE,
    EndDate       DATE,
    Jurisdictions VARCHAR
);

-- Volume drivers for unit costs.
CREATE TABLE IF NOT EXISTS ref_volume (
    PeriodStart      TIMESTAMP,
    LegalEntity      VARCHAR,
    EnrolledPatients BIGINT,
    TrialSites       BIGINT,
    ActiveStudies    BIGINT
);

-- AI use register, flagging GxP contexts.
CREATE TABLE IF NOT EXISTS ref_ai_use (
    Application   VARCHAR,
    UseCase       VARCHAR,
    GxpContext    BOOLEAN,
    ReviewStatus  VARCHAR,   -- Approved | Pending | Rejected
    ApprovedOn    DATE,
    RiskTier      VARCHAR,   -- internal risk tier set by AI governance: High | Limited | Minimal
    EuAiActClass  VARCHAR,   -- EU AI Act classification once recorded (NULL = not yet classified)
    GmpContext    BOOLEAN    -- used in GMP work (batch release, deviations, quality control)
);

-- Pipeline programs and the applications that serve each one (board view: cost by program and phase).
CREATE TABLE IF NOT EXISTS ref_program (
    ProgramId        VARCHAR,
    Name             VARCHAR,
    Phase            VARCHAR,   -- Discovery | Phase 1 | Phase 2 | Phase 3 | Marketed
    AnnualTechBudget DECIMAL(38,10)
);
CREATE TABLE IF NOT EXISTS ref_program_application (
    Application VARCHAR,
    ProgramId   VARCHAR
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
    CostClass           VARCHAR,   -- capital | opex | qre
    Share               DECIMAL(38,10),
    SourceCost          DECIMAL(38,10),
    AllocatedCost       DECIMAL(38,10),
    Exception           VARCHAR    -- NULL | no_opco_policy | no_gl_account
);

-- Filing evidence: one row per allocated charge line in a filing's review period, with the evidence a tax examiner
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

-- Examiner requests (IDRs) from tax authorities, with the drafted and approved response.
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
