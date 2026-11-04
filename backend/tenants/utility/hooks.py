"""Utility hooks: the tenant-specific parts of the shared engines. Same five functions and signatures as
tenants/pharma/hooks.py (see tenants/__init__.py); costiq/tenant.py hooks() loads the active one.

  findings(eng, scope, add)               cross-subsidy, suspense, capital held out of 107, CIP review requests, token check
  unlock(prudence, scope, filing=None)    earnings unlock: carrying return where a commission order allows deferral
  board_ai_metrics(bp)                    AI cost per customer contact, technology O&M per retail customer
  board_sections()                        rate-case readiness, capital vs O&M by function, T/D/Corporate IT split,
                                          cross-subsidy, Copilot seats (tenants/utility/board.py)
  summary_compliance(summ, b)             AI Summary sentences for the NERC CIP & Data section
"""
from __future__ import annotations
import datetime as dt
from collections import defaultdict


def _rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def _m(v):
    return f"${float(v or 0):,.0f}"


def _suspense_share(eng, scope, cc):
    """(share %, what it is a share of) for untagged cost-center spend. The Allocation tag panel's figure (untagged ÷ AWS
    and Azure usage, Engines.tag_compliance) for UT-15's month when that engine exists; else UT-15's own taggable spend."""
    share = 100 * (cc["UntaggedSpend"] or 0) / cc["TaggableSpend"] if cc["TaggableSpend"] else 0
    tag_compliance = getattr(eng, "tag_compliance", None)
    if tag_compliance is None:
        return share, "taggable spend"
    try:
        start = next((p.get("default") for p in eng.reg.get("UT-15").get("params", []) if p.get("name") == "period_start"), None)
        t = tag_compliance(scope, month=str(start)[:7]) if start else tag_compliance(scope)
        pct = (t.get("suspense") or {}).get("share_pct") if t.get("available") else None
        return (float(pct), "AWS and Azure usage") if pct is not None else (share, "taggable spend")
    except Exception:  # noqa: BLE001 - the finding keeps UT-15's own share
        return share, "taggable spend"


