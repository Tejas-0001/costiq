"""Monitoring and optimization API: Cloud Cost Monitor, AI & LLM Cost Monitor, Optimization Center, alerts."""
from __future__ import annotations
import datetime as dt
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from . import tenant
from .alerts import Alerts
from .auth import Principal, current_principal
from .context import audit, db, eng, ledger
from .monitoring import Monitor
from .optimization import LEVERS, Optimizer
from .window import RANGES, bounds, window

router = APIRouter(prefix="/api")
monitor, optimizer, alerts = Monitor(db, eng), Optimizer(db, eng), Alerts(db, eng)


PERIODS = RANGES
_period_bounds = bounds


@router.get("/period")
def period(range: str = "mtd", p: Principal = Depends(current_principal)):
    """Spend for Today / Yesterday / WTD / MTD / YTD (as of the latest billed day), against the comparable prior period."""
    if range not in PERIODS:
        raise HTTPException(400, f"range must be one of {', '.join(PERIODS)}")
    p.require("command")
    today = eng.as_of()
    (s, e), prev = _period_bounds(range, today)
    ts = lambda x: dt.datetime.combine(x, dt.time())  # noqa: E731
    split = """SUM(CASE WHEN x_AIProduct IS NULL THEN EffectiveCost ELSE 0 END) AS Cloud,
               SUM(CASE WHEN x_AIProduct IS NOT NULL THEN EffectiveCost ELSE 0 END) AS AI"""

    def total(a, b):
        r = db.query(f"SELECT {split} FROM CostAndUsage WHERE ChargePeriodStart >= :a AND ChargePeriodStart < :b",
                     {"a": ts(a), "b": ts(b + dt.timedelta(days=1))}, p.scope)["rows"][0]
        return round(float(r[0] or 0), 2), round(float(r[1] or 0), 2)

    cloud, ai = total(s, e)
    prev_total = sum(total(*prev)) if prev else None
    # daily series: the period itself, or the trailing 14 days for single-day periods
    a = s if (e - s).days >= 6 else e - dt.timedelta(days=13)
    daily = db.query(f"""SELECT strftime(ChargePeriodStart, '%Y-%m-%d') AS Day, {split} FROM CostAndUsage
                         WHERE ChargePeriodStart >= :a AND ChargePeriodStart < :b GROUP BY 1 ORDER BY 1""",
                     {"a": ts(a), "b": ts(e + dt.timedelta(days=1))}, p.scope)["rows"]
    providers = db.query("""SELECT COALESCE(ServiceProviderName, 'Other') AS Provider, SUM(EffectiveCost) AS Cost FROM CostAndUsage
                            WHERE ChargePeriodStart >= :a AND ChargePeriodStart < :b GROUP BY 1 ORDER BY 2 DESC LIMIT 6""",
                         {"a": ts(s), "b": ts(e + dt.timedelta(days=1))}, p.scope)["rows"]
    t = cloud + ai
    audit(p, "view", module="command", period=range)
    return {
        "range": range, "start": s.isoformat(), "end": e.isoformat(), "as_of": today.isoformat(),
        "total": round(t, 2), "cloud": cloud, "ai": ai,
        "previous": round(prev_total, 2) if prev_total is not None else None,
        "previous_start": prev[0].isoformat() if prev else None, "previous_end": prev[1].isoformat() if prev else None,
        "change_pct": round(100 * (t - prev_total) / prev_total, 1) if prev_total else None,
        "daily": [{"Day": r[0], "Cloud": round(float(r[1] or 0), 2), "AI": round(float(r[2] or 0), 2), "InPeriod": r[0] >= s.isoformat()} for r in daily],
        "providers": [{"Provider": r[0], "Cost": round(float(r[1] or 0), 2)} for r in providers],
        "data_starts": "2026-07-01" if range == "ytd" else None,
    }


@router.get("/cloud-monitor")
def cloud_monitor(provider: str | None = None, cost_center: str | None = None, range: str = "mtd",
                  p: Principal = Depends(current_principal)):
    p.require("cloud")
    w = window(range, eng.as_of())
    audit(p, "view", module="cloud", provider=provider, cost_center=cost_center, period=w.range)
    return monitor.cloud(p.scope, provider, cost_center, w)


