"""Conformance and security tests. Run: COSTIQ_DB_URL=... pytest -q backend/tests
The CI matrix runs this file once per supported database (DuckDB, PostgreSQL)."""
import glob, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from fastapi.testclient import TestClient
from costiq.config import settings
from costiq.db import connect
from costiq.registry import MODULES, Registry
from costiq import dialect

reg = Registry(settings.registry_dir)
db = connect(settings.db_url)


@pytest.mark.parametrize("uid", reg.order)
def test_use_case_runs_and_returns_rows(uid):
    uc = reg.get(uid)
    res = db.query(uc["sql"], reg.resolve_params(uc, None), limit=10)
    assert res["rows"], f"{uid} returned no rows on sample data"


def test_registry_is_complete():
    """Counts come from the registry folder itself; the 70 FinOps Foundation FOCUS 1.4 use cases are always present."""
    assert len(reg.items) == len(glob.glob(os.path.join(settings.registry_dir, "*.yaml")))
    assert sum(1 for u in reg.items.values() if u["id"].startswith("F-")) == 70
    assert all(u["module"] in MODULES for u in reg.items.values())


def test_every_use_case_translates_to_postgres():
    for uid in reg.order:
        dialect.translate(reg.get(uid)["sql"], "postgres")


@pytest.mark.parametrize("target", ["tsql", "clickhouse"])
def test_translation_coverage_other_targets(target):
    """SQL Server and ClickHouse are translation targets validated per engagement; track coverage here."""
    failed = []
    for uid in reg.order:
        try:
            dialect.translate(reg.get(uid)["sql"], target)
        except Exception:
            failed.append(uid)
    print(f"{target}: {len(reg.items) - len(failed)}/{len(reg.items)} translate; manual edition needed for {failed}")
    assert len(failed) <= 5


def test_scope_filters_rows():
    uc = reg.get("F-62")
    p = reg.resolve_params(uc, None)
    full = db.query(uc["sql"], p, limit=None)["rows"]
    mkt = db.query(uc["sql"], p, scope=["Research & Discovery"], limit=None)["rows"]
    assert 0 < sum(r[-1] for r in mkt) < sum(r[-1] for r in full)


def test_empty_scope_sees_nothing():
    uc = reg.get("F-62")
    assert db.query(uc["sql"], reg.resolve_params(uc, None), scope=[], limit=None)["rows"] == []


@pytest.fixture(scope="module")
def client():
    from costiq.main import app
    return TestClient(app)


H = lambda p: {"X-CostIQ-Persona": p}


def test_scoped_persona_cannot_open_reconciliation(client):
    assert client.get("/api/reconciliation", headers=H("pl_exec")).status_code == 403


def test_module_permission_enforced_on_run(client):
    assert client.post("/api/usecases/IC-01/run", json={}, headers=H("cloud_ops")).status_code == 403


def test_user_identifiers_are_pseudonymized(client):
    r = client.post("/api/usecases/RA-06/run", json={}, headers=H("cfo")).json()
    assert all(not str(row[0]).count("@") for row in r["rows"])


def test_separation_of_duties_and_ledger(client):
    # FinOps analyst may submit but not approve; CFO approves; ledger chain still verifies.
    assert client.post("/api/findings/F-47/decision", json={"action": "approve"}, headers=H("finops_analyst")).status_code == 403
    assert client.post("/api/findings/F-47/decision", json={"action": "submit"}, headers=H("finops_analyst")).status_code == 200
    assert client.post("/api/findings/F-47/decision", json={"action": "approve"}, headers=H("cfo")).status_code == 200
    assert client.get("/api/evidence", headers=H("auditor")).json()["ledger_verified"]["verified"]


def test_ask_answers_from_registry(client):
    a = client.post("/api/ask", json={"question": "How much are we spending on idle Copilot seats?"}, headers=H("cfo")).json()
    assert a["status"] == "answered" and a["use_case"]["id"] == "UO-05"
    b = client.post("/api/ask", json={"question": "'; DROP TABLE CostAndUsage; --"}, headers=H("cfo")).json()
    assert b["status"] in ("answered", "no_match")
    assert db.query("SELECT COUNT(*) FROM CostAndUsage")["rows"][0][0] > 0


def test_board_pack_sections_and_access(client):
    d = client.get("/api/board", headers=H("investor_relations")).json()
    assert [s["n"] for s in d["sections"]] == list(range(1, 14))
    assert all(s["status"] in ("live", "partial", "needs") for s in d["sections"]), [s.get("gap") for s in d["sections"]]
    spend = d["sections"][0]
    assert spend["title"] == "Spend and outlook" and {m["label"] for m in spend["metrics"]} >= {"Full-year run-rate", "Forecast confidence"}
    decisions = d["sections"][3]["table"]["rows"]
    assert 0 < len(decisions) <= 5 and {r["Label"] for r in decisions} <= {"Already spent", "Estimated saving", "Compliance exposure"}
    programs = d["sections"][4]["table"]["rows"]
    assert {r["Phase"] for r in programs} == {"Discovery", "Phase 1", "Phase 2", "Phase 3", "Marketed"}
    assert client.get("/api/board", headers=H("pl_exec")).status_code == 403


