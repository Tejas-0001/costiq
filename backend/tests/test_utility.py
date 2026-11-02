"""Utility module: allocation chain, controls, affiliate wall, policy workflow, UT checks, rate-case exhibit."""
import datetime as dt
from fastapi.testclient import TestClient
from costiq.main import app
from costiq.context import db, eng, reg

client = TestClient(app)


def h(persona):
    return {"X-CostIQ-Persona": persona}


def q(sql, params=None):
    return db.query(sql, params or {}, None, limit=None)["rows"]


def test_allocated_total_ties_to_source_every_period():
    out = q("""SELECT s.p, s.src, a.alloc FROM
               (SELECT BillingPeriodStart AS p, SUM(EffectiveCost) AS src FROM CostAndUsage GROUP BY 1) s
               JOIN (SELECT PeriodStart AS p, SUM(AllocatedCost) AS alloc FROM ut_allocated_cost GROUP BY 1) a ON a.p = s.p""")
    assert len(out) == 4
    for _, src, alloc in out:
        assert abs(float(src) - float(alloc)) <= 0.01


def test_driver_shares_equal_driver_ratios():
    key, ps = q("""SELECT ChargeKey, PeriodStart FROM ut_allocated_cost
                   WHERE OpcoPolicy = 'AP-CUSTOMER' ORDER BY SourceCost DESC LIMIT 1""")[0]
    shares = dict(q("""SELECT LegalEntity, SUM(OpcoShare * JurisdictionFactor) FROM ut_allocated_cost
                       WHERE ChargeKey = :k GROUP BY 1""", {"k": key}))
    drivers = dict(q("SELECT LegalEntity, DriverValue FROM ut_allocation_driver WHERE DriverName = 'Customers' AND PeriodStart = :p",
                     {"p": dt.datetime.fromisoformat(str(ps))}))
    total = sum(float(v) for v in drivers.values())
    for entity, v in drivers.items():
        assert abs(float(shares[entity]) - float(v) / total) < 1e-9


def test_utility_controls_report_seeded_issues():
    c = {x["id"]: x for x in eng.utility_controls()}
    assert c["C-08"]["result"] == "exceptions"      # Oracle Utilities Analytics add-on has no FERC map
    assert c["C-09"]["result"] == "exceptions"      # shared Claude API policy runs before the renewables policy
    assert c["C-10"]["result"] == "pass"
    assert c["C-11"]["result"] == "pass"


def test_every_utilities_check_flags_its_seeded_issue():
    out = client.get("/api/compliance/utilities", headers=h("regulatory_analyst")).json()
    items = {i["id"]: i for a in out["areas"] for i in a["items"]}
    assert set(items) == {"UT-01", "UT-03", "UT-04", "UT-06", "UT-07", "UT-08", "UT-09", "UT-11"}
    for uid, item in items.items():
        assert item["status"] == "attention" and item["flagged"] > 0, uid


def test_affiliate_wall_for_opco_finance():
    ohio = "Meridian Electric Ohio"
    rows = client.post("/api/usecases/UT-02/run", json={"params": {}}, headers=h("opco_finance")).json()["rows"]
    assert rows and {r[0] for r in rows} == {ohio}
    # shared, pre-allocation charges are invisible to an entity-scoped user
    explorer = client.post("/api/usecases/F-62/run", json={"params": {}}, headers=h("opco_finance"))
    assert explorer.status_code == 403 or not explorer.json()["rows"]
    assert client.get("/api/reconciliation", headers=h("opco_finance")).status_code == 403
    chain = client.get("/api/regulatory/allocation-chain", headers=h("opco_finance")).json()
    assert {r["LegalEntity"] for r in chain["opco"]} == {ohio}