@router.get("/ai-monitor")
def ai_monitor(range: str = "mtd", month: str | None = None, p: Principal = Depends(current_principal)):
    """AI & LLM Cost Monitor. A tenant whose pack names `usecases.seat_reharvest` also gets `reharvest` (idle Copilot
    seats); `usecases.token_check` adds `token_check` rows for the window, or for one billing month (`month=YYYY-MM`)."""
    p.require("ai")
    w = window(range, eng.as_of())
    audit(p, "view", module="ai", period=w.range)
    out = monitor.ai(p.scope, w)
    roles = tenant.pack().get("usecases") or {}
    if roles.get("seat_reharvest"):
        out["reharvest"] = monitor.reharvest(p.scope, roles["seat_reharvest"], p.pii)
    if roles.get("token_check"):
        if month and not re.fullmatch(r"\d{4}-\d{2}", month):
            raise HTTPException(400, "month must be YYYY-MM")
        out.update(monitor.token_check(p.scope, roles["token_check"], w, month))
    return out


@router.get("/optimization")
def optimization(p: Principal = Depends(current_principal)):
    p.require("optimization")
    audit(p, "view", module="optimization")
    return {**optimizer.summary(p.scope), "actions": optimizer.actions(p.scope),
            "levers": {k: {"label": v[0], "domain": v[1]} for k, v in LEVERS.items()}}


class NewAction(BaseModel):
    origin: str
    lever: str
    title: str
    scope_type: str = "resource"
    scope_key: str
    cost_center: str | None = None
    estimated_monthly_savings: float = 0


@router.post("/optimization")
def add_action(body: NewAction, p: Principal = Depends(current_principal)):
    """Add a finding or recommendation to the tracker (status identified)."""
    p.require("optimization")
    p.require_action("recommendation.submit")
    if type(p.scope) is list and body.cost_center not in p.scope:
        raise HTTPException(403, "This item is outside your cost-center scope")
    a = optimizer.create(body.origin, body.lever, body.title, body.scope_type, body.scope_key, body.cost_center,
                         p.name, body.estimated_monthly_savings)
    ledger.append(p.user, p.persona, "submit", f"optimization:{a['OptimizationId']}", body.title)
    audit(p, "optimization_add", optimization=a["OptimizationId"])
    return a


class Decision(BaseModel):
    action: str   # approve | reject | implement | verify
    note: str = ""


@router.post("/optimization/{oid}/decision")
def decide(oid: str, body: Decision, p: Principal = Depends(current_principal)):
    p.require("optimization")
    a, entry = optimizer.decide(oid, body.action, p, ledger, body.note)
    from .prudence import PrudenceEngine
    PrudenceEngine(db, eng).run()  # remediation and actioned savings change the deduction at risk
    audit(p, "optimization_decision", optimization=oid, decision=body.action)
    return {"action": a, "ledger": entry}


@router.get("/alerts")
def list_alerts(range: str | None = None, p: Principal = Depends(current_principal)):
    """Fired alerts; with `range`, only those fired in the window (`open_total` still counts every open alert)."""
    out = alerts.events()
    if p.scope is not None:  # scoped users see alerts about their own cost centers only
        allowed = set(p.scope)
        out["events"] = [e for e in out["events"] if e["Subject"] in allowed]
    out["unacknowledged"] = out["open_total"] = sum(1 for e in out["events"] if not e["Acknowledged"])
    if range:
        w = window(range, eng.as_of())
        out["events"] = [e for e in out["events"] if w.start.isoformat() <= str(e["FiredAt"])[:10] <= w.end.isoformat()]
        out["unacknowledged"] = sum(1 for e in out["events"] if not e["Acknowledged"])
        out["period"] = w.as_dict()
    return out


@router.post("/alerts/{eid}/ack")
def ack(eid: str, p: Principal = Depends(current_principal)):
    alerts.acknowledge(eid, p.user)
    audit(p, "alert_ack", alert=eid)
    return {"acknowledged": eid}
