"""Write registry/usecases/UT-12..14.yaml: the Prudence Engine's catalog entries (disallowance risk, Earnings Unlock,
evidence completeness). Run from mvp/backend: python tools/prudence_usecases.py
"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(__file__))
from utility_usecases import OUT, render  # noqa: E402

USE_CASES = [
    dict(
        id="UT-12", module="prudence", category="Utilities · Prudence",
        title="Disallowance risk by docket, reason and vendor",
        question="How much base-period technology spend could a commission challenge, and why?",
        personas=["regulatory_analyst", "cfo", "auditor", "opco_finance"],
        catalog_persona="Regulatory Accounting & Rates, CFO",
        description="Spend in each docket's base period that fails a prudence test: not used and useful (idle resources, unused "
                    "commitments, unused seats) unless remediated, not reconciled to an invoice, allocated without an approved "
                    "policy, missing a FERC account, or carrying a savings opportunity left unactioned for 60+ days.",
        keywords=["disallowance", "prudence", "used and useful", "imprudent", "rate case risk", "evidence gaps"],
        sql="""
SELECT
  Docket, LegalEntity, RiskReason, ServiceProviderName AS Vendor,
  COUNT(*) AS Lines,
  ROUND(SUM(AllocatedCost), 2) AS BasePeriodSpend,
  ROUND(SUM(RiskAmount), 2) AS DisallowanceRisk
FROM prudence_evidence
WHERE RiskAmount > 0
GROUP BY Docket, LegalEntity, RiskReason, ServiceProviderName
ORDER BY DisallowanceRisk DESC""",
        params=[], compliance=None,
        notes="Decision support, not a prediction of any commission's ruling. Recomputed after allocation, ingestion and every "
              "policy or optimization decision.",
        datasets=["Prudence evidence (computed)", "Utility reference tables"]),
    dict(
        id="UT-13", module="regulatory", category="Utilities · Prudence",
        title="Earnings Unlock: carrying return on deferred cloud subscription fees",
        question="What return could cloud subscription fees earn as a regulatory asset, by state?",
        personas=["regulatory_analyst", "cfo"], catalog_persona="Regulatory Accounting, CFO",
        description="Cloud subscriptions are O&M and earn nothing, which biases utilities toward on-premise software. Where a "
                    "commission allows deferral to a regulatory asset (Idaho PUC Order No. 34707 pattern), the unamortized "
                    "balance earns the authorized return. Annualized base-period fees, amortized over the contract's remaining "
                    "term (62 months without one); steady-state return = monthly deferral x (N + 1) / 2 x pre-tax return.",
        keywords=["earnings unlock", "regulatory asset", "deferral", "return", "capex bias", "rate base", "earnings"],
        sql="""
WITH bp AS (SELECT MIN(BasePeriodStart) AS b0, MAX(BasePeriodEnd) AS b1 FROM ut_rate_case),
fees AS (
  SELECT a.Jurisdiction, a.LegalEntity, a.ServiceProviderName AS Vendor,
         COUNT(DISTINCT a.PeriodStart) AS Months, SUM(a.AllocatedCost) AS Fees
  FROM ut_allocated_cost a CROSS JOIN bp
  WHERE a.ChargeCategory = 'Purchase' AND a.ChargeFrequency = 'Recurring' AND a.CostClass IN ('om', 'deferral')
    AND a.PeriodStart >= bp.b0 AND a.PeriodStart < bp.b1
  GROUP BY a.Jurisdiction, a.LegalEntity, a.ServiceProviderName
),
avg_ror AS (SELECT AVG(PreTaxRor) AS r FROM ut_rate_case WHERE Jurisdiction <> 'FERC'),
x AS (
  SELECT f.*, p.DeferralStatus, p.AmortizationMonths, v.ContractEnd,
         COALESCE(rj.TestYearStart, re.TestYearStart, TIMESTAMP '2027-01-01') AS TestYearStart,
         COALESCE(rj.PreTaxRor, re.PreTaxRor, (SELECT r FROM avg_ror)) AS PreTaxRor,
         COALESCE(rj.Docket, re.Docket) AS Docket
  FROM fees f
  JOIN ut_jurisdiction_policy p ON p.Jurisdiction = f.Jurisdiction
  LEFT JOIN ut_rate_case rj ON rj.Jurisdiction = f.Jurisdiction
  LEFT JOIN ut_rate_case re ON re.LegalEntity = f.LegalEntity AND rj.Docket IS NULL
  LEFT JOIN ut_vendor v ON v.Vendor = f.Vendor
  WHERE p.DeferralStatus <> 'not_applicable'
),
n AS (
  SELECT x.*, x.Fees * 12 / x.Months AS AnnualFees,
         CASE WHEN x.ContractEnd > x.TestYearStart
              THEN GREATEST(12, (EXTRACT(YEAR FROM x.ContractEnd) - EXTRACT(YEAR FROM x.TestYearStart)) * 12
                                + EXTRACT(MONTH FROM x.ContractEnd) - EXTRACT(MONTH FROM x.TestYearStart))
              ELSE x.AmortizationMonths END AS N
  FROM x
)
SELECT
  Jurisdiction, DeferralStatus, Docket,
  ROUND(MAX(PreTaxRor), 2) AS PreTaxRor,
  ROUND(SUM(AnnualFees), 2) AS AnnualSubscriptionFees,
  ROUND(SUM(AnnualFees / 12 * (N + 1) / 2) * MAX(PreTaxRor) / 100, 2) AS SteadyStateReturn,
  CASE WHEN DeferralStatus = 'allowed' THEN 'Earned under order' ELSE 'Unlock: request deferral' END AS Status
