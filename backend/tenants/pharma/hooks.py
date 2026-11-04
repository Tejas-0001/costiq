"""Pharma hooks: the tenant-specific parts of the shared engines. Every tenant's hooks.py defines these five functions
with the same signatures (see tenants/__init__.py); costiq/tenant.py hooks() loads the active one.

  findings(eng, scope, add)               Command Center / Board Pack findings from the tenant's use cases
  unlock(prudence, scope, filing=None)    the "unlock" figure (pharma: R&D credit eligible but not claimed)
  board_ai_metrics(bp)                    unit-cost metrics in the core "AI investment and value" Board Pack section
  board_sections()                        Board Pack sections after the four core ones
  summary_compliance(summ, b)             AI Summary sentences for the compliance section
"""
from __future__ import annotations
import datetime as dt
from collections import defaultdict


def _rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


# ------------------------------------------------------------------------------------------- findings
def findings(eng, scope, add):
    """Transfer-pricing, GxP and patient-data findings from the pharma use cases (enterprise scope only).
    add(uid, title, kind, severity, exposure, detail, owner, formula, evidence) as in Engines.findings."""
    r = [x for x in eng._uc("PH-03", scope) if (x["CrossCharge"] or 0) > 0]
    if r:
        add("PH-03", "GxP entities carrying Consumer Health work (cross-charging)", "compliance", "high",
            sum(x["CrossCharge"] for x in r), f"{r[0]['CostCenter']} via {r[0]['OpcoPolicy']} · {len(r)} allocations",
            "Transfer Pricing", "Allocated cost from Consumer Health cost centers that landed on GxP entities.", r)
    r = [x for x in eng._uc("PH-07", scope) if x["Status"] == "Provider can decrypt patient data"]
    if r:
        add("PH-07", "Patient data in cloud without customer-managed keys", "compliance", "high", sum(x["EffectiveCost"] or 0 for x in r),
            ", ".join(x["Application"] for x in r), "GxP Quality & Data Privacy",
            "Workloads holding patient data where the provider manages the keys (spend shown).", r)
    r = [x for x in eng._uc("PH-01", scope) if x["Status"] == "Unmapped"]
    if r:
        add("PH-01", "Technology spend with no GL account", "exception", "medium", sum(x["AllocatedCost"] or 0 for x in r),
            f"{len(r)} entity lines unmapped", "Internal Controller",
            "Allocated opex with no ref_gl_account_map match; needs a manual entry before close and filing.", r)
    r = [x for x in eng._uc("PH-06", scope) if x["Timing"] == "In forecast year"]
    if r:
        add("PH-06", "Contract escalators landing in the forecast year", "estimate", "low", sum(x["AnnualEscalation"] or 0 for x in r),
            f"{len(r)} renewals in {r[0]['FilingId']}'s forecast year", "FP&A Analyst",
            "Annual contract value × escalator for renewals inside the forecast year. Renegotiate or budget for it.", r)


