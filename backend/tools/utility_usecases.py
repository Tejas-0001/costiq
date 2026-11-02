"""Write registry/usecases/UT-*.yaml: the Utilities module (regulatory accounting, affiliate allocation, NERC CIP,
operations & AI). Run from mvp/backend: python tools/utility_usecases.py

Kept as code so the eleven entries share one structure; the YAML files are what the platform loads.
"""
from __future__ import annotations
import os, textwrap

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "registry", "usecases")
PERIOD = [("period_start", "date", "2026-07-01", "Start of the period (inclusive)"),
          ("period_end", "date", "2026-11-01", "End of the period (exclusive)")]
DOCKET = [("docket", "string", "OH-26-1105-EL-AIR", "Rate case docket")]

USE_CASES = [
    dict(
        id="UT-01", module="utilities", category="Utilities · Regulatory accounting",
        title="Technology spend by FERC account and cost class",
        question="Does every allocated technology dollar land in a FERC account, and how much is capital, O&M or deferral?",
        personas=["regulatory_analyst", "finance_controller", "auditor", "cfo"],
        catalog_persona="Regulatory Accounting, Controller",
        description="Summarizes the allocation chain's output by legal entity, FERC Uniform System of Accounts account and cost "
                    "class. Order No. 898 added computer hardware and software accounts inside each plant function; O&M is mapped "
                    "through ut_ferc_account_map. Dollars with no account are flagged, because they cannot be presented in a rate "
                    "case or a FERC Form 1 without a manual entry.",
        keywords=["ferc account", "uniform system of accounts", "usoa", "order 898", "capital", "o&m", "unmapped"],
        sql="""
SELECT
  LegalEntity,
  COALESCE(FercAccount, '(unmapped)') AS FercAccount,
  COALESCE(MAX(FercTitle), 'No FERC account mapped') AS FercTitle,
  CostClass,
  COUNT(DISTINCT ChargeKey) AS ChargeLines,
  ROUND(SUM(AllocatedCost), 2) AS AllocatedCost,
  CASE WHEN FercAccount IS NULL THEN 'Unmapped' ELSE 'Mapped' END AS Status
FROM ut_allocated_cost
WHERE PeriodStart >= :period_start AND PeriodStart < :period_end
GROUP BY LegalEntity, FercAccount, CostClass
ORDER BY CASE WHEN FercAccount IS NULL THEN 0 ELSE 1 END, AllocatedCost DESC""",
        params=PERIOD,
        compliance=dict(area="Regulatory accounting", reference="18 CFR Part 101 (USoA) · FERC Order No. 898",
                        unit="account lines", flag="Status = 'Unmapped'", amount="AllocatedCost"),
        notes="The FERC map is illustrative and owned by the client's regulatory accounting team. Capital (implementation "
              "services) books to 397.2 and deferred subscriptions to 182.3 by rule, before the map is consulted."),
    dict(
        id="UT-02", module="regulatory", category="Utilities · Affiliate allocation",
        title="Service-company billing by associate company and basis of allocation",
        question="What did the service company bill each associate company, and on which allocation basis?",
        personas=["affiliate_accounting", "regulatory_analyst", "finance_controller", "auditor"],
        catalog_persona="Service-Company Accounting, Regulatory Accounting",
        description="A FERC Form No. 60 Schedule XXI-style view of technology costs: amounts billed to each associate company by "
                    "allocation policy and statistical driver (customers, workstations, pole miles, headcount). Amounts with no "
                    "approved policy are flagged.",
        keywords=["form 60", "schedule xxi", "methods of allocation", "service company", "associate company", "affiliate billing"],
        sql="""
SELECT
  LegalEntity AS AssociateCompany,
  COALESCE(OpcoPolicy, '(none)') AS Policy,
  COALESCE(DriverName, 'Direct (100% to one company)') AS BasisOfAllocation,
  COUNT(DISTINCT ChargeKey) AS ChargeLines,
  ROUND(SUM(AllocatedCost), 2) AS AmountBilled,
  CASE WHEN OpcoPolicy IS NULL THEN 'No approved policy' ELSE 'Approved policy' END AS Status
FROM ut_allocated_cost
WHERE PeriodStart >= :period_start AND PeriodStart < :period_end
GROUP BY LegalEntity, OpcoPolicy, DriverName
ORDER BY AssociateCompany, AmountBilled DESC""",
        params=PERIOD,
        compliance=dict(area="Affiliate allocation", reference="FERC Form No. 60, Schedule XXI (18 CFR 369.1)",
                        unit="billing lines", flag="Status = 'No approved policy'", amount="AmountBilled"),
        notes="Form 60 is filed by May 1 for the prior year. This view covers technology costs allocated through CostIQ only."),
    dict(
        id="UT-03", module="utilities", category="Utilities · Affiliate allocation",
        title="Cross-subsidy: regulated entities carrying non-regulated work",
        question="Are regulated operating companies paying for work that belongs to a non-regulated affiliate?",
        personas=["affiliate_accounting", "regulatory_analyst", "auditor", "cfo"],
        catalog_persona="Service-Company Accounting, Internal Audit",
        description="Finds allocated cost from cost centers owned by a non-regulated affiliate (ut_cost_center) that landed on a "
                    "regulated entity. The usual cause is a broad shared-platform policy that runs before the affiliate's own "
                    "direct-assignment policy.",
        keywords=["cross subsidy", "cross-subsidization", "affiliate", "non-regulated", "renewables", "affiliate wall"],
        sql="""
SELECT
  a.CostCenter,
  c.OwningEntity,
  a.LegalEntity AS ChargedToEntity,
  a.OpcoPolicy,
  a.ServiceName,
  COUNT(DISTINCT a.ChargeKey) AS ChargeLines,
  ROUND(SUM(a.AllocatedCost), 2) AS CrossSubsidy
FROM ut_allocated_cost a
JOIN ut_cost_center c ON c.CostCenter = a.CostCenter
WHERE NOT c.Regulated AND a.Regulated
  AND a.PeriodStart >= :period_start AND a.PeriodStart < :period_end
GROUP BY a.CostCenter, c.OwningEntity, a.LegalEntity, a.OpcoPolicy, a.ServiceName
ORDER BY CrossSubsidy DESC""",
        params=PERIOD,
        compliance=dict(area="Affiliate allocation", reference="State affiliate cost-allocation rules · PUHCA 2005",
                        unit="allocations", flag="CrossSubsidy > 0", amount="CrossSubsidy"),
        notes="Fix by giving the affiliate's direct-assignment policy a lower priority number than shared-platform policies."),
    dict(
        id="UT-04", module="utilities", category="Utilities · Regulatory accounting",
        title="Cloud subscription fees eligible for regulatory-asset deferral",
        question="Which cloud and SaaS subscription fees could be deferred to a regulatory asset, by state?",
        personas=["regulatory_analyst", "cfo", "finance_controller"],
        catalog_persona="Regulatory Accounting, CFO",
        description="Recurring subscription fees are O&M and earn no return unless a commission allows deferral to a regulatory "
                    "asset. This view shows, per jurisdiction and vendor, what is already deferred under an order and what is "
                    "still expensed where no deferral has been requested.",
        keywords=["regulatory asset", "deferral", "capex bias", "asu 2018-15", "cloud computing arrangement", "182.3"],
        sql="""
SELECT
  a.Jurisdiction,
  p.DeferralStatus,
  a.ServiceProviderName AS Vendor,
  ROUND(SUM(a.AllocatedCost), 2) AS SubscriptionFees,
  ROUND(SUM(CASE WHEN a.CostClass = 'deferral' THEN a.AllocatedCost ELSE 0 END), 2) AS Deferred,
  ROUND(SUM(CASE WHEN a.CostClass = 'om' THEN a.AllocatedCost ELSE 0 END), 2) AS ExpensedNoReturn,
  MAX(p.AmortizationMonths) AS AmortizationMonths,
  MAX(p.OrderReference) AS OrderReference,
  CASE WHEN p.DeferralStatus = 'allowed' THEN 'Deferred under order'
       WHEN p.DeferralStatus = 'untested' THEN 'Not yet requested'
       WHEN p.DeferralStatus = 'rejected' THEN 'Rejected: re-file with a customer-savings case'
       ELSE 'Not applicable' END AS Opportunity
FROM ut_allocated_cost a
JOIN ut_jurisdiction_policy p ON p.Jurisdiction = a.Jurisdiction
WHERE a.ChargeCategory = 'Purchase' AND a.ChargeFrequency = 'Recurring' AND a.CostClass IN ('om', 'deferral')
  AND a.PeriodStart >= :period_start AND a.PeriodStart < :period_end
GROUP BY a.Jurisdiction, p.DeferralStatus, a.ServiceProviderName
ORDER BY ExpensedNoReturn DESC, Deferred DESC""",
        params=PERIOD,
        compliance=dict(area="Regulatory accounting", reference="ASU 2018-15 · FERC AI20-1-000 · Idaho PUC Order No. 34707",
                        unit="state-vendor pairs", flag="Opportunity = 'Not yet requested'", amount="ExpensedNoReturn"),
        notes="Idaho Order 34707 (2020) let Idaho Power defer non-capitalized cloud fees to a regulatory asset, amortized over "
              "the contract or 62 months. Illinois rejected an 80% deferral rule the same year."),
    dict(
        id="UT-05", module="regulatory", category="Utilities · Regulatory accounting",
        title="Rate-case test-year bridge for cloud, SaaS and AI O&M by vendor",
        question="How does each vendor's cloud, SaaS and AI O&M move from the base period to the test year, and does it tie to invoices?",
        personas=["regulatory_analyst", "cfo", "finance_controller", "opco_finance"],
        catalog_persona="Regulatory Accounting & Rates",
        description="The exhibit a rates team files: base-period actuals for the docket's operating company and jurisdiction, "
                    "annualized, then carried to the forecast test year with known-and-measurable renewal escalators. Each "
                    "vendor's base period is tied to its invoices; an invoice exception is flagged because it weakens the prudence "
                    "case.",
        keywords=["rate case", "test year", "base period", "exhibit", "bridge", "known and measurable", "o&m"],
        sql="""
WITH rc AS (SELECT * FROM ut_rate_case WHERE Docket = :docket),
base AS (
  SELECT a.ServiceProviderName AS Vendor, SUM(a.AllocatedCost) AS BaseActual
  FROM ut_allocated_cost a JOIN rc ON a.LegalEntity = rc.LegalEntity AND a.Jurisdiction = rc.Jurisdiction
  WHERE a.CostClass IN ('om', 'deferral') AND a.PeriodStart >= rc.BasePeriodStart AND a.PeriodStart < rc.BasePeriodEnd
  GROUP BY a.ServiceProviderName
),
mo AS (
  SELECT COUNT(DISTINCT a.PeriodStart) AS n FROM ut_allocated_cost a JOIN rc
    ON a.PeriodStart >= rc.BasePeriodStart AND a.PeriodStart < rc.BasePeriodEnd
),
inv AS (
  SELECT i.InvoiceIssuerName AS Vendor, SUM(i.BilledCost) AS Invoiced
  FROM InvoiceDetail i JOIN rc ON i.BillingPeriodStart >= rc.BasePeriodStart AND i.BillingPeriodStart < rc.BasePeriodEnd
  GROUP BY i.InvoiceIssuerName
),
chg AS (
  SELECT c.InvoiceIssuerName AS Vendor, SUM(c.BilledCost) AS Charged
  FROM CostAndUsage c JOIN rc ON c.BillingPeriodStart >= rc.BasePeriodStart AND c.BillingPeriodStart < rc.BasePeriodEnd
  GROUP BY c.InvoiceIssuerName
),
fx AS (
  SELECT b.Vendor, b.BaseActual, b.BaseActual * 12 / mo.n AS Annualized, COALESCE(v.EscalatorPct, 0) AS EscalatorPct,
         v.RenewalDate,
         CASE WHEN v.RenewalDate >= rc.TestYearStart AND v.RenewalDate < rc.TestYearEnd
              THEN (12 - ((EXTRACT(YEAR FROM v.RenewalDate) - EXTRACT(YEAR FROM rc.TestYearStart)) * 12
                          + EXTRACT(MONTH FROM v.RenewalDate) - EXTRACT(MONTH FROM rc.TestYearStart))) / 12.0
              WHEN v.RenewalDate < rc.TestYearStart THEN 1.0 ELSE 0.0 END AS EscalatedShare,
         i.Invoiced, c.Charged
  FROM base b CROSS JOIN mo CROSS JOIN rc
  LEFT JOIN ut_vendor v ON v.Vendor = b.Vendor
  LEFT JOIN inv i ON i.Vendor = b.Vendor
  LEFT JOIN chg c ON c.Vendor = b.Vendor
)
SELECT
  Vendor,
  ROUND(BaseActual, 2) AS BasePeriodActual,
  ROUND(Annualized, 2) AS BaseAnnualized,
  EscalatorPct,
  CAST(RenewalDate AS DATE) AS RenewalDate,
  ROUND(Annualized * (1 + EscalatorPct / 100 * EscalatedShare), 2) AS TestYearForecast,
  ROUND(Annualized * EscalatorPct / 100 * EscalatedShare, 2) AS KnownAndMeasurable,
  CASE WHEN EscalatedShare > 0 AND EscalatorPct > 0 THEN 'Contract escalator at renewal'
       ELSE 'Base period annualized' END AS Reason,
  CASE WHEN Invoiced IS NULL THEN 'Billed via another issuer'
       WHEN ABS(Invoiced - COALESCE(Charged, 0)) <= 1 THEN 'Tied to invoices'
       ELSE 'Invoice exception' END AS InvoiceTieOut,
  ROUND(COALESCE(Invoiced, 0) - COALESCE(Charged, 0), 2) AS InvoiceDifference
FROM fx
ORDER BY BaseAnnualized DESC""",
        params=DOCKET,
        compliance=dict(area="Regulatory accounting", reference="Rate-case base period and known-and-measurable adjustments",
                        unit="vendors", flag="InvoiceTieOut = 'Invoice exception'", amount="BasePeriodActual"),
        notes="Invoice tie-out compares the vendor's invoices with all of its charges in the base period (every entity), "
              "so one exception can affect several dockets."),
    dict(
        id="UT-06", module="utilities", category="Utilities · Regulatory accounting",
        title="Renewals and price escalators landing in a rate-case test year",
        question="Which contract renewals and escalators fall inside the test year and should be filed as known-and-measurable changes?",
        personas=["regulatory_analyst", "finops_analyst", "cfo"],
        catalog_persona="Regulatory Accounting, Vendor Management",
        description="Lists vendors whose renewal date falls inside the docket's forecast test year, with the escalator and its "
                    "annual dollar effect. Missing these understates the test year; renegotiating them before filing is a "
                    "prudence point.",
        keywords=["renewal", "escalator", "price increase", "known and measurable", "test year", "contract"],
        sql="""
SELECT
  rc.Docket,
  v.Vendor,
  CAST(v.RenewalDate AS DATE) AS RenewalDate,
  v.EscalatorPct,
  ROUND(v.AnnualContractValue, 2) AS AnnualContractValue,
  ROUND(v.AnnualContractValue * v.EscalatorPct / 100, 2) AS AnnualEscalation,
  CASE WHEN v.RenewalDate >= rc.TestYearStart AND v.RenewalDate < rc.TestYearEnd THEN 'In test year'
       WHEN v.RenewalDate < rc.TestYearStart THEN 'Before test year'
       ELSE 'After test year' END AS Timing
FROM ut_vendor v
CROSS JOIN ut_rate_case rc
WHERE rc.Docket = :docket AND v.EscalatorPct > 0
ORDER BY v.RenewalDate""",
        params=DOCKET,
        compliance=dict(area="Regulatory accounting", reference="Known-and-measurable test-year adjustments",
                        unit="contracts", flag="Timing = 'In test year'", amount="AnnualEscalation"),
        notes="Annual contract values come from ut_vendor (vendor management); escalators apply from the renewal date."),
    dict(
        id="UT-07", module="utilities", category="Utilities · NERC CIP",
        title="BCSI held in cloud services without customer-held keys",
        question="Which cloud workloads hold BES Cyber System Information where the provider could decrypt it?",
        personas=["cip_compliance", "auditor", "tech_exec"],
        catalog_persona="NERC CIP Compliance",
        description="Joins the CIP register (ut_cip_system) to cloud spend. Under CIP-004-7 and CIP-011-3, provider staff are "
                    "not counted as having access to encrypted BCSI only when they cannot decrypt it; workloads that hold BCSI "
                    "with provider-managed keys are flagged.",
        keywords=["bcsi", "cip-011", "cip-004", "encryption", "key management", "nerc cip", "customer managed keys"],
        sql="""
WITH spend AS (
  SELECT json_extract_string(Tags, '$.application') AS Application, SUM(EffectiveCost) AS EffectiveCost
  FROM CostAndUsage
  WHERE ChargePeriodStart >= :period_start AND ChargePeriodStart < :period_end
  GROUP BY json_extract_string(Tags, '$.application')
),
reg AS (
  SELECT s.*, CASE WHEN s.HoldsBcsi AND s.KeyOwnership <> 'customer' THEN 'Provider can decrypt BCSI'
                   WHEN s.HoldsBcsi THEN 'BCSI with customer-held keys'
                   ELSE 'No BCSI' END AS Status
  FROM ut_cip_system s
)
SELECT
  r.Application, r.SystemName, r.ImpactRating, r.HoldsBcsi, r.KeyOwnership, r.Owner, r.Status,
  ROUND(COALESCE(x.EffectiveCost, 0), 2) AS EffectiveCost
FROM reg r
LEFT JOIN spend x ON x.Application = r.Application
ORDER BY CASE WHEN r.Status = 'Provider can decrypt BCSI' THEN 0 ELSE 1 END, EffectiveCost DESC""",
        params=PERIOD,
        compliance=dict(area="NERC CIP", reference="CIP-004-7 R6 · CIP-011-3 R1", unit="workloads",
                        flag="Status = 'Provider can decrypt BCSI'", amount="EffectiveCost"),
        notes="The CIP register is maintained by the CIP compliance team. CostIQ supplies the spend; it does not determine "
              "compliance."),
    dict(
        id="UT-08", module="utilities", category="Utilities · NERC CIP",
        title="CIP-scoped vendors missing CIP-013 supply-chain clauses",
        question="Which vendors supporting CIP-scoped systems lack required supply-chain contract clauses?",
        personas=["cip_compliance", "auditor", "tech_exec"],
        catalog_persona="NERC CIP Compliance, Vendor Management",
        description="Checks each CIP-scoped vendor's contract for the CIP-013 supply-chain risk-management elements: vulnerability "
                    "disclosure, incident notification, remote-access controls and software integrity. FERC Order No. 919 extends "
                    "these duties to vendor-supported virtualization and cloud platforms from 1 April 2028.",
        keywords=["cip-013", "supply chain", "vendor risk", "contract clauses", "order 919", "nerc cip"],
        sql="""
WITH c AS (
  SELECT Vendor, CipScoped, AnnualContractValue,
         CASE WHEN json_extract_string(Cip013Clauses, '$.vulnerability_disclosure') = 'true' THEN '' ELSE 'vulnerability disclosure; ' END AS m1,
         CASE WHEN json_extract_string(Cip013Clauses, '$.incident_notification') = 'true' THEN '' ELSE 'incident notification; ' END AS m2,
         CASE WHEN json_extract_string(Cip013Clauses, '$.remote_access_controls') = 'true' THEN '' ELSE 'remote-access controls; ' END AS m3,
         CASE WHEN json_extract_string(Cip013Clauses, '$.software_integrity') = 'true' THEN '' ELSE 'software integrity; ' END AS m4
  FROM ut_vendor
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
        compliance=dict(area="NERC CIP", reference="CIP-013-2 R1.2 · FERC Order No. 919", unit="vendors",
                        flag="Status = 'Missing CIP-013 clauses'", amount="AnnualContractValue"),
        notes="Contract clause flags come from vendor management (ut_vendor.Cip013Clauses)."),
    dict(
        id="UT-09", module="utilities", category="Utilities · Operations & AI",
        title="Incremental technology cost by storm event",
        question="What extra cloud cost did each major storm cause, ready for a storm-cost recovery filing?",
        personas=["regulatory_analyst", "finops_analyst", "cfo"],
        catalog_persona="Regulatory Accounting, Storm Recovery",
        description="Storm-tagged charges (storm_event tag) compared with the same application's average daily cost in the 14 "
                    "days before the event. The incremental amount is what a storm-recovery filing can claim; capturing it by "
                    "event ID avoids rebuilding it from invoices months later.",
        keywords=["storm", "storm cost recovery", "hurricane", "outage", "incremental cost", "securitization"],
        sql="""
WITH ev AS (SELECT * FROM ut_storm_event),
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
  WHERE c.ChargePeriodStart >= CAST(ev.StartDate AS TIMESTAMP) - INTERVAL '14' DAY
    AND c.ChargePeriodStart < CAST(ev.StartDate AS TIMESTAMP)
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
        compliance=dict(area="Operations & AI", reference="State storm-cost recovery dockets", unit="applications",
                        flag="IncrementalCost > 0", amount="IncrementalCost"),
        notes="Flagged rows are recoverable costs to include in the event's filing, not errors."),
    dict(
        id="UT-10", module="regulatory", category="Utilities · Operations & AI",
        title="Technology unit costs: per customer, per AMI meter, per grid study",
        question="What does technology O&M cost per customer, per smart meter and per grid study, by operating company?",
        personas=["regulatory_analyst", "cfo", "opco_finance"],
        catalog_persona="Regulatory Accounting, CFO",
        description="Allocated technology O&M (including deferred subscriptions) divided by each entity's volume drivers. These "
                    "are the affordability measures commissions and intervenors compare across utilities.",
        keywords=["unit cost", "cost per customer", "cost per meter", "ami", "affordability", "benchmark"],
        sql="""
WITH cost AS (
  SELECT PeriodStart, LegalEntity, SUM(AllocatedCost) AS ItCost
  FROM ut_allocated_cost
  WHERE CostClass IN ('om', 'deferral')
  GROUP BY PeriodStart, LegalEntity
)
SELECT
  strftime(c.PeriodStart, '%Y-%m') AS Month,
  c.LegalEntity,
  ROUND(c.ItCost, 2) AS TechnologyOAndM,
  v.Customers,
  v.AmiMeters,
  v.GridStudies,
  ROUND(c.ItCost * 12 / NULLIF(v.Customers, 0), 2) AS AnnualizedPerCustomer,
  ROUND(c.ItCost / NULLIF(v.AmiMeters, 0), 4) AS PerMeterMonth,
  ROUND(c.ItCost / NULLIF(v.GridStudies, 0), 2) AS PerGridStudy
FROM cost c
JOIN ut_volume v ON v.PeriodStart = c.PeriodStart AND v.LegalEntity = c.LegalEntity
ORDER BY Month, c.LegalEntity""",
        params=[],
        compliance=None,
        notes="October is a partial month (data through 22 Oct)."),
    dict(
        id="UT-11", module="utilities", category="Utilities · Operations & AI",
        title="AI used in grid-operations contexts without an approved review",
        question="Which AI applications used in grid operations are running without an approved review?",
        personas=["cip_compliance", "ai_program_owner", "tech_exec", "auditor"],
        catalog_persona="AI Governance, NERC CIP Compliance",
        description="Joins the AI use register (ut_ai_use) to AI spend. Grid-operations AI should stay out of control paths and "
                    "have an approved review with human sign-off for consequential actions; applications without one are "
                    "flagged with the spend they are already incurring.",
        keywords=["ai governance", "grid operations", "ot", "review", "saferai", "agent", "control room"],
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
  ROUND(COALESCE(s.AISpend, 0), 2) AS AISpend,
  CASE WHEN u.ReviewStatus <> 'Approved' THEN 'Grid-ops AI without approved review' ELSE 'Approved' END AS Status
FROM ut_ai_use u
LEFT JOIN spend s ON s.Application = u.Application
WHERE u.GridOpsContext
ORDER BY CASE WHEN u.ReviewStatus <> 'Approved' THEN 0 ELSE 1 END, AISpend DESC""",
        params=[],
        compliance=dict(area="Operations & AI", reference="EPRI SAFERai.power · CISA AI-in-OT principles (Dec 2025)",
                        unit="AI applications", flag="Status = 'Grid-ops AI without approved review'", amount="AISpend"),
        notes="The AI use register is maintained by the AI program office with the CIP team."),
]


def block(text, indent=2):
    return "|-\n" + textwrap.indent(textwrap.dedent(text).strip("\n"), " " * indent)


def q(s):
    return "'" + s.replace("'", "''") + "'"


def render(u):
    lines = [f"id: {u['id']}", "source: MM CostIQ Utilities extension", f"title: {q(u['title'])}",
             f"category: {q(u['category'])}", f"module: {u['module']}", f"business_question: {q(u['question'])}",
             "personas:", *[f"- {p}" for p in u["personas"]], f"catalog_persona: {q(u['catalog_persona'])}",
             "datasets:", *[f"- {q(d)}" for d in u.get("datasets", ["Cost and Usage", "Utility reference tables"])],
             f"description: {q(u['description'])}",
             f"columns: {q(u.get('columns', 'FOCUS columns plus ut_* reference tables and ut_allocated_cost'))}",
             "keywords:", *[f"- {q(k)}" for k in u["keywords"]], "products: Any provider", f"sql: {block(u['sql'])}"]
    if u["params"]:
        lines.append("params:")
        for name, typ, default, label in u["params"]:
            lines += [f"- name: {name}", f"  type: {typ}", f"  default: {q(default)}", f"  label: {q(label)}"]
    else:
        lines.append("params: []")
    lines.append(f"notes: {q(u['notes'])}")
    if u["compliance"]:
        c = u["compliance"]
        lines += ["compliance:", f"  area: {q(c['area'])}", f"  reference: {q(c['reference'])}", f"  unit: {q(c['unit'])}",
                  f"  flag: {q(c['flag'])}", f"  amount: {c['amount']}"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    for u in USE_CASES:
        with open(os.path.join(OUT, f"{u['id']}.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(u))
        print("wrote", u["id"])
