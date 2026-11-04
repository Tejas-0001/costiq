"""MM CostIQ API (FastAPI). Run: uvicorn costiq.main:app --host 0.0.0.0 --port 8080"""
from __future__ import annotations
import csv, datetime as dt, io, json, logging, re, time
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from .auth import Principal, access, current_principal, pseudonym
from .config import settings
from . import tenant
from .context import asker, audit, db, eng, ledger, protect, reg, run_use_case
from . import dialect
from .board import BoardPack, confidence as forecast_confidence
from .window import use_case_params, window

app = FastAPI(title="MM CostIQ API", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.exception_handler(ValueError)
async def value_error(_: Request, e: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(e)})


# ---------------------------------------------------------------------------------------- public
@app.get("/api/health")
def health():
    return {"status": "ok", "use_cases": len(reg.items)}


_highlights_cache: dict = {}


def landing_highlights() -> dict | None:
    """Three headline figures for the landing page (persona sign-in mode only)."""
    if settings.auth_mode != "demo":
        return None
    if _highlights_cache:
        return _highlights_cache
    try:
        months = db.query("""SELECT BillingPeriodStart AS m, SUM(EffectiveCost) AS cost FROM CostAndUsage
                             GROUP BY BillingPeriodStart ORDER BY BillingPeriodStart""")["rows"]
        k = eng.kpis(None)
        f = eng.findings(None)
        closed = [round(float(r[1] or 0), 2) for r in months][:-1][-5:]  # closed months; the open month is forecast
        _highlights_cache.update({
            "run_rate": round(k["forecast"] * 12, 2),  # open-month forecast x 12
            "spend_series": closed + [round(k["forecast"], 2)],
            "forecast_variance_pct": round(100 * k["variance"] / k["budget"], 1) if k.get("budget") else None,
            "variance_series": [u["VariancePct"] for u in k["units"] if u.get("VariancePct") is not None][:8],
            "optimization_potential": round(sum(x["exposure"] for x in f if x["kind"] == "estimate"), 2),
            "optimization_series": sorted(round(x["exposure"], 2) for x in f if x["kind"] == "estimate")[-4:],
            "as_of": eng.as_of().isoformat(),
        })
        try:  # the three tax-risk numbers for the landing page
            from .optimization import Optimizer
            opt = Optimizer(db, eng)
            hl = _prudence.headline(None, opt)
            _highlights_cache["risk"] = {k: hl[k] for k in ("deduction_at_risk", "credit_unlock", "verified_savings_annual")}
            # breakdowns for the landing-page carousel visuals
            _highlights_cache["risk"]["by_reason"] = [{"Label": r["Label"], "Risk": r["Risk"]} for r in hl["by_reason"]]
            _highlights_cache["risk"]["jurisdictions"] = [
                {k: j.get(k) for k in ("Jurisdiction", "Credit", "Claimed", "CreditStatus")}
                for j in _prudence.credit_unlock(None)["jurisdictions"]]
            _highlights_cache["risk"]["stages"] = [{"Stage": s["Stage"], "MonthlySavings": s["MonthlySavings"]}
                                                      for s in opt.summary(None)["stages"]]
        except Exception:
            pass
        return _highlights_cache
    except Exception:  # never block sign-in because of a figure
        return None


@app.get("/api/config")
def public_config():
    acc = access()
    return {"auth_mode": settings.auth_mode, "tenant_name": settings.tenant_name,
            "tenant": settings.tenant, "copy": tenant.frontend_copy(),  # the tenant pack and its UI copy (copy.json "frontend")
            "highlights": landing_highlights(),
            "oidc": {"authority": settings.oidc_issuer.replace("/v2.0", ""), "client_id": settings.spa_client_id,
                     "scope": f"{settings.oidc_audience}/access_as_user" if settings.oidc_audience else ""},
            "personas": [{"key": k, "label": v["label"], "group": v["group"]} for k, v in acc["personas"].items()]
            if settings.auth_mode == "demo" else [],
            "modules": acc["modules"]}


