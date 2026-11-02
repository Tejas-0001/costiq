"""The Prudence Engine: disallowance risk, remediation, Earnings Unlock, Evidence Pack, Data Request Responder."""
import io, zipfile
from fastapi.testclient import TestClient
from costiq.main import app
from costiq.context import db, eng
from costiq.prudence import PrudenceEngine

client = TestClient(app)
pe = PrudenceEngine(db, eng)


def h(persona):
    return {"X-CostIQ-Persona": persona}


def risk(docket=None, **where):
    cond = " AND ".join(f"{k} = :{k}" for k in where) or "1 = 1"
    d = " AND Docket = :docket" if docket else ""
    v = db.query(f"SELECT COALESCE(SUM(RiskAmount), 0) FROM prudence_evidence WHERE {cond}{d}",
                 {**where, **({"docket": docket} if docket else {})})["rows"][0][0]
    return round(float(v), 2)


def test_each_seeded_issue_lands_under_the_right_reason():
    reasons = lambda **w: {r[0] for r in db.query(
        "SELECT DISTINCT RiskReason FROM prudence_evidence WHERE RiskAmount > 0 AND " + " AND ".join(f"{k} = :{k}" for k in w),
        w)["rows"]}
    assert reasons(ResourceName="gridplan-gpu-endpoint") == {"not_used_and_useful"}
    assert "unreconciled" in reasons(ServiceProviderName="ServiceNow")
    assert reasons(Application="cis-analytics") == {"unmapped_ferc"}
    assert reasons(ResourceName="aoai-ptu-gridplan") == {"unactioned_savings"}
    assert "not_used_and_useful" in reasons(ResourceName="oms-test-ec2")
    gpu = db.query("SELECT DISTINCT Docket FROM prudence_evidence WHERE ResourceName = 'gridplan-gpu-endpoint'")["rows"]
    assert gpu == [["ER27-118-000"]]  # Transmission Planning work lands in the FERC formula-rate docket


def test_headline_numbers_on_command_center():
    k = client.get("/api/command", headers=h("cfo")).json()
    hl = k["headline"]
    assert hl["disallowance_risk"] > 0 and hl["earnings_unlock"] > 0 and hl["verified_savings_annual"] > 0
    assert "headline" not in client.get("/api/command", headers=h("pl_exec")).json()


def test_fixing_the_idle_gpu_drops_risk_by_exactly_its_amount():
    before_total = risk()
    gpu = risk(ResourceName="gridplan-gpu-endpoint")
    assert gpu > 10000
    assert client.post("/api/optimization/OPT-005/decision", json={"action": "approve"}, headers=h("cfo")).status_code == 200
    assert client.post("/api/optimization/OPT-005/decision", json={"action": "implement"},
                       headers=h("ai_program_owner")).status_code == 200
    assert risk(ResourceName="gridplan-gpu-endpoint") == 0
    assert abs((before_total - risk()) - gpu) < 0.05
    line = db.query("SELECT DISTINCT Remediated FROM prudence_evidence WHERE ResourceName = 'gridplan-gpu-endpoint'")["rows"]
    assert line == [[True]]


def test_earnings_unlock_matches_hand_computation():
    out = pe.earnings_unlock(None)
    tx = next(j for j in out["jurisdictions"] if j["Jurisdiction"] == "TX")
    assert tx["DeferralStatus"] == "untested" and not tx["Earned"] and tx["SteadyStateReturn"] > 0
    hand = sum(c["AnnualFees"] / 12 * (c["AmortizationMonths"] + 1) / 2 for c in tx["Contracts"]) * tx["PreTaxRor"] / 100
    assert abs(hand - tx["SteadyStateReturn"]) < 1e-4 * tx["SteadyStateReturn"]  # PreTaxRor is shown rounded to 0.01
    sap = next(c for c in tx["Contracts"] if c["Vendor"] == "SAP")
    assert sap["AmortizationMonths"] == 23  # Apr 2027 test year start -> Mar 2029 contract end
    oh = next(j for j in out["jurisdictions"] if j["Jurisdiction"] == "OH")
    assert oh["Earned"] and out["earned_today"] == oh["SteadyStateReturn"]
    # catalog version (UT-13 SQL) agrees with the engine
    sql = {r[0]: r[5] for r in db.query(__import__("costiq.context", fromlist=["reg"]).reg.get("UT-13")["sql"])["rows"]}
    for j in out["jurisdictions"]:
        assert abs(sql[j["Jurisdiction"]] - j["SteadyStateReturn"]) < 0.02


def test_evidence_pack_is_deterministic_sealed_and_verifiable():
    url = "/api/regulatory/rate-case/OH-26-1105-EL-AIR/evidence-pack.zip"
    a = client.get(url, headers=h("regulatory_analyst"))
    b = client.get(url, headers=h("regulatory_analyst"))
    assert a.status_code == 200 and a.content == b.content
    sha = a.headers["X-CostIQ-SHA256"]
    names = zipfile.ZipFile(io.BytesIO(a.content)).namelist()
    assert any(n.endswith("exhibit_bridge.csv") for n in names) and any("/workpapers/" in n for n in names)
    assert any(n.endswith("ledger_certificate.json") for n in names)
    packs = client.get("/api/regulatory/evidence-packs", headers=h("regulatory_analyst")).json()
    assert any(p["Sha256"] == sha for p in packs)
    ok = client.post("/api/regulatory/evidence-pack/verify", files={"file": ("pack.zip", a.content, "application/zip")},
                     headers=h("auditor")).json()
    assert ok["recorded"] and ok["verdict"].startswith("Authentic")
    tampered = bytearray(a.content)
    tampered[-30] ^= 0xFF
    bad = client.post("/api/regulatory/evidence-pack/verify", files={"file": ("pack.zip", bytes(tampered), "application/zip")},
                      headers=h("auditor")).json()
    assert not bad["recorded"]
    assert client.get(url, headers=h("affiliate_accounting")).status_code == 403  # no exhibit.export


def test_data_request_responder_and_separation_of_duties():
    body = {"docket": "OH-26-1105-EL-AIR", "requester": "PUCO Staff",
            "text": "Provide cloud subscription costs for the test year by vendor, with any invoice differences."}
    r = client.post("/api/regulatory/data-requests", json=body, headers=h("regulatory_analyst")).json()
    assert r["MatchedUseCases"] == "UT-05" and r["Status"] == "draft"
    assert "PUCO Staff" in r["Draft"]["narrative"] and r["Draft"]["result"]["rows"]
    rid = r["RequestId"]
    assert client.post(f"/api/regulatory/data-requests/{rid}/decision", json={"action": "approve"},
                       headers=h("regulatory_analyst")).status_code == 409
    ok = client.post(f"/api/regulatory/data-requests/{rid}/decision", json={"action": "approve"}, headers=h("cfo")).json()
    assert ok["request"]["Status"] == "approved" and ok["ledger"]["Seq"]
    trail = [e for e in client.get("/api/evidence", headers=h("finance_controller")).json()["ledger"]
             if e["Target"] == f"data_request:{rid}"]
    assert [e["Action"] for e in trail] == ["prepare", "approve"]


def test_affiliate_wall_applies_to_prudence():
    lines = client.get("/api/prudence/lines", headers=h("opco_finance")).json()
    assert lines and {l["LegalEntity"] for l in lines} == {"Meridian Electric Ohio"}
    summary = client.get("/api/prudence", headers=h("opco_finance")).json()
    assert {d["LegalEntity"] for d in summary["by_docket"]} == {"Meridian Electric Ohio"}