# ------------------------------------------------------------------------------------------- unlock
def unlock(prudence, scope, filing=None):
    """R&D credit on recurring cloud and SaaS fees by jurisdiction: what is claimed today, and what would be claimed
    where the fees qualify but are not yet claimed."""
    from fastapi import HTTPException
    from costiq.prudence import expense_classes_sql
    from costiq.regulatory import credit_rate  # (rate %, basis, filing): own filing, else the entity's, else the average
    db = prudence.db
    pol = {r["Jurisdiction"]: r for r in _rows(db.query("SELECT * FROM ref_jurisdiction_policy", {}, None, limit=None))}
    filings = prudence.filings()
    b0 = min((f["ReviewPeriodStart"] for f in filings), default=None)
    b1 = max((f["ReviewPeriodEnd"] for f in filings), default=None)
    if b0 is None:
        return {"jurisdictions": [], "credit_unlock": 0, "credit_claimed": 0}
    ts = lambda v: v if isinstance(v, dt.datetime) else dt.datetime.fromisoformat(str(v))  # noqa: E731
    base = _rows(db.query(f"""
        SELECT Jurisdiction, LegalEntity, ServiceProviderName AS Vendor, COUNT(DISTINCT PeriodStart) AS Months,
               SUM(AllocatedCost) AS Fees
        FROM allocated_cost
        WHERE ChargeCategory = 'Purchase' AND ChargeFrequency = 'Recurring' AND CostClass IN ({expense_classes_sql()})
          AND PeriodStart >= :b0 AND PeriodStart < :b1
        GROUP BY 1, 2, 3""", {"b0": ts(b0), "b1": ts(b1)}, scope, limit=None))
    out = defaultdict(lambda: {"AnnualFees": 0.0, "Contracts": [], "Entity": None})
    for b in base:
        j = b["Jurisdiction"]
        if pol.get(j, {}).get("CreditStatus", "not_applicable") == "not_applicable":
            continue
        annual = float(b["Fees"] or 0) * 12 / max(1, int(b["Months"]))
        o = out[j]
        o["Entity"] = b["LegalEntity"]
        o["AnnualFees"] += annual
        o["Contracts"].append({"Vendor": b["Vendor"], "LegalEntity": b["LegalEntity"], "AnnualFees": round(annual, 2)})
    result = []
    for j, o in sorted(out.items()):
        rate, basis, f = credit_rate(j, o["Entity"], filings)  # e.g. Puerto Rico uses its entity's US filing rate
        status = pol[j]["CreditStatus"]
        result.append({"Jurisdiction": j, "CreditStatus": status, "Reference": pol[j]["Reference"],
                       "FilingId": f["FilingId"] if f else None, "CreditRate": round(rate, 2), "RateBasis": basis,
                       "AnnualQualifyingFees": round(o["AnnualFees"], 2),
                       "Credit": round(o["AnnualFees"] * rate / 100, 2),
                       "Claimed": status == "claimed",
                       "Contracts": sorted(o["Contracts"], key=lambda c: -c["AnnualFees"])})
    if filing:
        d = next((f for f in filings if f["FilingId"] == filing), None)
        if not d:
            raise HTTPException(404, f"Unknown filing {filing}")
        result = [r for r in result if r["Jurisdiction"] == d["Jurisdiction"]]
    unlocked = sum(r["Credit"] for r in result if r["CreditStatus"] == "eligible")
    claimed = sum(r["Credit"] for r in result if r["Claimed"])
    return {"jurisdictions": result, "credit_unlock": round(unlocked, 2), "credit_claimed": round(claimed, 2),
            "formula": "Credit = annualized review-period recurring cloud and SaaS fees x the jurisdiction's credit rate. "
                       "Unlock = jurisdictions where the fees qualify but are not yet claimed.",
            "disclaimer": "Illustrative: depends on each jurisdiction's qualifying rules and on substantiation."}


# ------------------------------------------------------------------------------------------- Board Pack
def board_ai_metrics(bp):
    """Technology cost per enrolled subject (PH-10) and AI cost per accepted study report section."""
    from costiq.board import _f, _money
    unit = bp.eng._uc("PH-10", None)
    last = max((r["Month"] for r in unit), default=None)
    lu = [r for r in unit if r["Month"] == last]
    patients = sum(r["EnrolledPatients"] or 0 for r in lu)
    opex = sum(r["TechnologyOpex"] or 0 for r in lu)
    csr = bp.q("""SELECT SUM(AcceptedOutcomes) AS Accepted FROM ai_app_telemetry_daily WHERE Application = 'csr-drafting-agent'""")[0]
    csr_cost = bp.q("""SELECT SUM(EffectiveCost) AS C FROM CostAndUsage
                       WHERE json_extract_string(Tags, '$.application') = 'csr-drafting-agent'""")[0]["C"]
    return [
        {"label": "Technology cost per enrolled subject", "value": round(12 * opex / patients, 2) if patients else None,
         "fmt": "money2", "note": f"Annualized, {last} · {patients:,} enrolled subjects (PH-10)"},
        {"label": "AI cost per study report section",
         "value": round(_f(csr_cost) / float(csr["Accepted"]), 2) if csr["Accepted"] else None, "fmt": "money2",
         "note": f"CSR drafting agent: {_money(_f(csr_cost))} ÷ {int(csr['Accepted'] or 0):,} accepted sections"},
    ]