# ------------------------------------------------------------------------------------------- findings
def findings(eng, scope, add):
    """Utility detectors from the UT use cases (enterprise scope only). add(uid, title, kind, severity, exposure, detail,
    owner, formula, evidence) as in Engines.findings."""
    r = [x for x in eng._uc("UT-03", scope) if (x["CrossSubsidy"] or 0) > 0]
    if r:
        apps = sorted({x["Application"] for x in r if x["Application"]})
        add("UT-03", "Non-utility affiliate spend carried by regulated companies (cross-subsidy)", "compliance", "high",
            sum(x["CrossSubsidy"] for x in r),
            f"{', '.join(apps) or r[0]['CostCenter']} tagged {r[0]['CostCenter']} · {len({x['ChargedToEntity'] for x in r})} companies",
            "Service Company Accounting",
            "Allocated cost of accounts and cost centers the non-utility affiliate owns that landed on regulated companies.", r)
    r = eng._uc("UT-15", scope)
    cc = next((x for x in r if x["TagKey"] == "cost_center"), None)
    if cc and (cc["UntaggedSpend"] or 0) + (cc["InvalidSpend"] or 0) > 0:
        share, base = _suspense_share(eng, scope, cc)
        add("UT-15", "Spend in suspense: no cost center or an invalid one", "exception", "high",
            (cc["UntaggedSpend"] or 0) + (cc["InvalidSpend"] or 0),
            f"{_m(cc['UntaggedSpend'])} untagged ({share:.1f}% of {base}), {_m(cc['InvalidSpend'])} with invalid values",
            "Corporate IT Finance / Cloud CoE",
            "Taggable spend with no valid cost_center tag; it waits in suspense at the service company until tagged or "
            "restated through an approved rule.", r)
    r = [x for x in eng._uc("UT-16", scope) if str(x["Status"]).startswith("Held")]
    if r:
        add("UT-16", "Capitalized spend held out of 107", "exception", "medium", sum(x["HeldOutOf107"] or 0 for x in r),
            "; ".join(f"{x['WorkOrderId'] if x['WorkOrderId'] != '(none)' else 'no work order'} {_m(x['HeldOutOf107'])}" for x in r),
            "Regulatory accounting",
            "Capitalized lines with no work order (no_work_order) or on a work order with no probable-to-complete date "
            "(capitalizable_without_threshold_date); expensed until fixed.", r)
    r = [x for x in eng._uc("UT-07", scope) if x["ReviewStatus"] == "pending"]
    if r:
        masked = [x for x in r if x["ResourceNames"] == "Masked"]
        add("UT-07", "Cloud workloads awaiting the client's CIP review", "compliance", "medium", sum(x["EffectiveCost"] or 0 for x in r),
            f"{len(r)} workloads; names masked on {', '.join(x['Account'] for x in masked) or 'none'}", "NERC CIP Compliance",
            "Spend in accounts the CIP program has been asked to classify (cip_review_pending). Spend shown.", r)
    r = [x for x in eng._uc("UT-21", scope) if (x["MissedIfCacheIgnored"] or 0) > 0]
    if r:
        top = max(r, key=lambda x: x["MissedIfCacheIgnored"])
        add("UT-21", "AI cost understated if cache tokens are left out", "exception", "low", sum(x["MissedIfCacheIgnored"] for x in r),
            f"{top['Application']}: {_m(top['MissedIfCacheIgnored'])} of {_m(top['TotalCost'])}", "AI & Copilot Program Owner",
            "Cost of cache-read and cache-write tokens that an input-plus-output sum would miss.", r)
    r = [x for x in eng._uc("UT-01", scope) if x["Status"] == "Unmapped"]
    if r:
        add("UT-01", "Technology spend with no FERC account", "exception", "medium", sum(x["AllocatedCost"] or 0 for x in r),
            f"{len(r)} company lines unmapped", "Regulatory accounting",
            "Allocated O&M with no account map row (unmapped_ferc_account).", r)
    r = [x for x in eng._uc("UT-06", scope) if x["Timing"] == "In rate year"]
    if r:
        add("UT-06", "Contract escalators landing in the rate year", "estimate", "low", sum(x["AnnualEscalation"] or 0 for x in r),
            f"{len(r)} renewals in {r[0]['Docket']}'s rate year", "Rates & Regulatory",
            "Annual contract value x escalator for renewals inside the rate year: file as known and measurable, or renegotiate.", r)


