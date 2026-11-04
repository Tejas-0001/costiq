"""Pharma Board Pack additions (tenants/pharma/hooks.py board_sections): nine sections after the four core ones.
Each function takes the BoardPack (costiq/board.py) and returns a section body: status, gap, metrics, table, formula."""
from __future__ import annotations
import datetime as dt
from costiq.board import _f, _money


def programs(bp):
    rows = bp.q("""
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
    span = bp.q("SELECT MIN(ChargePeriodStart) AS a, MAX(ChargePeriodStart) AS b FROM CostAndUsage")[0]
    d0 = lambda v: dt.date.fromisoformat(str(v)[:10])  # noqa: E731
    days = max(1, (d0(span["b"]) - d0(span["a"])).days + 1) if span["a"] else 1
    total = _f(bp.q("SELECT SUM(EffectiveCost) AS T FROM CostAndUsage")[0]["T"])
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

def rd_sga(bp):
    rows = bp.q("""SELECT COALESCE(PnlFunction, 'Not mapped') AS Function, CostClass, SUM(AllocatedCost) AS Cost
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

def ai_rd_value(bp):
    apps = {"csr-drafting-agent": "Days to a draft study report section", "ae-intake-bot": "Adverse-event case processing time",
            "protocol-assistant": "Time to answer a protocol question"}
    cost = {r["Application"]: r for r in bp.q("""
        SELECT json_extract_string(Tags, '$.application') AS Application, SUM(EffectiveCost) AS Cost
        FROM CostAndUsage WHERE json_extract_string(Tags, '$.application') IN ('csr-drafting-agent', 'ae-intake-bot', 'protocol-assistant')
        GROUP BY 1""")}
    tel = {r["Application"]: r for r in bp.q("""
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

def inspection(bp):
    asof = bp.eng.as_of()
    sysrows = bp.q("SELECT Application, SystemName, GxpImpact, Owner, NextReviewDate FROM ref_gxp_system WHERE GxpImpact <> 'None'")
    due = []
    for r in sysrows:
        d = r["NextReviewDate"]
        if d is None:
            continue
        d = d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
        if d <= asof + dt.timedelta(days=90):
            due.append({"System": r["SystemName"], "GxpImpact": r["GxpImpact"], "Owner": r["Owner"], "NextReview": d.isoformat(),
                        "Status": "overdue" if d < asof else "due within 90 days"})
    open_findings = (sum(1 for x in bp.eng._uc("PH-07", None) if x["Status"] == "Provider can decrypt patient data")
                     + sum(1 for x in bp.eng._uc("PH-08", None) if x["GxpScoped"] and x.get("MissingClauses"))
                     + sum(1 for x in bp.eng._uc("PH-11", None) if x["Status"] != "Approved"))
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

def patient_data(bp):
    out_region = bp.eng._uc("GV-02", None)
    keys = [x for x in bp.eng._uc("PH-07", None) if x["Status"] == "Provider can decrypt patient data"]
    vendors = [x for x in bp.eng._uc("PH-08", None) if x["GxpScoped"] and x.get("MissingClauses")]
    trend = bp.q("""
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

def ai_regulation(bp):
    reg = bp.q("""SELECT u.Application, u.UseCase, u.RiskTier, u.ReviewStatus, u.EuAiActClass, u.GxpContext
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

def cyber(bp):
    critical = bp.q("SELECT COUNT(*) AS N FROM ref_gxp_system WHERE GxpImpact IN ('High', 'Medium')")[0]["N"]
    vendors = [x for x in bp.eng._uc("PH-08", None) if x.get("MissingClauses")]
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

def vendors(bp):
    rows = bp.q("""SELECT ServiceProviderName AS Vendor, SUM(EffectiveCost) AS Spend FROM CostAndUsage
                     GROUP BY 1 ORDER BY 2 DESC""")
    total = sum(_f(r["Spend"]) for r in rows) or 1
    hhi = round(sum((100 * _f(r["Spend"]) / total) ** 2 for r in rows))
    gxp = bp.q("""
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

def gmp_ai(bp):
    n = bp.q("SELECT COUNT(*) AS N FROM ref_ai_use WHERE GmpContext")[0]["N"]
    return {
        "status": "needs" if not n else "partial",
        "gap": "No AI in GMP work (batch release, deviations, quality control) is in the AI register yet. Add this section once "
               "such workloads exist." if not n else "Validation status comes from the AI register.",
        "metrics": [{"label": "AI uses in GMP work", "value": n, "fmt": "count"}],
        "formula": "AI use register, GMP context flag.",
    }
