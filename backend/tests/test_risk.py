"""Tax risk: deduction at risk, remediation, credit unlock, audit file, Examiner Request Responder."""
import io, zipfile
import pytest
from fastapi.testclient import TestClient
from costiq.main import app
from costiq.context import db, eng
from costiq.prudence import PrudenceEngine

client = TestClient(app)
pytestmark = pytest.mark.tenant("pharma")  # pharma filings, reasons and the R&D credit
pe = PrudenceEngine(db, eng)


def h(persona):
    return {"X-CostIQ-Persona": persona}


def risk(filing=None, **where):
    cond = " AND ".join(f"{k} = :{k}" for k in where) or "1 = 1"
    d = " AND FilingId = :filing" if filing else ""
    v = db.query(f"SELECT COALESCE(SUM(RiskAmount), 0) FROM filing_evidence WHERE {cond}{d}",
                 {**where, **({"filing": filing} if filing else {})})["rows"][0][0]
    return round(float(v), 2)


def test_each_seeded_issue_lands_under_the_right_reason():
    reasons = lambda **w: {r[0] for r in db.query(
        "SELECT DISTINCT RiskReason FROM filing_evidence WHERE RiskAmount > 0 AND " + " AND ".join(f"{k} = :{k}" for k in w),
        w)["rows"]}
    assert reasons(ResourceName="molgen-gpu-endpoint") == {"no_benefit"}
    assert "unreconciled" in reasons(ServiceProviderName="ServiceNow")
    assert reasons(Application="pv-analytics") == {"unmapped_gl"}
    assert reasons(ResourceName="aoai-ptu-discovery") == {"unactioned_savings"}
    assert "no_benefit" in reasons(ResourceName="edc-test-ec2")
    gpu = db.query("SELECT DISTINCT FilingId FROM filing_evidence WHERE ResourceName = 'molgen-gpu-endpoint'")["rows"]
    assert gpu == [["IE-CT1-FY2026"]]  # Research & Discovery work lands in the Irish research entity's filing


def test_headline_numbers_on_command_center():
    k = client.get("/api/command", headers=h("cfo")).json()
    hl = k["headline"]
    assert hl["deduction_at_risk"] > 0 and hl["credit_unlock"] > 0 and hl["verified_savings_annual"] > 0
    assert "headline" not in client.get("/api/command", headers=h("pl_exec")).json()


def test_fixing_the_idle_gpu_drops_risk_by_exactly_its_amount():
    before_total = risk()
    gpu = risk(ResourceName="molgen-gpu-endpoint")
    assert gpu > 10000
    assert client.post("/api/optimization/OPT-005/decision", json={"action": "approve"}, headers=h("cfo")).status_code == 200
    assert client.post("/api/optimization/OPT-005/decision", json={"action": "implement"},
                       headers=h("ai_program_owner")).status_code == 200
    assert risk(ResourceName="molgen-gpu-endpoint") == 0
    assert abs((before_total - risk()) - gpu) < 0.05
    line = db.query("SELECT DISTINCT Remediated FROM filing_evidence WHERE ResourceName = 'molgen-gpu-endpoint'")["rows"]
    assert line == [[True]]