# ------------------------------------------------------------------------------------------- unlock
def unlock(prudence, scope, filing=None):
    """Earnings unlock by jurisdiction: the carrying return on recurring cloud and SaaS fees if they were deferred to a
    regulatory asset (no order on file yet), and the return on what is deferred today under an order (182.3)."""
    from fastapi import HTTPException
    from costiq.prudence import expense_classes_sql
    db = prudence.db
    pol = {r["Jurisdiction"]: r for r in _rows(db.query("SELECT * FROM ref_jurisdiction_policy", {}, None, limit=None))}
    filings = prudence.filings()
    ror = {r["FilingId"]: float(r["PreTaxRor"]) for r in _rows(db.query("SELECT FilingId, PreTaxRor FROM ref_rate_case", {}, None,
                                                                       limit=None)) if r["PreTaxRor"] is not None}
    b0 = min((f["ReviewPeriodStart"] for f in filings), default=None)
    b1 = max((f["ReviewPeriodEnd"] for f in filings), default=None)
    if b0 is None:
        return {"jurisdictions": [], "credit_unlock": 0, "credit_claimed": 0, "formula": "", "disclaimer": ""}
    ts = lambda v: v if isinstance(v, dt.datetime) else dt.datetime.fromisoformat(str(v))  # noqa: E731
    p = {"b0": ts(b0), "b1": ts(b1)}
    base = _rows(db.query(f"""
        SELECT Jurisdiction, LegalEntity, ServiceProviderName AS Vendor, COUNT(DISTINCT PeriodStart) AS Months,
               SUM(AllocatedCost) AS Fees
        FROM allocated_cost
        WHERE ChargeCategory = 'Purchase' AND ChargeFrequency = 'Recurring' AND CostClass IN ({expense_classes_sql()})
          AND PeriodStart >= :b0 AND PeriodStart < :b1
        GROUP BY 1, 2, 3""", p, scope, limit=None))
    deferred = {r["Jurisdiction"]: float(r["Amt"] or 0) for r in _rows(db.query("""
        SELECT Jurisdiction, SUM(AllocatedCost) AS Amt FROM allocated_cost
        WHERE CostClass = 'regulatory_asset' AND PeriodStart >= :b0 AND PeriodStart < :b1 GROUP BY 1""", p, scope, limit=None))}
    contract_end = {r[0]: r[1] for r in db.query("SELECT Vendor, ContractEnd FROM ref_vendor", {}, None, limit=None)["rows"]}
    by_j = {f["Jurisdiction"]: f for f in filings}
    open_rates = [ror[f["FilingId"]] for f in filings if f["FilingId"] in ror and f["Jurisdiction"] != "FERC"]
    avg = sum(open_rates) / len(open_rates) if open_rates else 8.0

    def term(vendor, start):
        end = contract_end.get(vendor)
        if end is None:
            return 62
        e = ts(end)
        n = (e.year - start.year) * 12 + e.month - start.month
        return max(12, n) if e > start else 62

    out = defaultdict(lambda: {"AnnualFees": 0.0, "Balance": 0.0, "Contracts": []})
    for b in base:
        j = b["Jurisdiction"]
        if pol.get(j, {}).get("CreditStatus", "not_applicable") == "not_applicable":
            continue
        annual = float(b["Fees"] or 0) * 12 / max(1, int(b["Months"]))
        start = ts(by_j[j]["ForecastYearStart"]) if j in by_j else dt.datetime(2027, 1, 1)
        n = term(b["Vendor"], start)
        o = out[j]
        o["AnnualFees"] += annual
        o["Balance"] += annual / 12 * (n + 1) / 2
        o["Contracts"].append({"Vendor": b["Vendor"], "LegalEntity": b["LegalEntity"], "AnnualFees": round(annual, 2),
                               "Months": n})
    result = []
    for j in sorted(set(out) | {k for k in deferred if pol.get(k, {}).get("CreditStatus") == "claimed"}):
        o = out[j]
        f = by_j.get(j)
        rate, basis = (ror[f["FilingId"]], "docket") if f and f["FilingId"] in ror else (avg, "average of open dockets")
        status = pol[j]["CreditStatus"]
        if status == "claimed":   # order on file: the return on the balance deferred to 182.3 today
            credit = deferred.get(j, 0.0) * rate / 100
        else:
            credit = o["Balance"] * rate / 100
        result.append({"Jurisdiction": j, "CreditStatus": status, "Reference": pol[j]["Reference"],
                       "FilingId": f["FilingId"] if f else None, "CreditRate": round(rate, 2), "RateBasis": basis,
                       "AnnualQualifyingFees": round(o["AnnualFees"], 2), "Credit": round(credit, 2),
                       "Claimed": status == "claimed",
                       "Contracts": sorted(o["Contracts"], key=lambda c: -c["AnnualFees"])})
    if filing:
        d = next((x for x in filings if x["FilingId"] == filing), None)
        if not d:
            raise HTTPException(404, f"Unknown docket {filing}")
        result = [r for r in result if r["Jurisdiction"] == d["Jurisdiction"]]
    unlocked = sum(r["Credit"] for r in result if r["CreditStatus"] == "eligible")
    claimed = sum(r["Credit"] for r in result if r["Claimed"])
    return {"jurisdictions": result, "credit_unlock": round(unlocked, 2), "credit_claimed": round(claimed, 2),
            "formula": "Earnings unlock = average regulatory-asset balance x the docket's pre-tax rate of return. Balance = "
                       "annualized test-period recurring cloud and SaaS fees / 12 x (amortization months + 1) / 2, over the "
                       "contract term (62 months when none). Unlock = jurisdictions with no order on file.",
            "disclaimer": "Depends on a commission order allowing deferral and on the authorized return."}


