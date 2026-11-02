-- MM CostIQ utility reference registers and allocation output.
-- Registers are maintained by the client's Regulatory Accounting, Affiliate (service-company) Accounting, Rates and
-- NERC CIP teams; the sample ships synthetic Meridian Energy rows (data/utility.py). ut_allocated_cost is computed
-- by costiq/allocation.py and never edited by hand.

-- Legal entities in the holding company: the service company, regulated operating companies, affiliates.
CREATE TABLE IF NOT EXISTS ut_legal_entity (
    LegalEntity  VARCHAR,
    Parent       VARCHAR,
    EntityType   VARCHAR,   -- service_company | opco | transmission | affiliate
    Regulated    BOOLEAN,
    States       VARCHAR    -- comma-separated jurisdictions served
);

-- Which entity owns each cost center's work (cross-subsidy checks compare this with where cost lands).
CREATE TABLE IF NOT EXISTS ut_cost_center (
    CostCenter   VARCHAR,
    OwningEntity VARCHAR,
    Regulated    BOOLEAN
);

-- Statistical allocation drivers by entity and month (FERC Form 60 Schedule XXI style bases).
CREATE TABLE IF NOT EXISTS ut_allocation_driver (
    DriverName   VARCHAR,   -- Customers | Employees | Workstations | PoleMiles | AmiMeters
    PeriodStart  TIMESTAMP,
    LegalEntity  VARCHAR,
    DriverValue  DECIMAL(38,10)
);

