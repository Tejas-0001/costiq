"""Board Pack: the board view, assembled from the same data and checks as the rest of CostIQ.

Four core sections (spend and outlook, AI investment and value, compliance and controls risk, decisions), then the
tenant's additions (tenants/<t>/hooks.py board_sections; pharma adds nine). Each section says how complete it is today:
    live     computed from data CostIQ already holds
    partial  computed, but part of the section needs a feed or an approval CostIQ does not have yet
    needs    CostIQ shows what it can and names the missing input
Nothing is estimated to fill a gap; a missing input is reported as missing.
"""
from __future__ import annotations
import datetime as dt
import json
from . import tenant

KIND_LABEL = {"incurred": "Already spent", "exception": "Already spent", "estimate": "Estimated saving",
              "compliance": "Compliance exposure"}


def _rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def _f(v):
    return round(float(v or 0), 2)


def confidence(db, eng, ledger, k):
    """Forecast confidence: unweighted mean of four observable inputs (also used by the Command Center)."""
    rec = eng.reconciliation()
    alloc = eng.allocation_summary(None)
    impl = db.query("""SELECT SUM(CASE WHEN m.Maturity = 'implemented' THEN c.EffectiveCost ELSE 0 END) / SUM(c.EffectiveCost)
                       FROM CostAndUsage c JOIN ingest_manifest m ON m.SourceSystem = c.x_SourceSystem
                       AND m.BillingPeriodStart = c.BillingPeriodStart""")["rows"][0][0] or 0
    closed = [l for l in rec["lines"] if l["IssueStatus"] == "Issued"]
    tie = sum(1 for l in closed if l["Status"] == "matched") / len(closed) if closed else 1
    parts = {"month_elapsed": k["days_elapsed"] / k["days_in_month"], "implemented_adapter_coverage": float(impl),
             "allocation_coverage": (alloc["coverage"] or 0) / 100, "prior_period_tie_out": tie}
    return {"score": round(100 * sum(parts.values()) / 4, 1), "parts": {a: round(100 * b, 1) for a, b in parts.items()},
            "formula": "Unweighted mean of four observable inputs: share of the month elapsed, share of spend from live "
                       "adapters, allocation coverage, and share of closed invoice lines that tie out."}