def test_policy_workflow_separation_of_duties_and_reallocation():
    pending = [r for r in client.get("/api/regulatory/policies", headers=h("regulatory_analyst")).json()["policies"]
               if r["Status"] == "submitted"]
    assert pending, "the sample ships one policy awaiting approval"
    pid, ver = pending[0]["PolicyId"], pending[0]["Version"]
    # the submitter cannot approve
    r = client.post(f"/api/regulatory/policies/{pid}/decision", json={"action": "approve", "version": ver},
                    headers=h("affiliate_accounting"))
    assert r.status_code == 403  # affiliate accounting lacks rule.approve entirely
    before = dict(q("""SELECT LegalEntity, SUM(AllocatedCost) FROM ut_allocated_cost
                       WHERE OpcoPolicy = :p AND PeriodStart = TIMESTAMP '2026-10-01' GROUP BY 1""", {"p": pid}))
    r = client.post(f"/api/regulatory/policies/{pid}/decision", json={"action": "approve", "version": ver, "note": "test"},
                    headers=h("finance_controller"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["policy"]["Status"] == "approved" and "2026-10" in body["reallocated"]
    after = dict(q("""SELECT LegalEntity, SUM(AllocatedCost) FROM ut_allocated_cost
                      WHERE OpcoPolicy = :p AND OpcoPolicyVersion = :v AND PeriodStart = TIMESTAMP '2026-10-01' GROUP BY 1""",
                   {"p": pid, "v": ver}))
    assert after and after != before
    assert any(e["Target"] == f"policy:{pid}:v{ver}" and e["Action"] == "approve" for e in
               client.get("/api/evidence", headers=h("finance_controller")).json()["ledger"])
    # a controller who submits a change cannot approve their own submission
    assert client.post(f"/api/regulatory/policies/{pid}/decision", json={"action": "submit", "driver": "Employees"},
                       headers=h("finance_controller")).status_code == 403


def test_policy_submitter_cannot_approve_own_submission():
    from costiq.routes_regulatory import regs
    pol = regs.submit("demo.finance_controller@meridian.example", "AP-GRID", driver="Customers")
    r = client.post("/api/regulatory/policies/AP-GRID/decision", json={"action": "approve", "version": pol["Version"]},
                    headers=h("finance_controller"))
    assert r.status_code == 409


def test_rate_case_bridge_exhibit():
    out = client.get("/api/regulatory/rate-case/OH-26-1105-EL-AIR/bridge", headers=h("regulatory_analyst")).json()
    vendors = {l["Vendor"]: l for l in out["lines"]}
    assert vendors["ServiceNow"]["InvoiceTieOut"] == "Invoice exception"
    assert vendors["SAP"]["KnownAndMeasurable"] > 0
    csv = client.get("/api/regulatory/rate-case/OH-26-1105-EL-AIR/bridge.csv", headers=h("regulatory_analyst"))
    assert csv.status_code == 200 and "TestYearForecast" in csv.text and "InvoiceTieOut" in csv.text
    assert client.get("/api/regulatory/rate-case/OH-26-1105-EL-AIR/bridge.csv", headers=h("affiliate_accounting")).status_code == 403


def test_regulatory_assets_amortize_to_contract_end():
    out = client.get("/api/regulatory/regulatory-assets", headers=h("regulatory_analyst")).json()
    assert {r["Jurisdiction"] for r in out["by_jurisdiction"]} == {"OH"}
    assert out["schedule"][0]["Deferred"] > 0 and out["schedule"][1]["Amortization"] > 0
    sap = next(l for l in out["lines"] if l["Vendor"] == "SAP")
    assert sap["AmortizationMonths"] == 32  # Jul 2026 -> SAP contract end Mar 2029


def test_lineage_shows_utility_steps():
    key = q("SELECT ChargeKey FROM ut_allocated_cost WHERE OpcoPolicy = 'AP-CUSTOMER' LIMIT 1")[0][0]
    steps = [s["step"] for s in client.get(f"/api/lineage/{key}", headers=h("cfo")).json()["steps"]]
    i = steps.index("OpCo split")
    assert steps[i:i + 3] == ["OpCo split", "Jurisdiction", "FERC account and cost class"]


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
