"""Pharma module: allocation chain, controls, entity wall, policy workflow, PH checks, forecast bridge."""
import datetime as dt
import pytest
from fastapi.testclient import TestClient
from costiq.main import app
from costiq.context import db, eng, reg

client = TestClient(app)


def h(persona):
    return {"X-CostIQ-Persona": persona}


def entity_scoped(entities):
    """An entity-scoped sign-in (Entra group in entityScopeGroups), as the API builds it from a token."""
    from costiq.auth import EntityScope, Principal, current_principal
    p = Principal(user="country.finance@company.example", name="Country finance", personas=["tax_analyst"],
                  modules=["tax", "risk", "catalog", "ask"], actions=[], scope=EntityScope(entities), pii=False)
    app.dependency_overrides[current_principal] = lambda: p


def q(sql, params=None):
    return db.query(sql, params or {}, None, limit=None)["rows"]


def test_allocated_total_ties_to_source_every_period():
    out = q("""SELECT s.p, s.src, a.alloc FROM
               (SELECT BillingPeriodStart AS p, SUM(EffectiveCost) AS src FROM CostAndUsage GROUP BY 1) s
               JOIN (SELECT PeriodStart AS p, SUM(AllocatedCost) AS alloc FROM allocated_cost GROUP BY 1) a ON a.p = s.p""")
    assert len(out) == 4
    for _, src, alloc in out:
        assert abs(float(src) - float(alloc)) <= 0.01


def test_driver_shares_equal_driver_ratios():
    key, ps = q("""SELECT ChargeKey, PeriodStart FROM allocated_cost
                   WHERE OpcoPolicy = 'AP-PATIENT' ORDER BY SourceCost DESC LIMIT 1""")[0]
    shares = dict(q("""SELECT LegalEntity, SUM(OpcoShare * JurisdictionFactor) FROM allocated_cost
                       WHERE ChargeKey = :k GROUP BY 1""", {"k": key}))
    drivers = dict(q("SELECT LegalEntity, DriverValue FROM ref_allocation_driver WHERE DriverName = 'EnrolledPatients' AND PeriodStart = :p",
                     {"p": dt.datetime.fromisoformat(str(ps))}))
    total = sum(float(v) for v in drivers.values())
    for entity, v in drivers.items():
        assert abs(float(shares[entity]) - float(v) / total) < 1e-9


def test_allocation_controls_report_seeded_issues():
    c = {x["id"]: x for x in eng.allocation_controls()}
    assert c["C-08"]["result"] == "exceptions"      # Oracle Argus Analytics add-on has no GL map
    assert c["C-09"]["result"] == "exceptions"      # shared Claude API policy runs before the Consumer Health policy
    assert c["C-10"]["result"] == "pass"
    assert c["C-11"]["result"] == "pass"


def test_every_pharma_check_flags_its_seeded_issue():
    gxp = client.get("/api/compliance/compliance", headers=h("tax_analyst")).json()
    items = {i["id"]: i for a in gxp["areas"] for i in a["items"]}
    assert set(items) == {"PH-07", "PH-08", "PH-09", "PH-11"}
    tax = client.get("/api/compliance/tax", headers=h("tax_analyst")).json()
    items.update({i["id"]: i for a in tax["areas"] for i in a["items"]})
    assert {"PH-01", "PH-02", "PH-03", "PH-04", "PH-05", "PH-06"} <= set(items)
    for uid in ("PH-01", "PH-03", "PH-04", "PH-06", "PH-07", "PH-08", "PH-09", "PH-11"):
        assert items[uid]["status"] == "attention" and items[uid]["flagged"] > 0, uid


def test_entity_wall_for_entity_scoped_user():
    us = "US Operations"
    entity_scoped([us])
    try:
        rows = client.post("/api/usecases/PH-02/run", json={"params": {}}).json()["rows"]
        assert rows and {r[0] for r in rows} == {us}
        # shared, pre-allocation charges are invisible to an entity-scoped user
        explorer = client.post("/api/usecases/F-62/run", json={"params": {}})
        assert explorer.status_code == 403 or not explorer.json()["rows"]
        assert client.get("/api/reconciliation").status_code == 403
        chain = client.get("/api/tax/allocation-chain").json()
        assert {r["LegalEntity"] for r in chain["opco"]} == {us}
    finally:
        app.dependency_overrides.clear()