def test_credit_unlock_matches_hand_computation():
    out = pe.credit_unlock(None)
    uk = next(j for j in out["jurisdictions"] if j["Jurisdiction"] == "UK")
    assert uk["CreditStatus"] == "eligible" and not uk["Claimed"] and uk["Credit"] > 0 and uk["CreditRate"] == 20.0
    hand = sum(c["AnnualFees"] for c in uk["Contracts"]) * uk["CreditRate"] / 100
    assert abs(hand - uk["Credit"]) < 0.05
    pr = next(j for j in out["jurisdictions"] if j["Jurisdiction"] == "PR")
    assert pr["FilingId"] == "US-1120-FY2026" and pr["CreditRate"] == 6.5  # Puerto Rico uses its entity's US filing rate
    de = next(j for j in out["jurisdictions"] if j["Jurisdiction"] == "DE")
    assert de["CreditStatus"] == "ineligible" and not de["Claimed"]
    us = next(j for j in out["jurisdictions"] if j["Jurisdiction"] == "US")
    assert us["Claimed"] and out["credit_claimed"] == us["Credit"]
    assert abs(out["credit_unlock"] - (uk["Credit"] + pr["Credit"])) < 0.02
    # catalog version (PH-13 SQL) agrees with the engine
    sql = {r[0]: r[5] for r in db.query(__import__("costiq.context", fromlist=["reg"]).reg.get("PH-13")["sql"])["rows"]}
    for j in out["jurisdictions"]:
        assert abs(sql[j["Jurisdiction"]] - j["Credit"]) < 0.02


def test_evidence_pack_is_deterministic_sealed_and_verifiable():
    url = "/api/tax/filing/US-1120-FY2026/audit-file.zip"
    a = client.get(url, headers=h("tax_analyst"))
    b = client.get(url, headers=h("tax_analyst"))
    assert a.status_code == 200 and a.content == b.content
    sha = a.headers["X-CostIQ-SHA256"]
    names = zipfile.ZipFile(io.BytesIO(a.content)).namelist()
    assert any(n.endswith("forecast_bridge.csv") for n in names) and any("/workpapers/" in n for n in names)
    assert any(n.endswith("ledger_certificate.json") for n in names)
    packs = client.get("/api/tax/audit-files", headers=h("tax_analyst")).json()
    assert any(p["Sha256"] == sha for p in packs)
    ok = client.post("/api/tax/audit-file/verify", files={"file": ("pack.zip", a.content, "application/zip")},
                     headers=h("auditor")).json()
    assert ok["recorded"] and ok["verdict"].startswith("Authentic")
    tampered = bytearray(a.content)
    tampered[-30] ^= 0xFF
    bad = client.post("/api/tax/audit-file/verify", files={"file": ("pack.zip", bytes(tampered), "application/zip")},
                      headers=h("auditor")).json()
    assert not bad["recorded"]
    assert client.get(url, headers=h("transfer_pricing")).status_code == 403  # no exhibit.export


def test_data_request_responder_and_separation_of_duties():
    body = {"filing": "US-1120-FY2026", "requester": "IRS examiner",
            "text": "Provide cloud subscription costs for the forecast year by vendor, with any invoice differences."}
    r = client.post("/api/tax/examiner-requests", json=body, headers=h("tax_analyst")).json()
    assert r["MatchedUseCases"] == "PH-05" and r["Status"] == "draft"
    assert "IRS examiner" in r["Draft"]["narrative"] and r["Draft"]["result"]["rows"]
    rid = r["RequestId"]
    assert client.post(f"/api/tax/examiner-requests/{rid}/decision", json={"action": "approve"},
                       headers=h("tax_analyst")).status_code == 409
    ok = client.post(f"/api/tax/examiner-requests/{rid}/decision", json={"action": "approve"}, headers=h("cfo")).json()
    assert ok["request"]["Status"] == "approved" and ok["ledger"]["Seq"]
    trail = [e for e in client.get("/api/evidence", headers=h("finance_controller")).json()["ledger"]
             if e["Target"] == f"examiner_request:{rid}"]
    assert [e["Action"] for e in trail] == ["prepare", "approve"]


def test_entity_wall_applies_to_risk():
    from test_tax import entity_scoped
    entity_scoped(["US Operations"])
    try:
        lines = client.get("/api/risk/lines").json()
        assert lines and {l["LegalEntity"] for l in lines} == {"US Operations"}
        summary = client.get("/api/risk").json()
        assert {d["LegalEntity"] for d in summary["by_filing"]} == {"US Operations"}
    finally:
        app.dependency_overrides.clear()
