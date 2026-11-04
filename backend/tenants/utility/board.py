"""Utility Board Pack additions (tenants/utility/hooks.py board_sections): five sections after the four core ones.
Each function takes the BoardPack (costiq/board.py) and returns a section body: status, gap, metrics, table, formula."""
from __future__ import annotations
import datetime as dt
from costiq.board import _f, _money

BU_LABEL = {"TRANSMISSION": "Transmission", "DISTRIBUTION": "Distribution", "CORPORATE_IT": "Corporate IT",
            "SHARED_SERVICES": "Shared Services", "NON_UTILITY": "Non-utility affiliate"}
FN_LABEL = {"T": "Transmission", "D": "Distribution", "CUST": "Customer accounts", "AG": "Administrative and general",
            "GENERAL": "General (multi-function)", "NONUTIL": "Non-utility"}


def rate_case(bp):
    risk = bp.prudence.summary(None)
    as_of = bp.eng.as_of()
    rows = []
    for d in risk["by_filing"]:
        f = next((x for x in risk["filings"] if x["FilingId"] == d["FilingId"]), {})
        fd = dt.date.fromisoformat(str(f.get("FilingDate"))[:10]) if f.get("FilingDate") else None
        rows.append({"Docket": d["FilingId"], "Company": d["LegalEntity"], "Jurisdiction": d["Jurisdiction"], "Status": d["Status"],
                     "FilingDate": fd.isoformat() if fd else None, "DaysToFiling": (fd - as_of).days if fd else None,
                     "TestPeriodSpend": _f(d["Spend"]), "EvidenceScore": d["Score"], "DisallowanceRisk": _f(d["Risk"])})
    rec = bp.eng._uc("UT-18", None)
    tied = sum(1 for r in rec if r["Status"] in ("Ties to the cent", "Difference labeled"))
    return {
        "status": "live",
        "metrics": [
            {"label": "Open dockets", "value": len(rows), "fmt": "count"},
            {"label": "Test-period spend evidenced", "value": risk["evidence_score"], "fmt": "pct",
             "note": f"{_money(risk['review_period_spend'])} of test-period technology O&M"},
            {"label": "Disallowance at risk", "value": _f(risk["deduction_at_risk"]), "fmt": "money",
             "tone": "bad" if risk["deduction_at_risk"] else "good"},
            {"label": "September invoices tied or labeled", "value": tied, "fmt": "count", "of": len(rec)},
        ],
        "table": {"columns": ["Docket", "Company", "Jurisdiction", "Status", "FilingDate", "DaysToFiling", "TestPeriodSpend",
                              "EvidenceScore", "DisallowanceRisk"], "rows": rows},
        "formula": risk["formula"],
    }


def capital_om(bp):
    rows = bp.q("""SELECT COALESCE(PnlFunction, 'Not mapped') AS Function, CostClass, COALESCE(GlAccount, '(unmapped)') AS Account,
                          SUM(AllocatedCost) AS Cost
                   FROM allocated_cost GROUP BY 1, 2, 3 ORDER BY 4 DESC""")
    total = sum(_f(r["Cost"]) for r in rows) or 1
    cap = sum(_f(r["Cost"]) for r in rows if r["CostClass"] == "cwip")
    reg = sum(_f(r["Cost"]) for r in rows if r["CostClass"] == "regulatory_asset")
    om = sum(_f(r["Cost"]) for r in rows if r["CostClass"] == "om")
    held = sum(_f(x["HeldOutOf107"]) for x in bp.eng._uc("UT-16", None) if str(x["Status"]).startswith("Held"))
    return {
        "status": "partial",
        "gap": "In-service transfers (106/101) and 403 depreciation come from the work-order register until the plant ledger "
               "feed is connected.",
        "metrics": [{"label": "Construction work in progress (107)", "value": round(100 * cap / total, 1), "fmt": "pct", "note": _money(cap)},
                    {"label": "Functional O&M", "value": round(100 * om / total, 1), "fmt": "pct", "note": _money(om)},
                    {"label": "Regulatory asset (182.3)", "value": round(reg, 2), "fmt": "money"},
                    {"label": "Capitalized spend held out of 107", "value": round(held, 2), "fmt": "money",
                     "tone": "bad" if held else "good", "note": "No work order, or no threshold date (UT-16)"}],
        "table": {"columns": ["Function", "CostClass", "Account", "Cost", "Share"],
                  "rows": [{"Function": r["Function"], "CostClass": r["CostClass"], "Account": r["Account"], "Cost": _f(r["Cost"]),
                            "Share": round(100 * _f(r["Cost"]) / total, 1)} for r in rows[:14]]},
        "formula": "Allocated cost by FERC function, cost class and account since July. 107 needs an open work order with a "
                   "threshold date (R1); recurring fees are functional O&M (R4-R8).",
    }


