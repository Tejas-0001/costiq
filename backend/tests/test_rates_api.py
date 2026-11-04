"""Regulatory Accounting & Rates page API (/api/tax, utility): service company billing by class, the FERC ledger and its
transaction listing, the rule register, the work-order capital path and the audit file's listing. September 2026, as the
Controller. Runs with COSTIQ_TENANT=utility:

    COSTIQ_TENANT=utility python -m pytest -q tests/test_rates_api.py
"""
import io, os, sys, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.tenant("utility")

H = {"X-CostIQ-Persona": "finance_controller"}
SEP = "2026-09"


@pytest.fixture(scope="module")
def client():
    from costiq.main import app
    return TestClient(app)


def get(client, path):
    r = client.get(path, headers=H)
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    return r.json()


def ledger_columns():
    from costiq.context import db, eng
    from costiq.regulatory import Regulatory
    return Regulatory(db, eng).ledger_columns()


def require_ledger_columns(*cols):
    have = ledger_columns()
    if not set(cols) <= have:
        pytest.skip(f"utility ledger has no {sorted(set(cols) - have)} column yet (rebuild sample/utility)")


# ----------------------------------------------------------------------------------------------- service company billing
def test_service_company_billing_by_class_and_factor(client):
    d = get(client, f"/api/tax/recharge-schedule?year=2026&period={SEP}")
    assert {"direct_share_pct", "factors", "totals", "periods", "closed_period"} <= set(d)
    assert d["closed_period"] == SEP
    t = d["totals"]
    billed = [e for e in d["by_entity"] if e["Total"]]
    assert billed and all({"Direct", "Indirect", "Total", "DirectSharePct"} <= set(e) for e in billed)
    for e in billed:
        assert round(e["Direct"] + e["Indirect"], 2) == pytest.approx(e["Total"], abs=0.02)
    assert round(sum(e["Total"] for e in billed), 2) == pytest.approx(t["billed"], abs=0.05)
    assert d["direct_share_pct"] == round(100 * t["direct"] / t["billed"], 1)
    lines = [l for l in d["lines"] if str(l.get("BillingClass") or "").startswith("457")]
    assert all(l["ReceivingAccount"] not in (None, "", "(unmapped)") for l in lines)
    assert round(sum(float(l["AmountRecharged"]) for l in lines), 2) == pytest.approx(t["ledger_billed"], abs=0.05)
    codes = {f["FactorCode"] for f in d["factors"]}
    assert {"F-EMP", "F-WKS", "F-CUS", "F-TPM", "F-GLT", "F-DIR", "F-EQ"} <= codes


# ----------------------------------------------------------------------------------------------- rule register
def test_rule_register_sign_off_dollars_and_blocked_920_change(client):
    d = get(client, f"/api/tax/rule-register?period={SEP}")
    assert d["available"] and d["period"] == SEP
    assert len(d["rules"]) == 19
    assert sorted(d["awaiting_signoff"]) == ["R13", "R17", "R5", "R6", "R7"]
    assert d["total"] == pytest.approx(d["ledger_total"], abs=0.1)
    assert all("SignedOffBy" in r and "SignedOffAt" in r for r in d["rules"])
    blocked = {b["ChangeId"]: b for b in d["blocked_changes"]}
    assert "RC-2026-014" in blocked


# ----------------------------------------------------------------------------------------------- work orders
def test_work_order_capital_path_and_held_items(client):
    d = get(client, "/api/tax/capital-path")
    assert d["available"]
    wo = next(w for w in d["work_orders"] if w["WorkOrderId"] == "WO-T-2026-0142")
    assert (wo["TargetPlantAccount"], str(wo["ReadyForUseDate"])[:10]) == ("351.2", "2026-09-01")
    assert round(float(wo["TransferredToPlant"]), 2) == 134121.16
    assert round(float(wo["MonthlyDepreciation403"]), 2) == 2235.35
    sep = next(s for s in d["schedule"] if s["WorkOrderId"] == "WO-T-2026-0142" and s["Month"] == SEP)
    assert (sep["TransferredToPlant"], sep["PlantAccount"], sep["Depreciation403"]) == (134121.16, "351.2", 2235.35)
    held = {h["ExceptionCode"]: h["Amount"] for h in d["held"]}
    assert held["no_work_order"] == 118000.00
    assert held["capitalizable_without_threshold_date"] == 85000.00
    one = get(client, "/api/tax/capital-path?work_order=WO-T-2026-0142")
    assert {w["WorkOrderId"] for w in one["work_orders"]} == {"WO-T-2026-0142"}


