"""Bill unbundling and tag enforcement: the invoice-to-ledger drill, labeled reconciling items, tag compliance with
suspense, the pooling-rule maker-checker, C-09 through account ownership, and the FERC rule step in the lineage.

    COSTIQ_TENANT=utility python -m pytest -q tests/test_ledger_drill.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from fastapi.testclient import TestClient

H = lambda p: {"X-CostIQ-Persona": p}  # noqa: E731
FC = H("finance_controller")
AWS_USAGE = "IND-AWS-aws-payer-7001-202609-Usage"
AZ_TAX, AZ_USAGE = "IND-MICROSOFT-ms-mca-7001-202609-Tax", "IND-MICROSOFT-ms-mca-7001-202609-Usage"
utility = pytest.mark.tenant("utility")


@pytest.fixture(scope="module")
def client():
    from costiq.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def eng():
    from costiq.context import eng
    return eng


def _needs_rule_columns(eng):
    if "RuleId" not in eng.ledger_columns():
        pytest.skip("the ledger has no RuleId column yet (utility sample before the FERC ledger rebuild)")


# ----------------------------------------------------------------------------------------------- invoice to ledger
@utility
def test_aws_usage_line_unbundles_into_the_ledger(client, eng):
    r = client.get(f"/api/reconciliation/{AWS_USAGE}/ledger", headers=FC)
    assert r.status_code == 200
    d = r.json()
    t = d["totals"]
    assert t["invoiced"] == 372480.55 and t["billed_rows"] == 372480.55 and t["difference"] == 0
    assert abs(t["allocated"] - t["effective_rows"]) <= 0.01        # C-10 for this invoice line
    assert abs(sum(e["AllocatedCost"] for e in d["by_entity"]) - t["allocated"]) <= 0.05
    assert d["charges"] and len(d["charges"]) <= 25 and d["formula"]
    assert all(c["ChargeKey"] for c in d["charges"])


@utility
def test_ledger_groups_carry_rule_citation_and_function(client, eng):
    _needs_rule_columns(eng)
    d = client.get(f"/api/reconciliation/{AWS_USAGE}/ledger", headers=FC).json()
    assert all(g.get("RuleId") and g.get("Citation") for g in d["groups"])
    assert {"T", "D", "AG"} <= {g.get("FunctionCode") for g in d["groups"]}


@utility
def test_azure_tax_line_is_a_labeled_residual(client):
    d = client.get(f"/api/reconciliation/{AZ_TAX}/ledger", headers=FC).json()
    assert d["charges"] == [] and d["totals"]["charge_lines"] == 0
    tax = [x for x in d["residuals"] if x["Item"] == "Tax not in the cost data"]
    assert len(tax) == 1 and tax[0]["Amount"] == 11212.40 and tax[0]["Explained"] is True


@utility
def test_invoice_ledger_needs_enterprise_scope_and_a_known_line(client):
    assert client.get("/api/reconciliation/NO-SUCH-LINE/ledger", headers=FC).status_code == 404
    assert client.get(f"/api/reconciliation/{AWS_USAGE}/ledger", headers=H("opco_finance")).status_code == 403


@utility
def test_reconciliation_lines_carry_register_labels(client):
    lines = {l["InvoiceDetailId"]: l for l in client.get("/api/reconciliation", headers=FC).json()["lines"]}
    use, tax = lines[AZ_USAGE], lines[AZ_TAX]
    assert use["Status"] == "awaiting" and use["Label"].lower().startswith("late charge") and use["Explained"] is False
    assert tax["Status"] == "labeled" and tax["Label"] == "Tax not in the cost data"
    assert tax["Reason"] != "Tax rate differs from expected"
    itsm = next(l for l in lines.values() if l["Period"] == "2026-09" and l["Issuer"].lower().startswith("servicenow"))
    assert itsm["Status"] == "labeled" and round(itsm["Difference"], 2) == 14200.00


# ----------------------------------------------------------------------------------------------- tag compliance
@utility
def test_tag_compliance_for_september(client):
    d = client.get("/api/tag-compliance?month=2026-09", headers=FC).json()
    assert d["available"] is True and d["period"]["start"] == "2026-09-01" and d["period"]["end"] == "2026-09-30"
    cc = next(k for k in d["keys"] if k["TagKey"] == "cost_center")
    assert (cc["UntaggedSpend"], cc["InvalidSpend"]) == (79000.00, 10400.00)
    opco = next(k for k in d["keys"] if k["TagKey"] == "opco")
    assert opco["InvalidSpend"] == 2000.00
    s = d["suspense"]
    assert s["amount"] == 79000.00 and s["share_pct"] == 8.4 and s["usage"] == 944888.62
    assert s["owner"] and s["oldest_days"] > 0 and {a["Account"] for a in s["accounts"]} >= {"aws-sandbox-shared"}
    assert d["cannot_be_tagged"]["amount"] > 0 and d["cannot_be_tagged"]["by_category"]
    assert d["violations"] and {v["Issue"] for v in d["violations"]} <= {"missing", "invalid"}
    ytd = client.get("/api/tag-compliance?range=ytd", headers=FC).json()
    assert ytd["suspense"]["amount"] > s["amount"] and ytd["cannot_be_tagged"]["amount"] > d["cannot_be_tagged"]["amount"]
    assert client.get("/api/tag-compliance?month=2026-13", headers=FC).status_code == 400


@pytest.mark.tenant("pharma")
def test_pharma_has_no_tag_policy(client):
    assert client.get("/api/tag-compliance?month=2026-09", headers=H("cfo")).json() == {"available": False}
    assert not any("Label" in l for l in client.get("/api/reconciliation", headers=H("cfo")).json()["lines"])


# ----------------------------------------------------------------------------------------------- maker-checker
@utility
def test_pooling_rule_maker_checker_restates_the_suspense(client, eng):
    from costiq.allocation import Allocator
    from costiq.context import db
    from costiq.prudence import PrudenceEngine
    tags = client.get("/api/tag-compliance?month=2026-09", headers=FC).json()
    acct = next(a for a in tags["suspense"]["accounts"] if a["Account"] == "aws-sandbox-shared")
    center = next(c for c in tags["cost_centers"] if c != "Unallocated")
    sub = client.post("/api/allocation/rule-proposals", headers=H("affiliate_accounting"),
                      json={"MatchColumn": "SubAccountId", "MatchValue": acct["SubAccountId"], "CostCenter": center})
    assert sub.status_code == 200, sub.text
    pid = sub.json()["proposal"]["ProposalId"]
    assert sub.json()["proposal"]["Status"] == "submitted" and sub.json()["proposal"]["EffectiveFrom"] == "2026-07-01"
    # the submitter cannot approve their own rule (and could not anyway: no rule.approve)
    assert client.post(f"/api/allocation/rule-proposals/{pid}/decision", json={"action": "approve"},
                       headers=H("affiliate_accounting")).status_code == 409
    ap = client.post(f"/api/allocation/rule-proposals/{pid}/decision", json={"action": "approve"}, headers=FC)
    assert ap.status_code == 200, ap.text   # a failing allocator answers 500 before touching the ledger rows
    try:
        body = ap.json()
        assert body["proposal"]["Status"] == "approved" and body["rule"]["CostCenter"] == center
        assert {"2026-07", "2026-08", "2026-09"} <= set(body["reallocated"])
        after = client.get("/api/tag-compliance?month=2026-09", headers=FC).json()["suspense"]
        assert acct["SubAccountId"] not in {a["SubAccountId"] for a in after["accounts"]}
        assert round(after["amount"], 2) == round(tags["suspense"]["amount"] - acct["Amount"], 2)
        ev = client.get("/api/evidence", headers=FC).json()
        assert next(c for c in ev["controls"] if c["id"] == "C-10")["result"] == "pass"
        acts = [e["Action"] for e in ev["ledger"] if e["Target"] == f"pooling_rule:{pid}"]
        assert acts == ["submit", "approve"]
        assert next(p for p in client.get("/api/allocation/rule-proposals", headers=FC).json()
                    if p["ProposalId"] == pid)["RuleId"] == f"R-{pid}"
    finally:  # put the allocation back for the tests that follow
        db.query("DELETE FROM allocation_rule WHERE RuleId = :r", {"r": f"R-{pid}"}, limit=None)
        Allocator(db, eng).run()
        PrudenceEngine(db, eng).run()


@utility
def test_pooling_rule_proposal_validation(client):
    bad = client.post("/api/allocation/rule-proposals", headers=H("affiliate_accounting"),
                      json={"MatchColumn": "ResourceId", "MatchValue": "x", "CostCenter": "y"})
    assert bad.status_code == 400
    assert client.post("/api/allocation/rule-proposals", headers=H("opco_finance"),
                       json={"MatchColumn": "SubAccountId", "MatchValue": "x", "CostCenter": "y"}).status_code == 403


# ----------------------------------------------------------------------------------------------- controls and lineage
@utility
def test_c09_flags_regulated_lines_from_affiliate_accounts(client):
    c09 = next(c for c in client.get("/api/evidence", headers=FC).json()["controls"] if c["id"] == "C-09")
    assert c09["result"] == "exceptions" and "$" in c09["evidence"] and "$0.00" not in c09["evidence"]


@utility
def test_lineage_names_the_ferc_rule(client, eng):
    _needs_rule_columns(eng)
    lines = client.get("/api/explorer?month=2026-09", headers=FC).json()["lines"][:5]
    steps = [s for l in lines for s in client.get(f"/api/lineage/{l['ChargeKey']}", headers=FC).json()["steps"]]
    acct = [s for s in steps if s["step"] == "FERC account, rule and citation"]
    assert acct and all(" · R" in s["detail"] for s in acct)