-- Versioned, effective-dated allocation policies: which charges go to operating companies, and by what basis.
-- Only Status = 'approved' policies are applied; lower Priority runs first; MatchValue '*' matches everything.
CREATE TABLE IF NOT EXISTS ut_allocation_policy (
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

-- Jurisdictional allocation factors per entity (share of an entity's cost that belongs to each state or FERC).
CREATE TABLE IF NOT EXISTS ut_jurisdiction_factor (
    LegalEntity  VARCHAR,
    Jurisdiction VARCHAR,
    PeriodStart  TIMESTAMP,
    Factor       DECIMAL(38,10)
);

-- FERC Uniform System of Accounts mapping for O&M (first match by Priority; EntityType NULL = any entity).
-- Illustrative: the client's regulatory accounting team owns this map.
CREATE TABLE IF NOT EXISTS ut_ferc_account_map (
    Priority     INTEGER,
    MatchColumn  VARCHAR,   -- ChargeCategory | ServiceCategory | ServiceName
    MatchValue   VARCHAR,
    EntityType   VARCHAR,
    FercFunction VARCHAR,
    FercAccount  VARCHAR,
    FercTitle    VARCHAR
);

-- State (and FERC) treatment of cloud subscription fees: may they be deferred to a regulatory asset?
CREATE TABLE IF NOT EXISTS ut_jurisdiction_policy (
    Jurisdiction       VARCHAR,
    DeferralStatus     VARCHAR,   -- allowed | untested | rejected | not_applicable
    DeferralAllowed    BOOLEAN,
    AmortizationMonths INTEGER,   -- used when a contract has no term
    OrderReference     VARCHAR
);

-- Rate cases: base period (actuals that support the filing) and forecast test year, with authorized returns.
CREATE TABLE IF NOT EXISTS ut_rate_case (
    Docket          VARCHAR,
    Jurisdiction    VARCHAR,
    LegalEntity     VARCHAR,
    BasePeriodStart TIMESTAMP,
    BasePeriodEnd   TIMESTAMP,
    TestYearStart   TIMESTAMP,
    TestYearEnd     TIMESTAMP,
    FilingDate      TIMESTAMP,
    Status          VARCHAR,
    AuthorizedRoe   DECIMAL(38,10),   -- percent
    EquityRatio     DECIMAL(38,10),   -- fraction of capital structure
    CostOfDebt      DECIMAL(38,10),   -- percent
    PreTaxRor       DECIMAL(38,10)    -- percent, pre-tax weighted return used for regulatory-asset carrying return
);

-- NERC CIP register: cloud workloads and whether they hold BES Cyber System Information (BCSI).
CREATE TABLE IF NOT EXISTS ut_cip_system (
    Application  VARCHAR,
    SystemName   VARCHAR,
    ImpactRating VARCHAR,   -- High | Medium | Low | None
    HoldsBcsi    BOOLEAN,
    KeyOwnership VARCHAR,   -- customer | provider
    Owner        VARCHAR
);

-- Vendors: CIP-013 supply-chain clauses, renewals and price escalators.
CREATE TABLE IF NOT EXISTS ut_vendor (
    Vendor              VARCHAR,   -- matches ServiceProviderName
    CipScoped           BOOLEAN,
    Cip013Clauses       JSON,      -- {"vulnerability_disclosure": bool, "incident_notification": bool, "remote_access_controls": bool, "software_integrity": bool}
    ContractEnd         TIMESTAMP,
    RenewalDate         TIMESTAMP,
    EscalatorPct        DECIMAL(38,10),
    AnnualContractValue DECIMAL(38,10)
);

-- Major storm events (cost recovery is filed per event).
CREATE TABLE IF NOT EXISTS ut_storm_event (
    StormEventId  VARCHAR,
    Name          VARCHAR,
    StartDate     DATE,
    EndDate       DATE,
    Jurisdictions VARCHAR
);

-- Volume drivers for unit costs.
CREATE TABLE IF NOT EXISTS ut_volume (
    PeriodStart TIMESTAMP,
    LegalEntity VARCHAR,
    Customers   BIGINT,
    AmiMeters   BIGINT,
    GridStudies BIGINT
);

-- AI use register for grid-operations contexts.
CREATE TABLE IF NOT EXISTS ut_ai_use (
    Application   VARCHAR,
    UseCase       VARCHAR,
    GridOpsContext BOOLEAN,
    ReviewStatus  VARCHAR,   -- Approved | Pending | Rejected
    ApprovedOn    DATE
);

-- Allocation output: one row per charge × entity × jurisdiction share (computed; lineage at every step).
CREATE TABLE IF NOT EXISTS ut_allocated_cost (
    ChargeKey          VARCHAR,
    PeriodStart        TIMESTAMP,
    ChargePeriodStart  TIMESTAMP,
    ServiceProviderName VARCHAR,
    ServiceName        VARCHAR,
    ServiceCategory    VARCHAR,
    ChargeCategory     VARCHAR,
    ChargeFrequency    VARCHAR,
    Application        VARCHAR,
    CostCenter         VARCHAR,
    PoolMethod         VARCHAR,
    LegalEntity        VARCHAR,
    Regulated          BOOLEAN,
    OpcoPolicy         VARCHAR,
    OpcoPolicyVersion  INTEGER,
    DriverName         VARCHAR,
    OpcoShare          DECIMAL(38,10),
    Jurisdiction       VARCHAR,
    JurisdictionFactor DECIMAL(38,10),
    FercFunction       VARCHAR,
    FercAccount        VARCHAR,
    FercTitle          VARCHAR,
    CostClass          VARCHAR,   -- capital | om | deferral
    Share              DECIMAL(38,10),
    SourceCost         DECIMAL(38,10),
    AllocatedCost      DECIMAL(38,10),
    Exception          VARCHAR    -- NULL | no_opco_policy | no_ferc_account
);

-- Prudence evidence: one row per allocated charge line in a docket's base period, with the evidence a commission
-- would test (computed by costiq/prudence.py; never edited by hand).
CREATE TABLE IF NOT EXISTS prudence_evidence (
    Docket              VARCHAR,
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
    FercAccount         VARCHAR,
    CostClass           VARCHAR,
    AllocatedCost       DECIMAL(38,10),
    Reconciled          BOOLEAN,
    PolicyApproved      BOOLEAN,
    FercMapped          BOOLEAN,
    UsedAndUseful       BOOLEAN,
    Remediated          BOOLEAN,
    SavingsActioned     BOOLEAN,
    RiskReason          VARCHAR,   -- not_used_and_useful | unreconciled | no_approved_policy | unmapped_ferc | unactioned_savings
    RiskAmount          DECIMAL(38,10),
    EvidenceScore       DECIMAL(38,10)   -- 0..1 weighted share of evidence present
);

-- Rate-case data requests from commission staff and intervenors, with the drafted and approved response.
CREATE TABLE IF NOT EXISTS data_request (
    RequestId       VARCHAR,
    Docket          VARCHAR,
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
