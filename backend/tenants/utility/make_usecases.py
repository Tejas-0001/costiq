"""Write tenants/utility/usecases/UT-01..25.yaml: the utility use cases (FERC accounting, service-company billing, rate
cases, tag enforcement, the capital path, invoice tie-out, NERC CIP review requests, Copilot seats and AI tokens).

  python tenants/utility/make_usecases.py      (from mvp/backend)

Kept as code so the entries share one structure (tools/pharma_usecases.render); the YAML files are what the platform loads.
"""
from __future__ import annotations
import os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from pharma_usecases import render  # noqa: E402  (shared YAML layout)

OUT = os.path.join(ROOT, "tenants", "utility", "usecases")
SOURCE = "MM CostIQ Utility extension"
DATASETS = ["Cost and Usage", "Utility reference tables"]
PERIOD = [("period_start", "date", "2026-07-01", "Start of the period (inclusive)"),
          ("period_end", "date", "2026-11-01", "End of the period (exclusive)")]
SEPTEMBER = [("period_start", "date", "2026-09-01", "Start of the period (inclusive)"),
             ("period_end", "date", "2026-10-01", "End of the period (exclusive)")]
DOCKET = [("filing", "string", "SA-26-0418-EL", "Rate case docket")]
REG_ACCT = "Regulatory Accounting"

