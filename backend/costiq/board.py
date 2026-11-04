"""Board Pack: the life-sciences board view, assembled from the same data and checks as the rest of CostIQ.

Thirteen sections: four core (spend and outlook, AI investment and value, compliance and controls risk, decisions) and nine
additions. Each section says how complete it is today:
    live     computed from data CostIQ already holds
    partial  computed, but part of the section needs a feed or an approval CostIQ does not have yet
    needs    CostIQ shows what it can and names the missing input
Nothing is estimated to fill a gap; a missing input is reported as missing.
"""
from __future__ import annotations
import datetime as dt
import json

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
        unit = self.eng._uc("PH-10", None)
        last = max((r["Month"] for r in unit), default=None)
        lu = [r for r in unit if r["Month"] == last]
        patients = sum(r["EnrolledPatients"] or 0 for r in lu)
        opex = sum(r["TechnologyOpex"] or 0 for r in lu)
        csr = self.q("""SELECT SUM(AcceptedOutcomes) AS Accepted FROM ai_app_telemetry_daily WHERE Application = 'csr-drafting-agent'""")[0]
        csr_cost = self.q("""SELECT SUM(EffectiveCost) AS C FROM CostAndUsage
                             WHERE json_extract_string(Tags, '$.application') = 'csr-drafting-agent'""")[0]["C"]
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
                {"label": "Technology cost per enrolled subject", "value": round(12 * opex / patients, 2) if patients else None,
                 "fmt": "money2", "note": f"Annualized, {last} · {patients:,} enrolled subjects (PH-10)"},
                {"label": "AI cost per study report section",
                 "value": round(_f(csr_cost) / float(csr["Accepted"]), 2) if csr["Accepted"] else None, "fmt": "money2",
                 "note": f"CSR drafting agent: {_money(_f(csr_cost))} ÷ {int(csr['Accepted'] or 0):,} accepted sections"},
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
                {"label": "GxP, Part 11 and privacy checks needing attention", "value": sum(1 for c in checks if c["flagged"]),
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

    # ------------------------------------------------------------------ additions
    def programs(self):
        rows = self.q("""
            WITH s AS (
              SELECT json_extract_string(Tags, '$.application') AS Application, SUM(EffectiveCost) AS Spend,
                     SUM(CASE WHEN x_AIProduct IS NOT NULL THEN EffectiveCost ELSE 0 END) AS AISpend
              FROM CostAndUsage GROUP BY 1)
            SELECT p.ProgramId, p.Name, p.Phase, p.AnnualTechBudget, COALESCE(SUM(s.Spend), 0) AS Spend,
                   COALESCE(SUM(s.AISpend), 0) AS AISpend
            FROM ref_program p LEFT JOIN ref_program_application a ON a.ProgramId = p.ProgramId
            LEFT JOIN s ON s.Application = a.Application
            GROUP BY p.ProgramId, p.Name, p.Phase, p.AnnualTechBudget
            ORDER BY CASE p.Phase WHEN 'Discovery' THEN 0 WHEN 'Phase 1' THEN 1 WHEN 'Phase 2' THEN 2
                                  WHEN 'Phase 3' THEN 3 ELSE 4 END""")
        span = self.q("SELECT MIN(ChargePeriodStart) AS a, MAX(ChargePeriodStart) AS b FROM CostAndUsage")[0]
        d0 = lambda v: dt.date.fromisoformat(str(v)[:10])  # noqa: E731
        days = max(1, (d0(span["b"]) - d0(span["a"])).days + 1) if span["a"] else 1
        total = _f(self.q("SELECT SUM(EffectiveCost) AS T FROM CostAndUsage")[0]["T"])
        out = []
        for r in rows:
            run = _f(r["Spend"]) / days * 365
            budget = _f(r["AnnualTechBudget"])
            out.append({"Program": f"{r['ProgramId']} {r['Name']}", "Phase": r["Phase"], "SpendToDate": _f(r["Spend"]),
                        "AI_Spend": _f(r["AISpend"]), "AnnualizedSpend": round(run, 2), "AnnualBudget": budget,
                        "Variance": round(run - budget, 2), "Status": "over budget" if run > budget else "on track"})
        mapped = sum(r["SpendToDate"] for r in out)
        return {
            "status": "live",
            "metrics": [{"label": "Spend mapped to a program", "value": round(100 * mapped / total, 1) if total else None, "fmt": "pct",
                         "note": f"{_money(mapped)}; the rest is shared platform, corporate or commercial spend"},
                        {"label": "Programs over budget", "value": sum(1 for r in out if r["Variance"] > 0), "fmt": "count", "of": len(out)}],
            "table": {"columns": ["Program", "Phase", "SpendToDate", "AI_Spend", "AnnualizedSpend", "AnnualBudget", "Variance", "Status"],
                      "rows": out},
            "formula": f"Applications are mapped to programs in the program register. Run-rate = spend to date ÷ {days} days × 365.",
        }

    def rd_sga(self):
        rows = self.q("""SELECT COALESCE(PnlFunction, 'Not mapped') AS Function, CostClass, SUM(AllocatedCost) AS Cost
                         FROM allocated_cost GROUP BY 1, 2 ORDER BY 3 DESC""")
        total = sum(_f(r["Cost"]) for r in rows) or 1
        rd = sum(_f(r["Cost"]) for r in rows if r["Function"] == "Research & Development")
        sga = sum(_f(r["Cost"]) for r in rows if r["Function"] == "SG&A")
        return {
            "status": "partial",
            "gap": "Indicative only. The split comes from the GL account map; finance must approve an allocation rule for "
                   "shared platforms before it is shown to the board or used for the R&D tax credit.",
            "metrics": [{"label": "R&D share", "value": round(100 * rd / total, 1), "fmt": "pct", "note": _money(rd)},
                        {"label": "SG&A share", "value": round(100 * sga / total, 1), "fmt": "pct", "note": _money(sga)}],
            "table": {"columns": ["Function", "CostClass", "Cost", "Share"],
                      "rows": [{"Function": r["Function"], "CostClass": r["CostClass"], "Cost": _f(r["Cost"]),
                                "Share": round(100 * _f(r["Cost"]) / total, 1)} for r in rows]},
            "formula": "Allocated cost by P&L function and cost class (qre = qualifying research expenditure).",
        }

    def ai_rd_value(self):
        apps = {"csr-drafting-agent": "Days to a draft study report section", "ae-intake-bot": "Adverse-event case processing time",
                "protocol-assistant": "Time to answer a protocol question"}
        cost = {r["Application"]: r for r in self.q("""
            SELECT json_extract_string(Tags, '$.application') AS Application, SUM(EffectiveCost) AS Cost
            FROM CostAndUsage WHERE json_extract_string(Tags, '$.application') IN ('csr-drafting-agent', 'ae-intake-bot', 'protocol-assistant')
            GROUP BY 1""")}
        tel = {r["Application"]: r for r in self.q("""
            SELECT Application, SUM(Requests) AS Requests, SUM(AcceptedOutcomes) AS Accepted FROM ai_app_telemetry_daily
            WHERE Application IN ('csr-drafting-agent', 'ae-intake-bot', 'protocol-assistant') GROUP BY 1""")}
        rows = [{"Application": a, "Measure": m, "AI_Cost": _f(cost.get(a, {}).get("Cost")),
                 "AcceptedOutputs": int((tel.get(a) or {}).get("Accepted") or 0), "CycleTime": "Not connected"} for a, m in apps.items()]
        return {
            "status": "needs",
            "gap": "Needs operational data: cycle times from the clinical (CTMS/eTMF) and safety (case management) systems. "
                   "AI cost and accepted outputs are already in CostIQ.",
            "metrics": [],
            "table": {"columns": ["Application", "Measure", "AI_Cost", "AcceptedOutputs", "CycleTime"], "rows": rows},
            "formula": "Accepted outputs come from the LLM gateway telemetry.",
        }

    def inspection(self):
        asof = self.eng.as_of()
        sysrows = self.q("SELECT Application, SystemName, GxpImpact, Owner, NextReviewDate FROM ref_gxp_system WHERE GxpImpact <> 'None'")
        due = []
        for r in sysrows:
            d = r["NextReviewDate"]
            if d is None:
                continue
            d = d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
            if d <= asof + dt.timedelta(days=90):
                due.append({"System": r["SystemName"], "GxpImpact": r["GxpImpact"], "Owner": r["Owner"], "NextReview": d.isoformat(),
                            "Status": "overdue" if d < asof else "due within 90 days"})
        open_findings = (sum(1 for x in self.eng._uc("PH-07", None) if x["Status"] == "Provider can decrypt patient data")
                         + sum(1 for x in self.eng._uc("PH-08", None) if x["GxpScoped"] and x.get("MissingClauses"))
                         + sum(1 for x in self.eng._uc("PH-11", None) if x["Status"] != "Approved"))
        return {
            "status": "partial",
            "gap": "Changes made without change control need the ITSM change feed; it is not connected.",
            "metrics": [{"label": "Validated systems due for review", "value": len(due), "fmt": "count", "of": len(sysrows),
                         "tone": "bad" if any(d["Status"] == "overdue" for d in due) else None,
                         "note": f"{sum(1 for d in due if d['Status'] == 'overdue')} overdue"},
                        {"label": "Open findings on computerized systems", "value": open_findings, "fmt": "count",
                         "note": "PH-07 keys, PH-08 quality clauses, PH-11 AI validation"},
                        {"label": "Changes without change control", "value": None, "fmt": "count", "note": "Not connected"}],
            "table": {"columns": ["System", "GxpImpact", "Owner", "NextReview", "Status"], "rows": due},
            "formula": "Review dates come from the GxP system register; due = next review within 90 days of the data date.",
        }

    def patient_data(self):
        out_region = self.eng._uc("GV-02", None)
        keys = [x for x in self.eng._uc("PH-07", None) if x["Status"] == "Provider can decrypt patient data"]
        vendors = [x for x in self.eng._uc("PH-08", None) if x["GxpScoped"] and x.get("MissingClauses")]
        trend = self.q("""
            SELECT strftime(BillingPeriodStart, '%Y-%m') AS Month, SUM(EffectiveCost) AS Cost FROM CostAndUsage
            WHERE (json_extract_string(Tags, '$.application') = 'genomics-data' OR x_WorkspaceName = 'genomics-data')
              AND COALESCE(x_InferenceGeo, 'us') <> 'us'
            GROUP BY 1 ORDER BY 1""")
        return {
            "status": "live",
            "metrics": [{"label": "Workloads processing regulated data outside agreed regions", "value": len(out_region), "fmt": "count",
                         "tone": "bad" if out_region else "good",
                         "note": f"{_money(sum(x['EffectiveCost'] or 0 for x in out_region))} affected spend (GV-02)"},
                        {"label": "Patient data readable by the provider", "value": len(keys), "fmt": "count",
                         "note": ", ".join(x["SystemName"] for x in keys)},
                        {"label": "GxP vendors missing required clauses", "value": len(vendors), "fmt": "count",
                         "note": ", ".join(f"{x['Vendor']} ({x['MissingClauses'].strip('; ')})" for x in vendors)}],
            "trend": [{"Month": r["Month"], "Value": _f(r["Cost"])} for r in trend],
            "trend_label": "Spend on regulated data processed outside agreed regions, by month",
            "table": {"columns": ["Application", "x_InferenceGeo", "FirstSeen", "LastSeen", "DaysInViolation", "EffectiveCost"],
                      "rows": out_region},
            "formula": "GV-02: regulated-data usage where the inference region is not the agreed one. PH-07 and PH-08 as on the "
                       "GxP & Patient Data page.",
        }

    def ai_regulation(self):
        reg = self.q("""SELECT u.Application, u.UseCase, u.RiskTier, u.ReviewStatus, u.EuAiActClass, u.GxpContext
                        FROM ref_ai_use u ORDER BY CASE u.RiskTier WHEN 'High' THEN 0 WHEN 'Limited' THEN 1 ELSE 2 END, u.Application""")
        high = [r for r in reg if r["RiskTier"] == "High"]
        assessed = [r for r in high if r["ReviewStatus"] == "Approved"]
        tiers = {}
        for r in reg:
            tiers[r["RiskTier"] or "Unrated"] = tiers.get(r["RiskTier"] or "Unrated", 0) + 1
        unclassified = sum(1 for r in reg if not r["EuAiActClass"])
        return {
            "status": "partial",
            "gap": "The AI register exists; EU AI Act classification is not yet recorded for "
                   f"{unclassified} of {len(reg)} uses. Check the EU AI Act dates in force before showing this to the board.",
            "metrics": [{"label": "AI uses in the register", "value": len(reg), "fmt": "count",
                         "note": " · ".join(f"{k} {v}" for k, v in tiers.items())},
                        {"label": "High-risk uses with an assessment", "value": len(assessed), "fmt": "count", "of": len(high),
                         "tone": "bad" if len(assessed) < len(high) else "good"},
                        {"label": "EU AI Act classification recorded", "value": len(reg) - unclassified, "fmt": "count", "of": len(reg)}],
            "table": {"columns": ["Application", "UseCase", "RiskTier", "ReviewStatus", "EuAiActClass"],
                      "rows": [{**r, "EuAiActClass": r["EuAiActClass"] or "Not classified"} for r in reg]},
            "formula": "Risk tier is set by AI governance in the AI use register. Assessed = review status Approved.",
        }

    def cyber(self):
        critical = self.q("SELECT COUNT(*) AS N FROM ref_gxp_system WHERE GxpImpact IN ('High', 'Medium')")[0]["N"]
        vendors = [x for x in self.eng._uc("PH-08", None) if x.get("MissingClauses")]
        return {
            "status": "partial",
            "gap": "Applies if the company is publicly listed (SEC cyber disclosure). Incidents and backup tests come from the "
                   "security team's systems; they are not connected.",
            "metrics": [{"label": "Critical (GxP high/medium) systems", "value": critical, "fmt": "count"},
                        {"label": "Vendors with contract gaps", "value": len(vendors), "fmt": "count",
                         "note": ", ".join(x["Vendor"] for x in vendors)},
                        {"label": "Incidents involving cloud or AI systems", "value": None, "fmt": "count", "note": "Not connected"},
                        {"label": "Critical systems without a tested backup", "value": None, "fmt": "count", "note": "Not connected"}],
            "formula": "Vendor risk uses the quality-agreement register (PH-08).",
        }

    def vendors(self):
        rows = self.q("""SELECT ServiceProviderName AS Vendor, SUM(EffectiveCost) AS Spend FROM CostAndUsage
                         GROUP BY 1 ORDER BY 2 DESC""")
        total = sum(_f(r["Spend"]) for r in rows) or 1
        hhi = round(sum((100 * _f(r["Spend"]) / total) ** 2 for r in rows))
        gxp = self.q("""
            SELECT s.SystemName, s.GxpImpact, COUNT(DISTINCT c.ServiceProviderName) AS Providers,
                   MIN(c.ServiceProviderName) AS Provider
            FROM ref_gxp_system s JOIN CostAndUsage c ON json_extract_string(c.Tags, '$.application') = s.Application
            WHERE s.GxpImpact <> 'None' GROUP BY 1, 2 ORDER BY 1""")
        single = [r for r in gxp if r["Providers"] == 1]
        return {
            "status": "live",
            "metrics": [{"label": "Top vendor share", "value": round(100 * _f(rows[0]["Spend"]) / total, 1) if rows else None,
                         "fmt": "pct", "note": rows[0]["Vendor"] if rows else ""},
                        {"label": "Top three vendors' share", "value": round(100 * sum(_f(r["Spend"]) for r in rows[:3]) / total, 1),
                         "fmt": "pct", "note": ", ".join(r["Vendor"] for r in rows[:3])},
                        {"label": "Concentration index (HHI)", "value": hhi, "fmt": "count",
                         "note": "Above 2,500 is highly concentrated"},
                        {"label": "GxP workloads on a single vendor", "value": len(single), "fmt": "count", "of": len(gxp),
                         "note": ", ".join(f"{r['SystemName']} ({r['Provider']})" for r in single)}],
            "table": {"columns": ["Vendor", "Spend", "Share"],
                      "rows": [{"Vendor": r["Vendor"], "Spend": _f(r["Spend"]), "Share": round(100 * _f(r["Spend"]) / total, 1)} for r in rows[:8]]},
            "formula": "Share of effective cost over the data window. HHI = Σ (vendor share in %)².",
        }

    def gmp_ai(self):
        n = self.q("SELECT COUNT(*) AS N FROM ref_ai_use WHERE GmpContext")[0]["N"]
        return {
            "status": "needs" if not n else "partial",
            "gap": "No AI in GMP work (batch release, deviations, quality control) is in the AI register yet. Add this section once "
                   "such workloads exist." if not n else "Validation status comes from the AI register.",
            "metrics": [{"label": "AI uses in GMP work", "value": n, "fmt": "count"}],
            "formula": "AI use register, GMP context flag.",
        }

    # ------------------------------------------------------------------ the pack
    SECTIONS = [
        ("spend", "core", "Spend and outlook", "Is technology spend on plan?", "spend_outlook"),
        ("ai", "core", "AI investment and value", "Evidence that the AI strategy pays off", "ai_value"),
        ("compliance", "core", "Compliance and controls risk", "The audit committee owns compliance and control risk", "compliance_controls"),
        ("decisions", "core", "Decisions and matters for attention", "Gives the board a clear \"so what\"", "decisions"),
        ("programs", "addition", "Cost by pipeline program and phase", "Boards make portfolio decisions by program", "programs"),
        ("rd_sga", "addition", "R&D versus SG&A split", "R&D intensity and the R&D tax credit", "rd_sga"),
        ("ai_rd", "addition", "AI value in R&D and safety", "Time saved on the critical path shows value", "ai_rd_value"),
        ("inspection", "addition", "Inspection readiness", "A finding on a GxP system becomes a board matter", "inspection"),
        ("patient", "addition", "Patient and trial data exposure", "GDPR fines and trial data integrity", "patient_data"),
        ("ai_reg", "addition", "AI regulation readiness", "Upcoming AI rules need board oversight", "ai_regulation"),
        ("cyber", "addition", "Cyber oversight (if publicly listed)", "US SEC rules on disclosing board oversight of cyber risk", "cyber"),
        ("vendors", "addition", "Vendor concentration", "An outage or price change can stall trials or manufacturing", "vendors"),
        ("gmp_ai", "addition", "Manufacturing and quality AI (if applicable)", "AI in GMP work is the next area inspectors will look at", "gmp_ai"),
    ]

    def build(self):
        out = []
        for n, (key, kind, title, why, fn) in enumerate(self.SECTIONS, 1):
            try:
                body = getattr(self, fn)()
            except Exception as e:  # noqa: BLE001 - one section never breaks the pack
                body = {"status": "error", "gap": f"Could not compute: {str(e)[:200]}", "metrics": []}
            out.append({"n": n, "key": key, "type": kind, "title": title, "why": why, **body})
        return {"as_of": self.eng.as_of().isoformat(), "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "sections": out,
                "caution": "Before showing rows 6 and 10 to a board, check the EU AI Act dates in force and the current US R&D tax rules."}


def _money(v):
    v = float(v or 0)
    a = abs(v)
    if a >= 1e6:
        return f"${v / 1e6:,.2f}M"
    if a >= 1e3:
        return f"${v / 1e3:,.1f}K"
    return f"${v:,.0f}"
