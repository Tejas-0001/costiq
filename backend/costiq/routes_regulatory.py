"""Tax & Transfer Pricing API: allocation chain, filing forecast bridge, intercompany recharge schedule, R&D credit
schedule and the allocation-policy approval workflow."""
from __future__ import annotations
import csv, datetime as dt, io
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from .allocation import Allocator
from .auth import Principal, current_principal
from .context import audit, db, eng, ledger, records, reg, run_use_case
from .regulatory import Regulatory

router = APIRouter(prefix="/api/tax")
regs = Regulatory(db, eng)


def _period(period: str | None):
    if not period:
        return None
    try:
        return dt.datetime.fromisoformat(period + "-01" if len(period) == 7 else period)
    except ValueError:
        raise HTTPException(400, "period must be YYYY-MM")


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
    res = run_use_case(p, reg.get("PH-05"), {"filing": filing}, limit=None)
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
        "formula": "Review period: allocated operating cost for the filing's entity and jurisdiction, annualized. Forecast "
                   "year: annualized review period plus contract escalators from each renewal date. Invoice tie-out compares "
                   "the vendor's invoices with its charges.",
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
    buf.write(f"# Technology cost forecast bridge | filing {filing} | {rc['LegalEntity']} ({rc['Jurisdiction']}) | "
              f"review period {str(rc['ReviewPeriodStart'])[:10]} to {str(rc['ReviewPeriodEnd'])[:10]} | forecast year "
              f"{str(rc['ForecastYearStart'])[:10]} to {str(rc['ForecastYearEnd'])[:10]} | prepared by {p.user} on "
              f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC | source: MM CostIQ PH-05\n")
    w = csv.writer(buf)
    w.writerow(res["columns"])
    w.writerows(res["rows"])
    t = out["totals"]
    w.writerow(["Total", t["ReviewPeriodActual"], t["ReviewAnnualized"], "", "", t["ForecastYear"], t["ContractEscalation"],
                "", f"{t['InvoiceExceptions']} invoice exceptions", ""])
    w.writerow([f"# Verified optimization savings already achieved (annualized, all entities): {t['VerifiedSavingsAnnual']:.2f} "
                f"from {len(out['verified_savings'])} verified actions; see the audit file optimization_actions.csv"])
    ledger.append(p.user, p.persona, "export", f"exhibit:{filing}:PH-05", f"{len(res['rows'])} vendor lines")
    audit(p, "export", module="tax", exhibit="PH-05", filing=filing)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filing}-technology-cost-bridge.csv"'})


@router.get("/recharge-schedule")
def recharge_schedule(year: int = 2026, p: Principal = Depends(current_principal)):
    p.require("tax")
    res = run_use_case(p, reg.get("PH-02"), {"period_start": f"{year}-01-01", "period_end": f"{year + 1}-01-01"}, limit=None)
    lines = records(res)
    by_entity = {}
    for l in lines:
        by_entity[l["RecipientEntity"]] = round(by_entity.get(l["RecipientEntity"], 0) + float(l["AmountRecharged"] or 0), 2)
    return {"year": year, "lines": lines,
            "by_entity": [{"RecipientEntity": k, "AmountRecharged": v} for k, v in sorted(by_entity.items(), key=lambda x: -x[1])],
            "formula": "Technology costs recharged by Global Services to each entity, by allocation policy and key "
                       "(transfer-pricing local file). Covers costs allocated through CostIQ only."}


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
