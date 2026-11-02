"""Regulatory Accounting API: allocation chain, rate-case bridge exhibits, Form 60 schedule, regulatory assets and the
allocation-policy approval workflow."""
from __future__ import annotations
import csv, datetime as dt, io
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from .allocation import Allocator
from .auth import Principal, current_principal
from .context import audit, db, eng, ledger, records, reg, run_use_case
from .regulatory import Regulatory

router = APIRouter(prefix="/api/regulatory")
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
    p.require("regulatory")
    ps = _period(period)
    out = Allocator(db, eng).chain(p.scope, ps)
    out["period"] = period
    out["periods"] = [str(x)[:7] for x in Allocator(db, eng).periods()]
    out["pending_policies"] = [r for r in regs.policies() if r["Status"] == "submitted"]
    audit(p, "view", module="regulatory", view="allocation-chain", period=period)
    return out


@router.get("/rate-cases")
def rate_cases(p: Principal = Depends(current_principal)):
    p.require("regulatory")
    return regs.rate_cases()


def _bridge(p: Principal, docket: str):
    rc = regs.rate_case(docket)
    res = run_use_case(p, reg.get("UT-05"), {"docket": docket}, limit=None)
    lines = records(res)
    tot = lambda k: round(sum(float(l[k] or 0) for l in lines), 2)
    from .optimization import Optimizer
    verified = [a for a in Optimizer(db, eng).actions(None) if a["Status"] == "verified"]
    return rc, res, {
        "docket": rc, "lines": lines,
        "totals": {"BasePeriodActual": tot("BasePeriodActual"), "BaseAnnualized": tot("BaseAnnualized"),
                   "KnownAndMeasurable": tot("KnownAndMeasurable"), "TestYearForecast": tot("TestYearForecast"),
                   "InvoiceExceptions": sum(1 for l in lines if l["InvoiceTieOut"] == "Invoice exception"),
                   "VerifiedSavingsAnnual": round(12 * sum(float(a["VerifiedMonthlySavings"] or 0) for a in verified), 2)},
        "verified_savings": [{k: a[k] for k in ("OptimizationId", "Title", "VerifiedMonthlySavings", "VerificationBasis", "VerifiedOn")}
                             for a in verified],
        "formula": "Base period: allocated O&M and deferred subscription fees for the docket's operating company and "
                   "jurisdiction, annualized. Test year: annualized base plus contract escalators from each renewal date "
                   "(known and measurable). Invoice tie-out compares the vendor's invoices with its charges.",
    }


@router.get("/rate-case/{docket}/bridge")
def bridge(docket: str, p: Principal = Depends(current_principal)):
    p.require("regulatory")
    _, _, out = _bridge(p, docket)
    audit(p, "view", module="regulatory", view="bridge", docket=docket)
    return out


@router.get("/rate-case/{docket}/bridge.csv")
def bridge_csv(docket: str, p: Principal = Depends(current_principal)):
    p.require("regulatory")
    p.require_action("exhibit.export")
    rc, res, out = _bridge(p, docket)
    buf = io.StringIO()
    buf.write(f"# Exhibit: technology O&M test-year bridge | docket {docket} | {rc['LegalEntity']} ({rc['Jurisdiction']}) | "
              f"base period {str(rc['BasePeriodStart'])[:10]} to {str(rc['BasePeriodEnd'])[:10]} | test year "
              f"{str(rc['TestYearStart'])[:10]} to {str(rc['TestYearEnd'])[:10]} | prepared by {p.user} on "
              f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC | source: MM CostIQ UT-05\n")
    w = csv.writer(buf)
    w.writerow(res["columns"])
    w.writerows(res["rows"])
    t = out["totals"]
    w.writerow(["Total", t["BasePeriodActual"], t["BaseAnnualized"], "", "", t["TestYearForecast"], t["KnownAndMeasurable"],
                "", f"{t['InvoiceExceptions']} invoice exceptions", ""])
    w.writerow([f"# Verified optimization savings already achieved (annualized, all entities): {t['VerifiedSavingsAnnual']:.2f} "
                f"from {len(out['verified_savings'])} verified actions; see the Evidence Pack optimization_actions.csv"])
    ledger.append(p.user, p.persona, "export", f"exhibit:{docket}:UT-05", f"{len(res['rows'])} vendor lines")
    audit(p, "export", module="regulatory", exhibit="UT-05", docket=docket)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{docket}-technology-om-bridge.csv"'})


@router.get("/form60")
def form60(year: int = 2026, p: Principal = Depends(current_principal)):
    p.require("regulatory")
    res = run_use_case(p, reg.get("UT-02"), {"period_start": f"{year}-01-01", "period_end": f"{year + 1}-01-01"}, limit=None)
    lines = records(res)
    by_company = {}
    for l in lines:
        by_company[l["AssociateCompany"]] = round(by_company.get(l["AssociateCompany"], 0) + float(l["AmountBilled"] or 0), 2)
    return {"year": year, "lines": lines,
            "by_company": [{"AssociateCompany": k, "AmountBilled": v} for k, v in sorted(by_company.items(), key=lambda x: -x[1])],
            "formula": "Technology costs billed by the service company to each associate company, by allocation policy and "
                       "basis (FERC Form No. 60, Schedule XXI style). Covers costs allocated through CostIQ only."}


@router.get("/regulatory-assets")
def regulatory_assets(jurisdiction: str | None = None, p: Principal = Depends(current_principal)):
    p.require("regulatory")
    return regs.regulatory_assets(p.scope, jurisdiction)


@router.get("/policies")
def policies(p: Principal = Depends(current_principal)):
    p.require("regulatory")
    drivers = [r[0] for r in db.query("SELECT DISTINCT DriverName FROM ut_allocation_driver ORDER BY 1")["rows"]]
    entities = [r[0] for r in db.query("SELECT LegalEntity FROM ut_legal_entity ORDER BY 1")["rows"]]
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
    p.require("regulatory")
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
