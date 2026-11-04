"""Data Readiness page (/api/readiness), the AI page's Copilot re-harvest and token check, and who may open them.

    python -m pytest -q tests/test_readiness.py                          # pharma: access and unchanged AI payload
    COSTIQ_TENANT=utility python -m pytest -q tests/test_readiness.py    # utility: figures
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
H = lambda p: {"X-CostIQ-Persona": p}  # noqa: E731
CTRL = "finance_controller"


def access_file(t):
    return yaml.safe_load(open(os.path.join(ROOT, "tenants", t, "access.yaml"), encoding="utf-8"))


@pytest.fixture(scope="module")
def client():
    from costiq.main import app
    return TestClient(app)


def roles():
    from costiq import tenant
    return tenant.pack().get("usecases") or {}


# ------------------------------------------------------------------------------------------------ access (any tenant)
def test_readiness_module_and_personas():
    pharma, utility = access_file("pharma"), access_file("utility")
    assert pharma["modules"]["readiness"] == utility["modules"]["readiness"] == "Data Readiness"
    assert not [k for k, p in pharma["personas"].items() if "readiness" in p["modules"]]
    ctrl = utility["personas"][CTRL]["modules"]
    assert {"ai", "readiness"} <= set(ctrl)
    for m in ("reconciliation", "allocation", "tax", "catalog", "explorer"):  # the rest of the demo path
        assert m in ctrl


@pytest.mark.tenant("pharma")
def test_pharma_personas_cannot_open_readiness_and_ai_payload_is_unchanged(client):
    from costiq.auth import access
    for persona in access()["personas"]:
        assert client.get("/api/readiness", headers=H(persona)).status_code == 403, persona
    d = client.get("/api/ai-monitor?range=ytd", headers=H("cfo")).json()
    assert not {"reharvest", "token_check", "token_check_period"} & set(d)


# ------------------------------------------------------------------------------------------------ utility
@pytest.mark.tenant("utility")
def test_readiness_access_utility(client):
    from costiq.auth import access
    personas = access()["personas"]
    for persona, cfg in personas.items():
        code = client.get("/api/readiness?range=mtd", headers=H(persona)).status_code
        assert code == (200 if "readiness" in cfg["modules"] else 403), persona
    assert client.get("/api/ai-monitor", headers=H(CTRL)).status_code == 200  # the Controller opens the AI page


@pytest.mark.tenant("utility")
def test_readiness_matches_the_pages_behind_it(client):
    d = client.get("/api/readiness?range=ytd", headers=H(CTRL)).json()
    assert [s["key"] for s in d["score"]["sections"]] == ["sources", "tags", "accounting", "reconciliation", "controls"]
    assert all(s["status"] in ("ready", "partial", "gaps", "na") for s in d["score"]["sections"])
    have = [s["score"] for s in d["score"]["sections"] if s["score"] is not None]
    assert d["score"]["overall"] == round(sum(have) / len(have), 1)

    # sources: one row per feed, rows against the manifest, InvoiceId presence, flagged spend
    src = {s["Source"]: s for s in d["sources"]}
    assert {"aws_focus_export", "azure_focus_export"} <= set(src)
    for s in d["sources"]:
        assert s["RowsMatch"] == (s["Rows"] == s["ManifestRows"]), s
        assert 0 <= s["InvoiceIdPresentPct"] <= 100
    assert src["aws_focus_export"]["FocusVersion"] and src["aws_focus_export"]["ExportMode"]
    assert src["aws_focus_export"]["CipFlaggedSpend"] > 0  # historian replica, network and SIEM accounts

    # tags: the Allocation page's figures when the tag-compliance endpoint exists
    tc = client.get("/api/tag-compliance?range=ytd", headers=H(CTRL))
    if tc.status_code == 200:
        t = tc.json()
        assert d["tags"]["keys"] == t["keys"] and d["tags"]["suspense"] == t["suspense"]
        assert d["tags"]["cannot_be_tagged"] == t["cannot_be_tagged"]

    # reconciliation: tied invoices counted from the Reconciliation page's lines
    lines = client.get("/api/reconciliation", headers=H(CTRL)).json()["lines"]
    inv = {}
    for line in lines:
        inv.setdefault(line["InvoiceId"], []).append(line["Status"])
    assert d["reconciliation"]["invoices"] == len(inv)
    assert d["reconciliation"]["tied"] == sum(1 for s in inv.values() if all(x == "matched" for x in s))

    # controls: the Governance page's controls
    ev = client.get("/api/evidence", headers=H(CTRL)).json()["controls"]
    assert d["controls"] == ev
    assert d["confidence"]["score"] > 0

    # accounting: the parts add up to the ledger total for the period
    a = d["accounting"]
    assert abs(a["mapped"] + a["unmapped"] - a["total"]) < 0.02
    if a["rules_available"]:
        parts = a["regulation"] + a["policy_signed_off"] + a["policy_awaiting"] + a["no_rule"] + a["unmapped"]
        assert abs(parts - a["total"]) < 0.05
        assert abs(sum(r["AllocatedCost"] for r in a["by_rule"]) - a["total"]) < 0.05
    assert a["work_orders"] >= a["work_orders_with_threshold"] > 0
    assert 0 < a["capitalized_valid_wo"] < a["capitalized"]  # integrator and threshold-date lines are held out


@pytest.mark.tenant("utility")
def test_readiness_follows_the_period_selector(client):
    ytd = client.get("/api/readiness?range=ytd", headers=H(CTRL)).json()
    mtd = client.get("/api/readiness?range=mtd", headers=H(CTRL)).json()
    assert mtd["period"]["range"] == "mtd" and ytd["period"]["range"] == "ytd"
    assert ytd["accounting"]["total"] > mtd["accounting"]["total"] > 0
    assert sum(s["Rows"] for s in ytd["sources"]) > sum(s["Rows"] for s in mtd["sources"]) > 0
    assert ytd["reconciliation"]["invoices"] > mtd["reconciliation"]["invoices"]
    assert client.get("/api/readiness?range=decade", headers=H(CTRL)).status_code == 400


@pytest.mark.tenant("utility")
def test_ai_page_copilot_reharvest(client):
    if not roles().get("seat_reharvest"):
        pytest.skip("utility pack has no usecases.seat_reharvest yet")
    r = client.get("/api/ai-monitor?range=mtd", headers=H(CTRL)).json()["reharvest"]
    assert (r["idle"], r["group"], r["direct"], r["in_grace"]) == (540, 380, 160, 45)
    assert r["monthly_avoidable"] == 16200.0
    assert r["renewal_date"] == "2027-03-01"
    assert r["rule"]["threshold_days"] == "30"
    assert len(r["rows"]) == 100 and all(x["UserPrincipal"].startswith("P-") for x in r["rows"])  # no pii.view
    assert sum(g["IdleSeats"] for g in r["groups"]) == 380


@pytest.mark.tenant("utility")
def test_ai_page_token_check(client):
    if not roles().get("token_check"):
        pytest.skip("utility pack has no usecases.token_check yet")
    d = client.get("/api/ai-monitor?range=mtd&month=2026-09", headers=H(CTRL)).json()
    agent = next(r for r in d["token_check"] if r["Application"] == "interconnection-agent")
    assert agent["MissedIfCacheIgnored"] == 3360.0
    assert d["token_check_period"]["month"] == "2026-09" and "2026-09" in d["token_check_period"]["months"]
    mtd = client.get("/api/ai-monitor?range=mtd", headers=H(CTRL)).json()
    assert mtd["token_check_period"]["month"] is None and mtd["token_check"] != d["token_check"]
    assert client.get("/api/ai-monitor?month=Sept", headers=H(CTRL)).status_code == 400