# ---------------------------------------------------------------------------------------- identity
@app.get("/api/me")
def me(p: Principal = Depends(current_principal)):
    acc = access()
    return {"user": p.user, "name": p.name, "personas": [{"key": k, "label": acc["personas"][k]["label"]} for k in p.personas],
            "modules": p.modules, "actions": p.actions, "scope": p.scope or "enterprise", "pii": p.pii,
            "as_of": eng.as_of().isoformat(), "tenant": settings.tenant, "copy": tenant.frontend_copy()}


# ---------------------------------------------------------------------------------------- use cases
class RunRequest(BaseModel):
    params: dict = {}
    range: str | None = None  # the period selected at the top of the page; used unless the dates are given


@app.get("/api/usecases")
def list_usecases(p: Principal = Depends(current_principal)):
    return [dict(reg.summary(reg.get(u)), allowed=p.can(reg.get(u)["module"])) for u in reg.order]


@app.get("/api/usecases/{uid}")
def get_usecase(uid: str, p: Principal = Depends(current_principal)):
    try:
        uc = reg.get(uid)
    except KeyError:
        raise HTTPException(404, "Unknown use case")
    out = dict(uc)
    out["allowed"] = p.can(uc["module"])
    out["translated_sql"] = dialect.translate(uc["sql"], db.target)[0]
    return out


@app.post("/api/usecases/{uid}/run")
def run(uid: str, body: RunRequest, p: Principal = Depends(current_principal)):
    try:
        uc = reg.get(uid)
    except KeyError:
        raise HTTPException(404, "Unknown use case")
    p.require(uc["module"])
    params, note = dict(body.params or {}), None
    if body.range and not {"period_start", "period_end"} & set(params):  # dates not set by hand: use the selected period
        wp, note = use_case_params(uc, window(body.range, eng.as_of()))
        params.update({k: v.isoformat()[:10] for k, v in wp.items()})
    res = run_use_case(p, uc, params)
    res["period_note"] = note
    return res


@app.get("/api/usecases/{uid}/export.csv")
def export(uid: str, request: Request, p: Principal = Depends(current_principal)):
    uc = reg.get(uid)
    p.require(uc["module"])
    res = run_use_case(p, uc, dict(request.query_params), limit=None)
    buf = io.StringIO()
    buf.write(f"# {uc['id']} {uc['title']} | scope: {p.scope or 'enterprise'} | data as of {eng.as_of()} | "
              f"exported {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC by {p.user}\n")
    w = csv.writer(buf)
    w.writerow(res["columns"])
    w.writerows(res["rows"])
    audit(p, "export", use_case=uid, rows=len(res["rows"]))
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{uid}.csv"'})


# ---------------------------------------------------------------------------------------- modules
@app.get("/api/command")
def command(range: str = "mtd", p: Principal = Depends(current_principal)):
    k = eng.kpis(p.scope)
    w = window(range, eng.as_of())
    cc, cp = eng.cost_center_expr()  # each cost center's spend in the selected window, next to its month position
    spent = {r[0]: float(r[1] or 0) for r in db.query(f"SELECT {cc}, SUM(EffectiveCost) FROM CostAndUsage WHERE {w.pred()} GROUP BY 1",
                                                       {**cp, **w.params()}, p.scope, limit=None)["rows"]}
    k["units"] = [{**u, "SpendInPeriod": round(spent.get(u["CostCenter"], 0.0), 2)} for u in k["units"]]
    k["period"] = w.as_dict()
    f = eng.findings(p.scope)
    k["findings"] = f[:8]
    k["exposure_incurred"] = round(sum(x["exposure"] for x in f if x["kind"] == "incurred"), 2)
    k["opportunity_estimated"] = round(sum(x["exposure"] for x in f if x["kind"] == "estimate"), 2)
    k["compliance_findings"] = sum(1 for x in f if x["kind"] == "compliance")
    if p.scope is None:
        rec = eng.reconciliation()
        k["reconciliation_exceptions"] = sum(1 for l in rec["lines"] if l["Status"] == "exception" and l["IssueStatus"] == "Issued")
        k["confidence"] = forecast_confidence(db, eng, ledger, k)
    if p.scope is None and (p.can("risk") or p.can("tax")):
        from .optimization import Optimizer
        opt = Optimizer(db, eng)
        k["headline"] = _prudence.headline(None, opt)  # the three numbers: risk, unlock, verified savings
        # breakdowns the Command Center draws (credit by jurisdiction, savings by lifecycle stage)
        k["headline"]["jurisdictions"] = [{x: j.get(x) for x in ("Jurisdiction", "Credit", "Claimed", "CreditStatus")}
                                          for j in _prudence.credit_unlock(None)["jurisdictions"]]
        k["headline"]["stages"] = [{"Stage": st["Stage"], "MonthlySavings": st["MonthlySavings"]} for st in opt.summary(None)["stages"]]
    k["formulas"]["exposure"] = "Incurred exposure, estimated opportunity and compliance findings are reported separately and never summed."
    audit(p, "view", module="command")
    return k


