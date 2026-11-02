"""The Prudence Engine: rate-case-ready evidence for every technology dollar.

Commissions can disallow costs that were not prudently incurred or that are not "used and useful". For each docket,
every allocated O&M line of the base period is tested for the evidence a commission (or an intervenor) would ask for:

  Reconciled        the charge ties to its invoice line (within $1)
  PolicyApproved    it was allocated under an approved, effective-dated policy
  FercMapped        it has a FERC account
  UsedAndUseful     the resource served work that day (not idle, not an unused commitment, not an unused seat)
  Remediated        an implemented or verified optimization covers it (documented corrective action)
  SavingsActioned   no known savings opportunity on it has sat untouched for more than 60 days

Disallowance risk is the spend a commission could challenge: the whole line when a hard test fails, the unused share of
a seat purchase, or the share of a line equal to an unactioned savings estimate. It is decision support, not a
prediction of any commission's ruling.

Earnings Unlock is the carrying return on cloud subscription fees if they were deferred to a regulatory asset (the Idaho
PUC Order No. 34707 pattern): steady-state average unamortized balance x the docket's pre-tax rate of return.
"""
from __future__ import annotations
import datetime as dt
from collections import defaultdict
from fastapi import HTTPException
from .engines import CHARGE_KEY

REASONS = {
    "not_used_and_useful": "Not used and useful (idle, unused seats or commitments)",
    "unreconciled": "Not reconciled to an invoice",
    "no_approved_policy": "Allocated without an approved policy",
    "unmapped_ferc": "No FERC account",
    "unactioned_savings": "Known savings left unactioned for 60+ days",
}
WEIGHTS = {"Reconciled": 0.25, "PolicyApproved": 0.15, "FercMapped": 0.15, "UsedAndUseful": 0.30, "SavingsActioned": 0.15}
SEAT_PRODUCTS = ("m365_copilot", "github_copilot", "claude_enterprise")
OPEN_DOCKETS = ("In preparation", "Planned", "Filed")
STALE_DAYS = 60
COLUMNS = ["Docket", "ChargeKey", "PeriodStart", "ChargeDay", "LegalEntity", "Jurisdiction", "ServiceProviderName", "ServiceName",
           "ResourceName", "Application", "CostCenter", "FercAccount", "CostClass", "AllocatedCost", "Reconciled",
           "PolicyApproved", "FercMapped", "UsedAndUseful", "Remediated", "SavingsActioned", "RiskReason", "RiskAmount",
           "EvidenceScore"]


def rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def as_date(v):
    return None if v is None else dt.date.fromisoformat(str(v)[:10])


