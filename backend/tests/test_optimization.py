"""Cloud and AI/LLM monitoring and optimization: seeded findings, lifecycle, verified savings, alerts, permissions."""
import datetime as dt
from fastapi.testclient import TestClient
from costiq.main import app
from costiq.context import db, eng
from costiq.optimization import Optimizer
from costiq.alerts import Alerts

client = TestClient(app)


def h(persona):
    return {"X-CostIQ-Persona": persona}


def run(uid, persona="cfo", params=None):
    r = client.post(f"/api/usecases/{uid}/run", json={"params": params or {}}, headers=h(persona)).json()
    return [dict(zip(r["columns"], row)) for row in r["rows"]]


def test_cloud_checks_find_seeded_waste():
    idle = {r["ResourceName"]: r for r in run("CO-02")}
    assert "edc-test-ec2" in idle and "vol-edc-test-orphan-01" in idle
    assert idle["vol-edc-test-orphan-01"]["Finding"] == "Unattached volume"
    assert {r["ResourceName"] for r in run("CO-03")} >= {"edc-dev-ec2", "edc-test-ec2"}
    assert any(r["IdleCost"] > 0 for r in run("CO-07"))


def test_ai_checks_find_seeded_waste():
    gpu = {r["ResourceName"]: r for r in run("UO-09")}["molgen-gpu-endpoint"]
    assert gpu["IdleDays"] > 30 and str(gpu["FirstIdleDay"]) <= "2026-07-05"
    ptu = run("UO-10")[0]
    assert ptu["AvgUtilizationPct"] < 50 and ptu["Finding"].startswith("Under-utilized") and ptu["MonthlySavingsIfPayg"] > 0
    retries = {r["Application"]: r for r in run("UO-11")}
    assert retries["patient-support-assistant"]["RetriedPct"] > 10
    bloat = {r["Application"]: r for r in run("UO-12")}
    assert bloat["csr-drafting-agent"]["InputGrowthPct"] > 50
    routes = [r for r in run("UO-14") if r["Application"] == "ae-intake-bot"]
    assert routes and routes[0]["CandidateModel"] == "claude-haiku-5-5" and routes[0]["Savings"] > 0
    burn = {r["CostCenter"]: r for r in run("BF-04")}
    assert burn["Corporate IT"]["Status"] == "Will run out this month"


def test_verified_savings_equal_baseline_minus_post_run_rate():
    opt = Optimizer(db, eng)
    a = opt.action("OPT-003")
    assert a["Status"] == "verified" and a["VerificationBasis"] == "measured"
    impl = dt.date.fromisoformat(str(a["ImplementedOn"])[:10])
    base, _ = opt.run_rate("resource", "insights-ec2", impl - dt.timedelta(days=30), impl)
    post, _ = opt.run_rate("resource", "insights-ec2", impl, eng.as_of() + dt.timedelta(days=1))
    assert abs(float(a["VerifiedMonthlySavings"]) - round(base - post, 2)) < 0.02
    assert float(a["VerifiedMonthlySavings"]) > 0


def test_lifecycle_and_separation_of_duties():
    oid = "OPT-002"  # approved, Clinical Operations
    assert client.post(f"/api/optimization/{oid}/decision", json={"action": "verify"}, headers=h("finops_analyst")).status_code == 409
    r = client.post(f"/api/optimization/{oid}/decision", json={"action": "implement"}, headers=h("cloud_ops"))
    assert r.status_code == 200 and r.json()["action"]["Status"] == "implemented"
    # the implementer cannot verify their own change
    db.query("UPDATE optimization_action SET ImplementedBy = 'demo.finops_analyst@company.example' WHERE OptimizationId = :i",
             {"i": oid}, limit=None)
    assert client.post(f"/api/optimization/{oid}/decision", json={"action": "verify"}, headers=h("finops_analyst")).status_code == 409
    db.query("UPDATE optimization_action SET ImplementedBy = 'demo.cloud_ops@company.example' WHERE OptimizationId = :i",
             {"i": oid}, limit=None)
    r = client.post(f"/api/optimization/{oid}/decision", json={"action": "verify"}, headers=h("finops_analyst")).json()
    assert r["action"]["Status"] == "verified" and r["action"]["VerificationBasis"] == "provisional"
    entries = [e for e in client.get("/api/evidence", headers=h("finance_controller")).json()["ledger"]
               if e["Target"] == f"optimization:{oid}"]
    assert [e["Action"] for e in entries][-2:] == ["implement", "verify"]


def test_scoped_cloud_ops_cannot_act_outside_its_cost_centers():
    # OPT-006 belongs to Research & Discovery; cloud_ops is scoped to Clinical Operations and Data & Analytics
    client.post("/api/optimization/OPT-006/decision", json={"action": "approve"}, headers=h("cfo"))
    assert client.post("/api/optimization/OPT-006/decision", json={"action": "implement"}, headers=h("cloud_ops")).status_code == 403