@app.get("/api/explorer")
def explorer(month: str | None = None, provider: str | None = None, cost_center: str | None = None, range: str | None = None,
             p: Principal = Depends(current_principal)):
    p.require("explorer")
    w = window(range, eng.as_of()) if range else None
    audit(p, "view", module="explorer", month=month, provider=provider, cost_center=cost_center, period=range)
    out = eng.explorer(p.scope, month, provider, cost_center, win=w)
    out["period"] = w.as_dict() if w and not month else None
    return out


@app.get("/api/lineage/{key}")
def lineage(key: str, p: Principal = Depends(current_principal)):
    p.require("explorer")
    out = eng.lineage(p.scope, key)
    if not out:
        raise HTTPException(404, "Charge not found in your scope")
    if not p.pii and out["charge"].get("x_UserPrincipal"):
        out["charge"]["x_UserPrincipal"] = pseudonym(out["charge"]["x_UserPrincipal"])
    return out


@app.get("/api/ai")
def ai(p: Principal = Depends(current_principal)):
    p.require("ai")
    return eng.ai_overview(p.scope)


@app.get("/api/findings")
def findings(p: Principal = Depends(current_principal)):
    return eng.findings(p.scope)


class Decision(BaseModel):
    action: str        # submit | endorse | approve | return
    note: str = ""


@app.post("/api/findings/{fid}/decision")
def decide(fid: str, body: Decision, p: Principal = Depends(current_principal)):
    need = {"submit": "recommendation.submit", "endorse": "recommendation.endorse", "approve": "recommendation.approve",
            "return": "recommendation.approve"}.get(body.action)
    if not need:
        raise HTTPException(400, "action must be submit, endorse, approve or return")
    p.require_action(need)
    history = [e for e in ledger.entries() if e["Target"] == f"finding:{fid}"]
    if body.action == "approve" and any(e["Actor"] == p.user and e["Action"] == "submit" for e in history):
        raise HTTPException(409, "Separation of duties: you submitted this recommendation, so someone else must approve it")
    entry = ledger.append(p.user, p.persona, body.action, f"finding:{fid}", body.note)
    audit(p, "decision", target=fid, decision=body.action)
    return entry


@app.get("/api/budgets")
def budgets(p: Principal = Depends(current_principal)):
    p.require("budgets")
    return eng.budgets(p.scope)


@app.get("/api/allocation")
def allocation(range: str | None = None, p: Principal = Depends(current_principal)):
    p.require("allocation")
    w = window(range, eng.as_of()) if range else None
    out = eng.allocation_summary(p.scope, w)
    out["period"] = w.as_dict() if w else None
    return out


@app.get("/api/reconciliation")
def reconciliation(p: Principal = Depends(current_principal)):
    p.require("reconciliation")
    if p.scope is not None:
        raise HTTPException(403, "Reconciliation needs enterprise scope")
    return eng.reconciliation()