class PrudenceEngine:
    def __init__(self, db, engines):
        self.db, self.eng = db, engines

    def dockets(self):
        return rows(self.db.query("SELECT * FROM ut_rate_case ORDER BY FilingDate", {}, None, limit=None))

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
        score = " + ".join(f"{w} * {flag(c if c != 'UsedAndUseful' else '(UsedAndUseful OR Remediated)')}"
                           for c, w in WEIGHTS.items())
        return f"""
INSERT INTO prudence_evidence ({', '.join(COLUMNS)})
WITH rc AS (SELECT * FROM ut_rate_case WHERE Docket = :docket),
a AS (
  SELECT u.* FROM ut_allocated_cost u JOIN rc ON u.LegalEntity = rc.LegalEntity AND u.Jurisdiction = rc.Jurisdiction
  WHERE u.PeriodStart >= rc.BasePeriodStart AND u.PeriodStart < rc.BasePeriodEnd AND u.CostClass IN ('om', 'deferral')
),
c AS (
  SELECT {CHARGE_KEY} AS ChargeKey, ResourceName, InvoiceDetailId, x_RequestCount, ConsumedUnit, x_AIProduct,
         CommitmentDiscountStatus, CAST(ChargePeriodStart AS DATE) AS ChargeDay
  FROM CostAndUsage
  WHERE BillingPeriodStart >= (SELECT BasePeriodStart FROM rc) AND BillingPeriodStart < (SELECT BasePeriodEnd FROM rc)
),
billed AS (SELECT InvoiceDetailId, SUM(BilledCost) AS Charged FROM CostAndUsage GROUP BY InvoiceDetailId),
tie AS (
  SELECT i.InvoiceDetailId, MAX(CASE WHEN ABS(i.BilledCost - COALESCE(b.Charged, 0)) <= 1 THEN 1 ELSE 0 END) AS Ok
  FROM InvoiceDetail i LEFT JOIN billed b ON b.InvoiceDetailId = i.InvoiceDetailId
  GROUP BY i.InvoiceDetailId
),
idle AS (SELECT DISTINCT ResourceName, UsageDate FROM resource_utilization_daily WHERE CpuP95 < 5 AND Requests = 0),
pol AS (SELECT DISTINCT PolicyId, Version FROM ut_allocation_policy
        WHERE ApprovedBy IS NOT NULL AND Status IN ('approved', 'superseded')),
x AS (
  SELECT a.*, c.ResourceName, c.ChargeDay,
         (COALESCE(t.Ok, 0) = 1) AS Reconciled,
         (p.PolicyId IS NOT NULL) AS PolicyApproved,
         (a.FercAccount IS NOT NULL) AS FercMapped,
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
  SELECT x.*, (IdleFree AND SeatShare = 0) AS UsedAndUseful, (OpenRatio IS NULL) AS SavingsActioned FROM x
)
SELECT CAST(:docket AS VARCHAR) AS Docket, ChargeKey, PeriodStart, ChargeDay, LegalEntity, Jurisdiction, ServiceProviderName, ServiceName,
       ResourceName, Application, CostCenter, FercAccount, CostClass, AllocatedCost,
       Reconciled, PolicyApproved, FercMapped, UsedAndUseful, Remediated, SavingsActioned,
       CASE WHEN NOT UsedAndUseful AND NOT Remediated THEN 'not_used_and_useful'
            WHEN NOT Reconciled THEN 'unreconciled'
            WHEN NOT PolicyApproved THEN 'no_approved_policy'
            WHEN NOT FercMapped THEN 'unmapped_ferc'
            WHEN NOT SavingsActioned AND NOT Remediated THEN 'unactioned_savings' END,
       CASE WHEN NOT IdleFree AND NOT Remediated THEN AllocatedCost
            WHEN SeatShare > 0 AND NOT Remediated THEN AllocatedCost * SeatShare
            WHEN NOT Reconciled OR NOT PolicyApproved OR NOT FercMapped THEN AllocatedCost
            WHEN NOT SavingsActioned AND NOT Remediated THEN AllocatedCost * OpenRatio
            ELSE 0 END,
       {score}
FROM y
"""

    def run(self, docket=None):
        """Recompute the evidence for one docket (default: all). Returns line counts per docket."""
        seats = self._seat_shares()
        stale = dt.datetime.combine(self.eng.as_of() - dt.timedelta(days=STALE_DAYS), dt.time())
        sql, done = self.sql(), {}
        for d in [docket] if docket else [r["Docket"] for r in self.dockets()]:
            self.db.query("DELETE FROM prudence_evidence WHERE Docket = :d", {"d": d}, limit=None)
            self.db.query(sql, {"docket": d, "stale": stale, "seat_m365": seats["m365_copilot"],
                                "seat_gh": seats["github_copilot"], "seat_claude": seats["claude_enterprise"]}, limit=None)
            done[d] = self.db.query("SELECT COUNT(*) FROM prudence_evidence WHERE Docket = :d", {"d": d})["rows"][0][0]
        return done

    # ------------------------------------------------------------------ read side
    def _where(self, docket, extra=""):
        if docket:
            return "WHERE Docket = :docket" + extra, {"docket": docket}
        names = ", ".join(f"'{s}'" for s in OPEN_DOCKETS)
        return (f"WHERE Docket IN (SELECT Docket FROM ut_rate_case WHERE Status IN ({names}))" + extra), {}

    def summary(self, scope, docket=None):
        w, p = self._where(docket)
        q = lambda sql: rows(self.db.query(sql, p, scope, limit=None))
        tot = q(f"""SELECT ROUND(SUM(RiskAmount), 2) AS Risk, ROUND(SUM(AllocatedCost), 2) AS Spend,
                           ROUND(SUM(AllocatedCost * EvidenceScore) / NULLIF(SUM(AllocatedCost), 0) * 100, 1) AS Score,
                           COUNT(*) AS Lines, SUM(CASE WHEN RiskAmount > 0 THEN 1 ELSE 0 END) AS LinesAtRisk
                    FROM prudence_evidence {w}""")[0]
        by_reason = q(f"""SELECT RiskReason, COUNT(*) AS Lines, ROUND(SUM(RiskAmount), 2) AS Risk
                          FROM prudence_evidence {w} AND RiskAmount > 0 GROUP BY 1 ORDER BY 3 DESC""")
        for r in by_reason:
            r["Label"] = REASONS.get(r["RiskReason"], r["RiskReason"])
        by_docket = q(f"""SELECT e.Docket, MAX(r.LegalEntity) AS LegalEntity, MAX(r.Jurisdiction) AS Jurisdiction, MAX(r.Status) AS Status,
                                 ROUND(SUM(e.RiskAmount), 2) AS Risk, ROUND(SUM(e.AllocatedCost), 2) AS Spend,
                                 ROUND(SUM(e.AllocatedCost * e.EvidenceScore) / NULLIF(SUM(e.AllocatedCost), 0) * 100, 1) AS Score
                          FROM prudence_evidence e JOIN ut_rate_case r ON r.Docket = e.Docket
                          {w.replace('WHERE Docket', 'WHERE e.Docket')} GROUP BY e.Docket ORDER BY 5 DESC""")
        heat = q(f"""SELECT LegalEntity, ServiceProviderName AS Vendor, ROUND(SUM(AllocatedCost), 2) AS Spend,
                            ROUND(SUM(RiskAmount), 2) AS Risk,
                            ROUND(SUM(AllocatedCost * EvidenceScore) / NULLIF(SUM(AllocatedCost), 0) * 100, 1) AS Score
                     FROM prudence_evidence {w} GROUP BY 1, 2 HAVING SUM(AllocatedCost) > 100 ORDER BY 4 DESC""")
        top = q(f"""SELECT RiskReason, ServiceProviderName AS Vendor, COALESCE(ResourceName, ServiceName) AS Item, Application,
                           CostCenter, LegalEntity, Docket, COUNT(*) AS Lines, MIN(ChargeDay) AS FromDay, MAX(ChargeDay) AS ToDay,
                           ROUND(SUM(RiskAmount), 2) AS Risk
                    FROM prudence_evidence {w} AND RiskAmount > 0
                    GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY 11 DESC LIMIT 15""")
        for r in top:
            r["Label"] = REASONS.get(r["RiskReason"], r["RiskReason"])
            r["Fix"] = self.fix_for(r)
        return {"docket": docket, "disallowance_risk": tot["Risk"] or 0, "base_period_spend": tot["Spend"] or 0,
                "prudence_score": tot["Score"], "lines": tot["Lines"], "lines_at_risk": tot["LinesAtRisk"],
                "by_reason": by_reason, "by_docket": by_docket, "heatmap": heat, "top": top,
                "dockets": self.dockets(),
                "formula": "Disallowance risk = base-period spend a commission could challenge: whole lines failing "
                           "reconciliation, approved-policy, FERC-account or used-and-useful tests (unless remediated), the "
                           "unused share of seat purchases, and the share of a line equal to a savings estimate left "
                           "unactioned for 60+ days. Prudence score = cost-weighted share of evidence present.",
                "disclaimer": "Decision support for the regulatory team; not a prediction of any commission's ruling."}

    def fix_for(self, r):
        """The screen or action that removes a risk line."""
        reason = r["RiskReason"]
        if reason in ("not_used_and_useful", "unactioned_savings"):
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
        return {"kind": "ferc_map", "target": r.get("Item"), "label": "Map to a FERC account"}

    def lines(self, scope, docket=None, reason=None, entity=None, vendor=None, limit=500):
        extra, p = "", {}
        if reason:
            extra += " AND RiskReason = :reason"; p["reason"] = reason
        if entity:
            extra += " AND LegalEntity = :entity"; p["entity"] = entity
        if vendor:
            extra += " AND ServiceProviderName = :vendor"; p["vendor"] = vendor
        w, wp = self._where(docket, extra + " AND RiskAmount > 0")
        return rows(self.db.query(f"""SELECT Docket, ChargeKey, ChargeDay, LegalEntity, Jurisdiction, ServiceProviderName AS Vendor,
                                             ServiceName, ResourceName, Application, CostCenter, FercAccount, CostClass,
                                             ROUND(AllocatedCost, 2) AS AllocatedCost, RiskReason, ROUND(RiskAmount, 2) AS RiskAmount,
                                             Reconciled, PolicyApproved, FercMapped, UsedAndUseful, Remediated, SavingsActioned
                                      FROM prudence_evidence {w} ORDER BY RiskAmount DESC""", {**wp, **p}, scope, limit=limit))

    # ------------------------------------------------------------------ earnings unlock
    def earnings_unlock(self, scope, docket=None):
        """Carrying return on cloud subscription fees deferred to a regulatory asset, by jurisdiction: what is earned under
        an existing order, and what would be earned if the remaining states allowed deferral."""
        pol = {r["Jurisdiction"]: r for r in rows(self.db.query("SELECT * FROM ut_jurisdiction_policy", {}, None, limit=None))}
        cases = self.dockets()
        by_entity = {c["LegalEntity"]: c for c in cases}
        by_juris = {c["Jurisdiction"]: c for c in cases}
        opco_rors = [float(c["PreTaxRor"]) for c in cases if c["Jurisdiction"] not in ("FERC",)]
        default_ror = sum(opco_rors) / len(opco_rors) if opco_rors else 8.0
        ends = {r["Vendor"]: as_date(r["ContractEnd"])
                for r in rows(self.db.query("SELECT Vendor, ContractEnd FROM ut_vendor", {}, None, limit=None))}
        b0 = min((c["BasePeriodStart"] for c in cases), default=None)
        b1 = max((c["BasePeriodEnd"] for c in cases), default=None)
        if b0 is None:
            return {"jurisdictions": [], "earnings_unlock": 0, "earned_today": 0}
        base = rows(self.db.query("""
            SELECT Jurisdiction, LegalEntity, ServiceProviderName AS Vendor, COUNT(DISTINCT PeriodStart) AS Months,
                   SUM(AllocatedCost) AS Fees
            FROM ut_allocated_cost
            WHERE ChargeCategory = 'Purchase' AND ChargeFrequency = 'Recurring' AND CostClass IN ('om', 'deferral')
              AND PeriodStart >= :b0 AND PeriodStart < :b1
            GROUP BY 1, 2, 3""", {"b0": b0 if isinstance(b0, dt.datetime) else dt.datetime.fromisoformat(str(b0)),
                                   "b1": b1 if isinstance(b1, dt.datetime) else dt.datetime.fromisoformat(str(b1))}, scope, limit=None))
        out = defaultdict(lambda: {"AnnualFees": 0.0, "SteadyBalance": 0.0, "Year1Balance": 0.0, "Contracts": [], "Entity": None})
        for b in base:
            j = b["Jurisdiction"]
            status = pol.get(j, {}).get("DeferralStatus", "not_applicable")
            if status == "not_applicable":
                continue
            case = by_juris.get(j) or by_entity.get(b["LegalEntity"])
            start = as_date(case["TestYearStart"]) if case else dt.date(2027, 1, 1)
            end = ends.get(b["Vendor"])
            n = max(12, (end.year - start.year) * 12 + end.month - start.month) if end and end > start else int(pol[j]["AmortizationMonths"] or 62)
            annual = float(b["Fees"] or 0) * 12 / max(1, int(b["Months"]))
            monthly = annual / 12
            year1 = sum(sum(monthly * max(0.0, 1 - (m - k) / n) for k in range(1, m + 1)) for m in range(1, 13)) / 12
            steady = monthly * (n + 1) / 2
            o = out[j]
            o["Entity"] = b["LegalEntity"]
            o["AnnualFees"] += annual
            o["SteadyBalance"] += steady
            o["Year1Balance"] += year1
            o["Contracts"].append({"Vendor": b["Vendor"], "LegalEntity": b["LegalEntity"], "AnnualFees": round(annual, 2),
                                   "AmortizationMonths": n})
        result = []
        for j, o in sorted(out.items()):
            case = by_juris.get(j) or by_entity.get(o["Entity"])  # e.g. Kentucky uses its OpCo's Ohio docket
            ror = float(case["PreTaxRor"]) if case else default_ror
            status = pol[j]["DeferralStatus"]
            result.append({"Jurisdiction": j, "DeferralStatus": status, "OrderReference": pol[j]["OrderReference"],
                           "Docket": case["Docket"] if case else None, "PreTaxRor": round(ror, 2),
                           "RorBasis": "docket" if case else "average of open dockets (assumed)",
                           "AnnualSubscriptionFees": round(o["AnnualFees"], 2),
                           "Year1Return": round(o["Year1Balance"] * ror / 100, 2),
                           "SteadyStateReturn": round(o["SteadyBalance"] * ror / 100, 2),
                           "Earned": status == "allowed",
                           "Contracts": sorted(o["Contracts"], key=lambda c: -c["AnnualFees"])})
        if docket:
            d = next((c for c in cases if c["Docket"] == docket), None)
            if not d:
                raise HTTPException(404, f"Unknown docket {docket}")
            result = [r for r in result if r["Jurisdiction"] == d["Jurisdiction"]]
        unlock = sum(r["SteadyStateReturn"] for r in result if not r["Earned"])
        earned = sum(r["SteadyStateReturn"] for r in result if r["Earned"])
        return {"jurisdictions": result, "earnings_unlock": round(unlock, 2), "earned_today": round(earned, 2),
                "formula": "Annualized base-period subscription fees (Jul-Sep), deferred monthly to Account 182.3 and amortized "
                           "over the contract's remaining term from the test year (62 months without a term). Steady-state "
                           "return = monthly deferral x (N + 1) / 2 x pre-tax rate of return; year 1 shown for reference. "
                           "Unlock = jurisdictions where deferral is untested or was rejected.",
                "disclaimer": "Illustrative: depends on each commission granting deferral and on the authorized return."}

    # ------------------------------------------------------------------ headline
    def headline(self, scope, optimizer):
        risk = self.summary(scope)
        unlock = self.earnings_unlock(scope)
        opt = optimizer.summary(scope if type(scope) is list else None)
        return {"disallowance_risk": risk["disallowance_risk"], "prudence_score": risk["prudence_score"],
                "earnings_unlock": unlock["earnings_unlock"], "earned_today": unlock["earned_today"],
                "verified_savings_annual": opt["verified_annual"], "verified_savings_monthly": opt["verified_monthly"],
                "top_risk": risk["top"][:1], "by_reason": risk["by_reason"],
                "dockets": [d["Docket"] for d in risk["dockets"] if d["Status"] in OPEN_DOCKETS]}