def test_ai_summary_cites_facts_and_respects_role(client):
    import re
    from costiq.auth import access
    from costiq.summary import PAGES, OPEN_TO_ALL, Summarizer
    for persona in ("cfo", "gxp_compliance", "pl_exec"):
        d = client.get("/api/summary", headers=H(persona)).json()
        texts = [d["headline"]] + [t for s in d["sections"] for t in s["sentences"]]
        cited = {f for t in texts for f in re.findall(r"⟦(f\d+)⟧", t)}
        assert cited and cited <= set(d["facts"]), persona
        assert [s["n"] for s in d["sources"]] == list(range(1, len(d["sources"]) + 1))
        modules = set(access()["personas"][persona]["modules"]) | OPEN_TO_ALL | {"catalog"}
        allowed = {PAGES[m][1] for m in modules if m in PAGES}
        for f in d["facts"].values():  # a figure only links to a page the role can open (or a use case in the catalog)
            path = (f["href"] or "").split("?")[0]
            assert not f["href"] or path in allowed or path.startswith("/catalog/"), (persona, f)
        assert len(d["actions"]) <= 4 and all(a["href"] for a in d["actions"])
    cfo = client.get("/api/summary", headers=H("cfo")).json()
    assert [s["key"] for s in cfo["sections"]] == ["spend", "risk", "gxp", "money", "ops"]
    assert "budget" in cfo["headline"] and cfo["writer"] == "costiq"
    assert all(sec["state"] and sec["tone"] in ("good", "warn", "bad", "info") for sec in cfo["sections"])
    scoped = client.get("/api/summary", headers=H("pl_exec")).json()  # Clinical Operations only: no enterprise tax figures
    assert "risk" not in {s["key"] for s in scoped["sections"]}
    # the Claude writer's guard: citations must exist and figures may only appear through citations
    check = Summarizer._check
    assert check({"headline": "Spend is ⟦f1⟧.", "sections": [{"key": "spend", "sentences": ["Up ⟦f2⟧."]}]}, cfo) is None
    assert "uncited figure" in check({"headline": "Spend is $1.2M.", "sections": []}, cfo)
    assert "unknown citation" in check({"headline": "Spend is ⟦f999⟧.", "sections": []}, cfo)


def test_period_selector_window_applies_across_pages(client):
    import datetime as dt
    from costiq.window import use_case_params, window
    today = dt.date(2026, 10, 22)
    assert (window("yesterday", today).start, window("yesterday", today).end) == (dt.date(2026, 10, 21),) * 2
    assert window("wtd", today).start == dt.date(2026, 10, 19) and window("ytd", today).prev is None
    h = H("cfo")
    day = client.get("/api/cloud-monitor?range=yesterday", headers=h).json()
    month = client.get("/api/cloud-monitor?range=mtd", headers=h).json()
    assert day["period"]["range"] == "yesterday" and 0 < day["kpis"]["period_spend"] < month["kpis"]["period_spend"]
    assert client.get("/api/ai-monitor?range=today", headers=h).json()["period"]["start"] == "2026-10-22"
    ex = client.get("/api/explorer?range=today", headers=h).json()
    assert ex["period"]["range"] == "today" and all(str(r["Day"])[:10] == "2026-10-22" for r in ex["lines"])
    units = client.get("/api/command?range=yesterday", headers=h).json()["units"]
    assert all("SpendInPeriod" in u for u in units) and sum(u["SpendInPeriod"] for u in units) > 0
    assert client.get("/api/alerts?range=yesterday", headers=h).json()["open_total"] >= 0
    s = client.get("/api/summary?range=yesterday", headers=h).json()
    assert s["period"]["range"] == "yesterday" and s["headline"].startswith("Yesterday's spend")
    assert client.get("/api/cloud-monitor?range=fortnight", headers=h).status_code == 400
    # use cases: daily ones run over the exact days, billing-period ones over the whole billing month
    run = client.post("/api/usecases/F-14/run", headers=h, json={"params": {}, "range": "yesterday"}).json()
    assert run["params"]["period_start"] == "2026-10-21" and run["rows"]
    from costiq.context import reg
    assert use_case_params(reg.get("F-01"), window("yesterday", today))[1].startswith("billing month 2026-10")
    asked = client.post("/api/ask", headers=h, json={"question": "spend by provider", "range": "yesterday"}).json()
    assert any("yesterday" in i for i in asked["interpreted"])
