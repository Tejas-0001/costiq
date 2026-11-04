"""Risk engine: reviewer-ready evidence for every allocated technology dollar (pharma: a tax examiner testing
intercompany charges; a utility: a commission testing prudence in a rate case). Labels come from the tenant's copy.

A reviewer can disallow a charge the entity cannot show a benefit from or cannot evidence. For each filing, every
allocated operating-cost line (the pack's allocation.expense_classes) of the review period is tested:

  Reconciled        the charge ties to its invoice line (within $1)
  PolicyApproved    it was allocated under an approved, effective-dated policy (allocation key)
  GlMapped          it has a GL account
  BenefitShown      the resource served work that day (not idle, not an unused commitment, not an unused seat)
  Remediated        an implemented or verified optimization covers it (documented corrective action)
  SavingsActioned   no known savings opportunity on it has sat untouched for more than 60 days

Amount at risk is the spend a reviewer could challenge: the whole line when a hard test fails, the unused share of
a seat purchase, or the share of a line equal to an unactioned savings estimate. Decision support, not a prediction of
any authority's ruling.

The unlock (credit_unlock) is the tenant's hook (tenants/<t>/hooks.py unlock): for pharma, the R&D credit on recurring
cloud and SaaS fees in jurisdictions where they qualify but are not yet claimed.
"""
from __future__ import annotations
import datetime as dt
from . import tenant
from .engines import CHARGE_KEY


def reason_label(code: str) -> str:
    """A risk reason's label, from the tenant's copy (backend.risk.reasons)."""
    return tenant.lookup("risk.reasons").get(code, code)


WEIGHTS = {"Reconciled": 0.25, "PolicyApproved": 0.15, "GlMapped": 0.15, "BenefitShown": 0.30, "SavingsActioned": 0.15}
SEAT_PRODUCTS = ("m365_copilot", "github_copilot", "claude_enterprise")
OPEN_FILINGS = ("In preparation", "Planned", "Filed")
STALE_DAYS = 60
COLUMNS = ["FilingId", "ChargeKey", "PeriodStart", "ChargeDay", "LegalEntity", "Jurisdiction", "ServiceProviderName", "ServiceName",
           "ResourceName", "Application", "CostCenter", "GlAccount", "CostClass", "AllocatedCost", "Reconciled",
           "PolicyApproved", "GlMapped", "BenefitShown", "Remediated", "SavingsActioned", "RiskReason", "RiskAmount",
           "EvidenceScore"]


def rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def expense_classes_sql() -> str:
    """The cost classes the evidence tests read, as a SQL list (pack.yaml allocation.expense_classes)."""
    from .allocation import classes
    return ", ".join("'" + str(c).replace("'", "''") + "'" for c in classes()["expense_classes"])


