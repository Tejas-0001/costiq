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
    mkt = db.query(uc["sql"], p, scope=["Transmission Planning"], limit=None)["rows"]
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