def test_add_to_tracker_is_idempotent():
    body = {"origin": "CO-06", "lever": "egress", "title": "Cache patient portal static assets at the CDN", "scope_type": "resource",
            "scope_key": "eu-web-door", "cost_center": "Patient Data", "estimated_monthly_savings": 900}
    a = client.post("/api/optimization", json=body, headers=h("finops_analyst")).json()
    b = client.post("/api/optimization", json=body, headers=h("finops_analyst")).json()
    assert a["OptimizationId"] == b["OptimizationId"] and a["Status"] == "identified"


def test_alerts_fire_acknowledge_and_never_call_out_by_default(monkeypatch):
    import httpx
    monkeypatch.delenv("COSTIQ_ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("webhook called")))
    Alerts(db, eng).evaluate()
    out = client.get("/api/alerts", headers=h("cfo")).json()
    kinds = {e["RuleId"] for e in out["events"]}
    assert {"AR-01", "AR-02", "AR-04", "AR-05"} <= kinds
    first = next(e for e in out["events"] if not e["Acknowledged"])
    n = out["unacknowledged"]
    client.post(f"/api/alerts/{first['EventId']}/ack", headers=h("cfo"))
    assert client.get("/api/alerts", headers=h("cfo")).json()["unacknowledged"] == n - 1
    Alerts(db, eng).evaluate()  # re-evaluation keeps the acknowledgement
    assert client.get("/api/alerts", headers=h("cfo")).json()["unacknowledged"] == n - 1


def test_monitors_and_permissions():
    cloud = client.get("/api/cloud-monitor", headers=h("cfo")).json()
    assert cloud["kpis"]["opportunity_monthly"] > 0 and cloud["daily"] and cloud["opportunities"]
    ai = client.get("/api/ai-monitor", headers=h("ai_program_owner")).json()
    apps = {a["Application"]: a for a in ai["apps"]}
    assert any(l.startswith("Provisioned") for l in apps["csr-drafting-agent"]["SuggestedLevers"])
    assert ai["kpis"]["cost_per_accepted_outcome"] > 0
    assert client.get("/api/ai-monitor", headers=h("cloud_ops")).status_code == 403
    assert client.get("/api/optimization", headers=h("auditor")).status_code == 403


def test_recommendation_and_telemetry_adapters_load_their_templates():
    import os
    from costiq.adapters import llm_gateway, recommendations
    t = os.path.join(os.path.dirname(__file__), "..", "data", "templates")
    for source, f, n in (("aws_cost_optimization_hub", "aws_cost_optimization_hub_template.csv", 2),
                         ("gcp_recommender", "gcp_recommender_template.csv", 1),
                         ("azure_advisor", "azure_advisor_template.json", 1)):  # the security item is skipped
        out = recommendations.load(db, source, [os.path.join(t, f)])
        assert out["status"] == "promoted" and out["rows"] == n, (source, out)
    gcp = db.query("SELECT ActionType, EstimatedMonthlySavings, ResourceName FROM optimization_recommendation "
                   "WHERE Source = 'gcp_recommender'")["rows"]
    assert gcp == [["stop_idle", 180.0, "mktg-etl-02"]]
    bad = recommendations.load(db, "aws_cost_optimization_hub", [os.path.join(t, "gcp_recommender_template.csv")])
    assert bad["status"] == "quarantined"
    out = llm_gateway.load(db, [os.path.join(t, "llm_gateway_template.csv")])
    assert out["status"] == "promoted" and out["rows"] == 2
    assert db.query("SELECT COUNT(*) FROM ai_app_telemetry_daily WHERE UsageDate = DATE '2026-11-01'")["rows"] == [[2]]


def test_period_spend_today_through_ytd():
    asof = eng.as_of().isoformat()
    seen = {}
    for rng in ("today", "yesterday", "wtd", "mtd", "ytd"):
        r = client.get(f"/api/period?range={rng}", headers=h("cfo"))
        assert r.status_code == 200
        d = r.json()
        assert d["end"] <= asof and d["start"] <= d["end"]
        assert abs(d["total"] - (d["cloud"] + d["ai"])) < 0.05
        assert d["daily"] and d["providers"]
        seen[rng] = d["total"]
    assert seen["today"] <= seen["wtd"] <= seen["mtd"] <= seen["ytd"]
    mtd = client.get("/api/period?range=mtd", headers=h("cfo")).json()
    assert mtd["previous"] and mtd["change_pct"] is not None
    assert client.get("/api/period?range=forever", headers=h("cfo")).status_code == 400