@app.get("/api/compliance/{module}")
def compliance(module: str, p: Principal = Depends(current_principal)):
    p.require(module)
    if p.scope is not None:
        raise HTTPException(403, "Compliance scorecards need enterprise scope")
    audit(p, "view", module=module)
    return eng.compliance(None, module)


@app.get("/api/evidence")
def evidence(p: Principal = Depends(current_principal)):
    p.require("governance")
    v = ledger.verify()
    out = eng.controls(v) if p.scope is None else {"controls": [], "manifests": []}
    out["ledger"] = ledger.entries()
    out["ledger_verified"] = v
    return out


class AskRequest(BaseModel):
    question: str
    range: str | None = None  # the period selected at the top of the page, used when the question names none


@app.post("/api/ask")
def ask(body: AskRequest, p: Principal = Depends(current_principal)):
    p.require("ask")
    if len(body.question) > 500:
        raise HTTPException(400, "Question too long")
    w = window(body.range, eng.as_of()) if body.range else None
    ans = asker.ask(body.question, p, lambda uc, params: run_use_case(p, uc, params, limit=200), w)
    audit(p, "ask", matched=ans.get("use_case", {}).get("id"))
    return ans


@app.get("/api/board")
def board(p: Principal = Depends(current_principal)):
    """Board Pack: thirteen sections for the board and audit committee, each marked live, partial or needs data."""
    p.require("board")
    if p.scope is not None:
        raise HTTPException(403, "The Board Pack needs enterprise scope")
    from .optimization import Optimizer
    out = BoardPack(db, eng, _prudence, Optimizer(db, eng), ledger).build()
    audit(p, "view", module="board")
    return out


# ---------------------------------------------------------------------------------------- tax, risk and monitoring modules
from .routes_monitoring import router as monitoring_router  # noqa: E402
from .routes_prudence import prudence as _prudence, router as prudence_router  # noqa: E402
from .routes_regulatory import router as regulatory_router  # noqa: E402

app.include_router(regulatory_router)
app.include_router(monitoring_router)
app.include_router(prudence_router)
from .routes_codebase import router as codebase_router  # noqa: E402  (Snowflake SQL + dbt project export)
app.include_router(codebase_router)

# Optional routers: served as soon as the module exists (invoice-to-ledger drill, tag compliance, pooling rules; readiness).
import importlib as _importlib  # noqa: E402

for _name in ("routes_ledger", "routes_readiness"):
    try:
        _mod = _importlib.import_module(f"costiq.{_name}")
    except ModuleNotFoundError as _e:
        if _e.name != f"costiq.{_name}":
            raise
        continue
    app.include_router(_mod.router)


# ---------------------------------------------------------------------------------------- AI summary
from .routes_monitoring import alerts as _alerts, optimizer as _optimizer  # noqa: E402
from .summary import Summarizer  # noqa: E402

_summarizer = Summarizer(db, eng, _prudence, _optimizer, _alerts, ledger)


@app.get("/api/summary")
def summary(fresh: bool = False, range: str = "mtd", p: Principal = Depends(current_principal)):
    """AI summary for the caller's role and scope: headline, sections and next actions. Every figure is a fact linked to
    the page (and row) that shows it; see summary.py for the writers."""
    label = access()["personas"].get(p.persona, {}).get("label", p.persona)
    out = _summarizer.build(p, label, fresh, window(range, eng.as_of()))
    audit(p, "view", module="summary", writer=out["writer"])
    return out


# ---------------------------------------------------------------------------------------- optional static web
# Single-container evaluation: set COSTIQ_WEB_DIR to the built SPA (web/dist). In Kubernetes the
# web app is served by its own nginx pod instead.
import os as _os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_web = _os.environ.get("COSTIQ_WEB_DIR")
if _web and _os.path.isdir(_web):
    app.mount("/assets", StaticFiles(directory=_os.path.join(_web, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = _os.path.join(_web, path)
        if path and _os.path.isfile(f) and _os.path.realpath(f).startswith(_os.path.realpath(_web)):
            return FileResponse(f)
        return FileResponse(_os.path.join(_web, "index.html"))
