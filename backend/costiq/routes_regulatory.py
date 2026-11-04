"""Tax module API (/api/tax; pharma: Tax & Transfer Pricing; utility: Regulatory Accounting & Rates): allocation chain,
filing forecast bridge, intercompany recharge schedule, credit schedule and the allocation-policy approval workflow.
Tenants whose ledger carries rule columns also get service-company billing by class, the ledger by account with its
transaction listing, the rule register and the capital path (each `{"available": false}` elsewhere). Use-case ids and
wording come from the tenant pack (pack.yaml `usecases`, copy.json and copy/rates.json backend.tax)."""
from __future__ import annotations
import csv, datetime as dt, io
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from . import tenant
from .allocation import Allocator
from .auth import Principal, current_principal
from .context import audit, db, eng, ledger, records, reg, run_use_case
from .regulatory import Regulatory, next_month

router = APIRouter(prefix="/api/tax")
regs = Regulatory(db, eng)
LISTING_ROWS = 2000   # rows a listing returns to the page; the CSV export has all of them


def _period(period: str | None):
    if not period:
        return None
    try:
        return dt.datetime.fromisoformat(period + "-01" if len(period) == 7 else period)
    except ValueError:
        raise HTTPException(400, "period must be YYYY-MM")


def _role(name: str):
    """A use-case id by role from pack.yaml `usecases`, or None when this tenant has no such feature (or the use case is
    not in the registry)."""
    uid = (tenant.pack().get("usecases") or {}).get(name)
    try:
        return uid if uid and reg.get(uid) else None
    except KeyError:
        return None


def _say(key: str, **values) -> str:
    """Backend copy (backend.tax.*) or "" when this tenant's copy has no such key."""
    try:
        return tenant.text(key, **values)
    except KeyError:
        return ""


def _month(period: str | None):
    """(period 'YYYY-MM', start, end, periods) for a billing-period select; default the latest closed period."""
    periods, closed = regs.periods()
    period = period or closed
    if not period:
        raise HTTPException(404, "No billing periods")
    start = _period(period[:7])
    return start.strftime("%Y-%m"), start, next_month(start), periods


@router.get("/allocation-chain")
def allocation_chain(period: str | None = None, p: Principal = Depends(current_principal)):
    p.require("tax")
    ps = _period(period)
    out = Allocator(db, eng).chain(p.scope, ps)
    out["period"] = period
    out["periods"] = [str(x)[:7] for x in Allocator(db, eng).periods()]
    out["pending_policies"] = [r for r in regs.policies() if r["Status"] == "submitted"]
    audit(p, "view", module="tax", view="allocation-chain", period=period)
    return out


@router.get("/filings")
def filings(p: Principal = Depends(current_principal)):
    p.require("tax")
    return regs.filings()


def _bridge(p: Principal, filing: str):
    rc = regs.filing(filing)
    res = run_use_case(p, reg.get(tenant.usecase("bridge")), {"filing": filing}, limit=None)
    lines = records(res)
    tot = lambda k: round(sum(float(l[k] or 0) for l in lines), 2)
    from .optimization import Optimizer
    verified = [a for a in Optimizer(db, eng).actions(None) if a["Status"] == "verified"]
    return rc, res, {
        "filing": rc, "lines": lines,
        "totals": {"ReviewPeriodActual": tot("ReviewPeriodActual"), "ReviewAnnualized": tot("ReviewAnnualized"),
                   "ContractEscalation": tot("ContractEscalation"), "ForecastYear": tot("ForecastYear"),
                   "InvoiceExceptions": sum(1 for l in lines if l["InvoiceTieOut"] == "Invoice exception"),
                   "VerifiedSavingsAnnual": round(12 * sum(float(a["VerifiedMonthlySavings"] or 0) for a in verified), 2)},
        "verified_savings": [{k: a[k] for k in ("OptimizationId", "Title", "VerifiedMonthlySavings", "VerificationBasis", "VerifiedOn")}
                             for a in verified],
        "formula": tenant.text("tax.bridge_formula"),
    }


