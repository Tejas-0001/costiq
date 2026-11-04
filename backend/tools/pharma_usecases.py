"""Write registry/usecases/PH-01..11.yaml: the pharma use cases (tax and transfer pricing, R&D credit, GxP and patient
data, clinical operations and AI). Run from mvp/backend: python tools/pharma_usecases.py

Kept as code so the entries share one structure; the YAML files are what the platform loads.
"""
from __future__ import annotations
import os, textwrap

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "registry", "usecases")
PERIOD = [("period_start", "date", "2026-07-01", "Start of the period (inclusive)"),
          ("period_end", "date", "2026-11-01", "End of the period (exclusive)")]
FILING = [("filing", "string", "US-1120-FY2026", "Tax filing")]

USE_CASES = [
    dict(
        id="PH-01", module="tax", category="Pharma · Tax & accounting",
        title="Technology spend by GL account and cost class",
        question="Does every allocated technology dollar land in a GL account, and how much is capital, opex or qualified research?",
        personas=["tax_analyst", "finance_controller", "auditor", "cfo"],
        catalog_persona="Tax, Controller",
        description="Summarizes the allocation chain's output by legal entity, GL account and cost class. Opex is mapped through "
                    "ref_gl_account_map; capitalized implementation (ASC 350-40) and claimed qualifying research (qre) book by "
                    "rule. Dollars with no account are flagged: they need a manual entry before close and filing.",
        keywords=["gl account", "chart of accounts", "cost class", "capital", "opex", "unmapped", "r&d", "sg&a"],
        sql="""
SELECT
  LegalEntity,
  COALESCE(GlAccount, '(unmapped)') AS GlAccount,
  COALESCE(MAX(GlTitle), 'No GL account mapped') AS GlTitle,
  CostClass,
  COUNT(DISTINCT ChargeKey) AS ChargeLines,
  ROUND(SUM(AllocatedCost), 2) AS AllocatedCost,
  CASE WHEN GlAccount IS NULL THEN 'Unmapped' ELSE 'Mapped' END AS Status
FROM allocated_cost
WHERE PeriodStart >= :period_start AND PeriodStart < :period_end
GROUP BY LegalEntity, GlAccount, CostClass
ORDER BY CASE WHEN GlAccount IS NULL THEN 0 ELSE 1 END, AllocatedCost DESC""",
        params=PERIOD,
        compliance=dict(area="Tax & accounting", reference="ASC 350-40 · group chart of accounts",
                        unit="account lines", flag="Status = 'Unmapped'", amount="AllocatedCost"),
        notes="The GL map is illustrative and owned by controllership. Capital books to 1750 and claimed qualifying research "
              "to 6150 by rule, before the map is consulted."),
    dict(
        id="PH-02", module="tax", category="Pharma · Transfer pricing",
        title="Intercompany recharges by entity and allocation key",
        question="What did Global Services recharge to each entity, and on which allocation key?",
        personas=["transfer_pricing", "tax_analyst", "finance_controller", "auditor"],
        catalog_persona="Transfer Pricing, Tax",
        description="A transfer-pricing local-file view of technology costs: amounts recharged to each entity by allocation "
                    "policy and key (enrolled patients, trial sites, active studies, headcount, workstations). Amounts with no "
                    "approved policy are flagged.",
        keywords=["transfer pricing", "recharge", "intercompany", "local file", "allocation key", "intra-group services"],
        sql="""
SELECT
  LegalEntity AS RecipientEntity,
  COALESCE(OpcoPolicy, '(none)') AS Policy,
  COALESCE(DriverName, 'Direct (100% to one entity)') AS AllocationKey,
  COUNT(DISTINCT ChargeKey) AS ChargeLines,
  ROUND(SUM(AllocatedCost), 2) AS AmountRecharged,
  CASE WHEN OpcoPolicy IS NULL THEN 'No approved policy' ELSE 'Approved policy' END AS Status
FROM allocated_cost
WHERE PeriodStart >= :period_start AND PeriodStart < :period_end
GROUP BY LegalEntity, OpcoPolicy, DriverName
ORDER BY RecipientEntity, AmountRecharged DESC""",
        params=PERIOD,
        compliance=dict(area="Transfer pricing", reference="OECD Transfer Pricing Guidelines, Chapter VII (intra-group services)",
                        unit="recharge lines", flag="Status = 'No approved policy'", amount="AmountRecharged"),
        notes="Covers technology costs allocated through CostIQ only."),
    dict(
        id="PH-03", module="tax", category="Pharma · Transfer pricing",
        title="Cross-charging: GxP entities carrying Consumer Health work",
        question="Are pharma entities paying for work that belongs to Consumer Health?",
        personas=["transfer_pricing", "tax_analyst", "auditor", "cfo"],
        catalog_persona="Transfer Pricing, Internal Audit",
        description="Finds allocated cost from cost centers owned by Consumer Health (ref_cost_center) that landed on a GxP "
                    "entity. The usual cause is a broad shared-platform policy that runs before the affiliate's own "
                    "direct-assignment policy.",
        keywords=["cross charging", "consumer health", "affiliate", "benefit test", "entity wall", "arm's length"],
        sql="""
SELECT
  a.CostCenter,
  c.OwningEntity,
  a.LegalEntity AS ChargedToEntity,
  a.OpcoPolicy,
  a.ServiceName,
  COUNT(DISTINCT a.ChargeKey) AS ChargeLines,
  ROUND(SUM(a.AllocatedCost), 2) AS CrossCharge
FROM allocated_cost a
JOIN ref_cost_center c ON c.CostCenter = a.CostCenter
WHERE NOT c.Regulated AND a.Regulated
  AND a.PeriodStart >= :period_start AND a.PeriodStart < :period_end
GROUP BY a.CostCenter, c.OwningEntity, a.LegalEntity, a.OpcoPolicy, a.ServiceName
ORDER BY CrossCharge DESC""",
        params=PERIOD,
        compliance=dict(area="Transfer pricing", reference="OECD TPG Chapter VII benefit test · arm's-length principle",
                        unit="allocations", flag="CrossCharge > 0", amount="CrossCharge"),
        notes="Fix by giving the affiliate's direct-assignment policy a lower priority number than shared-platform policies."),
    dict(
        id="PH-04", module="tax", category="Pharma · R&D credit",
        title="Cloud fees eligible as qualifying R&D expenditure",
        question="Which recurring cloud and SaaS fees qualify for an R&D credit, by jurisdiction, and which are not yet claimed?",
        personas=["tax_analyst", "cfo", "finance_controller"],
        catalog_persona="Tax, CFO",
        description="Per jurisdiction and vendor: recurring fees already claimed as qualifying research expenditure (qre) and "
                    "fees still expensed where they qualify but no claim is made.",
        keywords=["r&d credit", "qualifying expenditure", "qre", "cloud computing", "research credit", "section 41", "rdec"],
        sql="""
SELECT
  a.Jurisdiction,
  p.CreditStatus,
  a.ServiceProviderName AS Vendor,
  ROUND(SUM(a.AllocatedCost), 2) AS SubscriptionFees,
  ROUND(SUM(CASE WHEN a.CostClass = 'qre' THEN a.AllocatedCost ELSE 0 END), 2) AS Claimed,
  ROUND(SUM(CASE WHEN a.CostClass = 'opex' THEN a.AllocatedCost ELSE 0 END), 2) AS NotClaimed,
  MAX(p.Reference) AS Reference,
  CASE WHEN p.CreditStatus = 'claimed' THEN 'Claimed'
       WHEN p.CreditStatus = 'eligible' THEN 'Eligible: not yet claimed'
       WHEN p.CreditStatus = 'ineligible' THEN 'Not eligible'
       ELSE 'Not applicable' END AS Opportunity
FROM allocated_cost a
JOIN ref_jurisdiction_policy p ON p.Jurisdiction = a.Jurisdiction
WHERE a.ChargeCategory = 'Purchase' AND a.ChargeFrequency = 'Recurring' AND a.CostClass IN ('opex', 'qre')
  AND a.PeriodStart >= :period_start AND a.PeriodStart < :period_end
GROUP BY a.Jurisdiction, p.CreditStatus, a.ServiceProviderName
ORDER BY NotClaimed DESC, Claimed DESC""",
        params=PERIOD,
        compliance=dict(area="R&D credit", reference="IRC §41 · UK R&D relief (data and cloud costs from April 2023)",
                        unit="jurisdiction-vendor pairs", flag="Opportunity = 'Eligible: not yet claimed'", amount="NotClaimed"),
        notes="Credit rules are illustrative; the tax team owns ref_jurisdiction_policy."),
    dict(
        id="PH-05", module="tax", category="Pharma · Tax & accounting",
        title="Forecast bridge: review period to forecast year by vendor",
        question="How does each vendor's cloud, SaaS and AI cost move from the review period to the forecast year, and does it tie to invoices?",
        personas=["tax_analyst", "cfo", "finance_controller"],
        catalog_persona="Tax, CFO",
        description="Review-period actuals for the filing's entity and jurisdiction, annualized, then carried to the forecast year "
                    "with renewal escalators. Each vendor's review period is tied to its invoices; an invoice exception is "
                    "flagged because it weakens the substantiation.",
        keywords=["forecast year", "review period", "bridge", "subscription costs", "vendor", "invoice", "annualized"],
        sql="""
WITH rc AS (SELECT * FROM ref_tax_filing WHERE FilingId = :filing),
base AS (
  SELECT a.ServiceProviderName AS Vendor, SUM(a.AllocatedCost) AS BaseActual
  FROM allocated_cost a JOIN rc ON a.LegalEntity = rc.LegalEntity AND a.Jurisdiction = rc.Jurisdiction
  WHERE a.CostClass IN ('opex', 'qre') AND a.PeriodStart >= rc.ReviewPeriodStart AND a.PeriodStart < rc.ReviewPeriodEnd
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
       ELSE 'Review period annualized' END AS Reason,
  CASE WHEN Invoiced IS NULL THEN 'Billed via another issuer'
       WHEN ABS(Invoiced - COALESCE(Charged, 0)) <= 1 THEN 'Tied to invoices'
       ELSE 'Invoice exception' END AS InvoiceTieOut,
  ROUND(COALESCE(Invoiced, 0) - COALESCE(Charged, 0), 2) AS InvoiceDifference
FROM fx
ORDER BY ReviewAnnualized DESC""",
        params=FILING,
        compliance=dict(area="Tax & accounting", reference="Filing substantiation · invoice tie-out",
                        unit="vendors", flag="InvoiceTieOut = 'Invoice exception'", amount="ReviewPeriodActual"),
        notes="Invoice tie-out compares the vendor's invoices with all of its charges in the review period (every entity), "
              "so one exception can affect several filings."),
    dict(
        id="PH-06", module="tax", category="Pharma · Tax & accounting",
        title="Renewals and price escalators landing in the forecast year",
        question="Which contract renewals and escalators fall inside the forecast year?",
        personas=["tax_analyst", "finops_analyst", "fpa_analyst", "cfo"],
        catalog_persona="FP&A, Vendor Management",
        description="Vendors whose renewal date falls inside the filing's forecast year, with the escalator and its annual dollar "
                    "effect. Missing these understates the forecast; renegotiating them before renewal is the lever.",
        keywords=["renewal", "escalator", "price increase", "forecast year", "contract"],
        sql="""
SELECT
  rc.FilingId,
  v.Vendor,
  CAST(v.RenewalDate AS DATE) AS RenewalDate,
  v.EscalatorPct,
  ROUND(v.AnnualContractValue, 2) AS AnnualContractValue,
  ROUND(v.AnnualContractValue * v.EscalatorPct / 100, 2) AS AnnualEscalation,
  CASE WHEN v.RenewalDate >= rc.ForecastYearStart AND v.RenewalDate < rc.ForecastYearEnd THEN 'In forecast year'
       WHEN v.RenewalDate < rc.ForecastYearStart THEN 'Before forecast year'
       ELSE 'After forecast year' END AS Timing
FROM ref_vendor v
CROSS JOIN ref_tax_filing rc
WHERE rc.FilingId = :filing AND v.EscalatorPct > 0
ORDER BY v.RenewalDate""",
        params=FILING,
        compliance=dict(area="Forecast", reference="Budget forecast: contract escalators",
                        unit="contracts", flag="Timing = 'In forecast year'", amount="AnnualEscalation"),
        notes="Annual contract values come from ref_vendor (vendor management); escalators apply from the renewal date."),
    dict(
        id="PH-07", module="compliance", category="Pharma · GxP & patient data",
        title="Patient data in cloud services without customer-managed keys",
        question="Which cloud workloads hold patient data where the provider could decrypt it?",
        personas=["gxp_compliance", "auditor", "tech_exec"],
        catalog_persona="GxP Quality & Data Privacy",
        description="Joins the GxP and patient-data register (ref_gxp_system) to cloud spend. Workloads that hold patient data "
                    "with provider-managed keys are flagged.",
        keywords=["patient data", "phi", "hipaa", "gdpr", "encryption", "key management", "customer managed keys"],
        sql="""
WITH spend AS (
  SELECT json_extract_string(Tags, '$.application') AS Application, SUM(EffectiveCost) AS EffectiveCost
  FROM CostAndUsage
  WHERE ChargePeriodStart >= :period_start AND ChargePeriodStart < :period_end
  GROUP BY json_extract_string(Tags, '$.application')
),
reg AS (
  SELECT s.*, CASE WHEN s.HoldsPatientData AND s.KeyOwnership <> 'customer' THEN 'Provider can decrypt patient data'
                   WHEN s.HoldsPatientData THEN 'Patient data with customer-managed keys'
                   ELSE 'No patient data' END AS Status
  FROM ref_gxp_system s
)
SELECT
  r.Application, r.SystemName, r.GxpImpact, r.HoldsPatientData, r.KeyOwnership, r.Owner, r.Status,
  ROUND(COALESCE(x.EffectiveCost, 0), 2) AS EffectiveCost
FROM reg r
LEFT JOIN spend x ON x.Application = r.Application
ORDER BY CASE WHEN r.Status = 'Provider can decrypt patient data' THEN 0 ELSE 1 END, EffectiveCost DESC""",
        params=PERIOD,
        compliance=dict(area="GxP & patient data", reference="HIPAA 45 CFR 164.312 · GDPR Art. 32", unit="workloads",
                        flag="Status = 'Provider can decrypt patient data'", amount="EffectiveCost"),
        notes="The register is maintained by Data Privacy with GxP Quality. CostIQ supplies the spend; it does not determine "
              "compliance."),
    dict(
        id="PH-08", module="compliance", category="Pharma · GxP & patient data",
        title="GxP vendors missing quality-agreement clauses",
        question="Which vendors supporting GxP systems lack required quality-agreement clauses?",
        personas=["gxp_compliance", "auditor", "tech_exec"],
        catalog_persona="GxP Quality, Vendor Management",
        description="Checks each GxP-scoped vendor's contract for a signed quality agreement, audit rights, change notification "
                    "and data-integrity commitments (21 CFR Part 11, EU Annex 11).",
        keywords=["gxp", "quality agreement", "part 11", "annex 11", "vendor qualification", "data integrity", "audit rights"],
        sql="""
WITH c AS (
  SELECT Vendor, GxpScoped, AnnualContractValue,
         CASE WHEN json_extract_string(QualityClauses, '$.quality_agreement') = 'true' THEN '' ELSE 'quality agreement; ' END AS m1,
         CASE WHEN json_extract_string(QualityClauses, '$.audit_rights') = 'true' THEN '' ELSE 'audit rights; ' END AS m2,
         CASE WHEN json_extract_string(QualityClauses, '$.change_notification') = 'true' THEN '' ELSE 'change notification; ' END AS m3,
         CASE WHEN json_extract_string(QualityClauses, '$.data_integrity') = 'true' THEN '' ELSE 'data integrity; ' END AS m4
  FROM ref_vendor
)
SELECT
  Vendor,
  GxpScoped,
  CONCAT(m1, m2, m3, m4) AS MissingClauses,
  ROUND(AnnualContractValue, 2) AS AnnualContractValue,
  CASE WHEN GxpScoped AND CONCAT(m1, m2, m3, m4) <> '' THEN 'Missing quality clauses'
       WHEN GxpScoped THEN 'Clauses in place'
       ELSE 'Not GxP-scoped' END AS Status
FROM c
ORDER BY CASE WHEN GxpScoped AND CONCAT(m1, m2, m3, m4) <> '' THEN 0 ELSE 1 END, AnnualContractValue DESC""",
        params=[],
        compliance=dict(area="GxP & patient data", reference="21 CFR Part 11 · EU GMP Annex 11 · ICH Q10", unit="vendors",
                        flag="Status = 'Missing quality clauses'", amount="AnnualContractValue"),
        notes="Clause flags come from vendor management (ref_vendor.QualityClauses)."),
    dict(
        id="PH-09", module="compliance", category="Pharma · Clinical operations & AI",
        title="Incremental technology cost by study event",
        question="What extra cloud cost did each study event (database lock, interim analysis) cause?",
        personas=["tax_analyst", "finops_analyst", "cfo"],
        catalog_persona="Clinical Operations, FinOps",
        description="Charges tagged with a study_event compared with the same application's average daily cost in the 14 days "
                    "before the event. Capturing the increment by event lets it be charged to the study budget.",
        keywords=["study event", "database lock", "clinical trial", "incremental cost", "study budget", "surge"],
        sql="""
WITH ev AS (SELECT * FROM ref_study_event),
evt AS (
  SELECT json_extract_string(Tags, '$.study_event') AS StudyEventId, json_extract_string(Tags, '$.application') AS Application,
         COUNT(DISTINCT CAST(ChargePeriodStart AS DATE)) AS Days, SUM(EffectiveCost) AS Cost
  FROM CostAndUsage
  WHERE json_extract_string(Tags, '$.study_event') IS NOT NULL
  GROUP BY json_extract_string(Tags, '$.study_event'), json_extract_string(Tags, '$.application')
),
pre AS (
  SELECT ev.StudyEventId, json_extract_string(c.Tags, '$.application') AS Application, SUM(c.EffectiveCost) / 14 AS DailyBaseline
  FROM CostAndUsage c CROSS JOIN ev
  WHERE c.ChargePeriodStart >= CAST(ev.StartDate AS TIMESTAMP) - INTERVAL '14' DAY
    AND c.ChargePeriodStart < CAST(ev.StartDate AS TIMESTAMP)
  GROUP BY ev.StudyEventId, json_extract_string(c.Tags, '$.application')
)
SELECT
  s.StudyEventId, ev.Name, ev.Jurisdictions, s.Application, s.Days,
  ROUND(s.Cost, 2) AS EventPeriodCost,
  ROUND(COALESCE(p.DailyBaseline, 0) * s.Days, 2) AS BaselineCost,
  ROUND(s.Cost - COALESCE(p.DailyBaseline, 0) * s.Days, 2) AS IncrementalCost
FROM evt s
JOIN ev ON ev.StudyEventId = s.StudyEventId
LEFT JOIN pre p ON p.StudyEventId = s.StudyEventId AND p.Application = s.Application
ORDER BY IncrementalCost DESC""",
        params=[],
        compliance=dict(area="Clinical operations & AI", reference="Study budget tracking", unit="applications",
                        flag="IncrementalCost > 0", amount="IncrementalCost"),
        notes="Flagged rows are costs to charge to the study, not errors."),
    dict(
        id="PH-10", module="tax", category="Pharma · Clinical operations & AI",
        title="Technology unit costs: per enrolled patient, per trial site, per active study",
        question="What does technology cost per enrolled patient, per trial site and per active study, by entity?",
        personas=["tax_analyst", "cfo"],
        catalog_persona="CFO, Clinical Operations",
        description="Allocated technology opex (including claimed qualifying research) divided by each entity's volume drivers.",
        keywords=["unit cost", "cost per patient", "cost per site", "cost per study", "clinical trial", "benchmark"],
        sql="""
WITH cost AS (
  SELECT PeriodStart, LegalEntity, SUM(AllocatedCost) AS ItCost
  FROM allocated_cost
  WHERE CostClass IN ('opex', 'qre')
  GROUP BY PeriodStart, LegalEntity
)
SELECT
  strftime(c.PeriodStart, '%Y-%m') AS Month,
  c.LegalEntity,
  ROUND(c.ItCost, 2) AS TechnologyOpex,
  v.EnrolledPatients,
  v.TrialSites,
  v.ActiveStudies,
  ROUND(c.ItCost * 12 / NULLIF(v.EnrolledPatients, 0), 2) AS AnnualizedPerPatient,
  ROUND(c.ItCost / NULLIF(v.TrialSites, 0), 2) AS PerSiteMonth,
  ROUND(c.ItCost / NULLIF(v.ActiveStudies, 0), 2) AS PerActiveStudy
FROM cost c
JOIN ref_volume v ON v.PeriodStart = c.PeriodStart AND v.LegalEntity = c.LegalEntity
ORDER BY Month, c.LegalEntity""",
        params=[],
        compliance=None,
        notes="October is a partial month (data through 22 Oct)."),
    dict(
        id="PH-11", module="compliance", category="Pharma · Clinical operations & AI",
        title="AI used in GxP contexts without an approved validation review",
        question="Which AI applications used in GxP processes are running without an approved validation review?",
        personas=["gxp_compliance", "ai_program_owner", "tech_exec", "auditor"],
        catalog_persona="AI Governance, GxP Quality",
        description="Joins the AI use register (ref_ai_use) to AI spend. AI that supports GxP work (clinical, safety, quality) "
                    "needs a risk-based validation review with human oversight; applications without one are flagged with the "
                    "spend they already incur.",
        keywords=["ai governance", "gxp", "validation", "csv", "pharmacovigilance", "agent", "human oversight"],
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
  CASE WHEN u.ReviewStatus <> 'Approved' THEN 'GxP AI without approved review' ELSE 'Approved' END AS Status
FROM ref_ai_use u
LEFT JOIN spend s ON s.Application = u.Application
WHERE u.GxpContext
ORDER BY CASE WHEN u.ReviewStatus <> 'Approved' THEN 0 ELSE 1 END, AISpend DESC""",
        params=[],
        compliance=dict(area="Clinical operations & AI", reference="FDA draft guidance on AI for regulatory decision-making (2025) · EU GMP Annex 22 (draft)",
                        unit="AI applications", flag="Status = 'GxP AI without approved review'", amount="AISpend"),
        notes="The AI use register is maintained by the AI program office with GxP Quality."),
]


def block(text, indent=2):
    return "|-\n" + textwrap.indent(textwrap.dedent(text).strip("\n"), " " * indent)


def q(s):
    return "'" + s.replace("'", "''") + "'"


def render(u):
    lines = [f"id: {u['id']}", "source: MM CostIQ Pharma extension", f"title: {q(u['title'])}",
             f"category: {q(u['category'])}", f"module: {u['module']}", f"business_question: {q(u['question'])}",
             "personas:", *[f"- {p}" for p in u["personas"]], f"catalog_persona: {q(u['catalog_persona'])}",
             "datasets:", *[f"- {q(d)}" for d in u.get("datasets", ["Cost and Usage", "Pharma reference tables"])],
             f"description: {q(u['description'])}",
             f"columns: {q(u.get('columns', 'FOCUS columns plus ref_* reference tables and allocated_cost'))}",
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
