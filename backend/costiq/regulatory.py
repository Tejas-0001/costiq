"""Tax computations: filings, the R&D credit schedule on qualifying cloud fees, allocation-policy workflow.

Recurring cloud and SaaS fees allocated to a jurisdiction that claims them as qualifying research expenditure carry
cost class 'qre'. The credit on them is the fee times the credit rate of that jurisdiction's filing.
"""
from __future__ import annotations
import datetime as dt
from collections import defaultdict
from fastapi import HTTPException
from .allocation import Allocator


def rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def credit_rate(jurisdiction, entity, filings):
    """(rate %, basis, filing) for a jurisdiction: its own filing, else its entity's filing, else the average of filings."""
    by_j = {f["Jurisdiction"]: f for f in filings}
    by_e = {f["LegalEntity"]: f for f in filings}
    f = by_j.get(jurisdiction) or by_e.get(entity)
    if f:
        return float(f["CreditRate"]), "filing", f
    rates = [float(x["CreditRate"]) for x in filings]
    return (sum(rates) / len(rates) if rates else 10.0), "average of filings (assumed)", None


class Regulatory:
    def __init__(self, db, engines):
        self.db, self.eng = db, engines

    # ------------------------------------------------------------------ reference
    def filings(self):
        return rows(self.db.query("SELECT * FROM ref_tax_filing ORDER BY FilingDate", {}, None, limit=None))

    def filing(self, filing):
        r = rows(self.db.query("SELECT * FROM ref_tax_filing WHERE FilingId = :d", {"d": filing}, None, limit=1))
        if not r:
            raise HTTPException(404, f"Unknown filing {filing}")
        return r[0]

    def jurisdiction_policies(self):
        return {r["Jurisdiction"]: r for r in rows(self.db.query("SELECT * FROM ref_jurisdiction_policy", {}, None, limit=None))}

    # ------------------------------------------------------------------ R&D credit
    def credit_schedule(self, scope, jurisdiction=None):
        """Qualifying (qre) fees and the credit on them, by month, jurisdiction and vendor."""
        pol, filings = self.jurisdiction_policies(), self.filings()
        where = " AND Jurisdiction = :j" if jurisdiction else ""
        qre = rows(self.db.query(f"""
            SELECT PeriodStart, Jurisdiction, LegalEntity, ServiceProviderName AS Vendor, SUM(AllocatedCost) AS Amount
            FROM allocated_cost WHERE CostClass = 'qre'{where}
            GROUP BY PeriodStart, Jurisdiction, LegalEntity, ServiceProviderName ORDER BY PeriodStart, Vendor""",
                                 {"j": jurisdiction} if jurisdiction else {}, scope, limit=None))
        lines, by_month, by_j = [], defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0, 0.0])
        for q in qre:
            rate, _, _ = credit_rate(q["Jurisdiction"], q["LegalEntity"], filings)
            amt = float(q["Amount"] or 0)
            month = str(q["PeriodStart"])[:7]
            lines.append({"Month": month, "Jurisdiction": q["Jurisdiction"], "LegalEntity": q["LegalEntity"], "Vendor": q["Vendor"],
                          "Qre": round(amt, 2), "CreditRate": rate, "Credit": round(amt * rate / 100, 2)})
            by_month[month][0] += amt
            by_month[month][1] += amt * rate / 100
            by_j[q["Jurisdiction"]][0] += amt
            by_j[q["Jurisdiction"]][1] += amt * rate / 100
            by_j[q["Jurisdiction"]][2] = rate
        return {
            "by_jurisdiction": [{"Jurisdiction": j, "Qre": round(v[0], 2), "Credit": round(v[1], 2), "CreditRate": v[2],
                                 "CreditStatus": pol.get(j, {}).get("CreditStatus"), "Reference": pol.get(j, {}).get("Reference")}
                                for j, v in sorted(by_j.items())],
            "schedule": [{"Month": m, "Qre": round(v[0], 2), "Credit": round(v[1], 2)} for m, v in sorted(by_month.items())],
            "lines": lines,
            "policies": list(pol.values()),
            "formula": "Credit = recurring cloud and SaaS fees claimed as qualifying research expenditure (cost class qre) "
                       "x the jurisdiction's credit rate.",
        }

    # ------------------------------------------------------------------ allocation policies
    def policies(self):
        return rows(self.db.query("SELECT * FROM ref_allocation_policy ORDER BY PolicyId, Version", {}, None, limit=None))

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
        if driver and not self.db.query("SELECT COUNT(*) FROM ref_allocation_driver WHERE DriverName = :d", {"d": driver})["rows"][0][0]:
            raise HTTPException(400, f"Unknown driver {driver}")
        version = (max(r["Version"] for r in cur) + 1) if cur else 1
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
        eff = dt.datetime.fromisoformat(effective_from) if effective_from else dt.datetime(now.year, now.month, 1)
        self.db.query("""INSERT INTO ref_allocation_policy VALUES (:pid, :v, 'opco', :n, :mc, :mv, :drv, :fx, :pr, :ef, NULL,
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
        self.db.query("""UPDATE ref_allocation_policy SET Status = 'superseded', EffectiveTo = :ef
                         WHERE PolicyId = :pid AND Status = 'approved' AND Version < :v""",
                      {"ef": eff, "pid": pid, "v": version}, limit=None)
        self.db.query("""UPDATE ref_allocation_policy SET Status = 'approved', ApprovedBy = :u, ApprovedAt = :now
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
        self.db.query("UPDATE ref_allocation_policy SET Status = 'returned' WHERE PolicyId = :pid AND Version = :v",
                      {"pid": pid, "v": version}, limit=None)
        return self.policy(pid, version)