# ----------------------------------------------------------------------------------------------- FERC ledger and listing
def test_ferc_ledger_ties_to_the_period_total(client):
    require_ledger_columns("FunctionCode", "RuleId", "PolicyFlag")
    d = get(client, f"/api/tax/ledger?period={SEP}")
    assert d["available"] and d["period"] == SEP
    reg = get(client, f"/api/tax/rule-register?period={SEP}")
    assert d["total"] == pytest.approx(reg["ledger_total"], abs=0.1)
    assert round(sum(r["AllocatedCost"] for r in d["by_entity_function"]), 2) == pytest.approx(d["total"], abs=0.1)
    assert all(r["RuleId"] for r in d["accounts"])
    assert not any(r["GlAccount"] == "920" for r in d["accounts"])   # 920 is A&G salaries only


def test_account_566_listing_has_vendor_description_amount_invoice(client):
    require_ledger_columns("InvoiceDetailId", "ChargeDescription", "RuleId")
    d = get(client, f"/api/tax/ledger/listing?account=566&period={SEP}")
    if not d.get("available"):
        pytest.skip("no account_listing use case (UT-25) in the registry yet")
    assert d["account"] == "566" and d["rows"]
    for r in d["rows"][:50]:
        assert r["GlAccount"] == "566" and r["Vendor"] and r["Description"] and r["InvoiceId"] and r["InvoiceDetailId"]
    assert d["total"] == pytest.approx(sum(float(r["AllocatedCost"]) for r in d["rows"]), abs=0.05) or d["truncated"]
    led = get(client, f"/api/tax/ledger?period={SEP}")
    cents = max(0.05, 0.005 * d["count"])   # listing rows are rounded to the cent one by one
    assert d["total"] == pytest.approx(sum(a["AllocatedCost"] for a in led["accounts"] if a["GlAccount"] == "566"), abs=cents)
    r = client.get(f"/api/tax/ledger/listing.csv?account=566&period={SEP}", headers=H)
    assert r.status_code == 200 and "566-2026-09-transactions.csv" in r.headers["content-disposition"]
    assert r.text.startswith("# Transactions by FERC account 566")
    from costiq.context import ledger
    entries = ledger.entries()
    assert any(e["Target"] == f"listing:566:{SEP}" and e["Action"] == "export" for e in entries)


def test_listing_export_needs_exhibit_export(client):
    require_ledger_columns("RuleId")
    r = client.get(f"/api/tax/ledger/listing.csv?account=566&period={SEP}", headers={"X-CostIQ-Persona": "opco_finance"})
    assert r.status_code == 403


def test_audit_file_carries_the_listing_and_the_rule_register(client):
    require_ledger_columns("RuleId", "InvoiceDetailId")
    r = client.get("/api/tax/filing/SA-26-0418-EL/audit-file.zip", headers=H)
    assert r.status_code == 200
    names = {n.split("/", 1)[1] for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist()}
    assert {"listing_by_account.csv", "rule_register.csv"} <= names


def test_chain_carries_rules_and_billing(client):
    require_ledger_columns("RuleId", "BillingClass")
    d = get(client, f"/api/tax/allocation-chain?period={SEP}")
    if "rules" not in d:
        pytest.skip("allocation chain has no rule breakdown yet")
    assert round(sum(r["AllocatedCost"] for r in d["rules"]), 2) == pytest.approx(
        get(client, f"/api/tax/rule-register?period={SEP}")["ledger_total"], abs=0.1)