def test_policy_workflow_separation_of_duties_and_reallocation():
    pending = [r for r in client.get("/api/tax/policies", headers=h("tax_analyst")).json()["policies"]
               if r["Status"] == "submitted"]
    assert pending, "the sample ships one policy awaiting approval"
    pid, ver = pending[0]["PolicyId"], pending[0]["Version"]
    # the submitter cannot approve
    r = client.post(f"/api/tax/policies/{pid}/decision", json={"action": "approve", "version": ver},
                    headers=h("transfer_pricing"))
    assert r.status_code == 403  # transfer pricing lacks rule.approve entirely
    before = dict(q("""SELECT LegalEntity, SUM(AllocatedCost) FROM allocated_cost
                       WHERE OpcoPolicy = :p AND PeriodStart = TIMESTAMP '2026-10-01' GROUP BY 1""", {"p": pid}))
    r = client.post(f"/api/tax/policies/{pid}/decision", json={"action": "approve", "version": ver, "note": "test"},
                    headers=h("finance_controller"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["policy"]["Status"] == "approved" and "2026-10" in body["reallocated"]
    after = dict(q("""SELECT LegalEntity, SUM(AllocatedCost) FROM allocated_cost
                      WHERE OpcoPolicy = :p AND OpcoPolicyVersion = :v AND PeriodStart = TIMESTAMP '2026-10-01' GROUP BY 1""",
                   {"p": pid, "v": ver}))
    assert after and after != before
    assert any(e["Target"] == f"policy:{pid}:v{ver}" and e["Action"] == "approve" for e in
               client.get("/api/evidence", headers=h("finance_controller")).json()["ledger"])
    # a controller who submits a change cannot approve their own submission
    assert client.post(f"/api/tax/policies/{pid}/decision", json={"action": "submit", "driver": "Employees"},
                       headers=h("finance_controller")).status_code == 403


def test_policy_submitter_cannot_approve_own_submission():
    from costiq.routes_regulatory import regs
    pol = regs.submit("demo.finance_controller@company.example", "AP-CLINICAL", driver="EnrolledPatients")
    r = client.post("/api/tax/policies/AP-CLINICAL/decision", json={"action": "approve", "version": pol["Version"]},
                    headers=h("finance_controller"))
    assert r.status_code == 409


def test_filing_forecast_bridge():
    out = client.get("/api/tax/filing/US-1120-FY2026/bridge", headers=h("tax_analyst")).json()
    vendors = {l["Vendor"]: l for l in out["lines"]}
    assert vendors["ServiceNow"]["InvoiceTieOut"] == "Invoice exception"
    assert vendors["SAP"]["ContractEscalation"] > 0
    csv = client.get("/api/tax/filing/US-1120-FY2026/bridge.csv", headers=h("tax_analyst"))
    assert csv.status_code == 200 and "ForecastYear" in csv.text and "InvoiceTieOut" in csv.text
    assert client.get("/api/tax/filing/US-1120-FY2026/bridge.csv", headers=h("transfer_pricing")).status_code == 403


def test_credit_schedule_applies_the_filing_rate():
    out = client.get("/api/tax/credit-schedule", headers=h("tax_analyst")).json()
    assert {r["Jurisdiction"] for r in out["by_jurisdiction"]} == {"US"}
    us = out["by_jurisdiction"][0]
    assert us["CreditRate"] == 6.5 and us["Qre"] > 0 and abs(us["Credit"] - us["Qre"] * 0.065) < 0.05
    assert sum(s["Qre"] for s in out["schedule"]) == pytest.approx(us["Qre"], abs=0.05)


def test_lineage_shows_entity_steps():
    key = q("SELECT ChargeKey FROM allocated_cost WHERE OpcoPolicy = 'AP-PATIENT' LIMIT 1")[0][0]
    steps = [s["step"] for s in client.get(f"/api/lineage/{key}", headers=h("cfo")).json()["steps"]]
    i = steps.index("Entity split")
    assert steps[i:i + 3] == ["Entity split", "Jurisdiction", "GL account and cost class"]


def test_saas_invoice_template_ingests_and_ties_to_invoices():
    import os
    from costiq.adapters import saas_invoice
    template = os.path.join(os.path.dirname(__file__), "..", "data", "templates", "saas_invoice_template.csv")
    out = saas_invoice.load(db, "2026-11", [template])
    assert out["status"] == "promoted" and out["rows"] == 3
    tie = q("""SELECT i.InvoiceDetailId, i.BilledCost, SUM(c.BilledCost) FROM InvoiceDetail i
               JOIN CostAndUsage c ON c.InvoiceDetailId = i.InvoiceDetailId
               WHERE c.x_SourceSystem = 'saas_invoice_filedrop' AND c.BillingPeriodStart = TIMESTAMP '2026-11-01'
               GROUP BY 1, 2""")
    assert len(tie) == 3 and all(abs(float(a) - float(b)) < 0.01 for _, a, b in tie)
    impl = q("""SELECT json_extract_string(Tags, '$.implementation') FROM CostAndUsage
                WHERE ServiceName = 'SAP S/4HANA implementation services' AND BillingPeriodStart = TIMESTAMP '2026-11-01'""")
    assert impl == [["true"]]
    # replay is idempotent
    assert saas_invoice.load(db, "2026-11", [template])["rows"] == 3
    assert q("SELECT COUNT(*) FROM CostAndUsage WHERE x_SourceSystem = 'saas_invoice_filedrop' AND BillingPeriodStart = TIMESTAMP '2026-11-01'") == [[3]]