# ------------------------------------------------------------------------------------------- Board Pack
def board_ai_metrics(bp):
    """AI cost per customer contact (contact-center summarization) and technology O&M per retail customer."""
    from costiq.board import _f
    unit = bp.eng._uc("UT-10", None)
    months = sorted({r["Month"] for r in unit})
    last = next((m for m in reversed(months) if not str(m).startswith("2026-10")), months[-1] if months else None)
    lu = [r for r in unit if r["Month"] == last]
    customers = sum(r["RetailCustomers"] or 0 for r in lu)
    contacts = sum(r["CustomerContacts"] or 0 for r in lu)
    oam = sum(_f(r["TechnologyOAndM"]) for r in lu)
    ai = sum(_f(r["AiCostPerContact"]) * (r["CustomerContacts"] or 0) for r in lu)
    return [
        {"label": "AI cost per customer contact", "value": round(ai / contacts, 4) if contacts else None, "fmt": "money2",
         "note": f"Contact-center summarization, {str(last)[:7]} · {contacts:,} contacts (UT-10)"},
        {"label": "Technology O&M per retail customer", "value": round(12 * oam / customers, 2) if customers else None,
         "fmt": "money2", "note": f"Annualized, {str(last)[:7]} · {customers:,} retail customers"},
    ]


def board_sections():
    """(key, kind, title, why, fn(bp) -> section body) after the four core sections."""
    from . import board as b
    return [
        ("rate_case", "addition", "Rate-case readiness", "Technology O&M has to be evidenced in every open docket", b.rate_case),
        ("capital_om", "addition", "Capital vs O&M by FERC function", "Placement decides what earns a return and what reaches rates",
         b.capital_om),
        ("tdit", "addition", "Transmission, Distribution and Corporate IT split", "Function drives formula-rate and retail recovery",
         b.function_split),
        ("cross_subsidy", "addition", "Cross-subsidy", "Regulated customers must not carry non-utility work", b.cross_subsidy),
        ("copilot", "addition", "Copilot seats", "Seats reassigned before the renewal true-down avoid new purchases", b.copilot_seats),
    ]


# ------------------------------------------------------------------------------------------- AI Summary
def summary_compliance(summ, b):
    """Sentences for the NERC CIP & Data section: the largest high-severity compliance findings, then the CIP review
    requests still open. `summ` is the Summarizer, `b` the Brief (b.f registers a cited fact)."""
    from costiq.summary import money, plural
    p, s = b.p, []
    fnd = [x for x in summ.eng.findings(p.scope) if x["kind"] == "compliance" and x["severity"] == "high"]
    for x in sorted(fnd, key=lambda x: -x["exposure"])[:2]:
        ref = summ._finding(b, x, x["id"], f"{x['title']} ({x['detail']})")
        amt = summ._finding(b, x, money(x["exposure"]), "Spend affected")
        if x["id"] == "UT-03":
            s.append(f"{amt} of non-utility affiliate spend landed on regulated companies ({x['detail']}), flagged by {ref}.")
            b.lead.setdefault("compliance", f"{amt} of affiliate spend sits on regulated companies")
            b.act("compliance", "Retag the affiliate's account and restate the months", f"{x['detail']} ({x['id']})",
                  f"{money(x['exposure'])} of spend", b.href("compliance", hl=x["id"]) or f"/catalog/{x['id']}", float(x["exposure"]))
        else:
            s.append(summ.finding_sentence(x, ref, amt))
    if p.can("compliance"):
        pending = summ.db.query("""SELECT COUNT(*) FROM ref_cip_review WHERE ReviewStatus = 'pending'""", {}, None)["rows"][0][0]
        if pending:
            s.append(f"{b.f(plural(int(pending), 'workload'), 'Workloads awaiting the CIP review', 'compliance', 'Checks', hl='UT-07')} "
                     f"await the CIP program's classification; names on flagged accounts stay masked until then.")
    return s
