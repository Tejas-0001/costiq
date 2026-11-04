"""Filings, the credit schedule, the allocation-policy workflow and the regulatory ledger views.

Pharma: recurring cloud and SaaS fees allocated to a jurisdiction that claims them as qualifying research expenditure
carry cost class 'qre'; the credit on them is the fee times the credit rate of that jurisdiction's filing. A tenant
without a 'qre' class gets an empty schedule.

Ledger views (tenants whose ledger carries the rule columns, tenants/<t>/pack.yaml `allocation.ledger_columns`):
service-company billing by class, the ledger by account, function and rule, and the capital path by work order. A
tenant without those columns gets `{"available": false}` from the routes (routes_regulatory.py).
"""
from __future__ import annotations
import datetime as dt
from collections import defaultdict
from fastapi import HTTPException
from . import allocation, tenant
from .allocation import Allocator


def rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def month_start(v) -> dt.datetime:
    """First day of the month of a date, datetime or 'YYYY-MM[-DD]' string."""
    if isinstance(v, str):
        v = dt.datetime.fromisoformat(v + "-01" if len(v) == 7 else v[:10])
    return dt.datetime(v.year, v.month, 1)


def next_month(v) -> dt.datetime:
    m = month_start(v)
    return dt.datetime(m.year + m.month // 12, m.month % 12 + 1, 1)


def billing_class(v) -> str | None:
    """'direct' for 457.1, 'indirect' for 457.2, None for amounts kept at the service company."""
    s = str(v or "")
    return "direct" if s.startswith("457.1") else "indirect" if s.startswith("457.2") else None


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
            "formula": tenant.text("tax.credit_schedule_formula"),
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

    # ------------------------------------------------------------------ ledger views (rule columns)
    def ledger_columns(self) -> set[str]:
        """The tenant's extra ledger columns (allocation.extra_columns()) that the served allocated_cost table has."""
        declared = getattr(allocation, "extra_columns", None)
        want = set(declared() if declared else [])
        if not want:
            return set()
        return want & set(self.db.query("SELECT * FROM allocated_cost WHERE 1 = 0", limit=None)["columns"])

    def periods(self):
        """(billing periods 'YYYY-MM', latest closed period): a period is closed once its month has ended by the data date."""
        ps = [str(x)[:7] for x in Allocator(self.db, self.eng).periods()]
        as_of = self.eng.as_of()
        closed = [m for m in ps if next_month(m).date() <= as_of]
        return ps, (closed[-1] if closed else (ps[-1] if ps else None))

    def table(self, name, order=""):
        """All rows of a register table, or [] when this tenant has no such table."""
        try:
            return rows(self.db.query(f"SELECT * FROM {name} {order}", {}, None, limit=None))
        except Exception:
            return []

    def ledger_total(self, scope, start, end, where=""):
        r = self.db.query(f"""SELECT ROUND(COALESCE(SUM(AllocatedCost), 0), 2) AS t FROM allocated_cost
                              WHERE PeriodStart >= :a AND PeriodStart < :b {where}""", {"a": start, "b": end}, scope, limit=None)
        return float(r["rows"][0][0] or 0)

    def billing(self, scope, lines, start, end):
        """Service-company billing by class from the recharge use case's lines (BillingClass 457.1 direct, 457.2 by
        factor, anything else kept at the service company): totals by receiving company, the direct share, the factor
        register with what each factor billed, and the tie to the ledger."""
        per = defaultdict(lambda: {"direct": 0.0, "indirect": 0.0})
        by_factor = defaultdict(float)
        held = no_account = 0.0
        for l in lines:
            amt, c = float(l.get("AmountRecharged") or 0), billing_class(l.get("BillingClass"))
            if c:
                per[l["RecipientEntity"]][c] += amt
                if l.get("Factor"):
                    by_factor[l["Factor"]] += amt
                if l.get("ReceivingAccount") in (None, "", "(unmapped)"):
                    no_account += amt
            else:
                held += amt
        direct = sum(v["direct"] for v in per.values())
        indirect = sum(v["indirect"] for v in per.values())
        where = "AND BillingClass IS NOT NULL" if "BillingClass" in self.ledger_columns() else "AND OpcoPolicy IS NOT NULL"
        ledger_billed = self.ledger_total(scope, start, end, where)
        factors = self.table("ref_allocation_factor", "ORDER BY FactorCode")
        for f in factors:
            f["AmountBilled"] = round(by_factor.get(f["FactorCode"], 0.0), 2)
        share = lambda d, t: round(100 * d / t, 1) if t else None  # noqa: E731
        return {
            "per_entity": {k: {"Direct": round(v["direct"], 2), "Indirect": round(v["indirect"], 2),
                               "Total": round(v["direct"] + v["indirect"], 2),
                               "DirectSharePct": share(v["direct"], v["direct"] + v["indirect"])} for k, v in per.items()},
            "direct_share_pct": share(direct, direct + indirect),
            "factors": factors,
            "totals": {"direct": round(direct, 2), "indirect": round(indirect, 2), "billed": round(direct + indirect, 2),
                       "held": round(held, 2), "ledger_billed": ledger_billed,
                       "difference": round(direct + indirect - ledger_billed, 2), "without_account": round(no_account, 2)},
        }

    def ferc_ledger(self, scope, start, end):
        """The period's ledger by account, function, class and rule, and by company and function."""
        accounts = rows(self.db.query("""
            SELECT COALESCE(GlAccount, '(unmapped)') AS GlAccount, MAX(GlTitle) AS GlTitle, FunctionCode, CostClass, RuleId,
                   PolicyFlag, COUNT(*) AS Lines, ROUND(SUM(AllocatedCost), 2) AS AllocatedCost
            FROM allocated_cost WHERE PeriodStart >= :a AND PeriodStart < :b
            GROUP BY 1, 3, 4, 5, 6""", {"a": start, "b": end}, scope, limit=None))

        def acct_key(r):
            a = str(r["GlAccount"])
            try:
                return (0, float(a), a, str(r["RuleId"] or ""), str(r["FunctionCode"] or ""))
            except ValueError:
                return (1, 0.0, a, str(r["RuleId"] or ""), str(r["FunctionCode"] or ""))
        accounts.sort(key=acct_key)
        ef = rows(self.db.query("""
            SELECT LegalEntity, FunctionCode, ROUND(SUM(AllocatedCost), 2) AS AllocatedCost
            FROM allocated_cost WHERE PeriodStart >= :a AND PeriodStart < :b
            GROUP BY 1, 2 ORDER BY 1, 2""", {"a": start, "b": end}, scope, limit=None))
        return {"accounts": accounts, "by_entity_function": ef,
                "total": round(sum(float(r["AllocatedCost"] or 0) for r in accounts), 2)}

    def account_title(self, account):
        r = self.db.query("SELECT MAX(GlTitle) FROM allocated_cost WHERE GlAccount = :a", {"a": account}, None, limit=None)
        return r["rows"][0][0] if r["rows"] else None

    def capital_path(self, scope, work_orders, by_work_order: bool, described: bool):
        """Monthly 107 additions, balance, transfer to plant and 403 depreciation per work order and module, from the
        ledger's cwip lines (matched on WorkOrderId when the ledger carries it, else on the work order's application),
        starting from each module's opening balance; and the lines held out of 107 by exception code."""
        meta = {(r["WorkOrderId"], r["Module"]): r for r in self.table("ref_work_order")}
        if by_work_order:   # use the ledger's work orders only once the allocator stamps them on cwip lines
            by_work_order = bool(self.db.query("SELECT COUNT(*) FROM allocated_cost WHERE CostClass = 'cwip' AND WorkOrderId IS NOT NULL",
                                               {}, scope, limit=None)["rows"][0][0])
        key = "WorkOrderId" if by_work_order else "Application"
        adds = defaultdict(float)
        for r in rows(self.db.query(f"""
                SELECT {key} AS k, PeriodStart, SUM(AllocatedCost) AS Amount FROM allocated_cost
                WHERE CostClass = 'cwip' AND {key} IS NOT NULL GROUP BY 1, 2""", {}, scope, limit=None)):
            adds[(r["k"], str(r["PeriodStart"])[:7])] += float(r["Amount"] or 0)
        months = [m for m in self.periods()[0] if month_start(m).date() <= self.eng.as_of()]
        schedule = []
        for w in work_orders:
            wo, mod = w.get("WorkOrderId"), w.get("Module")
            m = meta.get((wo, mod))
            if not m:
                continue   # the "(none)" row for lines held with no work order
            share = float(m.get("ModuleShare") or 1)
            ready = month_start(m["ReadyForUseDate"]).strftime("%Y-%m") if m.get("ReadyForUseDate") else None
            rate = float(m.get("DepreciationRate") or 0)
            balance, plant = float(m.get("CwipBeforeWindow") or 0), 0.0
            for mo in months:
                add = adds.get((wo if by_work_order else m.get("Application"), mo), 0.0) * share
                balance += add
                moved = 0.0
                if ready and mo >= ready:
                    moved, plant, balance = balance, plant + balance, 0.0
                schedule.append({"WorkOrderId": wo, "Module": mod, "Month": mo, "Cwip107Additions": round(add, 2),
                                 "Cwip107Balance": round(balance, 2), "TransferredToPlant": round(moved, 2),
                                 "PlantAccount": m.get("TargetPlantAccount") if plant else None,
                                 "Depreciation403": round(plant * rate / 100 / 12, 2)})
        return {"schedule": schedule, "held": self.held(scope, work_orders, by_work_order, described)}

    def held(self, scope, work_orders, from_ledger: bool, described: bool):
        """Capitalizable lines held out of 107: from the ledger's exception codes when it carries work orders, else
        from the capital-path use case's 'Held: <code>' rows."""
        codes = ("no_work_order", "capitalizable_without_threshold_date")
        items = []
        if from_ledger:
            desc = "ChargeDescription" if described else "ServiceName"
            items = rows(self.db.query(f"""
                SELECT Exception AS ExceptionCode, ServiceProviderName AS Vendor, {desc} AS Description, CostCenter,
                       WorkOrderId, COUNT(*) AS Lines, ROUND(SUM(AllocatedCost), 2) AS Amount
                FROM allocated_cost WHERE Exception IN ('{codes[0]}', '{codes[1]}')
                GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 7 DESC""", {}, scope, limit=None))
        if not from_ledger or not items:   # ledger without the codes stamped yet: the capital-path use case's held rows
            items = [{"ExceptionCode": str(w["Status"]).split(":", 1)[1].strip(), "Vendor": None,
                      "Description": w.get("Description"), "CostCenter": None,
                      "WorkOrderId": None if w.get("WorkOrderId") == "(none)" else w.get("WorkOrderId"), "Lines": 1,
                      "Amount": round(float(w.get("HeldOutOf107") or 0), 2)}
                     for w in work_orders if str(w.get("Status") or "").startswith("Held:")]
        out = {}
        for i in items:
            o = out.setdefault(i["ExceptionCode"], {"ExceptionCode": i["ExceptionCode"], "Lines": 0, "Amount": 0.0, "Items": []})
            o["Lines"] += int(i.pop("Lines") or 0)
            o["Amount"] = round(o["Amount"] + float(i["Amount"] or 0), 2)
            o["Items"].append({k: v for k, v in i.items() if k != "ExceptionCode"})
        return sorted(out.values(), key=lambda o: -o["Amount"])