@router.get("/filing/{filing}/bridge")
def bridge(filing: str, p: Principal = Depends(current_principal)):
    p.require("tax")
    _, _, out = _bridge(p, filing)
    audit(p, "view", module="tax", view="bridge", filing=filing)
    return out


@router.get("/filing/{filing}/bridge.csv")
def bridge_csv(filing: str, p: Principal = Depends(current_principal)):
    p.require("tax")
    p.require_action("exhibit.export")
    rc, res, out = _bridge(p, filing)
    buf = io.StringIO()
    uid = tenant.usecase("bridge")
    buf.write(tenant.text("tax.bridge_csv_header", filing=filing, entity=rc["LegalEntity"], jurisdiction=rc["Jurisdiction"],
                          review_start=str(rc["ReviewPeriodStart"])[:10], review_end=str(rc["ReviewPeriodEnd"])[:10],
                          forecast_start=str(rc["ForecastYearStart"])[:10], forecast_end=str(rc["ForecastYearEnd"])[:10],
                          user=p.user, prepared=f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M}", usecase=uid) + "\n")
    w = csv.writer(buf)
    w.writerow(res["columns"])
    w.writerows(res["rows"])
    t = out["totals"]
    w.writerow(["Total", t["ReviewPeriodActual"], t["ReviewAnnualized"], "", "", t["ForecastYear"], t["ContractEscalation"],
                "", f"{t['InvoiceExceptions']} invoice exceptions", ""])
    w.writerow([f"# Verified optimization savings already achieved (annualized, all entities): {t['VerifiedSavingsAnnual']:.2f} "
                f"from {len(out['verified_savings'])} verified actions; see the audit file optimization_actions.csv"])
    ledger.append(p.user, p.persona, "export", f"exhibit:{filing}:{uid}", f"{len(res['rows'])} vendor lines")
    audit(p, "export", module="tax", exhibit=uid, filing=filing)
    name = tenant.text("tax.bridge_csv_filename", filing=filing)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/recharge-schedule")
def recharge_schedule(year: int = 2026, period: str | None = None, p: Principal = Depends(current_principal)):
    """The year's recharge lines (or one billing period's with `period=YYYY-MM`). When the lines carry a billing class
    (service-company billing, 457.1 direct / 457.2 by factor), each company also gets Direct, Indirect, Total and
    DirectSharePct, and the body adds the direct share, the factor register and the tie to the ledger."""
    p.require("tax")
    if period:
        start = _period(period[:7])
        rng = {"period_start": start.date().isoformat(), "period_end": next_month(start).date().isoformat()}
    else:
        rng = {"period_start": f"{year}-01-01", "period_end": f"{year + 1}-01-01"}
    res = run_use_case(p, reg.get(tenant.usecase("recharge")), rng, limit=None)
    lines = records(res)
    by_entity = {}
    for l in lines:
        by_entity[l["RecipientEntity"]] = round(by_entity.get(l["RecipientEntity"], 0) + float(l["AmountRecharged"] or 0), 2)
    out = {"year": year, "lines": lines,
           "by_entity": [{"RecipientEntity": k, "AmountRecharged": v} for k, v in sorted(by_entity.items(), key=lambda x: -x[1])],
           "formula": tenant.text("tax.recharge_formula")}
    if "BillingClass" in res["columns"]:
        b = regs.billing(p.scope, lines, _period(rng["period_start"]), _period(rng["period_end"]))
        empty = {"Direct": 0.0, "Indirect": 0.0, "Total": 0.0, "DirectSharePct": None}
        for e in out["by_entity"]:
            e.update(b["per_entity"].get(e["RecipientEntity"], empty))
        periods, closed = regs.periods()
        out.update({"period": period[:7] if period else None, "periods": periods, "closed_period": closed,
                    "direct_share_pct": b["direct_share_pct"], "factors": b["factors"], "totals": b["totals"],
                    "billing_formula": _say("tax.billing_formula")})
    return out


# ------------------------------------------------------------------------------------------------ ledger by account
@router.get("/ledger")
def ferc_ledger(period: str | None = None, p: Principal = Depends(current_principal)):
    """The period's ledger by account, function, cost class and rule, and by company and function."""
    p.require("tax")
    if not {"FunctionCode", "RuleId", "PolicyFlag"} <= regs.ledger_columns():
        return {"available": False}
    period, start, end, periods = _month(period)
    out = regs.ferc_ledger(p.scope, start, end)
    audit(p, "view", module="tax", view="ledger", period=period)
    return {"available": True, "period": period, "periods": periods, **out, "listing": bool(_role("account_listing")),
            "formula": _say("tax.ledger_formula")}


def _listing(p: Principal, account: str, period: str | None, entity: str | None):
    uid = _role("account_listing")
    if not uid:
        return None, None
    if not account or len(account) > 40:
        raise HTTPException(400, "account is required")
    period, start, end, _ = _month(period)
    uc = reg.get(uid)
    params = {"account": account, "period_start": start.date().isoformat(), "period_end": end.date().isoformat()}
    names = {x["name"] for x in uc.get("params", [])}
    if entity and "entity" in names:
        params["entity"] = entity
    res = run_use_case(p, uc, {k: v for k, v in params.items() if k in names}, limit=None)
    lines = records(res)
    entities = sorted({str(r["LegalEntity"]) for r in lines if r.get("LegalEntity")})
    if entity:
        lines = [r for r in lines if r.get("LegalEntity") == entity]
    title = next((r["GlTitle"] for r in lines if r.get("GlTitle")), None) or regs.account_title(account)
    out = {"available": True, "account": account, "title": title, "period": period, "entity": entity or None,
           "entities": entities, "columns": res["columns"], "count": len(lines),
           "total": round(sum(float(r.get("AllocatedCost") or 0) for r in lines), 2),
           "formula": _say("tax.listing_formula", usecase=uid)}
    return out, lines


@router.get("/ledger/listing")
def ledger_listing(account: str = "566", period: str | None = None, entity: str | None = None,
                   p: Principal = Depends(current_principal)):
    """Every ledger line booked to one account in a billing period, with vendor, description, amount and invoice."""
    p.require("tax")
    out, lines = _listing(p, account, period, entity)
    if out is None:
        return {"available": False}
    audit(p, "view", module="tax", view="listing", account=account, period=out["period"], entity=entity)
    return {**out, "rows": lines[:LISTING_ROWS], "truncated": len(lines) > LISTING_ROWS}


@router.get("/ledger/listing.csv")
def ledger_listing_csv(account: str = "566", period: str | None = None, entity: str | None = None,
                       p: Principal = Depends(current_principal)):
    p.require("tax")
    p.require_action("exhibit.export")
    out, lines = _listing(p, account, period, entity)
    if out is None:
        raise HTTPException(404, "No transaction listing for this tenant")
    buf = io.StringIO()
    buf.write(_say("tax.listing_csv_header", account=account, title=out["title"] or "", period=out["period"],
                   entity=entity or _say("tax.listing_all_entities"), user=p.user,
                   prepared=f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M}", usecase=_role("account_listing")) + "\n")
    cols = out["columns"]
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows([[r.get(c) for c in cols] for r in lines])
    w.writerow(["Total" if i == 0 else (out["total"] if c == "AllocatedCost" else "") for i, c in enumerate(cols)])
    ledger.append(p.user, p.persona, "export", f"listing:{account}:{out['period']}",
                  f"{len(lines)} lines" + (f", {entity}" if entity else ""))
    audit(p, "export", module="tax", listing=account, period=out["period"], entity=entity)
    name = _say("tax.listing_csv_filename", account=account, period=out["period"]) or f"{account}-{out['period']}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------------------------------------ rule register
@router.get("/rule-register")
def rule_register(period: str | None = None, p: Principal = Depends(current_principal)):
    """The mapping rules with the dollars each placed in the period, their sign-off status, and blocked rule changes."""
    p.require("tax")
    uid = _role("rule_register")
    if not uid:
        return {"available": False}
    period, start, end, periods = _month(period)
    res = run_use_case(p, reg.get(uid), {"period_start": start.date().isoformat(), "period_end": end.date().isoformat()},
                       limit=None)
    rules = records(res)
    signed = {r["RuleId"]: r for r in regs.table("ref_ferc_rule")}
    for r in rules:
        s = signed.get(r["RuleId"], {})
        r.setdefault("SignedOffBy", s.get("SignedOffBy"))
        r.setdefault("SignedOffAt", s.get("SignedOffAt"))
    total = round(sum(float(r.get("AllocatedCost") or 0) for r in rules), 2)
    ledger_total = regs.ledger_total(p.scope, start, end)
    audit(p, "view", module="tax", view="rule-register", period=period)
    return {"available": True, "period": period, "periods": periods, "usecase": uid, "rules": rules,
            "awaiting_signoff": [r["RuleId"] for r in rules if str(r.get("Status") or "").lower().startswith("awaiting")],
            "total": total, "ledger_total": ledger_total, "difference": round(total - ledger_total, 2),
            "blocked_changes": regs.table("ref_rule_change", "ORDER BY ProposedAt, ChangeId"),
            "formula": _say("tax.rule_register_formula")}


# ------------------------------------------------------------------------------------------------ capital path
@router.get("/capital-path")
def capital_path(work_order: str | None = None, p: Principal = Depends(current_principal)):
    """Work orders: 107 additions and balance, transfer to plant on the ready-for-use date, 403 depreciation by month,
    and the capitalizable lines held out of 107."""
    p.require("tax")
    uid = _role("capital_path")
    if not uid:
        return {"available": False}
    wos = records(run_use_case(p, reg.get(uid), {}, limit=None))
    cols = regs.ledger_columns()
    out = regs.capital_path(p.scope, wos, "WorkOrderId" in cols, "ChargeDescription" in cols)
    if work_order:
        wos = [w for w in wos if w.get("WorkOrderId") == work_order]
        out["schedule"] = [s for s in out["schedule"] if s["WorkOrderId"] == work_order]
    audit(p, "view", module="tax", view="capital-path", work_order=work_order)
    return {"available": True, "usecase": uid, "as_of": eng.as_of().isoformat(), "work_orders": wos, **out,
            "formula": _say("tax.capital_formula")}


@router.get("/credit-schedule")
def credit_schedule(jurisdiction: str | None = None, p: Principal = Depends(current_principal)):
    p.require("tax")
    return regs.credit_schedule(p.scope, jurisdiction)


@router.get("/policies")
def policies(p: Principal = Depends(current_principal)):
    p.require("tax")
    drivers = [r[0] for r in db.query("SELECT DISTINCT DriverName FROM ref_allocation_driver ORDER BY 1")["rows"]]
    entities = [r[0] for r in db.query("SELECT LegalEntity FROM ref_legal_entity ORDER BY 1")["rows"]]
    return {"policies": regs.policies(), "drivers": drivers, "entities": entities}


class PolicyDecision(BaseModel):
    action: str                  # submit | approve | return
    version: int | None = None   # approve / return: the submitted version
    name: str | None = None      # submit: new name (optional)
    driver: str | None = None    # submit: new driver, or
    fixed_entity: str | None = None  # submit: send 100% to this entity
    effective_from: str | None = None  # submit: YYYY-MM-DD (default: first day of this month)
    note: str = ""


@router.post("/policies/{pid}/decision")
def policy_decision(pid: str, body: PolicyDecision, p: Principal = Depends(current_principal)):
    p.require("tax")
    if body.action == "submit":
        p.require_action("driver.submit")
        pol = regs.submit(p.user, pid, body.name, body.driver, body.fixed_entity, body.effective_from)
        rerun = {}
    elif body.action in ("approve", "return"):
        p.require_action("rule.approve")
        if body.version is None:
            raise HTTPException(400, "version is required")
        if body.action == "approve":
            pol, rerun = regs.approve(p.user, pid, body.version)
            from .prudence import PrudenceEngine
            PrudenceEngine(db, eng).run()
        else:
            pol, rerun = regs.return_(p.user, pid, body.version), {}
    else:
        raise HTTPException(400, "action must be submit, approve or return")
    entry = ledger.append(p.user, p.persona, body.action, f"policy:{pid}:v{pol['Version']}", body.note)
    audit(p, "policy_decision", policy=pid, version=pol["Version"], decision=body.action)
    return {"policy": pol, "ledger": entry, "reallocated": rerun}
