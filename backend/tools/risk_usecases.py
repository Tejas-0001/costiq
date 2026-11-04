"""Write registry/usecases/PH-12..14.yaml: the tax-risk catalog entries (deduction at risk, credit unlock, evidence
completeness). Run from mvp/backend: python tools/risk_usecases.py
"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(__file__))
from pharma_usecases import OUT, render  # noqa: E402

USE_CASES = [
    dict(
        id="PH-12", module="risk", category="Pharma · Tax risk",
        title="Deduction at risk by filing, reason and vendor",
        question="How much review-period technology spend could a tax examiner disallow, and why?",
        personas=["tax_analyst", "cfo", "auditor"],
        catalog_persona="Tax, CFO",
        description="Recharged spend in each filing's review period that fails an evidence test: no benefit shown (idle "
                    "resources, unused commitments, unused seats) unless remediated, not reconciled to an invoice, allocated "
                    "without an approved policy, missing a GL account, or carrying a savings opportunity left unactioned for "
                    "60+ days.",
        keywords=["deduction at risk", "disallowed deduction", "benefit test", "tax risk", "substantiation", "evidence gaps"],
        sql="""
SELECT
  FilingId, LegalEntity, RiskReason, ServiceProviderName AS Vendor,
  COUNT(*) AS Lines,
  ROUND(SUM(AllocatedCost), 2) AS ReviewPeriodSpend,
  ROUND(SUM(RiskAmount), 2) AS DeductionAtRisk
FROM filing_evidence
WHERE RiskAmount > 0
GROUP BY FilingId, LegalEntity, RiskReason, ServiceProviderName
ORDER BY DeductionAtRisk DESC""",
        params=[], compliance=None,
        notes="Decision support, not a prediction of any tax authority's ruling. Recomputed after allocation, ingestion and "
              "every policy or optimization decision.",
        datasets=["Filing evidence (computed)", "Pharma reference tables"]),
    dict(
        id="PH-13", module="tax", category="Pharma · R&D credit",
        title="Credit unlock: R&D credit on cloud fees not yet claimed",
        question="What R&D credit could recurring cloud and SaaS fees earn, by jurisdiction?",
        personas=["tax_analyst", "cfo"], catalog_persona="Tax, CFO",
        description="Annualized review-period recurring fees x the jurisdiction's credit rate. Claimed where the group already "
                    "claims them; unlock where they qualify but are not yet claimed.",
        keywords=["credit unlock", "r&d credit", "qualifying expenditure", "research credit", "cloud computing", "cash tax"],
        sql="""
WITH bp AS (SELECT MIN(ReviewPeriodStart) AS b0, MAX(ReviewPeriodEnd) AS b1 FROM ref_tax_filing),
fees AS (
  SELECT a.Jurisdiction, a.LegalEntity, a.ServiceProviderName AS Vendor,
         COUNT(DISTINCT a.PeriodStart) AS Months, SUM(a.AllocatedCost) AS Fees
  FROM allocated_cost a CROSS JOIN bp
  WHERE a.ChargeCategory = 'Purchase' AND a.ChargeFrequency = 'Recurring' AND a.CostClass IN ('opex', 'qre')
    AND a.PeriodStart >= bp.b0 AND a.PeriodStart < bp.b1
  GROUP BY a.Jurisdiction, a.LegalEntity, a.ServiceProviderName
),
avg_rate AS (SELECT AVG(CreditRate) AS r FROM ref_tax_filing),
x AS (
  SELECT f.*, p.CreditStatus,
         COALESCE(rj.CreditRate, re.CreditRate, (SELECT r FROM avg_rate)) AS CreditRate,
         COALESCE(rj.FilingId, re.FilingId) AS FilingId
  FROM fees f
  JOIN ref_jurisdiction_policy p ON p.Jurisdiction = f.Jurisdiction
  LEFT JOIN ref_tax_filing rj ON rj.Jurisdiction = f.Jurisdiction
  LEFT JOIN ref_tax_filing re ON re.LegalEntity = f.LegalEntity AND rj.FilingId IS NULL
  WHERE p.CreditStatus <> 'not_applicable'
)
SELECT
  Jurisdiction, CreditStatus, FilingId,
  ROUND(MAX(CreditRate), 2) AS CreditRate,
  ROUND(SUM(Fees * 12 / Months), 2) AS AnnualQualifyingFees,
  ROUND(SUM(Fees * 12 / Months) * MAX(CreditRate) / 100, 2) AS Credit,
  CASE WHEN CreditStatus = 'claimed' THEN 'Claimed'
       WHEN CreditStatus = 'eligible' THEN 'Unlock: claim it'
       ELSE 'Not eligible' END AS Status
FROM x
GROUP BY Jurisdiction, CreditStatus, FilingId
ORDER BY Credit DESC""",
        params=[], compliance=None,
        notes="Illustrative: depends on each jurisdiction's qualifying rules and on substantiation; jurisdictions without a "
              "filing use their entity's filing rate, else the average of filings.",
        datasets=["Pharma reference tables", "Allocated cost (computed)"]),
    dict(
        id="PH-14", module="risk", category="Pharma · Tax risk",
        title="Evidence completeness by filing",
        question="For each tax filing, how complete is the evidence behind its recharged technology spend?",
        personas=["tax_analyst", "cfo", "auditor"], catalog_persona="Tax, Internal Audit",
        description="Share of each filing's review-period spend that is reconciled, allocated under approved policy, mapped to a "
                    "GL account, shows a benefit (or is remediated) and is free of unactioned savings, with the cost-weighted "
                    "evidence score and the deduction at risk.",
        keywords=["evidence", "completeness", "evidence score", "filing", "readiness", "audit readiness"],
        sql="""
SELECT
  e.FilingId, r.LegalEntity, r.Jurisdiction, r.Status AS FilingStatus,
  COUNT(*) AS Lines,
  ROUND(SUM(e.AllocatedCost), 2) AS ReviewPeriodSpend,
  ROUND(100 * SUM(CASE WHEN e.Reconciled THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS ReconciledPct,
  ROUND(100 * SUM(CASE WHEN e.PolicyApproved THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS PolicyApprovedPct,
  ROUND(100 * SUM(CASE WHEN e.GlMapped THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS GlMappedPct,
  ROUND(100 * SUM(CASE WHEN e.BenefitShown OR e.Remediated THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS BenefitShownPct,
  ROUND(100 * SUM(CASE WHEN e.SavingsActioned OR e.Remediated THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS SavingsActionedPct,
  ROUND(100 * SUM(e.AllocatedCost * e.EvidenceScore) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS EvidenceScore,
  ROUND(SUM(e.RiskAmount), 2) AS DeductionAtRisk,
  CASE WHEN SUM(e.RiskAmount) > 0 THEN 'Evidence gaps' ELSE 'Complete' END AS Status
FROM filing_evidence e
JOIN ref_tax_filing r ON r.FilingId = e.FilingId
GROUP BY e.FilingId, r.LegalEntity, r.Jurisdiction, r.Status
ORDER BY DeductionAtRisk DESC""",
        params=[],
        compliance=dict(area="Tax risk", reference="OECD TPG benefit test · filing substantiation", unit="filings",
                        flag="Status = 'Evidence gaps'", amount="DeductionAtRisk"),
        notes="Weights: reconciled 25%, approved policy 15%, GL account 15%, benefit shown 30%, savings actioned 15%.",
        datasets=["Filing evidence (computed)", "Pharma reference tables"]),
]

if __name__ == "__main__":
    for u in USE_CASES:
        with open(os.path.join(OUT, f"{u['id']}.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(u))
        print("wrote", u["id"])