FROM n
GROUP BY Jurisdiction, DeferralStatus, Docket
ORDER BY SteadyStateReturn DESC""",
        params=[], compliance=None,
        notes="Illustrative: depends on the commission granting deferral and on the authorized return; states without a docket "
              "use the average of the open dockets' pre-tax return.",
        datasets=["Utility reference tables", "Allocated cost (computed)"]),
    dict(
        id="UT-14", module="prudence", category="Utilities · Prudence",
        title="Evidence completeness by docket",
        question="For each rate case, how complete is the evidence behind its technology spend?",
        personas=["regulatory_analyst", "cfo", "auditor"], catalog_persona="Regulatory Accounting & Rates, Internal Audit",
        description="Share of each docket's base-period spend that is reconciled, allocated under approved policy, mapped to a "
                    "FERC account, used and useful (or remediated) and free of unactioned savings, with the cost-weighted "
                    "prudence score and the disallowance risk.",
        keywords=["evidence", "completeness", "prudence score", "docket", "readiness", "filing readiness"],
        sql="""
SELECT
  e.Docket, r.LegalEntity, r.Jurisdiction, r.Status AS DocketStatus,
  COUNT(*) AS Lines,
  ROUND(SUM(e.AllocatedCost), 2) AS BasePeriodSpend,
  ROUND(100 * SUM(CASE WHEN e.Reconciled THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS ReconciledPct,
  ROUND(100 * SUM(CASE WHEN e.PolicyApproved THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS PolicyApprovedPct,
  ROUND(100 * SUM(CASE WHEN e.FercMapped THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS FercMappedPct,
  ROUND(100 * SUM(CASE WHEN e.UsedAndUseful OR e.Remediated THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS UsedAndUsefulPct,
  ROUND(100 * SUM(CASE WHEN e.SavingsActioned OR e.Remediated THEN e.AllocatedCost ELSE 0 END) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS SavingsActionedPct,
  ROUND(100 * SUM(e.AllocatedCost * e.EvidenceScore) / NULLIF(SUM(e.AllocatedCost), 0), 1) AS PrudenceScore,
  ROUND(SUM(e.RiskAmount), 2) AS DisallowanceRisk,
  CASE WHEN SUM(e.RiskAmount) > 0 THEN 'Evidence gaps' ELSE 'Complete' END AS Status
FROM prudence_evidence e
JOIN ut_rate_case r ON r.Docket = e.Docket
GROUP BY e.Docket, r.LegalEntity, r.Jurisdiction, r.Status
ORDER BY DisallowanceRisk DESC""",
        params=[],
        compliance=dict(area="Prudence", reference="Prudent-investment and used-and-useful standards", unit="dockets",
                        flag="Status = 'Evidence gaps'", amount="DisallowanceRisk"),
        notes="Weights: reconciled 25%, approved policy 15%, FERC account 15%, used and useful 30%, savings actioned 15%.",
        datasets=["Prudence evidence (computed)", "Utility reference tables"]),
]

if __name__ == "__main__":
    for u in USE_CASES:
        with open(os.path.join(OUT, f"{u['id']}.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(u))
        print("wrote", u["id"])