class BoardPack:
    def __init__(self, db, eng, prudence, optimizer, ledger):
        self.db, self.eng, self.prudence, self.optimizer, self.ledger = db, eng, prudence, optimizer, ledger

    def q(self, sql, params=None):
        return _rows(self.db.query(sql, params or {}, None, limit=None))

    # ------------------------------------------------------------------ core
    def spend_outlook(self):
        k = self.eng.kpis(None)
        start, _, _, _ = self.eng.open_month()
        ms = dt.datetime.combine(start, dt.time())
        todate = self.q("""SELECT SUM(EffectiveCost) AS Spend FROM CostAndUsage""")[0]["Spend"]
        closed = self.q("SELECT SUM(EffectiveCost) AS Spend FROM CostAndUsage WHERE BillingPeriodStart < :ms", {"ms": ms})[0]["Spend"]
        budget_closed = self.q("SELECT SUM(BudgetAmount) AS B FROM cost_budget WHERE BudgetPeriodStart < :ms", {"ms": ms})[0]["B"]
        months = self.q("SELECT MIN(BillingPeriodStart) AS a FROM CostAndUsage")[0]["a"]
        conf = confidence(self.db, self.eng, self.ledger, k)
        spend_to_date = _f(closed) + k["forecast"]  # closed months actual + open month at forecast
        budget_to_date = _f(budget_closed) + k["budget"]
        return {
            "status": "live",
            "metrics": [
                {"label": "Spend to date", "value": _f(todate), "fmt": "money",
                 "note": f"Billed since {str(months)[:10]}, as of {self.eng.as_of().isoformat()}"},
                {"label": "Forecast vs budget to date", "value": round(spend_to_date - budget_to_date, 2), "fmt": "money",
                 "tone": "bad" if spend_to_date > budget_to_date else "good",
                 "note": f"{_money(spend_to_date)} forecast against {_money(budget_to_date)} budget"},
                {"label": "Full-year run-rate", "value": round(k["forecast"] * 12, 2), "fmt": "money",
                 "note": "Open-month forecast × 12"},
                {"label": "Forecast confidence", "value": conf["score"], "fmt": "pct", "note": conf["formula"]},
            ],
            "table": {"columns": ["CostCenter", "Forecast", "Budget", "Variance", "Status"],
                      "rows": [{c: u[c] for c in ("CostCenter", "Forecast", "Budget", "Variance", "Status")} for u in k["units"][:6]]},
            "formula": "Forecast = non-usage charges booked this month + usage month to date ÷ days elapsed × days in month. "
                       "Closed months count at actual.",
        }

    def ai_value(self):
        total = self.q("""SELECT SUM(EffectiveCost) AS T, SUM(CASE WHEN x_AIProduct IS NOT NULL THEN EffectiveCost ELSE 0 END) AS AI
                          FROM CostAndUsage""")[0]
        cc, p = self.eng.cost_center_expr()
        by_area = _rows(self.db.query(f"""SELECT {cc} AS BusinessArea, SUM(EffectiveCost) AS AISpend FROM CostAndUsage
                                          WHERE x_AIProduct IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""", p, None, limit=None))
        idle_seats = self.eng._uc("UO-05", None)
        ptu = self.q("""SELECT Deployment, AVG(UtilizationPct) AS Util, SUM(DailyCost) AS Cost FROM ai_provisioned_capacity
                        GROUP BY 1""")
        idle_ptu = sum(_f(r["Cost"]) * (1 - float(r["Util"] or 0) / 100) for r in ptu)
        ai, t = _f(total["AI"]), _f(total["T"])
        return {
            "status": "live",
            "metrics": [
                {"label": "AI share of spend", "value": round(100 * ai / t, 1) if t else None, "fmt": "pct",
                 "note": f"{_money(ai)} of {_money(t)}"},
                *tenant.hooks().board_ai_metrics(self),  # the tenant's unit costs (pharma: per enrolled subject, per report section)
                {"label": "Idle AI capacity", "value": round(sum(x["MonthlySeatCost"] or 0 for x in idle_seats) + idle_ptu / 4, 2),
                 "fmt": "money", "note": f"{len(idle_seats)} seats idle 28+ days, plus unused provisioned throughput (per month)"},
            ],
            "table": {"columns": ["BusinessArea", "AI_Spend"], "rows": [{"BusinessArea": r["BusinessArea"], "AI_Spend": _f(r["AISpend"])} for r in by_area[:8]]},
            "formula": "AI = charges with an AI product (models, seats, credits, GPU endpoints). Unused provisioned throughput = "
                       "daily cost × (1 − utilization), averaged to a month over the data window.",
        }

    def compliance_controls(self):
        comp = self.eng.compliance(None, "compliance")
        checks = [i for a in comp["areas"] for i in a["items"]]
        gov = []
        for uid in ("GV-01", "GV-02"):
            r = self.eng._uc(uid, None)
            gov.append({"id": uid, "title": self.eng.reg.get(uid)["title"], "flagged": len(r),
                        "amount_affected": round(sum(x.get("EffectiveCost") or 0 for x in r), 2)})
        ctl = self.eng.controls(self.ledger.verify())["controls"]
        passing = sum(1 for c in ctl if c["result"] == "pass")
        rec = self.eng.reconciliation()
        exc = [l for l in rec["lines"] if l["Status"] == "exception" and l["IssueStatus"] == "Issued"]
        rows = ([{"Check": f"{c['id']} {c['title']}", "Area": c["area"], "Flagged": c["flagged"], "Checked": c["checked"],
                  "Status": "Attention" if c["flagged"] else "Pass"} for c in checks]
                + [{"Check": f"{g['id']} {g['title']}", "Area": "AI governance", "Flagged": g["flagged"], "Checked": None,
                    "Status": "Attention" if g["flagged"] else "Pass"} for g in gov])
        return {
            "status": "live",
            "metrics": [
                {"label": tenant.text("board.compliance_checks"), "value": sum(1 for c in checks if c["flagged"]),
                 "fmt": "count", "of": len(checks), "tone": "bad" if any(c["flagged"] for c in checks) else "good"},
                {"label": "AI governance checks needing attention", "value": sum(1 for g in gov if g["flagged"]), "fmt": "count",
                 "of": len(gov)},
                {"label": "Financial controls passing", "value": passing, "fmt": "count", "of": len(ctl),
                 "tone": "good" if passing == len(ctl) else "bad"},
                {"label": "Invoice exceptions", "value": len(exc), "fmt": "count",
                 "note": f"{_money(sum(abs(l.get('Variance') or 0) for l in exc))} variance on issued invoices"},
            ],
            "table": {"columns": ["Check", "Area", "Flagged", "Checked", "Status"], "rows": rows},
            "formula": comp["formula"],
        }

    def decisions(self):
        f = self.eng.findings(None)
        top = f[:5]
        return {
            "status": "live",
            "metrics": [],
            "table": {"columns": ["Item", "Amount", "Label", "Owner"],
                      "rows": [{"Item": x["title"], "Detail": x["detail"], "Amount": x["exposure"], "Label": KIND_LABEL.get(x["kind"], x["kind"]),
                                "Owner": x["owner"], "UseCase": x["id"]} for x in top]},
            "formula": "Top five findings by severity, then amount. Already spent, estimated savings and compliance exposure are "
                       "labelled separately and never added together.",
        }

    # ------------------------------------------------------------------ the pack
    CORE = [
        ("spend", "core", "Spend and outlook", "Is technology spend on plan?", "spend_outlook"),
        ("ai", "core", "AI investment and value", "Evidence that the AI strategy pays off", "ai_value"),
        ("compliance", "core", "Compliance and controls risk", "The audit committee owns compliance and control risk", "compliance_controls"),
        ("decisions", "core", "Decisions and matters for attention", "Gives the board a clear \"so what\"", "decisions"),
    ]

    @property
    def SECTIONS(self):  # noqa: N802 - the four core sections, then the tenant's additions
        return self.CORE + list(tenant.hooks().board_sections())

    def build(self):
        out = []
        for n, (key, kind, title, why, fn) in enumerate(self.SECTIONS, 1):
            try:
                body = getattr(self, fn)() if isinstance(fn, str) else fn(self)
            except Exception as e:  # noqa: BLE001 - one section never breaks the pack
                body = {"status": "error", "gap": f"Could not compute: {str(e)[:200]}", "metrics": []}
            out.append({"n": n, "key": key, "type": kind, "title": title, "why": why, **body})
        return {"as_of": self.eng.as_of().isoformat(), "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "sections": out,
                "caution": tenant.text("board.caution")}


def _money(v):
    v = float(v or 0)
    a = abs(v)
    if a >= 1e6:
        return f"${v / 1e6:,.2f}M"
    if a >= 1e3:
        return f"${v / 1e3:,.1f}K"
    return f"${v:,.0f}"