USE_CASES = [
    dict(
        id="UT-01", module="tax", category="Utility · FERC accounting",
        title="Technology spend by FERC account and cost class",
        question="Does every allocated technology dollar land in a FERC account, and how much is construction work in progress, O&M or a regulatory asset?",
        personas=["finance_controller", "regulatory_analyst", "auditor", "cfo"],
        catalog_persona="Controller, Regulatory Accounting",
        description="The allocated ledger by receiving company, FERC account and cost class. Build work under an open work order "
                    "books to 107; recurring cloud and SaaS fees go to functional O&M through the account map (R4-R8, R17); tax "
                    "and credits follow the line they belong to (R12, R13). Dollars with no account are flagged.",
        keywords=["ferc account", "uniform system of accounts", "order 898", "cost class", "cwip", "107", "o&m", "unmapped"],
        sql="""
SELECT
  LegalEntity,
  COALESCE(GlAccount, '(unmapped)') AS FercAccount,
  COALESCE(MAX(GlTitle), 'No FERC account mapped') AS FercTitle,
  MAX(PnlFunction) AS Function,
  FunctionCode,
  CostClass,
  RuleId,
  MAX(PolicyFlag) AS PolicyFlag,
  COUNT(DISTINCT ChargeKey) AS ChargeLines,
  ROUND(SUM(AllocatedCost), 2) AS AllocatedCost,
  CASE WHEN GlAccount IS NULL THEN 'Unmapped' ELSE 'Mapped' END AS Status
FROM allocated_cost
WHERE PeriodStart >= :period_start AND PeriodStart < :period_end
GROUP BY LegalEntity, GlAccount, FunctionCode, CostClass, RuleId
ORDER BY CASE WHEN GlAccount IS NULL THEN 0 ELSE 1 END, AllocatedCost DESC""",
        params=PERIOD,
        compliance=dict(area="FERC accounting", reference="18 CFR Part 101 and Part 367 · Order No. 898",
                        unit="account lines", flag="Status = 'Unmapped'", amount="AllocatedCost"),
        notes="The account map is owned by regulatory accounting; [POLICY] rows await the controller's sign-off (UT-22). "
              "Account 920 is A&G salaries and never receives a cloud or software line (UT-23)."),
    dict(
        id="UT-02", module="tax", category="Utility · Service company billing",
        title="Service company billing by receiving company: 457.1 direct and 457.2 by factor",
        question="What did the service company bill each receiving company, direct or by allocation factor, and to which account?",
        personas=["affiliate_accounting", "finance_controller", "regulatory_analyst", "auditor"],
        catalog_persona="Service Company Accounting, Controller",
        description="The monthly bill in the shape of Form 60 Schedule XXI: amounts charged directly to one company or by "
                    "work-order benefiting location (457.1) and amounts spread by an approved factor (457.2), with the receiving "
                    "company's own Part 101 account on every line (18 CFR 367.27). Amounts with no approved policy stay at the "
                    "service company in the exception queue.",
        keywords=["service company", "457.1", "457.2", "form 60", "schedule xxi", "allocation factor", "affiliate billing", "unbundle"],
        sql="""
WITH a AS (
  SELECT x.*,
         CASE WHEN x.OpcoPolicy IS NOT NULL THEN 'Approved policy'
              WHEN x.CostClass = 'prepayment' THEN 'Retained: prepayment (R9)'
              ELSE 'No approved policy' END AS PolicyStatus
  FROM allocated_cost x
  WHERE x.PeriodStart >= :period_start AND x.PeriodStart < :period_end
),
t AS (
  SELECT LegalEntity,
         SUM(CASE WHEN BillingClass = '457.1' THEN AllocatedCost ELSE 0 END) AS Direct,
         SUM(CASE WHEN BillingClass IS NOT NULL THEN AllocatedCost ELSE 0 END) AS Billed
  FROM a GROUP BY LegalEntity
)
SELECT
  a.LegalEntity AS RecipientEntity,
  MAX(e.EntityCode) AS EntityCode,
  CASE WHEN a.BillingClass = '457.1' THEN '457.1 Direct'
       WHEN a.BillingClass = '457.2' THEN '457.2 Indirect'
       ELSE 'Held at the service company' END AS BillingClass,
  COALESCE(f.FactorCode, CASE WHEN a.BillingClass = '457.1' AND a.DriverName IS NULL THEN 'F-DIR' END) AS Factor,
  COALESCE(a.DriverName, CASE WHEN a.BillingClass IS NULL THEN '(none)' ELSE '100% to one company' END) AS BasisOfAllocation,
  COALESCE(a.OpcoPolicy, '(none)') AS Policy,
  COALESCE(a.GlAccount, '(unmapped)') AS ReceivingAccount,
  COUNT(DISTINCT a.ChargeKey) AS ChargeLines,
  ROUND(SUM(a.AllocatedCost), 2) AS AmountRecharged,
  ROUND(100 * MAX(t.Direct) / NULLIF(MAX(t.Billed), 0), 1) AS EntityDirectSharePct,
  a.PolicyStatus AS Status
FROM a
LEFT JOIN t ON t.LegalEntity = a.LegalEntity
LEFT JOIN ref_legal_entity e ON e.LegalEntity = a.LegalEntity
LEFT JOIN ref_allocation_factor f ON f.DriverName = a.DriverName
GROUP BY a.LegalEntity, a.BillingClass, a.OpcoPolicy, a.DriverName, f.FactorCode, a.GlAccount, a.PolicyStatus
ORDER BY RecipientEntity, AmountRecharged DESC""",
        params=SEPTEMBER,
        compliance=dict(area="Service company billing", reference="18 CFR 367.25-367.28 · Form No. 60 Schedule XXI",
                        unit="billing lines", flag="Status = 'No approved policy'", amount="AmountRecharged"),
        notes="BillingClass is stamped on every ledger line: an opco tag naming one company (approved policy AP-DIRECT, F-DIR), "
              "a fixed-entity policy or a work order's benefiting companies bill 457.1; an approved factor bills 457.2. "
              "EntityDirectSharePct is the company's 457.1 share of what it was billed. 457.3 (compensation for use of capital) "
              "is billed from the service company's capital base and is not part of cloud cost."),
    dict(
        id="UT-03", module="tax", category="Utility · Affiliate rules",
        title="Cross-subsidy: non-utility affiliate work carried by regulated companies",
        question="Are regulated companies paying for work that belongs to the non-utility affiliate?",
        personas=["affiliate_accounting", "regulatory_analyst", "auditor", "cfo"],
        catalog_persona="Service Company Accounting, Internal Audit",
        description="Two tests. Cost centers owned by the non-utility affiliate whose cost landed on a regulated company, and "
                    "accounts the affiliate owns (ref_cloud_account) whose spend was tagged to a regulated cost center and "
                    "spread to regulated companies.",
        keywords=["cross subsidy", "cross-subsidization", "affiliate", "non-utility", "affiliate rules", "competitive"],
        sql="""
WITH own AS (
  SELECT a.Application, MIN(a.OwningEntity) AS OwningEntity, MIN(a.SubAccountName) AS Account
  FROM ref_cloud_account a JOIN ref_legal_entity e ON e.LegalEntity = a.OwningEntity
  WHERE NOT e.Regulated AND a.Application IS NOT NULL
  GROUP BY a.Application
)
SELECT
  'Cost center owned by the affiliate' AS Test,
  a.CostCenter, a.Application, c.OwningEntity, a.LegalEntity AS ChargedToEntity, a.OpcoPolicy,
  COUNT(DISTINCT a.ChargeKey) AS ChargeLines,
  ROUND(SUM(a.AllocatedCost), 2) AS CrossSubsidy
FROM allocated_cost a
JOIN ref_cost_center c ON c.CostCenter = a.CostCenter
WHERE NOT c.Regulated AND a.Regulated
  AND a.PeriodStart >= :period_start AND a.PeriodStart < :period_end
GROUP BY a.CostCenter, a.Application, c.OwningEntity, a.LegalEntity, a.OpcoPolicy
UNION ALL
SELECT
  'Affiliate account tagged to a regulated cost center' AS Test,
  a.CostCenter, a.Application, o.OwningEntity, a.LegalEntity AS ChargedToEntity, a.OpcoPolicy,
  COUNT(DISTINCT a.ChargeKey) AS ChargeLines,
  ROUND(SUM(a.AllocatedCost), 2) AS CrossSubsidy
FROM allocated_cost a
JOIN own o ON o.Application = a.Application
JOIN ref_cost_center c ON c.CostCenter = a.CostCenter
WHERE a.Regulated AND c.Regulated
  AND a.PeriodStart >= :period_start AND a.PeriodStart < :period_end
GROUP BY a.CostCenter, a.Application, o.OwningEntity, a.LegalEntity, a.OpcoPolicy
ORDER BY CrossSubsidy DESC""",
        params=SEPTEMBER,
        compliance=dict(area="Affiliate rules", reference="18 CFR 367.25 (at cost) · state affiliate rules",
                        unit="allocations", flag="CrossSubsidy > 0", amount="CrossSubsidy"),
        notes="Fix by tagging the affiliate's resources to its own cost center (or setting the account default) and restating "
              "the months through an approved rule."),
    dict(
        id="UT-04", module="tax", category="Utility · Regulatory accounting",
        title="Regulatory-asset deferral by jurisdiction",
        question="Where do commission orders allow cloud costs to be deferred, and where has no deferral been requested?",
        personas=["regulatory_analyst", "cfo", "finance_controller"],
        catalog_persona="Rates & Regulatory, CFO",
        description="Recurring cloud and SaaS fees and non-capitalizable implementation costs by jurisdiction, against each "
                    "jurisdiction's deferral status: deferred to 182.3 under an order, not yet requested, or denied.",
        keywords=["regulatory asset", "182.3", "deferral", "commission order", "cloud computing arrangement"],
        sql="""
SELECT
  a.Jurisdiction,
  p.CreditStatus AS DeferralStatus,
  p.Reference AS OrderReference,
  ROUND(SUM(a.AllocatedCost), 2) AS EligibleCost,
  ROUND(SUM(CASE WHEN a.CostClass = 'regulatory_asset' THEN a.AllocatedCost ELSE 0 END), 2) AS Deferred182_3,
  ROUND(SUM(CASE WHEN a.CostClass = 'om' THEN a.AllocatedCost ELSE 0 END), 2) AS ExpensedNoReturn,
  CASE WHEN p.CreditStatus = 'claimed' THEN 'Deferred under order'
       WHEN p.CreditStatus = 'eligible' THEN 'No order on file: request deferral'
       WHEN p.CreditStatus = 'ineligible' THEN 'Denied: re-file with a customer-savings case'
       ELSE 'Not applicable' END AS Opportunity
FROM allocated_cost a
JOIN ref_jurisdiction_policy p ON p.Jurisdiction = a.Jurisdiction
WHERE a.CostClass IN ('om', 'regulatory_asset')
  AND ((a.ChargeCategory = 'Purchase' AND a.ChargeFrequency = 'Recurring') OR a.CostClass = 'regulatory_asset')
  AND a.PeriodStart >= :period_start AND a.PeriodStart < :period_end
GROUP BY a.Jurisdiction, p.CreditStatus, p.Reference
ORDER BY ExpensedNoReturn DESC""",
        params=PERIOD,
        compliance=dict(area="Rates", reference="Acct 182.3 · ASC 350-40 · Idaho PUC Order No. 34707 (precedent)",
                        unit="jurisdictions", flag="Opportunity = 'No order on file: request deferral'", amount="ExpensedNoReturn"),
        notes="182.3 needs an order on file; a requested deferral without one stays in O&M (exception "
              "regulatory_asset_without_order)."),
    dict(
        id="UT-05", module="tax", category="Utility · Rate cases",
        title="Rate-case test-year bridge for cloud, SaaS and AI O&M by vendor",
        question="How does each vendor's cloud, SaaS and AI O&M move from the test period to the rate year, and does it tie to invoices?",
        personas=["regulatory_analyst", "cfo", "finance_controller", "opco_finance"],
        catalog_persona="Rates & Regulatory",
        description="Test-period actuals for the docket's company and jurisdiction, annualized, then carried to the rate year "
                    "with known-and-measurable renewal escalators. Each vendor's test period is tied to its invoices; an invoice "
                    "difference is flagged.",
        keywords=["rate case", "test year", "test period", "rate year", "exhibit", "bridge", "known and measurable", "docket"],
        sql="""
WITH rc AS (SELECT * FROM ref_tax_filing WHERE FilingId = :filing),
base AS (
  SELECT a.ServiceProviderName AS Vendor, SUM(a.AllocatedCost) AS BaseActual
  FROM allocated_cost a JOIN rc ON a.LegalEntity = rc.LegalEntity AND a.Jurisdiction = rc.Jurisdiction
  WHERE a.CostClass = 'om' AND a.PeriodStart >= rc.ReviewPeriodStart AND a.PeriodStart < rc.ReviewPeriodEnd
  GROUP BY a.ServiceProviderName
),
mo AS (
  SELECT COUNT(DISTINCT a.PeriodStart) AS n FROM allocated_cost a JOIN rc
    ON a.PeriodStart >= rc.ReviewPeriodStart AND a.PeriodStart < rc.ReviewPeriodEnd
),
inv AS (
  SELECT i.InvoiceIssuerName AS Vendor, SUM(i.BilledCost) AS Invoiced
  FROM InvoiceDetail i JOIN rc ON i.BillingPeriodStart >= rc.ReviewPeriodStart AND i.BillingPeriodStart < rc.ReviewPeriodEnd
  GROUP BY i.InvoiceIssuerName
),
chg AS (
  SELECT c.InvoiceIssuerName AS Vendor, SUM(c.BilledCost) AS Charged
  FROM CostAndUsage c JOIN rc ON c.BillingPeriodStart >= rc.ReviewPeriodStart AND c.BillingPeriodStart < rc.ReviewPeriodEnd
  GROUP BY c.InvoiceIssuerName
),
fx AS (
  SELECT b.Vendor, b.BaseActual, b.BaseActual * 12 / mo.n AS Annualized, COALESCE(v.EscalatorPct, 0) AS EscalatorPct,
         v.RenewalDate,
         CASE WHEN v.RenewalDate >= rc.ForecastYearStart AND v.RenewalDate < rc.ForecastYearEnd
              THEN (12 - ((EXTRACT(YEAR FROM v.RenewalDate) - EXTRACT(YEAR FROM rc.ForecastYearStart)) * 12
                          + EXTRACT(MONTH FROM v.RenewalDate) - EXTRACT(MONTH FROM rc.ForecastYearStart))) / 12.0
              WHEN v.RenewalDate < rc.ForecastYearStart THEN 1.0 ELSE 0.0 END AS EscalatedShare,
         i.Invoiced, c.Charged
  FROM base b CROSS JOIN mo CROSS JOIN rc
  LEFT JOIN ref_vendor v ON v.Vendor = b.Vendor
  LEFT JOIN inv i ON i.Vendor = b.Vendor
  LEFT JOIN chg c ON c.Vendor = b.Vendor
)
SELECT
  Vendor,
  ROUND(BaseActual, 2) AS ReviewPeriodActual,
  ROUND(Annualized, 2) AS ReviewAnnualized,
  EscalatorPct,
  CAST(RenewalDate AS DATE) AS RenewalDate,
  ROUND(Annualized * (1 + EscalatorPct / 100 * EscalatedShare), 2) AS ForecastYear,
  ROUND(Annualized * EscalatorPct / 100 * EscalatedShare, 2) AS ContractEscalation,
  CASE WHEN EscalatedShare > 0 AND EscalatorPct > 0 THEN 'Contract escalator at renewal'
       ELSE 'Test period annualized' END AS Reason,
  CASE WHEN Invoiced IS NULL THEN 'Billed via another issuer'
       WHEN ABS(Invoiced - COALESCE(Charged, 0)) <= 1 THEN 'Tied to invoices'
       ELSE 'Invoice exception' END AS InvoiceTieOut,
  ROUND(COALESCE(Invoiced, 0) - COALESCE(Charged, 0), 2) AS InvoiceDifference
FROM fx
ORDER BY ReviewAnnualized DESC""",
        params=DOCKET,
        compliance=dict(area="Rates", reference="Test period and known-and-measurable adjustments",
                        unit="vendors", flag="InvoiceTieOut = 'Invoice exception'", amount="ReviewPeriodActual"),
        notes="Columns follow the shared exhibit layout: review period = test period, forecast year = rate year. Invoice tie-out "
              "compares the vendor's invoices with all of its charges in the test period (every company); labeled reconciling "
              "items (UT-18) explain by-design differences such as tax that is not in the Azure cost data."),
    dict(
        id="UT-06", module="tax", category="Utility · Rate cases",
        title="Renewals and price escalators landing in the rate year",
        question="Which contract renewals and escalators fall inside the rate year and belong in the filing as known-and-measurable changes?",
        personas=["regulatory_analyst", "finops_analyst", "cfo"],
        catalog_persona="Rates & Regulatory, Cloud CoE",
        description="Vendors whose renewal date falls inside the docket's rate year, with the escalator and its annual effect.",
        keywords=["renewal", "escalator", "price increase", "known and measurable", "rate year", "contract", "true-down"],
        sql="""
SELECT
  rc.FilingId AS Docket,
  v.Vendor,
  CAST(v.RenewalDate AS DATE) AS RenewalDate,
  v.EscalatorPct,
  ROUND(v.AnnualContractValue, 2) AS AnnualContractValue,
  ROUND(v.AnnualContractValue * v.EscalatorPct / 100, 2) AS AnnualEscalation,
  CASE WHEN v.RenewalDate >= rc.ForecastYearStart AND v.RenewalDate < rc.ForecastYearEnd THEN 'In rate year'
       WHEN v.RenewalDate < rc.ForecastYearStart THEN 'Before rate year'
       ELSE 'After rate year' END AS Timing
FROM ref_vendor v
CROSS JOIN ref_tax_filing rc
WHERE rc.FilingId = :filing AND v.EscalatorPct > 0
ORDER BY v.RenewalDate""",
        params=DOCKET,
        compliance=dict(area="Rates", reference="Known-and-measurable rate-year adjustments",
                        unit="contracts", flag="Timing = 'In rate year'", amount="AnnualEscalation"),
        notes="Annual contract values come from vendor management; escalators apply from the renewal date."),
    dict(
        id="UT-07", module="compliance", category="Utility · NERC CIP & data",
        title="Cloud workloads awaiting the client's CIP review",
        question="Which cloud accounts carry names or data the CIP program has been asked to classify, and what do they cost?",
        personas=["cip_compliance", "auditor", "tech_exec", "finance_controller"],
        catalog_persona="NERC CIP Compliance",
        description="Accounts flagged ceii, cip_related_workload or potential_bcsi_context, with the review status the client's "
                    "CIP program records. Resource names on accounts awaiting review are pseudonymized at ingest. CostIQ flags "
                    "and masks; the client's CIP-011 R1.1 method decides what is BES Cyber System Information.",
        keywords=["cip", "bcsi", "ceii", "cip-011", "masking", "pseudonymized", "review pending"],
        sql="""
WITH spend AS (
  SELECT SubAccountId, COUNT(DISTINCT ResourceName) AS Resources, SUM(EffectiveCost) AS EffectiveCost
  FROM CostAndUsage
  WHERE ChargePeriodStart >= :period_start AND ChargePeriodStart < :period_end
  GROUP BY SubAccountId
)
SELECT
  r.SystemName,
  r.Application,
  ca.SubAccountName AS Account,
  r.SensitivityFlag,
  CASE WHEN ca.NamesMasked THEN 'Masked' ELSE 'Shown' END AS ResourceNames,
  CASE WHEN r.HoldsBcsi IS NULL THEN 'Not yet classified' WHEN r.HoldsBcsi THEN 'Yes' ELSE 'No' END AS HoldsBcsi,
  r.KeyOwnership,
  r.ReviewStatus,
  CAST(r.ReviewRequestedOn AS DATE) AS ReviewRequestedOn,
  r.Owner,
  COALESCE(s.Resources, 0) AS Resources,
  ROUND(COALESCE(s.EffectiveCost, 0), 2) AS EffectiveCost,
  CASE WHEN r.ReviewStatus = 'pending' AND ca.NamesMasked THEN 'Names masked; awaiting CIP review'
       WHEN r.ReviewStatus = 'pending' THEN 'Awaiting CIP review'
       ELSE 'Classified by the CIP program' END AS Status
FROM ref_cip_review r
LEFT JOIN ref_cloud_account ca ON ca.SubAccountId = r.SubAccountId
LEFT JOIN spend s ON s.SubAccountId = r.SubAccountId
ORDER BY CASE WHEN r.ReviewStatus = 'pending' THEN 0 ELSE 1 END, EffectiveCost DESC""",
        params=SEPTEMBER,
        compliance=dict(area="NERC CIP & data", reference="CIP-011 R1.1 (client classification) · CIP-004 access program",
                        unit="workloads", flag="ReviewStatus = 'pending'", amount="EffectiveCost"),
        notes="The review register is maintained by the client's CIP program. Exception code cip_review_pending."),
    dict(
        id="UT-08", module="compliance", category="Utility · NERC CIP & data",
        title="CIP-scoped vendors missing CIP-013 supply-chain clauses",
        question="Which vendors in CIP scope lack supply-chain risk clauses in their contracts?",
        personas=["cip_compliance", "auditor", "finops_analyst"],
        catalog_persona="NERC CIP Compliance",
        description="Contract clause flags from vendor management: vulnerability disclosure, incident notification, remote-access "
                    "controls and software integrity.",
        keywords=["cip-013", "supply chain", "vendor risk", "contract clauses", "nerc"],
        sql="""
WITH c AS (
  SELECT Vendor, CipScoped, AnnualContractValue,
         CASE WHEN json_extract_string(Cip013Clauses, '$.vulnerability_disclosure') = 'true' THEN '' ELSE 'vulnerability disclosure; ' END AS m1,
         CASE WHEN json_extract_string(Cip013Clauses, '$.incident_notification') = 'true' THEN '' ELSE 'incident notification; ' END AS m2,
         CASE WHEN json_extract_string(Cip013Clauses, '$.remote_access_controls') = 'true' THEN '' ELSE 'remote-access controls; ' END AS m3,
         CASE WHEN json_extract_string(Cip013Clauses, '$.software_integrity') = 'true' THEN '' ELSE 'software integrity; ' END AS m4
  FROM ref_vendor
)
SELECT
  Vendor,
  CipScoped,
  CONCAT(m1, m2, m3, m4) AS MissingClauses,
  ROUND(AnnualContractValue, 2) AS AnnualContractValue,
  CASE WHEN CipScoped AND CONCAT(m1, m2, m3, m4) <> '' THEN 'Missing CIP-013 clauses'
       WHEN CipScoped THEN 'Clauses in place'
       ELSE 'Not CIP-scoped' END AS Status
FROM c
ORDER BY CASE WHEN CipScoped AND CONCAT(m1, m2, m3, m4) <> '' THEN 0 ELSE 1 END, AnnualContractValue DESC""",
        params=[],
        compliance=dict(area="NERC CIP & data", reference="CIP-013-2 R1.2", unit="vendors",
                        flag="Status = 'Missing CIP-013 clauses'", amount="AnnualContractValue"),
        notes="Clause flags come from vendor management (ref_vendor.Cip013Clauses)."),
    dict(
        id="UT-09", module="tax", category="Utility · Rate cases",
        title="Incremental technology cost by storm event",
        question="How much extra cloud cost did each storm cause, by application, for the storm-cost recovery filing?",
        personas=["regulatory_analyst", "distribution_finance", "finops_analyst"],
        catalog_persona="Rates & Regulatory, Distribution Finance",
        description="Cost tagged to a storm event against the application's daily average over the 14 days before the event.",
        keywords=["storm", "storm cost", "recovery", "outage", "event", "surge"],
        sql="""
WITH ev AS (SELECT * FROM ref_storm_event),
storm AS (
  SELECT json_extract_string(Tags, '$.storm_event') AS StormEventId, json_extract_string(Tags, '$.application') AS Application,
         COUNT(DISTINCT CAST(ChargePeriodStart AS DATE)) AS Days, SUM(EffectiveCost) AS Cost
  FROM CostAndUsage
  WHERE json_extract_string(Tags, '$.storm_event') IS NOT NULL
  GROUP BY json_extract_string(Tags, '$.storm_event'), json_extract_string(Tags, '$.application')
),
pre AS (
  SELECT ev.StormEventId, json_extract_string(c.Tags, '$.application') AS Application, SUM(c.EffectiveCost) / 14 AS DailyBaseline
  FROM CostAndUsage c CROSS JOIN ev
  WHERE CAST(c.ChargePeriodStart AS DATE) >= ev.StartDate - 14 AND CAST(c.ChargePeriodStart AS DATE) < ev.StartDate
    AND c.ChargeCategory = 'Usage'
  GROUP BY ev.StormEventId, json_extract_string(c.Tags, '$.application')
)
SELECT
  s.StormEventId, ev.Name, ev.Jurisdictions, s.Application, s.Days,
  ROUND(s.Cost, 2) AS StormPeriodCost,
  ROUND(COALESCE(p.DailyBaseline, 0) * s.Days, 2) AS BaselineCost,
  ROUND(s.Cost - COALESCE(p.DailyBaseline, 0) * s.Days, 2) AS IncrementalCost
FROM storm s
JOIN ev ON ev.StormEventId = s.StormEventId
LEFT JOIN pre p ON p.StormEventId = s.StormEventId AND p.Application = s.Application
ORDER BY IncrementalCost DESC""",
        params=[],
        compliance=None,
        notes="Flagged rows are recoverable costs to include in the event's filing, not errors."),
    dict(
        id="UT-10", module="tax", category="Utility · Unit costs",
        title="Technology unit costs: per retail customer, per customer contact, per work order",
        question="What does technology O&M cost per customer, and what does AI cost per customer contact?",
        personas=["regulatory_analyst", "cfo", "distribution_finance", "opco_finance"],
        catalog_persona="Rates & Regulatory, CFO",
        description="Allocated O&M per operating company and month against its volumes (counts from the volume register).",
        keywords=["unit cost", "per customer", "per contact", "per work order", "benchmark", "o&m per customer"],
        sql="""
WITH cost AS (
  SELECT PeriodStart, LegalEntity, SUM(AllocatedCost) AS OAndM,
         SUM(CASE WHEN Application = 'contact-center-summarization' THEN AllocatedCost ELSE 0 END) AS ContactAi
  FROM allocated_cost
  WHERE CostClass = 'om'
  GROUP BY PeriodStart, LegalEntity
)
SELECT
  CAST(c.PeriodStart AS DATE) AS Month,
  c.LegalEntity,
  ROUND(c.OAndM, 2) AS TechnologyOAndM,
  v.RetailCustomers,
  ROUND(c.OAndM * 12 / NULLIF(v.RetailCustomers, 0), 2) AS AnnualizedPerCustomer,
  v.CustomerContacts,
  ROUND(c.ContactAi / NULLIF(v.CustomerContacts, 0), 4) AS AiCostPerContact,
  v.WorkOrdersCompleted,
  ROUND(c.OAndM / NULLIF(v.WorkOrdersCompleted, 0), 2) AS OAndMPerWorkOrder,
  v.FieldCrewReports
FROM cost c
JOIN ref_volume v ON v.PeriodStart = c.PeriodStart AND v.LegalEntity = c.LegalEntity
ORDER BY Month, c.LegalEntity""",
        params=[],
        compliance=None,
        notes="October is a partial month (data through 22 Oct). Counts are the volume register's, not benchmarks."),
    dict(
        id="UT-11", module="compliance", category="Utility · NERC CIP & data",
        title="AI used in grid-operations contexts without an approved review",
        question="Which AI workloads touch grid operations without an approved review, and what do they cost?",
        personas=["cip_compliance", "ai_program_owner", "tech_exec", "auditor"],
        catalog_persona="NERC CIP Compliance, AI & Copilot Program Owner",
        description="The AI use register joined to AI spend by application.",
        keywords=["ai governance", "grid operations", "ai review", "model risk", "operations ai"],
        sql="""
WITH spend AS (
  SELECT json_extract_string(Tags, '$.application') AS Application, SUM(EffectiveCost) AS AISpend
  FROM CostAndUsage
  WHERE x_AIProduct IS NOT NULL OR ServiceCategory = 'AI and Machine Learning'
  GROUP BY json_extract_string(Tags, '$.application')
)
SELECT
  u.Application,
  u.UseCase,
  u.ReviewStatus,
  u.ApprovedOn,
  u.Owner,
  ROUND(COALESCE(s.AISpend, 0), 2) AS AISpend,
  CASE WHEN u.ReviewStatus <> 'Approved' THEN 'Grid-operations AI without approved review' ELSE 'Approved' END AS Status
FROM ref_ai_use u
LEFT JOIN spend s ON s.Application = u.Application
WHERE u.GridOpsContext
ORDER BY CASE WHEN u.ReviewStatus <> 'Approved' THEN 0 ELSE 1 END, AISpend DESC""",
        params=[],
        compliance=dict(area="Operations AI", reference="Client AI governance policy", unit="AI applications",
                        flag="Status = 'Grid-operations AI without approved review'", amount="AISpend"),
        notes="The AI use register is maintained by the AI program office with the CIP program."),
    dict(
        id="UT-12", module="risk", category="Utility · Prudence",
        title="Disallowance risk by docket, reason and vendor",
        question="Which test-period spend could a commission challenge, and why?",
        personas=["regulatory_analyst", "cfo", "auditor", "opco_finance"],
        catalog_persona="Rates & Regulatory, CFO",
        description="The prudence evidence tests on every allocated O&M line of each docket's test period, summed by reason "
                    "and vendor.",
        keywords=["disallowance", "prudence", "used and useful", "evidence", "docket", "risk"],
        sql="""
SELECT
  e.FilingId AS Docket, e.LegalEntity, e.RiskReason, e.ServiceProviderName AS Vendor,
  COUNT(*) AS Lines,
  ROUND(SUM(e.AllocatedCost), 2) AS TestPeriodSpend,
  ROUND(SUM(e.RiskAmount), 2) AS DisallowanceRisk
FROM filing_evidence e
WHERE e.RiskAmount > 0
GROUP BY e.FilingId, e.LegalEntity, e.RiskReason, e.ServiceProviderName
ORDER BY DisallowanceRisk DESC""",
        params=[],
        compliance=None,
        notes="Decision support, not a prediction of any commission's order. Recomputed after allocation, ingestion and "
              "every policy or optimization decision."),
    dict(
        id="UT-13", module="tax", category="Utility · Regulatory accounting",
        title="Earnings unlock: carrying return on deferrable cloud costs",
        question="What return would deferring recurring cloud and SaaS costs earn where a commission allows it?",
        personas=["regulatory_analyst", "cfo"],
        catalog_persona="Rates & Regulatory, CFO",
        description="Annualized recurring cloud and SaaS O&M by jurisdiction, as a regulatory asset amortized over the "
                    "contract (or the order's default term), earning the docket's pre-tax return on the average balance.",
        keywords=["earnings unlock", "regulatory asset", "carrying return", "rate of return", "deferral"],
        sql="""
WITH bp AS (SELECT MIN(ReviewPeriodStart) AS b0, MAX(ReviewPeriodEnd) AS b1 FROM ref_tax_filing),
fees AS (
  SELECT a.Jurisdiction, a.ServiceProviderName AS Vendor, COUNT(DISTINCT a.PeriodStart) AS Months, SUM(a.AllocatedCost) AS Fees
  FROM allocated_cost a CROSS JOIN bp
  WHERE a.ChargeCategory = 'Purchase' AND a.ChargeFrequency = 'Recurring' AND a.CostClass = 'om'
    AND a.PeriodStart >= bp.b0 AND a.PeriodStart < bp.b1
  GROUP BY a.Jurisdiction, a.ServiceProviderName
),
ror AS (SELECT AVG(r.PreTaxRor) AS r FROM ref_rate_case r JOIN ref_tax_filing f ON f.FilingId = r.FilingId WHERE f.Jurisdiction <> 'FERC'),
x AS (
  SELECT f.*, p.CreditStatus AS DeferralStatus, v.ContractEnd,
         COALESCE(rj.PreTaxRor, (SELECT r FROM ror)) AS PreTaxRor, tf.FilingId AS Docket,
         COALESCE(tf.ForecastYearStart, TIMESTAMP '2027-01-01') AS RateYearStart
  FROM fees f
  JOIN ref_jurisdiction_policy p ON p.Jurisdiction = f.Jurisdiction
  LEFT JOIN ref_tax_filing tf ON tf.Jurisdiction = f.Jurisdiction
  LEFT JOIN ref_rate_case rj ON rj.FilingId = tf.FilingId
  LEFT JOIN ref_vendor v ON v.Vendor = f.Vendor
  WHERE p.CreditStatus <> 'not_applicable'
),
n AS (
  SELECT x.*, x.Fees * 12 / x.Months AS AnnualFees,
         CASE WHEN x.ContractEnd > x.RateYearStart
              THEN GREATEST(12, (EXTRACT(YEAR FROM x.ContractEnd) - EXTRACT(YEAR FROM x.RateYearStart)) * 12
                                + EXTRACT(MONTH FROM x.ContractEnd) - EXTRACT(MONTH FROM x.RateYearStart))
              ELSE 62 END AS N
  FROM x
)
SELECT
  Jurisdiction, DeferralStatus, MAX(Docket) AS Docket,
  ROUND(MAX(PreTaxRor), 2) AS PreTaxRor,
  ROUND(SUM(AnnualFees), 2) AS AnnualRecurringFees,
  ROUND(SUM(AnnualFees / 12 * (N + 1) / 2) * MAX(PreTaxRor) / 100, 2) AS SteadyStateReturn,
  CASE WHEN DeferralStatus = 'claimed' THEN 'Earned under order'
       WHEN DeferralStatus = 'eligible' THEN 'Unlock: request deferral'
       ELSE 'Denied' END AS Status
FROM n
GROUP BY Jurisdiction, DeferralStatus
ORDER BY SteadyStateReturn DESC""",
        params=[],
        compliance=None,
        notes="Illustrative: depends on a commission granting deferral and on the authorized return. Jurisdictions without a "
              "docket use the average pre-tax return of the open dockets; contracts without an end date use 62 months."),
    dict(
        id="UT-14", module="risk", category="Utility · Prudence",
        title="Evidence completeness by docket",
        question="How complete is the evidence behind each docket's test-period technology O&M?",
        personas=["regulatory_analyst", "auditor", "finance_controller"],
        catalog_persona="Rates & Regulatory, Internal Audit",
        description="Share of test-period spend that is reconciled to an invoice, allocated under an approved policy, mapped to a "
                    "FERC account, shown used and useful, and free of savings left unactioned.",
        keywords=["evidence", "prudence", "completeness", "docket", "audit file"],
        sql="""
SELECT
  e.FilingId AS Docket, r.LegalEntity, r.Jurisdiction, r.Status AS DocketStatus,
  COUNT(*) AS Lines,
  ROUND(SUM(e.AllocatedCost), 2) AS TestPeriodSpend,
  ROUND(100 * SUM(CASE WHEN e.Reconciled THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS ReconciledPct,
  ROUND(100 * SUM(CASE WHEN e.PolicyApproved THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS PolicyApprovedPct,
  ROUND(100 * SUM(CASE WHEN e.GlMapped THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS FercMappedPct,
  ROUND(100 * SUM(CASE WHEN e.BenefitShown OR e.Remediated THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS UsedAndUsefulPct,
  ROUND(100 * SUM(CASE WHEN e.SavingsActioned OR e.Remediated THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS SavingsActionedPct,
  ROUND(100 * SUM(e.AllocatedCost * e.EvidenceScore) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS EvidenceScore,
  ROUND(SUM(e.RiskAmount), 2) AS DisallowanceRisk,
  CASE WHEN SUM(e.RiskAmount) > 0 THEN 'Evidence gaps' ELSE 'Complete' END AS Status
FROM filing_evidence e
JOIN ref_tax_filing r ON r.FilingId = e.FilingId
GROUP BY e.FilingId, r.LegalEntity, r.Jurisdiction, r.Status
ORDER BY DisallowanceRisk DESC""",
        params=[],
        compliance=dict(area="Prudence", reference="Prudent-investment and used-and-useful standards", unit="dockets",
                        flag="Status = 'Evidence gaps'", amount="DisallowanceRisk"),
        notes="Weights: reconciled 25%, approved policy 15%, FERC account 15%, used and useful 30%, savings actioned 15%."),
    dict(
        id="UT-15", module="allocation", category="Utility · Tag enforcement",
        title="Tag compliance weighted by cost, by required tag",
        question="What share of taggable spend carries each required tag with a valid value, and how much sits in suspense?",
        personas=["finops_analyst", "finance_controller", "regulatory_analyst", "auditor"],
        catalog_persona="Cloud CoE, Controller",
        description="For each required tag (ref_required_tag), taggable spend with a valid value, with no value (suspense) and "
                    "with an invalid value, checked against the reference registers. Tax, credits, adjustments, support and "
                    "Marketplace purchases cannot be tagged and are reported apart, never as violations.",
        keywords=["tag compliance", "tagging", "untagged", "suspense", "required tags", "invalid tags", "tag enforcement"],
        sql="""
WITH c AS (
  SELECT EffectiveCost AS Cost,
         CASE WHEN ChargeCategory IN ('Tax', 'Credit', 'Adjustment') OR ResourceId IS NULL
                   OR (ChargeCategory = 'Purchase' AND ServiceProviderName <> InvoiceIssuerName) THEN 1 ELSE 0 END AS NotTaggable,
         json_extract_string(Tags, '$.opco') AS opco,
         json_extract_string(Tags, '$.business_unit') AS business_unit,
         COALESCE(json_extract_string(AllocatedTags, '$.cost_center'), json_extract_string(Tags, '$.cost_center')) AS cost_center,
         json_extract_string(Tags, '$.ferc_function') AS ferc_function,
         json_extract_string(Tags, '$.application') AS application,
         json_extract_string(Tags, '$.environment') AS environment,
         json_extract_string(Tags, '$.capex') AS capex,
         json_extract_string(Tags, '$.work_order') AS work_order
  FROM CostAndUsage
  WHERE ChargePeriodStart >= :period_start AND ChargePeriodStart < :period_end
),
t AS (SELECT * FROM c WHERE NotTaggable = 0),
k AS (
  SELECT 'opco' AS TagKey, t.Cost, t.opco AS Val,
         CASE WHEN t.opco IS NULL THEN NULL WHEN e.EntityCode IS NOT NULL OR t.opco = 'SHARED' THEN 1 ELSE 0 END AS Valid
  FROM t LEFT JOIN ref_legal_entity e ON e.EntityCode = t.opco
  UNION ALL
  SELECT 'business_unit', t.Cost, t.business_unit,
         CASE WHEN t.business_unit IS NULL THEN NULL WHEN b.BusinessUnit IS NOT NULL THEN 1 ELSE 0 END
  FROM t LEFT JOIN (SELECT DISTINCT BusinessUnit FROM ref_cost_center WHERE BusinessUnit IS NOT NULL) b ON b.BusinessUnit = t.business_unit
  UNION ALL
  SELECT 'cost_center', t.Cost, t.cost_center,
         CASE WHEN t.cost_center IS NULL THEN NULL WHEN cc.CostCenter IS NOT NULL THEN 1 ELSE 0 END
  FROM t LEFT JOIN ref_cost_center cc ON cc.CostCenter = t.cost_center AND cc.BusinessUnit IS NOT NULL
  UNION ALL
  SELECT 'ferc_function', t.Cost, t.ferc_function,
         CASE WHEN t.ferc_function IS NULL THEN NULL WHEN f.FercFunction IS NOT NULL THEN 1 ELSE 0 END
  FROM t LEFT JOIN (SELECT DISTINCT FercFunction FROM ref_cost_center WHERE FercFunction IS NOT NULL) f ON f.FercFunction = t.ferc_function
  UNION ALL
  SELECT 'application', t.Cost, t.application, CASE WHEN t.application IS NULL THEN NULL ELSE 1 END FROM t
  UNION ALL
  SELECT 'environment', t.Cost, t.environment,
         CASE WHEN t.environment IS NULL THEN NULL WHEN t.environment IN ('prod', 'nonprod', 'sandbox') THEN 1 ELSE 0 END
  FROM t
  UNION ALL
  SELECT 'work_order', t.Cost, t.work_order,
         CASE WHEN t.work_order IS NULL THEN NULL WHEN w.WorkOrderId IS NOT NULL THEN 1 ELSE 0 END
  FROM t LEFT JOIN (SELECT DISTINCT WorkOrderId FROM ref_work_order) w ON w.WorkOrderId = t.work_order
  WHERE t.capex = 'true'
),
nt AS (SELECT SUM(Cost) AS CannotBeTagged FROM c WHERE NotTaggable = 1)
SELECT
  k.TagKey,
  MAX(r.Requirement) AS Requirement,
  ROUND(SUM(k.Cost), 2) AS TaggableSpend,
  ROUND(SUM(CASE WHEN k.Valid = 1 THEN k.Cost ELSE 0 END), 2) AS ValidSpend,
  ROUND(SUM(CASE WHEN k.Val IS NULL THEN k.Cost ELSE 0 END), 2) AS UntaggedSpend,
  ROUND(SUM(CASE WHEN k.Valid = 0 THEN k.Cost ELSE 0 END), 2) AS InvalidSpend,
  ROUND(100 * SUM(CASE WHEN k.Valid = 1 THEN k.Cost ELSE 0 END) / NULLIF(SUM(k.Cost), 0), 1) AS CoveragePct,
  ROUND(MAX(nt.CannotBeTagged), 2) AS CannotBeTagged,
  CASE WHEN SUM(CASE WHEN k.Valid = 1 THEN k.Cost ELSE 0 END) >= 0.99 * SUM(k.Cost) THEN 'Meets 99%' ELSE 'Below 99%' END AS Status
FROM k
CROSS JOIN nt
LEFT JOIN ref_required_tag r ON r.TagKey = k.TagKey
GROUP BY k.TagKey
ORDER BY CoveragePct""",
        params=SEPTEMBER,
        compliance=dict(area="Tag enforcement", reference="Required-tag policy (ref_required_tag)", unit="tag keys",
                        flag="Status = 'Below 99%'", amount="UntaggedSpend + InvalidSpend"),
        notes="Precedence: resource tag, then account or inherited tag, then approved rule; spend with no cost center waits in "
              "suspense with an owner and is restated through an approved rule with an effective date."),
    dict(
        id="UT-16", module="tax", category="Utility · Capital",
        title="Capital path by work order: 107 to in service to 403",
        question="What sits in construction work in progress by work order, what went into service, and what depreciates through 403?",
        personas=["finance_controller", "regulatory_analyst", "transmission_finance", "distribution_finance"],
        catalog_persona="Controller, Regulatory Accounting",
        description="Per work order and module: CWIP before the window, additions from the ledger (cost class cwip, by the "
                    "ledger's WorkOrderId), the "
                    "transfer to the Order No. 898 software account on the ready-for-use date, and monthly depreciation at the "
                    "existing rate (¶137). Capitalized lines without a work order, or on a work order without a threshold "
                    "date, are held out of 107.",
        keywords=["cwip", "107", "work order", "in service", "351.2", "363.2", "397.2", "403", "depreciation", "capital"],
        sql="""
WITH adds AS (
  SELECT WorkOrderId, SUM(AllocatedCost) AS Additions
  FROM allocated_cost WHERE CostClass = 'cwip' AND WorkOrderId IS NOT NULL
  GROUP BY WorkOrderId
),
held AS (
  SELECT WorkOrderId, SUM(AllocatedCost) AS Held
  FROM allocated_cost
  WHERE Exception IN ('no_work_order', 'capitalizable_without_threshold_date')
  GROUP BY WorkOrderId
),
asof AS (SELECT MAX(CAST(ChargePeriodStart AS DATE)) AS d FROM CostAndUsage WHERE ChargeCategory = 'Usage'),
x AS (
  SELECT w.*, COALESCE(a.Additions, 0) * w.ModuleShare AS AddInWindow,
         w.CwipBeforeWindow + COALESCE(a.Additions, 0) * w.ModuleShare AS Cwip,
         COALESCE(h.Held, 0) AS Held,
         CASE WHEN w.ReadyForUseDate IS NOT NULL AND w.ReadyForUseDate <= asof.d THEN 1 ELSE 0 END AS InService,
         asof.d AS AsOf
  FROM ref_work_order w
  CROSS JOIN asof
  LEFT JOIN adds a ON a.WorkOrderId = w.WorkOrderId
  LEFT JOIN held h ON h.WorkOrderId = w.WorkOrderId
)
SELECT
  WorkOrderId, Module, Description, FercFunction, TargetPlantAccount,
  CapitalizationStartDate, ReadyForUseDate,
  ROUND(CwipBeforeWindow, 2) AS Cwip107Opening,
  ROUND(AddInWindow, 2) AS Cwip107Additions,
  ROUND(CASE WHEN InService = 1 THEN 0 ELSE Cwip END, 2) AS Cwip107Balance,
  ROUND(CASE WHEN InService = 1 THEN Cwip ELSE 0 END, 2) AS TransferredToPlant,
  DepreciationRate,
  ROUND(CASE WHEN InService = 1 THEN Cwip * DepreciationRate / 100 / 12 ELSE 0 END, 2) AS MonthlyDepreciation403,
  CASE WHEN InService = 1 THEN DATE_DIFF('month', ReadyForUseDate, AsOf) + 1 ELSE 0 END AS MonthsInService,
  ROUND(CASE WHEN InService = 1 THEN Cwip * DepreciationRate / 100 / 12 * (DATE_DIFF('month', ReadyForUseDate, AsOf) + 1) ELSE 0 END, 2)
    AS Depreciation403ToDate,
  ROUND(Held, 2) AS HeldOutOf107,
  CASE WHEN CapitalizationStartDate IS NULL THEN 'Held: capitalizable_without_threshold_date'
       WHEN InService = 1 THEN 'In service: depreciating through 403'
       ELSE 'Building in 107' END AS Status
FROM x
UNION ALL
SELECT
  '(none)' AS WorkOrderId, '-' AS Module, 'Capitalized lines with no work order' AS Description, NULL AS FercFunction,
  NULL AS TargetPlantAccount, NULL AS CapitalizationStartDate, NULL AS ReadyForUseDate,
  0 AS Cwip107Opening, 0 AS Cwip107Additions, 0 AS Cwip107Balance, 0 AS TransferredToPlant, NULL AS DepreciationRate,
  0 AS MonthlyDepreciation403, 0 AS MonthsInService, 0 AS Depreciation403ToDate,
  ROUND(SUM(Held), 2) AS HeldOutOf107,
  'Held: no_work_order' AS Status
FROM held WHERE WorkOrderId IS NULL
ORDER BY WorkOrderId, Module""",
        params=[],
        compliance=dict(area="Capital", reference="Acct 107 · Order No. 898 ¶112-113, ¶137 · ASU 2025-06",
                        unit="work orders", flag="Status LIKE 'Held%'", amount="HeldOutOf107"),
        notes="R1 (107 with a valid work order), R2 (ready for use: 351.2 Transmission, 363.2 Distribution, 397.2 General or "
              "multi-function) and R3 (403/108 at the existing rate). Lines held out of 107 are expensed until the work order "
              "or threshold date exists."),
    dict(
        id="UT-17", module="allocation", category="Utility · Tag enforcement",
        title="Exception queue by code",
        question="What is waiting in the exception queue, by exception code, and who owns each?",
        personas=["finance_controller", "finops_analyst", "regulatory_analyst", "auditor"],
        catalog_persona="Controller, Cloud CoE",
        description="Every exception code with the lines and dollars it holds in the period: unresolved operating company or "
                    "function, capital without a work order or threshold date, deferral without an order, multi-function "
                    "default, unmapped account, the 920 guard and masked names awaiting CIP review.",
        keywords=["exception queue", "exceptions", "suspense", "no_opco", "no_work_order", "readiness"],
        sql="""
WITH alloc AS (
  SELECT * FROM allocated_cost WHERE PeriodStart >= :period_start AND PeriodStart < :period_end
),
cu AS (
  SELECT EffectiveCost, SubAccountId, ServiceName,
         CASE WHEN ChargeCategory IN ('Tax', 'Credit', 'Adjustment') OR ResourceId IS NULL THEN 1 ELSE 0 END AS NotTaggable,
         COALESCE(json_extract_string(AllocatedTags, '$.cost_center'), json_extract_string(Tags, '$.cost_center')) AS cost_center,
         json_extract_string(Tags, '$.ferc_function') AS ferc_function,
         json_extract_string(Tags, '$.capex') AS capex,
         json_extract_string(Tags, '$.work_order') AS work_order,
         json_extract_string(Tags, '$.implementation') AS implementation
  FROM CostAndUsage
  WHERE BillingPeriodStart >= :period_start AND BillingPeriodStart < :period_end
),
q AS (
  SELECT Exception AS ExceptionCode, COUNT(*) AS Lines, SUM(AllocatedCost) AS Amount
  FROM alloc WHERE Exception IS NOT NULL AND Exception NOT IN ('no_ferc_function', 'cip_review_pending')
  GROUP BY Exception
  UNION ALL
  SELECT 'no_ferc_function', COUNT(*), SUM(cu.EffectiveCost)
  FROM cu LEFT JOIN ref_cost_center c ON c.CostCenter = cu.cost_center
  WHERE cu.NotTaggable = 0 AND cu.ferc_function IS NULL AND c.FercFunction IS NULL
  UNION ALL
  SELECT 'labor_account_on_non_labor', COUNT(*), 0 FROM ref_rule_change WHERE ExceptionCode = 'labor_account_on_non_labor'
  UNION ALL
  SELECT 'cip_review_pending', COUNT(*), SUM(cu.EffectiveCost)
  FROM cu
  JOIN ref_cloud_account ca ON ca.SubAccountId = cu.SubAccountId
  JOIN (SELECT DISTINCT SubAccountId FROM ref_cip_review WHERE ReviewStatus = 'pending') r ON r.SubAccountId = cu.SubAccountId
  WHERE ca.NamesMasked AND cu.NotTaggable = 0
),
qq AS (SELECT ExceptionCode, SUM(Lines) AS Lines, SUM(Amount) AS Amount FROM q GROUP BY ExceptionCode)
SELECT
  e.ExceptionCode,
  e.Meaning,
  COALESCE(q.Lines, 0) AS Lines,
  ROUND(COALESCE(q.Amount, 0), 2) AS Amount,
  e.Owner,
  e.Resolution,
  CASE WHEN COALESCE(q.Lines, 0) = 0 THEN 'Clear'
       WHEN e.ExceptionCode = 'labor_account_on_non_labor' THEN 'Blocked by the 920 guard'
       ELSE 'Open' END AS Status
FROM ref_exception_code e
LEFT JOIN qq q ON q.ExceptionCode = e.ExceptionCode
ORDER BY Amount DESC""",
        params=SEPTEMBER,
        compliance=dict(area="Tag enforcement", reference="Exception codes (ref_exception_code)", unit="exception codes",
                        flag="Status = 'Open'", amount="Amount"),
        notes="The ledger stamps one code per line (first match: no_opco, the 920 guard, unmapped account, no work order, no "
              "threshold date, no order, no function, multi-function default, CIP review). no_ferc_function and "
              "cip_review_pending are also read from the tags and the CIP review register, so a line can appear under two "
              "codes here and the amounts are not added together."),
    dict(
        id="UT-18", module="reconciliation", category="Utility · Invoice tie-out",
        title="Invoice tie-out with labeled reconciling items",
        question="Does each invoice tie to its cost rows, and is every difference labeled?",
        personas=["finance_controller", "finops_analyst", "auditor", "affiliate_accounting"],
        catalog_persona="Controller, Cloud CoE",
        description="Each invoice against the sum of BilledCost of the cost rows carrying its InvoiceId. Differences are "
                    "labeled from the reconciling-item register: tax that is not in the cost data by design, a late charge "
                    "expected in the provider's re-run, or a vendor overbilling raised as an exception.",
        keywords=["invoice", "tie out", "reconciliation", "late charge", "tax not in cost data", "overbilling", "invoice to ledger"],
        sql="""
WITH inv AS (
  SELECT InvoiceId, MIN(InvoiceIssuerName) AS Issuer, MIN(BillingAccountId) AS BillingAccountId,
         MIN(BillingPeriodStart) AS BillingPeriodStart, MIN(InvoiceIssueStatus) AS IssueStatus, SUM(BilledCost) AS Invoiced
  FROM InvoiceDetail
  WHERE BillingPeriodStart >= :period_start AND BillingPeriodStart < :period_end
  GROUP BY InvoiceId
),
chg AS (SELECT InvoiceId, SUM(BilledCost) AS CostRows, COUNT(*) AS Lines FROM CostAndUsage GROUP BY InvoiceId),
items AS (
  SELECT InvoiceId,
         SUM(CASE WHEN Explained THEN Amount ELSE 0 END) AS Labeled,
         SUM(CASE WHEN Explained THEN 0 ELSE Amount END) AS Pending
  FROM ref_invoice_reconciling_item
  WHERE Category NOT IN ('rounding', 'correction_next_period')
  GROUP BY InvoiceId
)
SELECT
  i.Issuer,
  i.InvoiceId,
  i.BillingAccountId,
  CAST(i.BillingPeriodStart AS DATE) AS Period,
  i.IssueStatus,
  ROUND(i.Invoiced, 2) AS Invoiced,
  ROUND(COALESCE(c.CostRows, 0), 2) AS CostRows,
  COALESCE(c.Lines, 0) AS CostLines,
  ROUND(i.Invoiced - COALESCE(c.CostRows, 0), 2) AS Difference,
  ROUND(COALESCE(t.Labeled, 0), 2) AS Labeled,
  ROUND(COALESCE(t.Pending, 0), 2) AS AwaitingRerun,
  ROUND(i.Invoiced - COALESCE(c.CostRows, 0) - COALESCE(t.Labeled, 0) - COALESCE(t.Pending, 0), 2) AS Unlabeled,
  CASE WHEN ABS(i.Invoiced - COALESCE(c.CostRows, 0)) < 0.005 THEN 'Ties to the cent'
       WHEN ABS(i.Invoiced - COALESCE(c.CostRows, 0) - COALESCE(t.Labeled, 0) - COALESCE(t.Pending, 0)) < 0.005
            AND COALESCE(t.Pending, 0) = 0 THEN 'Difference labeled'
       WHEN ABS(i.Invoiced - COALESCE(c.CostRows, 0) - COALESCE(t.Labeled, 0) - COALESCE(t.Pending, 0)) < 0.005
            THEN 'Labeled; part awaiting the re-run'
       ELSE 'Unlabeled difference' END AS Status
FROM inv i
LEFT JOIN chg c ON c.InvoiceId = i.InvoiceId
LEFT JOIN items t ON t.InvoiceId = i.InvoiceId
ORDER BY Period, Issuer, InvoiceId""",
        params=SEPTEMBER,
        compliance=dict(area="Invoice tie-out", reference="Invoice to cost rows (BilledCost per InvoiceId)", unit="invoices",
                        flag="Status IN ('Unlabeled difference', 'Labeled; part awaiting the re-run')", amount="ABS(Difference)"),
        notes="A credit that arrives after a period closes is posted in the next open period (R13) and is listed on that "
              "period's invoice."),
    dict(
        id="UT-19", module="tax", category="Utility · FERC accounting",
        title="Prepayments (165) and their monthly amortization",
        question="What sits in prepayments for commitments and annual seats, and how much has been amortized to the consuming accounts?",
        personas=["finance_controller", "regulatory_analyst", "finops_analyst"],
        catalog_persona="Controller, Cloud CoE",
        description="Upfront commitments and annual subscriptions recorded to 165 (R9) and amortized each month to the account "
                    "of the usage that consumed them: reserved-instance and reservation coverage through EffectiveCost, annual "
                    "Copilot seats by the seat holder's cost center.",
        keywords=["prepayment", "165", "amortization", "reserved instance", "reservation", "annual seats", "commitment"],
        sql="""
WITH asof AS (SELECT MAX(CAST(ChargePeriodStart AS DATE)) AS d FROM CostAndUsage WHERE ChargeCategory = 'Usage'),
by_commitment AS (
  SELECT CommitmentDiscountId, SUM(EffectiveCost) AS Amortized
  FROM CostAndUsage WHERE CommitmentDiscountStatus = 'Used'
  GROUP BY CommitmentDiscountId
),
by_invoice AS (
  SELECT InvoiceId, SUM(EffectiveCost) AS Amortized
  FROM CostAndUsage WHERE BilledCost = 0 AND EffectiveCost > 0
  GROUP BY InvoiceId
),
x AS (
  SELECT p.*,
         CASE WHEN p.AmortizationStart < DATE '2026-07-01'
              THEN DATE_DIFF('month', p.AmortizationStart, DATE '2026-07-01') * p.MonthlyAmortization ELSE 0 END AS BeforeWindow,
         COALESCE(c.Amortized, i.Amortized, 0) AS InWindow,
         asof.d AS AsOf
  FROM ref_prepayment p
  CROSS JOIN asof
  LEFT JOIN by_commitment c ON c.CommitmentDiscountId = p.CommitmentDiscountId
  LEFT JOIN by_invoice i ON i.InvoiceId = p.InvoiceId
)
SELECT
  PrepaymentId, Vendor, Description, IssueDate, ROUND(Amount, 2) AS Amount, Account,
  ROUND(MonthlyAmortization, 2) AS MonthlyAmortization,
  ROUND(BeforeWindow, 2) AS AmortizedBeforeJuly,
  ROUND(InWindow, 2) AS AmortizedJulyToDate,
  ROUND(Amount - BeforeWindow - InWindow, 2) AS Balance165,
  ExpenseRule,
  CASE WHEN AmortizationStart > AsOf THEN 'Not started' ELSE 'Amortizing' END AS Status
FROM x
ORDER BY IssueDate""",
        params=[],
        compliance=None,
        notes="The October amortization runs to the data date."),
    dict(
        id="UT-20", module="ai", category="Utility · Copilot and AI",
        title="M365 Copilot seat re-harvest list (30-day rule, latency buffer, grace period)",
        question="Which M365 Copilot licenses have had no activity for more than 30 days and can be reassigned?",
        personas=["ai_program_owner", "finops_analyst", "tech_exec", "cfo"],
        catalog_persona="AI & Copilot Program Owner",
        description="Seats whose last activity (or assignment date, if later) is more than the threshold before the refresh "
                    "date, after a buffer for data latency. Seats assigned within the grace period are held back. Reassigning "
                    "a seat avoids buying a new one; the bill drops at the renewal true-down.",
        keywords=["re-harvest", "reassign", "copilot licenses", "m365 copilot", "license reclaim", "true-down", "graph activity"],
        sql="""
WITH snap AS (SELECT MAX(SnapshotDate) AS d FROM ai_seat_assignment WHERE x_AIProduct = 'm365_copilot'),
act AS (
  SELECT UserPrincipal, MAX(ActivityDate) AS LastActivity
  FROM ai_user_activity WHERE x_AIProduct = 'm365_copilot'
  GROUP BY UserPrincipal
),
s AS (
  SELECT a.UserPrincipal, a.Department, a.AssignedByGroupId, a.AssignedDate, a.MonthlySeatCost, t.LastActivity,
         DATE_DIFF('day', CASE WHEN t.LastActivity IS NULL OR t.LastActivity < a.AssignedDate THEN a.AssignedDate ELSE t.LastActivity END,
                   snap.d) - CAST(:buffer_days AS INTEGER) AS DaysIdle,
         DATE_DIFF('day', a.AssignedDate, snap.d) AS DaysAssigned
  FROM ai_seat_assignment a
  CROSS JOIN snap
  LEFT JOIN act t ON t.UserPrincipal = a.UserPrincipal
  WHERE a.x_AIProduct = 'm365_copilot'
)
SELECT
  UserPrincipal,
  Department,
  CASE WHEN AssignedByGroupId IS NULL THEN 'Direct' ELSE 'Group' END AS AssignedVia,
  AssignedByGroupId,
  AssignedDate,
  LastActivity,
  DaysIdle,
  MonthlySeatCost,
  CASE WHEN DaysAssigned < CAST(:grace_days AS INTEGER) AND LastActivity IS NULL THEN 'Grace period'
       ELSE 'Idle' END AS Status
FROM s
WHERE (DaysAssigned < CAST(:grace_days AS INTEGER) AND LastActivity IS NULL) OR DaysIdle > CAST(:threshold_days AS INTEGER)
ORDER BY CASE WHEN DaysAssigned < CAST(:grace_days AS INTEGER) AND LastActivity IS NULL THEN 1 ELSE 0 END, DaysIdle DESC""",
        params=[("threshold_days", "string", "30", "Days without activity"), ("buffer_days", "string", "2", "Data latency buffer (days)"),
                ("grace_days", "string", "14", "Grace period after assignment (days)")],
        compliance=None,
        notes="Activity comes from daily Graph snapshots kept since 2026-07-01 (the report returns only the latest values). "
              "Group-assigned seats are released through the group; direct assignments one by one."),
    dict(
        id="UT-21", module="ai", category="Utility · Copilot and AI",
        title="AI token cost check: cache tokens counted",
        question="Would AI cost be understated if only input and output tokens were added up?",
        personas=["ai_program_owner", "finops_analyst", "finance_controller"],
        catalog_persona="AI & Copilot Program Owner, Cloud CoE",
        description="Token cost by application and model, by token type. The check fails where cache-read or cache-write "
                    "tokens carry cost that an input-plus-output sum would miss.",
        keywords=["tokens", "cache tokens", "token reconciliation", "bedrock", "azure openai", "ai cost check"],
        sql="""
SELECT
  json_extract_string(Tags, '$.application') AS Application,
  x_ModelName AS Model,
  ServiceName,
  ROUND(SUM(EffectiveCost), 2) AS TotalCost,
  ROUND(SUM(CASE WHEN x_TokenType = 'input' THEN EffectiveCost ELSE 0 END), 2) AS InputCost,
  ROUND(SUM(CASE WHEN x_TokenType = 'output' THEN EffectiveCost ELSE 0 END), 2) AS OutputCost,
  ROUND(SUM(CASE WHEN x_TokenType = 'cache_read' THEN EffectiveCost ELSE 0 END), 2) AS CacheReadCost,
  ROUND(SUM(CASE WHEN x_TokenType = 'cache_write' THEN EffectiveCost ELSE 0 END), 2) AS CacheWriteCost,
  ROUND(SUM(CASE WHEN x_TokenType IN ('input', 'output') THEN EffectiveCost ELSE 0 END), 2) AS InputPlusOutputOnly,
  ROUND(SUM(CASE WHEN x_TokenType IN ('cache_read', 'cache_write') THEN EffectiveCost ELSE 0 END), 2) AS MissedIfCacheIgnored,
  CASE WHEN SUM(CASE WHEN x_TokenType IN ('cache_read', 'cache_write') THEN EffectiveCost ELSE 0 END) > 0
       THEN 'Fails if only input and output are added' ELSE 'Passes' END AS CheckResult
FROM CostAndUsage
WHERE x_TokenType IN ('input', 'output', 'cache_read', 'cache_write')
  AND ChargePeriodStart >= :period_start AND ChargePeriodStart < :period_end
GROUP BY json_extract_string(Tags, '$.application'), x_ModelName, ServiceName
ORDER BY MissedIfCacheIgnored DESC""",
        params=SEPTEMBER,
        compliance=None,
        notes="FOCUS 1.4 carries the token counts in extension columns; the ledger keeps all four token types."),
    dict(
        id="UT-22", module="tax", category="Utility · FERC accounting",
        title="FERC rule register: dollars by rule and sign-off status",
        question="Which mapping rule placed each dollar, on what citation, and which [POLICY] rules await the controller's sign-off?",
        personas=["finance_controller", "regulatory_analyst", "auditor", "cfo"],
        catalog_persona="Controller, Regulatory Accounting",
        description="Rules R1-R17 in order of evaluation with their accounts by function, condition, citation and basis "
                    "(Regulation or [POLICY]), and the allocated dollars each placed in the period.",
        keywords=["rule register", "ferc rules", "policy", "sign-off", "citation", "r1", "r5", "r7", "mapping rules"],
        sql="""
WITH r AS (
  SELECT RuleId, AllocatedCost FROM allocated_cost
  WHERE PeriodStart >= :period_start AND PeriodStart < :period_end
)
SELECT
  f.RuleId, f.EvaluationStep, f.ChargePattern, f.TransmissionAccount, f.DistributionAccount, f.GeneralAccount,
  f.Condition, f.Citation, f.Basis, f.PolicyElement, f.Status,
  COUNT(r.RuleId) AS AllocatedLines,
  ROUND(COALESCE(SUM(r.AllocatedCost), 0), 2) AS AllocatedCost
FROM ref_ferc_rule f
LEFT JOIN r ON r.RuleId = f.RuleId
GROUP BY f.RuleId, f.EvaluationStep, f.ChargePattern, f.TransmissionAccount, f.DistributionAccount, f.GeneralAccount,
         f.Condition, f.Citation, f.Basis, f.PolicyElement, f.Status
ORDER BY f.EvaluationStep, f.RuleId""",
        params=SEPTEMBER,
        compliance=dict(area="FERC accounting", reference="Rule register R1-R17 · controller sign-off", unit="rules",
                        flag="Status = 'awaiting sign-off'", amount="AllocatedCost"),
        notes="Dollars come from the rule stamped on each ledger line, so the rules add up to the ledger total. Tax and credit "
              "lines (R12, R13) take the account of the line they belong to. We make the company's policy executable and "
              "auditable; the controller signs off every [POLICY] row."),
    dict(
        id="UT-23", module="governance", category="Utility · FERC accounting",
        title="Account 920 guard: no non-labor line in A&G salaries",
        question="Does any cloud or software line, or any proposed mapping rule, land in account 920?",
        personas=["finance_controller", "auditor", "regulatory_analyst"],
        catalog_persona="Controller, Internal Audit",
        description="Account 920 is A&G salaries (compensation only). The guard checks the ledger, the account map and every "
                    "proposed rule change; a proposal that would send a non-labor line to 920 is blocked "
                    "(labor_account_on_non_labor).",
        keywords=["920", "a&g salaries", "guard", "labor account", "rule change", "blocked"],
        sql="""
SELECT 'Allocated ledger' AS Source, '920' AS Account, NULL AS ChangeId, NULL AS Description,
       COUNT(*) AS Lines, ROUND(COALESCE(SUM(AllocatedCost), 0), 2) AS Amount,
       CASE WHEN COUNT(*) = 0 THEN 'Pass' ELSE 'Fail' END AS Status
FROM allocated_cost WHERE GlAccount = '920'
UNION ALL
SELECT 'Account map', '920', NULL, NULL, COUNT(*), 0,
       CASE WHEN COUNT(*) = 0 THEN 'Pass' ELSE 'Fail' END
FROM ref_gl_account_map WHERE GlAccount = '920'
UNION ALL
SELECT 'Proposed rule change', ProposedAccount, ChangeId, Description, 0, 0,
       CASE WHEN Status = 'blocked' THEN 'Blocked' ELSE 'Fail' END
FROM ref_rule_change WHERE ProposedAccount = '920'""",
        params=[],
        compliance=dict(area="FERC accounting", reference="Acct 920 (compensation only) · rule R7", unit="checks",
                        flag="Status = 'Fail'", amount="Amount"),
        notes="The same guard ships as a singular test in the Client-Owned Codebase (tests/assert_no_non_labor_in_920.sql)."),
    dict(
        id="UT-24", module="tax", category="Utility · FERC accounting",
        title="Ledger accounts against the FERC account reference",
        question="Is every account the ledger used a valid FERC account for the period, under the right Part?",
        personas=["finance_controller", "regulatory_analyst", "auditor"],
        catalog_persona="Controller, Regulatory Accounting",
        description="Each account in the allocated ledger checked against ref_ferc_account: present, effective for the period "
                    "(Order No. 898 accounts from 2025-01-01; 303/404 software placement before), and not a labor-only account.",
        keywords=["ferc account", "order 898", "effective date", "303", "404", "account validity", "chart of accounts"],
        sql="""
WITH a AS (
  SELECT GlAccount, MAX(GlTitle) AS LedgerTitle, MIN(PeriodStart) AS FirstPeriod, MAX(PeriodStart) AS LastPeriod,
         COUNT(*) AS Lines, SUM(AllocatedCost) AS Amount, COUNT(DISTINCT RuleId) AS RulesUsed,
         MAX(CASE WHEN FunctionCode = 'NONUTIL' THEN 1 ELSE 0 END) AS NonUtility
  FROM allocated_cost
  WHERE PeriodStart >= :period_start AND PeriodStart < :period_end
  GROUP BY GlAccount
),
f AS (
  SELECT FercAccount, MIN(Title) AS Title, MIN(Part) AS Part, MIN(AccountType) AS AccountType,
         MIN(EffectiveFrom) AS EffectiveFrom, MAX(EffectiveTo) AS EffectiveTo,
         SUM(CASE WHEN EffectiveTo IS NULL THEN 1 ELSE 0 END) AS OpenRows,
         MAX(CASE WHEN LaborOnly THEN 1 ELSE 0 END) AS LaborOnly
  FROM ref_ferc_account
  GROUP BY FercAccount
)
SELECT
  COALESCE(a.GlAccount, '(unmapped)') AS Account,
  COALESCE(f.Title, a.LedgerTitle) AS Title,
  f.Part, f.AccountType, f.EffectiveFrom,
  a.Lines, a.RulesUsed, ROUND(a.Amount, 2) AS Amount,
  CASE WHEN a.GlAccount IS NULL THEN 'Unmapped'
       WHEN a.GlAccount = 'NONUTIL' OR (f.FercAccount IS NULL AND a.NonUtility = 1) THEN 'Non-utility affiliate (outside Part 101)'
       WHEN f.FercAccount IS NULL THEN 'Not in the FERC account reference'
       WHEN f.LaborOnly = 1 THEN 'Labor-only account'
       WHEN f.EffectiveFrom IS NOT NULL AND f.EffectiveFrom > CAST(a.FirstPeriod AS DATE) THEN 'Not yet effective'
       WHEN f.OpenRows = 0 AND f.EffectiveTo < CAST(a.LastPeriod AS DATE) THEN 'No longer effective'
       ELSE 'Valid' END AS Status
FROM a
LEFT JOIN f ON f.FercAccount = a.GlAccount
ORDER BY Amount DESC""",
        params=PERIOD,
        compliance=dict(area="FERC accounting", reference="ref_ferc_account · Order No. 898 effective 2025-01-01", unit="accounts",
                        flag="Status NOT IN ('Valid', 'Non-utility affiliate (outside Part 101)')", amount="Amount"),
        notes="The affiliate's share is billed at cost and recorded in its own books, outside the Uniform System of Accounts."),
    dict(
        id="UT-25", module="tax", category="Utility · FERC accounting",
        title="Transactions by FERC account: vendor, description, amount and invoice",
        question="Which transactions make up the balance of one FERC account in the period, with vendor, description, amount and invoice?",
        personas=["finance_controller", "regulatory_analyst", "auditor", "affiliate_accounting"],
        catalog_persona="Controller, Regulatory Accounting",
        description="The transaction listing behind one FERC account: every allocated ledger line in the period with the "
                    "receiving company, jurisdiction, the rule that placed it, vendor, charge description, application, cost "
                    "center and the invoice and invoice line it came from. The listing a data request or an audit asks for.",
        keywords=["transaction listing", "transactions", "ferc account", "account detail", "vendor", "invoice", "566", "data request"],
        sql="""
SELECT
  PeriodStart,
  CAST(ChargePeriodStart AS DATE) AS ChargeDay,
  LegalEntity,
  Jurisdiction,
  GlAccount,
  GlTitle,
  RuleId,
  ServiceProviderName AS Vendor,
  ChargeDescription AS Description,
  Application,
  CostCenter,
  InvoiceId,
  InvoiceDetailId,
  ChargeKey,
  ROUND(AllocatedCost, 2) AS AllocatedCost
FROM allocated_cost
WHERE GlAccount = :account
  AND PeriodStart >= :period_start AND PeriodStart < :period_end
ORDER BY AllocatedCost DESC, ChargeDay, ChargeKey""",
        params=[("account", "string", "566", "FERC account")] + SEPTEMBER,
        compliance=None,
        notes="One row per ledger line: a charge spread across companies and jurisdictions appears once per share, so the "
              "rows add up to the account balance."),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    for u in USE_CASES:
        u.setdefault("datasets", DATASETS)
        u["columns"] = "FOCUS columns plus ref_* utility registers and allocated_cost"
        with open(os.path.join(OUT, f"{u['id']}.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(u, source=SOURCE))
        print("wrote", u["id"])


if __name__ == "__main__":
    main()