class PrudenceEngine:
    def __init__(self, db, engines):
        self.db, self.eng = db, engines

    def filings(self):
        return rows(self.db.query("SELECT * FROM ref_tax_filing ORDER BY FilingDate", {}, None, limit=None))

    # ------------------------------------------------------------------ evidence
    def _seat_shares(self):
        """Unused share of each seat product (UO-06), applied to that product's seat purchases."""
        out = {p: 0.0 for p in SEAT_PRODUCTS}
        for r in self.eng._uc("UO-06", None):
            if r["x_AIProduct"] in out and r["AssignedSeats"]:
                out[r["x_AIProduct"]] = round((r["UnusedSeats"] or 0) / r["AssignedSeats"], 4)
        return out

    def sql(self):
        flag = lambda c: f"CAST({c} AS INTEGER)"
        score = " + ".join(f"{w} * {flag(c if c != 'BenefitShown' else '(BenefitShown OR Remediated)')}"
                           for c, w in WEIGHTS.items())
        return f"""
INSERT INTO filing_evidence ({', '.join(COLUMNS)})
WITH rc AS (SELECT * FROM ref_tax_filing WHERE FilingId = :filing),
a AS (
  SELECT u.* FROM allocated_cost u JOIN rc ON u.LegalEntity = rc.LegalEntity AND u.Jurisdiction = rc.Jurisdiction
  WHERE u.PeriodStart >= rc.ReviewPeriodStart AND u.PeriodStart < rc.ReviewPeriodEnd AND u.CostClass IN ({expense_classes_sql()})
),
c AS (
  SELECT {CHARGE_KEY} AS ChargeKey, ResourceName, InvoiceDetailId, x_RequestCount, ConsumedUnit, x_AIProduct,
         CommitmentDiscountStatus, CAST(ChargePeriodStart AS DATE) AS ChargeDay
  FROM CostAndUsage
  WHERE BillingPeriodStart >= (SELECT ReviewPeriodStart FROM rc) AND BillingPeriodStart < (SELECT ReviewPeriodEnd FROM rc)
),
billed AS (SELECT InvoiceDetailId, SUM(BilledCost) AS Charged FROM CostAndUsage GROUP BY InvoiceDetailId),
tie AS (
  SELECT i.InvoiceDetailId, MAX(CASE WHEN ABS(i.BilledCost - COALESCE(b.Charged, 0)) <= 1 THEN 1 ELSE 0 END) AS Ok
  FROM InvoiceDetail i LEFT JOIN billed b ON b.InvoiceDetailId = i.InvoiceDetailId
  GROUP BY i.InvoiceDetailId
),
idle AS (SELECT DISTINCT ResourceName, UsageDate FROM resource_utilization_daily WHERE CpuP95 < 5 AND Requests = 0),
pol AS (SELECT DISTINCT PolicyId, Version FROM ref_allocation_policy
        WHERE ApprovedBy IS NOT NULL AND Status IN ('approved', 'superseded')),
x AS (
  SELECT a.*, c.ResourceName, c.ChargeDay,
         (COALESCE(t.Ok, 0) = 1) AS Reconciled,
         (p.PolicyId IS NOT NULL) AS PolicyApproved,
         (a.GlAccount IS NOT NULL) AS GlMapped,
         NOT (ix.ResourceName IS NOT NULL
              OR (c.ConsumedUnit = 'Hours' AND c.x_AIProduct IS NOT NULL AND a.ServiceCategory = 'AI and Machine Learning'
                  AND COALESCE(c.x_RequestCount, 0) = 0)
              OR COALESCE(c.CommitmentDiscountStatus, '') = 'Unused') AS IdleFree,
         CASE WHEN a.ChargeCategory = 'Purchase' AND c.x_AIProduct = 'm365_copilot' THEN :seat_m365
              WHEN a.ChargeCategory = 'Purchase' AND c.x_AIProduct = 'github_copilot' THEN :seat_gh
              WHEN a.ChargeCategory = 'Purchase' AND c.x_AIProduct = 'claude_enterprise' THEN :seat_claude
              ELSE 0 END AS SeatShare,
         EXISTS (SELECT 1 FROM optimization_action o WHERE o.Status IN ('implemented', 'verified')
                 AND ((o.ScopeType = 'resource' AND o.ScopeKey = c.ResourceName)
                   OR (o.ScopeType = 'application' AND o.ScopeKey = a.Application)
                   OR (o.ScopeType = 'seat_pool' AND a.ChargeCategory = 'Purchase'
                       AND c.x_AIProduct IN ('m365_copilot', 'github_copilot', 'claude_enterprise')))) AS Remediated,
         (SELECT MAX(LEAST(1.0, o.EstimatedMonthlySavings / NULLIF(o.BaselineMonthlyCost, 0))) FROM optimization_action o
          WHERE o.Status IN ('identified', 'approved') AND o.IdentifiedOn < :stale
            AND ((o.ScopeType = 'resource' AND o.ScopeKey = c.ResourceName)
              OR (o.ScopeType = 'application' AND o.ScopeKey = a.Application))) AS OpenRatio
  FROM a
  JOIN c ON c.ChargeKey = a.ChargeKey
  LEFT JOIN tie t ON t.InvoiceDetailId = c.InvoiceDetailId
  LEFT JOIN pol p ON p.PolicyId = a.OpcoPolicy AND p.Version = a.OpcoPolicyVersion
  LEFT JOIN idle ix ON ix.ResourceName = c.ResourceName AND ix.UsageDate = c.ChargeDay
),
y AS (
  SELECT x.*, (IdleFree AND SeatShare = 0) AS BenefitShown, (OpenRatio IS NULL) AS SavingsActioned FROM x
)
SELECT CAST(:filing AS VARCHAR) AS FilingId, ChargeKey, PeriodStart, ChargeDay, LegalEntity, Jurisdiction, ServiceProviderName, ServiceName,
       ResourceName, Application, CostCenter, GlAccount, CostClass, AllocatedCost,
       Reconciled, PolicyApproved, GlMapped, BenefitShown, Remediated, SavingsActioned,
       CASE WHEN NOT BenefitShown AND NOT Remediated THEN 'no_benefit'
            WHEN NOT Reconciled THEN 'unreconciled'
            WHEN NOT PolicyApproved THEN 'no_approved_policy'
            WHEN NOT GlMapped THEN 'unmapped_gl'
            WHEN NOT SavingsActioned AND NOT Remediated THEN 'unactioned_savings' END,
       CASE WHEN NOT IdleFree AND NOT Remediated THEN AllocatedCost
            WHEN SeatShare > 0 AND NOT Remediated THEN AllocatedCost * SeatShare
            WHEN NOT Reconciled OR NOT PolicyApproved OR NOT GlMapped THEN AllocatedCost
            WHEN NOT SavingsActioned AND NOT Remediated THEN AllocatedCost * OpenRatio
            ELSE 0 END,
       {score}
FROM y
"""

    def run(self, filing=None):
        """Recompute the evidence for one filing (default: all). Returns line counts per filing."""
        seats = self._seat_shares()
        stale = dt.datetime.combine(self.eng.as_of() - dt.timedelta(days=STALE_DAYS), dt.time())
        sql, done = self.sql(), {}
        for d in [filing] if filing else [r["FilingId"] for r in self.filings()]:
            self.db.query("DELETE FROM filing_evidence WHERE FilingId = :d", {"d": d}, limit=None)
            self.db.query(sql, {"filing": d, "stale": stale, "seat_m365": seats["m365_copilot"],
                                "seat_gh": seats["github_copilot"], "seat_claude": seats["claude_enterprise"]}, limit=None)
            done[d] = self.db.query("SELECT COUNT(*) FROM filing_evidence WHERE FilingId = :d", {"d": d})["rows"][0][0]
        return done

    # ------------------------------------------------------------------ read side
    def _where(self, filing, extra=""):
        if filing:
            return "WHERE FilingId = :filing" + extra, {"filing": filing}
        names = ", ".join(f"'{s}'" for s in OPEN_FILINGS)
        return (f"WHERE FilingId IN (SELECT FilingId FROM ref_tax_filing WHERE Status IN ({names}))" + extra), {}

    def summary(self, scope, filing=None):
        w, p = self._where(filing)
        q = lambda sql: rows(self.db.query(sql, p, scope, limit=None))
        tot = q(f"""SELECT ROUND(SUM(RiskAmount), 2) AS Risk, ROUND(SUM(AllocatedCost), 2) AS Spend,
                           ROUND(SUM(AllocatedCost * EvidenceScore) / NULLIF(SUM(AllocatedCost), 0) * 100, 1) AS Score,
                           COUNT(*) AS Lines, SUM(CASE WHEN RiskAmount > 0 THEN 1 ELSE 0 END) AS LinesAtRisk
                    FROM filing_evidence {w}""")[0]
        by_reason = q(f"""SELECT RiskReason, COUNT(*) AS Lines, ROUND(SUM(RiskAmount), 2) AS Risk
                          FROM filing_evidence {w} AND RiskAmount > 0 GROUP BY 1 ORDER BY 3 DESC""")
        for r in by_reason:
            r["Label"] = reason_label(r["RiskReason"])
        by_filing = q(f"""SELECT e.FilingId, MAX(r.LegalEntity) AS LegalEntity, MAX(r.Jurisdiction) AS Jurisdiction, MAX(r.Status) AS Status,
                                 ROUND(SUM(e.RiskAmount), 2) AS Risk, ROUND(SUM(e.AllocatedCost), 2) AS Spend,
                                 ROUND(SUM(e.AllocatedCost * e.EvidenceScore) / NULLIF(SUM(e.AllocatedCost), 0) * 100, 1) AS Score
                          FROM filing_evidence e JOIN ref_tax_filing r ON r.FilingId = e.FilingId
                          {w.replace('WHERE FilingId', 'WHERE e.FilingId')} GROUP BY e.FilingId ORDER BY 5 DESC""")
        heat = q(f"""SELECT LegalEntity, ServiceProviderName AS Vendor, ROUND(SUM(AllocatedCost), 2) AS Spend,
                            ROUND(SUM(RiskAmount), 2) AS Risk,
                            ROUND(SUM(AllocatedCost * EvidenceScore) / NULLIF(SUM(AllocatedCost), 0) * 100, 1) AS Score
                     FROM filing_evidence {w} GROUP BY 1, 2 HAVING SUM(AllocatedCost) > 100 ORDER BY 4 DESC""")
        top = q(f"""SELECT RiskReason, ServiceProviderName AS Vendor, COALESCE(ResourceName, ServiceName) AS Item, Application,
                           CostCenter, LegalEntity, FilingId, COUNT(*) AS Lines, MIN(ChargeDay) AS FromDay, MAX(ChargeDay) AS ToDay,
                           ROUND(SUM(RiskAmount), 2) AS Risk
                    FROM filing_evidence {w} AND RiskAmount > 0
                    GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY 11 DESC LIMIT 15""")
        for r in top:
            r["Label"] = reason_label(r["RiskReason"])
            r["Fix"] = self.fix_for(r)
        return {"filing": filing, "deduction_at_risk": tot["Risk"] or 0, "review_period_spend": tot["Spend"] or 0,
                "evidence_score": tot["Score"], "lines": tot["Lines"], "lines_at_risk": tot["LinesAtRisk"],
                "by_reason": by_reason, "by_filing": by_filing, "heatmap": heat, "top": top,
                "filings": self.filings(),
                "formula": tenant.text("risk.formula"), "disclaimer": tenant.text("risk.disclaimer")}

    def fix_for(self, r):
        """The screen or action that removes a risk line."""
        reason = r["RiskReason"]
        if reason in ("no_benefit", "unactioned_savings"):
            item = r.get("Item") or r.get("Application")
            act = rows(self.db.query("""SELECT OptimizationId, Status FROM optimization_action
                                        WHERE Status <> 'rejected' AND (ScopeKey = :k OR ScopeKey = :a)
                                        ORDER BY OptimizationId LIMIT 1""", {"k": item, "a": r.get("Application")}, None, limit=1))
            if act:
                return {"kind": "optimization", "target": act[0]["OptimizationId"], "status": act[0]["Status"],
                        "label": f"{act[0]['OptimizationId']} ({act[0]['Status']})"}
            return {"kind": "optimization_new", "target": item, "label": "Add to Optimization Center"}
        if reason == "unreconciled":
            return {"kind": "reconciliation", "target": r.get("Vendor"), "label": "Resolve invoice exception"}
        if reason == "no_approved_policy":
            return {"kind": "policy", "target": None, "label": "Approve allocation policy"}
        return {"kind": "gl_map", "target": r.get("Item"), "label": tenant.text("risk.fix_gl")}

    def lines(self, scope, filing=None, reason=None, entity=None, vendor=None, limit=500):
        extra, p = "", {}
        if reason:
            extra += " AND RiskReason = :reason"; p["reason"] = reason
        if entity:
            extra += " AND LegalEntity = :entity"; p["entity"] = entity
        if vendor:
            extra += " AND ServiceProviderName = :vendor"; p["vendor"] = vendor
        w, wp = self._where(filing, extra + " AND RiskAmount > 0")
        return rows(self.db.query(f"""SELECT FilingId, ChargeKey, ChargeDay, LegalEntity, Jurisdiction, ServiceProviderName AS Vendor,
                                             ServiceName, ResourceName, Application, CostCenter, GlAccount, CostClass,
                                             ROUND(AllocatedCost, 2) AS AllocatedCost, RiskReason, ROUND(RiskAmount, 2) AS RiskAmount,
                                             Reconciled, PolicyApproved, GlMapped, BenefitShown, Remediated, SavingsActioned
                                      FROM filing_evidence {w} ORDER BY RiskAmount DESC""", {**wp, **p}, scope, limit=limit))

    # ------------------------------------------------------------------ unlock (tenant hook)
    def credit_unlock(self, scope, filing=None):
        """The tenant's unlock figure by jurisdiction (tenants/<t>/hooks.py unlock): {"jurisdictions": [...],
        "credit_unlock", "credit_claimed", "formula", "disclaimer"}. Pharma: the R&D credit on recurring cloud and SaaS
        fees, claimed today and eligible but not yet claimed."""
        return tenant.hooks().unlock(self, scope, filing)

    # ------------------------------------------------------------------ headline
    def headline(self, scope, optimizer):
        risk = self.summary(scope)
        unlock = self.credit_unlock(scope)
        opt = optimizer.summary(scope if type(scope) is list else None)
        return {"deduction_at_risk": risk["deduction_at_risk"], "evidence_score": risk["evidence_score"],
                "credit_unlock": unlock["credit_unlock"], "credit_claimed": unlock["credit_claimed"],
                "verified_savings_annual": opt["verified_annual"], "verified_savings_monthly": opt["verified_monthly"],
                "top_risk": risk["top"][:1], "by_reason": risk["by_reason"],
                "filings": [d["FilingId"] for d in risk["filings"] if d["Status"] in OPEN_FILINGS]}
