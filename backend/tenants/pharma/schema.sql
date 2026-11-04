-- MM CostIQ pharma tenant registers (tenants/pharma/pack.yaml `schema`), on top of schema/costiq_regulatory_schema.sql.
-- Maintained by the client's GxP Quality, Data Privacy and R&D teams; the sample ships generated rows (tenants/pharma/sample.py).

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
