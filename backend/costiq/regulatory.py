"""Regulatory accounting computations: rate cases, regulatory-asset amortization, allocation-policy workflow.

Amortization follows the Idaho PUC Order No. 34707 pattern: each month's deferred subscription fees amortize
straight-line from the following month over the contract's remaining term, or over the jurisdiction's default
(62 months) when the contract has no term on file.
"""
from __future__ import annotations
import datetime as dt
from collections import defaultdict
from fastapi import HTTPException
from .allocation import Allocator


def rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def month_add(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return dt.date(d.year + y, m + 1, 1)


def months_between(a: dt.date, b: dt.date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def as_date(v) -> dt.date | None:
    if v is None:
        return None
    return v if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else dt.date.fromisoformat(str(v)[:10])


class Regulatory:
    def __init__(self, db, engines):
        self.db, self.eng = db, engines

    # ------------------------------------------------------------------ reference
    def rate_cases(self):
        return rows(self.db.query("SELECT * FROM ut_rate_case ORDER BY FilingDate", {}, None, limit=None))

    def rate_case(self, docket):
        r = rows(self.db.query("SELECT * FROM ut_rate_case WHERE Docket = :d", {"d": docket}, None, limit=1))
        if not r:
            raise HTTPException(404, f"Unknown docket {docket}")
        return r[0]

    def jurisdiction_policies(self):
        return {r["Jurisdiction"]: r for r in rows(self.db.query("SELECT * FROM ut_jurisdiction_policy", {}, None, limit=None))}

    # ------------------------------------------------------------------ regulatory assets
    def deferrals(self, scope, jurisdiction=None, what_if=False):
        """Monthly deferred (or, with what_if, deferrable) subscription fees by jurisdiction and vendor."""
        cls = "CostClass IN ('om', 'deferral') AND ChargeCategory = 'Purchase' AND ChargeFrequency = 'Recurring'" \
            if what_if else "CostClass = 'deferral'"
        where = " AND Jurisdiction = :j" if jurisdiction else ""
        return rows(self.db.query(f"""
            SELECT PeriodStart, Jurisdiction, LegalEntity, ServiceProviderName AS Vendor, SUM(AllocatedCost) AS Amount
            FROM ut_allocated_cost WHERE {cls}{where}
            GROUP BY PeriodStart, Jurisdiction, LegalEntity, ServiceProviderName ORDER BY PeriodStart""",
                                  {"j": jurisdiction} if jurisdiction else {}, scope, limit=None))

    def contract_months(self):
        """Vendor -> contract end date (amortization runs to contract end when known)."""
        return {r["Vendor"]: as_date(r["ContractEnd"])
                for r in rows(self.db.query("SELECT Vendor, ContractEnd FROM ut_vendor", {}, None, limit=None))}

    def amortization(self, deferrals, horizon_months=36, default_months=62):
        """Straight-line amortization of each month's deferral. Returns (schedule rows, per-line terms)."""
        ends = self.contract_months()
        adds, amort = defaultdict(float), defaultdict(float)
        terms = []
        for d in deferrals:
            m0 = as_date(d["PeriodStart"]).replace(day=1)
            end = ends.get(d["Vendor"])
            n = max(1, months_between(m0, end)) if end and end > m0 else default_months
            amt = float(d["Amount"] or 0)
            adds[m0] += amt
            for k in range(1, n + 1):
                amort[month_add(m0, k)] += amt / n
            terms.append({**d, "AmortizationMonths": n, "MonthlyAmortization": round(amt / n, 2)})
        if not adds:
            return [], terms
        start = min(adds)
        sched, bal = [], 0.0
        for k in range(horizon_months):
            m = month_add(start, k)
            bal += adds.get(m, 0.0) - amort.get(m, 0.0)
            sched.append({"Month": m.isoformat()[:7], "Deferred": round(adds.get(m, 0.0), 2),
                          "Amortization": round(amort.get(m, 0.0), 2), "EndingBalance": round(bal, 2)})
        return sched, terms

    def regulatory_assets(self, scope, jurisdiction=None):
        pol = self.jurisdiction_policies()
        defs = self.deferrals(scope, jurisdiction)
        sched, terms = self.amortization(defs)
        by_j = defaultdict(float)
        for d in defs:
            by_j[d["Jurisdiction"]] += float(d["Amount"] or 0)
        return {
            "by_jurisdiction": [{"Jurisdiction": j, "Deferred": round(v, 2), "DeferralStatus": pol.get(j, {}).get("DeferralStatus"),
                                 "OrderReference": pol.get(j, {}).get("OrderReference")} for j, v in sorted(by_j.items())],
            "schedule": sched,
            "lines": [{k: (v.isoformat()[:7] if isinstance(v, (dt.date, dt.datetime)) else v) for k, v in t.items()} for t in terms],
            "policies": list(pol.values()),
            "formula": "Each month's deferred subscription fees (Account 182.3) amortize straight-line from the next month over "
                       "the contract's remaining term, or 62 months when no term is on file (Idaho PUC Order No. 34707 pattern).",
        }

    # ------------------------------------------------------------------ allocation policies
    def policies(self):
        return rows(self.db.query("SELECT * FROM ut_allocation_policy ORDER BY PolicyId, Version", {}, None, limit=None))

    def policy(self, pid, version=None):
        cand = [r for r in self.policies() if r["PolicyId"] == pid and (version is None or r["Version"] == version)]
        if not cand:
            raise HTTPException(404, f"Unknown policy {pid}" + (f" v{version}" if version else ""))
        return cand[-1]

    def submit(self, user, pid, name=None, driver=None, fixed=None, effective_from=None):
        """Propose a new version of a policy (or a new policy). It applies only once someone else approves it."""
        cur = [r for r in self.policies() if r["PolicyId"] == pid]
        base = cur[-1] if cur else None
        if not base and not (name and (driver or fixed)):
            raise HTTPException(400, "A new policy needs a name and a driver or fixed entity")
        if driver and fixed:
            raise HTTPException(400, "Give a driver or a fixed entity, not both")
        if driver and not self.db.query("SELECT COUNT(*) FROM ut_allocation_driver WHERE DriverName = :d", {"d": driver})["rows"][0][0]:
            raise HTTPException(400, f"Unknown driver {driver}")
        version = (max(r["Version"] for r in cur) + 1) if cur else 1
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
        eff = dt.datetime.fromisoformat(effective_from) if effective_from else dt.datetime(now.year, now.month, 1)
        self.db.query("""INSERT INTO ut_allocation_policy VALUES (:pid, :v, 'opco', :n, :mc, :mv, :drv, :fx, :pr, :ef, NULL,
                         'submitted', :u, :now, NULL, NULL)""",
                      dict(pid=pid, v=version, n=name or base["Name"], mc=base["MatchColumn"] if base else "CostCenter",
                           mv=base["MatchValue"] if base else "*", drv=driver if (driver or fixed) else base["DriverName"],
                           fx=fixed if (driver or fixed) else base["FixedEntity"], pr=base["Priority"] if base else 50,
                           ef=eff, u=user, now=now), limit=None)
        return self.policy(pid, version)

    def approve(self, user, pid, version):
        pol = self.policy(pid, version)
        if pol["Status"] != "submitted":
            raise HTTPException(409, f"{pid} v{version} is {pol['Status']}, not awaiting approval")
        if pol["SubmittedBy"] == user:
            raise HTTPException(409, "Separation of duties: you submitted this policy, so someone else must approve it")
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
        eff = pol["EffectiveFrom"] if isinstance(pol["EffectiveFrom"], dt.datetime) else dt.datetime.fromisoformat(str(pol["EffectiveFrom"]))
        self.db.query("""UPDATE ut_allocation_policy SET Status = 'superseded', EffectiveTo = :ef
                         WHERE PolicyId = :pid AND Status = 'approved' AND Version < :v""",
                      {"ef": eff, "pid": pid, "v": version}, limit=None)
        self.db.query("""UPDATE ut_allocation_policy SET Status = 'approved', ApprovedBy = :u, ApprovedAt = :now
                         WHERE PolicyId = :pid AND Version = :v""", {"u": user, "now": now, "pid": pid, "v": version}, limit=None)
        alloc = Allocator(self.db, self.eng)
        rerun = {}
        for ps in alloc.periods():
            p = ps if isinstance(ps, dt.datetime) else dt.datetime.fromisoformat(str(ps))
            if p >= eff.replace(day=1):
                rerun.update(alloc.run(p))
        return self.policy(pid, version), rerun

    def return_(self, user, pid, version):
        pol = self.policy(pid, version)
        if pol["Status"] != "submitted":
            raise HTTPException(409, f"{pid} v{version} is {pol['Status']}, not awaiting approval")
        self.db.query("UPDATE ut_allocation_policy SET Status = 'returned' WHERE PolicyId = :pid AND Version = :v",
                      {"pid": pid, "v": version}, limit=None)
        return self.policy(pid, version)