def board_sections():
    """(key, kind, title, why, fn(bp) -> section body) after the four core sections."""
    from . import board as b
    return [
        ("programs", "addition", "Cost by pipeline program and phase", "Boards make portfolio decisions by program", b.programs),
        ("rd_sga", "addition", "R&D versus SG&A split", "R&D intensity and the R&D tax credit", b.rd_sga),
        ("ai_rd", "addition", "AI value in R&D and safety", "Time saved on the critical path shows value", b.ai_rd_value),
        ("inspection", "addition", "Inspection readiness", "A finding on a GxP system becomes a board matter", b.inspection),
        ("patient", "addition", "Patient and trial data exposure", "GDPR fines and trial data integrity", b.patient_data),
        ("ai_reg", "addition", "AI regulation readiness", "Upcoming AI rules need board oversight", b.ai_regulation),
        ("cyber", "addition", "Cyber oversight (if publicly listed)", "US SEC rules on disclosing board oversight of cyber risk", b.cyber),
        ("vendors", "addition", "Vendor concentration", "An outage or price change can stall trials or manufacturing", b.vendors),
        ("gmp_ai", "addition", "Manufacturing and quality AI (if applicable)", "AI in GMP work is the next area inspectors will look at", b.gmp_ai),
    ]


# ------------------------------------------------------------------------------------------- AI Summary
def summary_compliance(summ, b):
    """Sentences for the compliance section: the two largest high-severity compliance findings, then the GxP system
    whose validation review is most overdue. `summ` is the Summarizer, `b` the Brief (b.f registers a cited fact)."""
    from costiq.summary import money, plural
    p, s = b.p, []
    fnd = [x for x in summ.eng.findings(p.scope) if x["kind"] == "compliance" and x["severity"] == "high"]
    for x in sorted(fnd, key=lambda x: -x["exposure"])[:2]:
        ref = summ._finding(b, x, x["id"], f"{x['title']} ({x['detail']})")
        amt = summ._finding(b, x, money(x["exposure"]), "Spend affected")
        if x["id"] == "PH-07":
            s.append(f"{amt} of spend runs where patient data is encrypted with provider-managed keys ({x['detail']}), flagged by {ref}.")
            b.lead.setdefault("compliance", f"{amt} of spend holds patient data on provider-managed keys")
            b.act("compliance", "Move patient-data workloads to customer-managed keys", f"{x['detail']} ({x['id']})",
                  f"{money(x['exposure'])} of spend", b.href("compliance", hl=x["id"]) or f"/catalog/{x['id']}", float(x["exposure"]))
        elif x["id"] == "GV-02":
            s.append(f"A regulated workload has been served outside the US ({x['detail']}), {amt} of spend, flagged by {ref}.")
        else:
            s.append(summ.finding_sentence(x, ref, amt))
    overdue = summ.db.query("""SELECT SystemName, NextReviewDate FROM ref_gxp_system WHERE GxpImpact <> 'None'
                               AND NextReviewDate < :d ORDER BY NextReviewDate""", {"d": summ.eng.as_of()})["rows"]
    if overdue and p.can("compliance"):
        name, d = overdue[0]
        late = (summ.eng.as_of() - dt.date.fromisoformat(str(d)[:10])).days
        mod = "board" if p.can("board") else "compliance"
        sec = "Inspection readiness" if mod == "board" else "Checks"
        s.append(f"The validation review for {b.f(name, 'GxP system with an overdue review', mod, sec, hl=name)} is "
                 f"{b.f(plural(late, 'day'), 'Days past the review date', mod, sec, hl=name)} overdue.")
    return s