def function_split(bp):
    rows = bp.q("""SELECT COALESCE(c.BusinessUnit, 'SUSPENSE') AS BusinessUnit, COALESCE(c.FercFunction, '-') AS FercFunction,
                          SUM(a.AllocatedCost) AS Cost
                   FROM allocated_cost a LEFT JOIN ref_cost_center c ON c.CostCenter = a.CostCenter
                   GROUP BY 1, 2 ORDER BY 3 DESC""")
    total = sum(_f(r["Cost"]) for r in rows) or 1
    by = {}
    for r in rows:
        k = BU_LABEL.get(r["BusinessUnit"], "Suspense")
        by[k] = by.get(k, 0) + _f(r["Cost"])
    share = lambda k: round(100 * by.get(k, 0) / total, 1)  # noqa: E731
    return {
        "status": "live",
        "metrics": [{"label": "Transmission", "value": share("Transmission"), "fmt": "pct", "note": _money(by.get("Transmission", 0))},
                    {"label": "Distribution", "value": share("Distribution"), "fmt": "pct", "note": _money(by.get("Distribution", 0))},
                    {"label": "Corporate IT", "value": share("Corporate IT"), "fmt": "pct", "note": _money(by.get("Corporate IT", 0))},
                    {"label": "Suspense", "value": share("Suspense"), "fmt": "pct", "note": _money(by.get("Suspense", 0)),
                     "tone": "bad" if by.get("Suspense") else "good"}],
        "table": {"columns": ["BusinessUnit", "FercFunction", "Cost", "Share"],
                  "rows": [{"BusinessUnit": BU_LABEL.get(r["BusinessUnit"], "Suspense"),
                            "FercFunction": FN_LABEL.get(r["FercFunction"], r["FercFunction"]), "Cost": _f(r["Cost"]),
                            "Share": round(100 * _f(r["Cost"]) / total, 1)} for r in rows]},
        "formula": "Allocated cost by the business unit and FERC function of its cost center (ref_cost_center). Suspense is "
                   "spend with no valid cost center.",
    }


def cross_subsidy(bp):
    r = bp.eng._uc("UT-03", None)
    total = sum(_f(x["CrossSubsidy"]) for x in r)
    by = {}
    for x in r:
        by[x["ChargedToEntity"]] = by.get(x["ChargedToEntity"], 0) + _f(x["CrossSubsidy"])
    return {
        "status": "live",
        "metrics": [{"label": "Affiliate spend on regulated companies", "value": round(total, 2), "fmt": "money",
                     "tone": "bad" if total else "good", "note": "September (UT-03)"},
                    {"label": "Regulated companies affected", "value": len(by), "fmt": "count"}],
        "table": {"columns": ["Company", "CrossSubsidy"],
                  "rows": [{"Company": k, "CrossSubsidy": round(v, 2)} for k, v in sorted(by.items(), key=lambda kv: -kv[1])]},
        "formula": "Allocated cost of accounts and cost centers the non-utility affiliate owns that landed on regulated companies.",
    }


def copilot_seats(bp):
    seats = bp.eng._uc("UT-20", None)
    idle = [x for x in seats if x["Status"] == "Idle"]
    grace = [x for x in seats if x["Status"] == "Grace period"]
    total = bp.q("SELECT COUNT(*) AS N, MAX(MonthlySeatCost) AS P FROM ai_seat_assignment WHERE x_AIProduct = 'm365_copilot'")[0]
    by = {}
    for x in idle:
        d = by.setdefault(x["Department"] or "Unassigned", {"Group": 0, "Direct": 0})
        d[x["AssignedVia"]] += 1
    price = _f(total["P"])
    return {
        "status": "live",
        "metrics": [{"label": "M365 Copilot seats", "value": int(total["N"] or 0), "fmt": "count"},
                    {"label": "Idle beyond 30 days", "value": len(idle), "fmt": "count", "of": int(total["N"] or 0),
                     "note": f"{sum(1 for x in idle if x['AssignedVia'] == 'Group')} group-assigned, "
                             f"{sum(1 for x in idle if x['AssignedVia'] == 'Direct')} direct; {len(grace)} in the grace period"},
                    {"label": "Avoided cost if reassigned", "value": round(len(idle) * price, 2), "fmt": "money",
                     "note": "A month; the bill drops at the renewal true-down"}],
        "table": {"columns": ["Department", "Group", "Direct", "MonthlyCost"],
                  "rows": [{"Department": k, "Group": v["Group"], "Direct": v["Direct"],
                            "MonthlyCost": round((v["Group"] + v["Direct"]) * price, 2)}
                           for k, v in sorted(by.items(), key=lambda kv: -(kv[1]["Group"] + kv[1]["Direct"]))]},
        "formula": "Idle = no activity for more than 30 days before the refresh date, after a 2-day latency buffer; seats assigned "
                   "in the last 14 days are held back (UT-20).",
    }
