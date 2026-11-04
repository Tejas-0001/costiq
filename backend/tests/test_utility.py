"""Utility tenant: the invoice ties, the FERC placement rules the first-cut account map follows, tag enforcement, the
capital path, the exception queue, Copilot seats and the policy workflow. Runs with COSTIQ_TENANT=utility:

    COSTIQ_TENANT=utility python -m pytest -q tests/test_utility.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.tenant("utility")  # the utility sample's accounts, invoices, registers and personas

AWS_SEP, AZ_SEP = "INV-AWS-aws-payer-7001-202609", "INV-MICROSOFT-ms-mca-7001-202609"


@pytest.fixture(scope="module")
def ctx():
    from costiq.context import db, eng, reg
    return db, eng, reg


@pytest.fixture(scope="module")
def client():
    from costiq.main import app
    return TestClient(app)


def q(db, sql, params=None):
    r = db.query(sql, params or {}, None, limit=None)
    return [dict(zip(r["columns"], row)) for row in r["rows"]]


def uc(ctx, uid, params=None):
    db, eng, reg = ctx
    u = reg.get(uid)
    return q(db, u["sql"], reg.resolve_params(u, params))


H = lambda p: {"X-CostIQ-Persona": p}  # noqa: E731


# ----------------------------------------------------------------------------------------------- invoices
def test_aws_september_invoice_ties_to_the_cent(ctx):
    db = ctx[0]
    lines = {r["d"].rsplit("-", 1)[1]: round(float(r["amt"]), 2)
             for r in q(db, "SELECT InvoiceDetailId AS d, BilledCost AS amt FROM InvoiceDetail WHERE InvoiceId = :i", {"i": AWS_SEP})}
    assert lines == {"Usage": 372480.55, "SavingsPlan": 96000.00, "Support": 41870.12, "Tax": 9214.67, "Credit": -12500.00}
    billed = q(db, "SELECT ROUND(SUM(BilledCost), 2) AS t FROM CostAndUsage WHERE InvoiceId = :i", {"i": AWS_SEP})[0]["t"]
    assert round(float(billed), 2) == round(sum(lines.values()), 2) == 507065.34
    issued = q(db, "SELECT MIN(InvoiceIssueDate) AS d FROM InvoiceDetail WHERE InvoiceId = :i", {"i": AWS_SEP})[0]["d"]
    assert str(issued)[:10] == "2026-10-03"


def test_azure_invoice_gap_is_tax_plus_a_late_charge(ctx):
    db = ctx[0]
    inv = q(db, "SELECT ROUND(SUM(BilledCost), 2) AS t FROM InvoiceDetail WHERE InvoiceId = :i", {"i": AZ_SEP})[0]["t"]
    rows = q(db, "SELECT ROUND(SUM(BilledCost), 2) AS t FROM CostAndUsage WHERE InvoiceId = :i", {"i": AZ_SEP})[0]["t"]
    assert (round(float(inv), 2), round(float(rows), 2)) == (605261.39, 591408.44)
    t = next(r for r in uc(ctx, "UT-18") if r["InvoiceId"] == AZ_SEP)
    assert (t["Labeled"], t["AwaitingRerun"], t["Unlabeled"]) == (11212.40, 2640.55, 0.0)
    assert t["Status"] == "Labeled; part awaiting the re-run"
    aws = next(r for r in uc(ctx, "UT-18") if r["InvoiceId"] == AWS_SEP)
    assert aws["Status"] == "Ties to the cent"
    itsm = next(r for r in uc(ctx, "UT-18") if r["Issuer"] == "ServiceNow")
    assert itsm["Difference"] == 14200.00 and itsm["Status"] == "Difference labeled"


def test_sla_credit_after_close_posts_in_the_next_period(ctx):
    r = q(ctx[0], """SELECT BillingPeriodStart, ChargePeriodStart, BilledCost, InvoiceId FROM CostAndUsage
                     WHERE ChargeClass = 'Correction' AND BillingAccountId = 'aws-payer-7001'""")
    assert len(r) == 1 and float(r[0]["BilledCost"]) == -3180.00
    assert str(r[0]["BillingPeriodStart"])[:7] == "2026-10" and str(r[0]["ChargePeriodStart"])[:7] == "2026-09"
    acct = q(ctx[0], """SELECT DISTINCT GlAccount FROM allocated_cost WHERE PeriodStart = TIMESTAMP '2026-10-01'
                        AND ChargeCategory = 'Credit' AND ServiceName = 'Amazon CloudFront'""")
    assert [a["GlAccount"] for a in acct] == ["588"]   # reversed to the account the original charge hit (R13)


# ----------------------------------------------------------------------------------------------- FERC placement
def test_allocated_ledger_ties_and_maps_every_dollar(ctx):
    db, eng, _ = ctx
    ctl = {c["id"]: c for c in eng.allocation_controls()}
    assert ctl["C-10"]["result"] == "pass" and ctl["C-08"]["result"] == "pass"
    assert ctl["C-09"]["result"] == "exceptions"   # the affiliate's account spread to regulated companies (ref_cloud_account)


def test_every_ledger_line_carries_its_rule_citation_and_flag(ctx, client):
    db = ctx[0]
    assert q(db, """SELECT COUNT(*) AS n FROM allocated_cost
                    WHERE RuleId IS NULL OR Citation IS NULL OR PolicyFlag IS NULL""")[0]["n"] == 0
    assert {r["PolicyFlag"] for r in q(db, "SELECT DISTINCT PolicyFlag FROM allocated_cost")} == {"Regulation", "POLICY"}
    total = q(db, "SELECT ROUND(SUM(AllocatedCost), 2) AS t FROM allocated_cost WHERE PeriodStart = TIMESTAMP '2026-09-01'")[0]["t"]
    assert round(sum(r["AllocatedCost"] for r in uc(ctx, "UT-22")), 2) == round(float(total), 2)
    rules = {(r["ChargeCategory"], r["RuleId"]) for r in q(db, """SELECT DISTINCT ChargeCategory, RuleId FROM allocated_cost
                                                                   WHERE ChargeCategory IN ('Tax', 'Credit')""")}
    assert rules == {("Tax", "R12"), ("Credit", "R13")}   # tax follows its line, credits reverse to the original account
    chain = client.get("/api/tax/allocation-chain", headers=H("finance_controller")).json()
    assert chain["rules"] and {b["BillingClass"] for b in chain["billing"]} >= {"457.1", "457.2"}


def test_capital_only_with_a_valid_work_order(ctx):
    db = ctx[0]
    cwip = q(db, "SELECT DISTINCT WorkOrderId, RuleId FROM allocated_cost WHERE GlAccount = '107'")
    assert cwip and all(r["RuleId"] == "R1" and r["WorkOrderId"] for r in cwip)
    sep = {r["WorkOrderId"] for r in q(db, """SELECT DISTINCT WorkOrderId FROM allocated_cost
                                              WHERE GlAccount = '107' AND PeriodStart = TIMESTAMP '2026-09-01'""")}
    assert sep == {"WO-D-2026-0207", "WO-G-2026-0101"}
    held = {r["Exception"]: r for r in q(db, """SELECT Exception, MIN(CostClass) AS c, MAX(CostClass) AS c2,
                                                       ROUND(SUM(AllocatedCost), 2) AS amt
                                                FROM allocated_cost WHERE PeriodStart = TIMESTAMP '2026-09-01'
                                                  AND Exception IN ('no_work_order', 'capitalizable_without_threshold_date')
                                                GROUP BY 1""")}
    assert float(held["no_work_order"]["amt"]) == 118000.00 and held["no_work_order"]["c"] == held["no_work_order"]["c2"] == "om"
    assert float(held["capitalizable_without_threshold_date"]["amt"]) == 85000.00


def test_first_cut_account_map_follows_the_rule_table(ctx):
    db = ctx[0]
    acct = lambda where: {r["GlAccount"] for r in q(db, f"SELECT DISTINCT GlAccount FROM allocated_cost WHERE {where}")}  # noqa: E731
    everything = acct("1 = 1")
    assert not everything & {"920", "408.1", "935.1", "561.4", "561.8", "303"}
    # tax and credits follow the line they belong to (R12, R13): never a tax or "outside services" bucket of their own
    assert acct("ChargeCategory = 'Tax' AND CostCenter = 'Transmission Planning'") == {"561.5"}
    assert acct("ChargeCategory = 'Credit' AND CostCenter = 'Distribution Operations' AND CostClass = 'om'") == {"588"}
    # IaaS usage is functional O&M by the cost center's function (R5-R8)
    assert acct("ServiceName = 'Amazon EC2' AND CostCenter = 'Transmission Planning'") == {"561.5"}
    assert acct("ServiceName = 'Azure Databricks'") == {"566"}
    assert acct("ServiceName = 'ExpressRoute' AND ChargeCategory = 'Usage' AND Regulated") == {"921"}
    # capital under an open work order with a threshold date -> 107; the integrator line without one stays out of it
    assert acct("Application = 'vegetation-analytics' AND ChargeCategory = 'Usage'") == {"107"}
    assert acct("ServiceName = 'ERP upgrade integration services' AND Regulated") == {"923"}
    # support plans as maintenance of software by function (R4: 569.2 T, 592.3 D, 935.2 otherwise); Copilot seats to A&G (R7)
    assert acct("ServiceName = 'AWS Support (Enterprise)' AND Regulated") == {"569.2", "592.3", "935.2"}
    support = {r["GlAccount"]: float(r["amt"]) for r in q(db, """SELECT GlAccount, SUM(AllocatedCost) AS amt FROM allocated_cost
                                                                  WHERE ServiceName = 'AWS Support (Enterprise)'
                                                                    AND PeriodStart = TIMESTAMP '2026-09-01' GROUP BY 1""")}
    assert all(support[a] > 0 for a in ("569.2", "592.3", "935.2")) and round(sum(support.values()), 2) == 41870.12
    assert acct("ServiceName = 'Microsoft 365 Copilot' AND Regulated") == {"921"}


def test_order_898_accounts_and_the_920_guard(ctx):
    db = ctx[0]
    acc = {(r["FercAccount"], r["Part"]): r for r in q(db, "SELECT * FROM ref_ferc_account")}
    for a in ("351.2", "363.2", "397.2"):
        assert str(acc[(a, "101")]["EffectiveFrom"])[:10] == "2025-01-01"
    assert str(acc[("303", "101")]["EffectiveTo"])[:10] == "2024-12-31"
    assert acc[("920", "101/367")]["LaborOnly"]
    guard = uc(ctx, "UT-23")
    assert {r["Source"]: r["Status"] for r in guard} == {"Allocated ledger": "Pass", "Account map": "Pass", "Proposed rule change": "Blocked"}
    assert all(r["Status"] == "Valid" or r["Status"].startswith("Non-utility") for r in uc(ctx, "UT-24"))


def test_rule_register_marks_policy_rules_for_sign_off(ctx):
    rules = {r["RuleId"]: r for r in q(ctx[0], "SELECT * FROM ref_ferc_rule")}
    assert len(rules) == 19 and {"R1", "R1a", "R1b", "R17"} <= set(rules)
    assert all(r["Status"] == ("awaiting sign-off" if r["Basis"] == "POLICY" else "approved") for r in rules.values())
    assert {k for k, r in rules.items() if r["Basis"] == "POLICY"} == {"R5", "R6", "R7", "R13", "R17"}
    placed = {r["RuleId"]: r["AllocatedCost"] for r in uc(ctx, "UT-22")}
    assert placed["R1"] > 0 and placed["R5"] > 0 and placed["R7"] > 0


# ----------------------------------------------------------------------------------------------- tags and exceptions
def test_tag_compliance_and_suspense(ctx):
    t = {r["TagKey"]: r for r in uc(ctx, "UT-15")}
    assert t["cost_center"]["UntaggedSpend"] == 79000.00 and t["cost_center"]["InvalidSpend"] == 10400.00
    assert t["opco"]["InvalidSpend"] == 2000.00
    assert t["work_order"]["UntaggedSpend"] == 118000.00
    sus = q(ctx[0], """SELECT ROUND(SUM(AllocatedCost), 2) AS s FROM allocated_cost
                       WHERE PeriodStart = TIMESTAMP '2026-09-01' AND Exception = 'no_opco' AND ChargeCategory = 'Usage'""")[0]["s"]
    # untagged usage (79,000) and the value that cannot be normalized ('T&D', 4,200) wait at the service company; the
    # normalized values ('Distrib', the retired cost center) follow their policies (ref_tag_value_map)
    assert float(sus) == 83200.00


def test_exception_queue(ctx):
    e = {r["ExceptionCode"]: r for r in uc(ctx, "UT-17")}
    assert e["no_work_order"]["Amount"] == 118000.00
    assert e["capitalizable_without_threshold_date"]["Amount"] == 85000.00
    assert e["cip_review_pending"]["Amount"] == 38000.00
    stamped = {r["Exception"]: float(r["amt"]) for r in q(ctx[0], """SELECT Exception, ROUND(SUM(AllocatedCost), 2) AS amt
                                                                     FROM allocated_cost WHERE PeriodStart = TIMESTAMP '2026-09-01'
                                                                       AND Exception IS NOT NULL GROUP BY 1""")}
    for code in ("no_opco", "no_work_order", "capitalizable_without_threshold_date", "multi_function_default_397.2",
                 "regulatory_asset_without_order"):
        assert e[code]["Amount"] == stamped[code] and stamped[code] > 0, code   # read from the ledger's stamped codes
    assert e["no_ferc_function"]["Amount"] == 79000.00
    assert e["labor_account_on_non_labor"]["Status"] == "Blocked by the 920 guard"
    assert e["unmapped_ferc_account"]["Status"] == "Clear"


def test_cross_subsidy_from_the_affiliate_account(ctx):
    r = uc(ctx, "UT-03")
    assert r and all(x["OwningEntity"] == "Competitive Energy Services" for x in r)
    assert 29000 < sum(x["CrossSubsidy"] for x in r) < 31000   # $31,000 of affiliate analytics, the affiliate's own share excluded


def test_capital_path_107_to_in_service_to_403(ctx):
    w = {(r["WorkOrderId"], r["Module"]): r for r in uc(ctx, "UT-16")}
    t = w[("WO-T-2026-0142", "Module 1")]
    assert t["Status"] == "In service: depreciating through 403" and t["TargetPlantAccount"] == "351.2"
    assert t["Cwip107Balance"] == 0 and t["TransferredToPlant"] > 96400 and t["MonthlyDepreciation403"] > 0
    assert w[("WO-D-2026-0207", "Module 1")]["Status"] == "Building in 107"
    assert w[("WO-D-2026-0211", "Module 1")]["HeldOutOf107"] == 85000.00
    assert w[("(none)", "-")]["HeldOutOf107"] == 118000.00


# ----------------------------------------------------------------------------------------------- service company billing
def test_service_company_billing_457(ctx, client):
    r = uc(ctx, "UT-02")
    classes = {x["BillingClass"] for x in r}
    assert {"457.1 Direct", "457.2 Indirect", "Held at the service company"} <= classes
    assert all(x["ReceivingAccount"] for x in r)
    regulated = {x["RecipientEntity"] for x in r if x["EntityCode"] not in ("SVC", "CES")}
    for ent in regulated:   # every operating and transmission company is billed both directly and by factor
        assert {x["BillingClass"] for x in r if x["RecipientEntity"] == ent} == {"457.1 Direct", "457.2 Indirect"}, ent
    share = q(ctx[0], """SELECT SUM(CASE WHEN BillingClass = '457.1' THEN AllocatedCost ELSE 0 END) / SUM(AllocatedCost) AS s
                         FROM allocated_cost WHERE PeriodStart = TIMESTAMP '2026-09-01' AND CostClass = 'om' AND Regulated
                           AND BillingClass IS NOT NULL""")[0]["s"]
    assert 0.30 <= float(share) <= 0.85
    sched = client.get("/api/tax/recharge-schedule", headers=H("affiliate_accounting")).json()
    assert sched["by_entity"] and "service company" in sched["formula"]


def test_transaction_listing_by_ferc_account(ctx):
    rows = uc(ctx, "UT-25")
    assert rows and {r["GlAccount"] for r in rows} == {"566"}
    total = q(ctx[0], """SELECT ROUND(SUM(AllocatedCost), 2) AS t FROM allocated_cost
                         WHERE GlAccount = '566' AND PeriodStart = TIMESTAMP '2026-09-01'""")[0]["t"]
    assert round(sum(r["AllocatedCost"] for r in rows), 2) == round(float(total), 2)
    assert all(r["InvoiceDetailId"] and r["Vendor"] and r["RuleId"] for r in rows)
    nonutil = q(ctx[0], "SELECT DISTINCT GlTitle, FunctionCode FROM allocated_cost WHERE GlAccount = 'NONUTIL'")
    assert nonutil == [{"GlTitle": "Non-utility affiliate (no FERC account)", "FunctionCode": "NONUTIL"}]


def test_policy_needs_a_second_approver_and_reallocates(ctx, client):
    pol = client.get("/api/tax/policies", headers=H("finance_controller")).json()["policies"]
    v2 = next(p for p in pol if p["PolicyId"] == "AP-NET" and p["Version"] == 2)
    assert v2["Status"] == "submitted" and v2["DriverName"] == "Network bytes by attachment (TB)"
    assert client.post("/api/tax/policies/AP-NET/decision", json={"action": "approve", "version": 2},
                       headers=H("affiliate_accounting")).status_code == 403   # cannot approve
    r = client.post("/api/tax/policies/AP-NET/decision", json={"action": "approve", "version": 2}, headers=H("finance_controller"))
    assert r.status_code == 200 and r.json()["reallocated"]
    db = ctx[0]
    drivers = {x["DriverName"] for x in q(db, """SELECT DISTINCT DriverName FROM allocated_cost
                                                  WHERE CostCenter = 'Network & Connectivity' AND PeriodStart = TIMESTAMP '2026-10-01'""")}
    assert drivers == {"Network bytes by attachment (TB)"}


def test_entity_wall_for_the_operating_company_finance_manager(client):
    me = client.get("/api/me", headers=H("opco_finance")).json()
    assert me["scope"] == ["North Electric Company"]
    rows = client.post("/api/usecases/UT-02/run", json={}, headers=H("opco_finance")).json()
    assert rows["rows"] and {r[0] for r in rows["rows"]} == {"North Electric Company"}


# ----------------------------------------------------------------------------------------------- Copilot and AI
def test_copilot_reharvest_list(ctx):
    r = uc(ctx, "UT-20")
    idle = [x for x in r if x["Status"] == "Idle"]
    assert len(idle) == 540 and sum(1 for x in idle if x["AssignedVia"] == "Group") == 380
    assert sum(1 for x in r if x["Status"] == "Grace period") == 45
    seats = q(ctx[0], """SELECT COUNT(*) AS n, SUM(CASE WHEN AssignedByGroupId IS NOT NULL THEN 1 ELSE 0 END) AS g
                         FROM ai_seat_assignment WHERE x_AIProduct = 'm365_copilot'""")[0]
    assert (seats["n"], seats["g"]) == (3000, 2100)
    amort = q(ctx[0], """SELECT ROUND(SUM(EffectiveCost), 2) AS e, ROUND(SUM(BilledCost), 2) AS b FROM CostAndUsage
                         WHERE x_AIProduct = 'm365_copilot' AND BillingPeriodStart = TIMESTAMP '2026-09-01'""")[0]
    assert (float(amort["e"]), float(amort["b"])) == (90000.00, 0.00)   # annual invoice in 165, amortized monthly


def test_token_check_counts_cache_tokens(ctx):
    r = next(x for x in uc(ctx, "UT-21") if x["Application"] == "interconnection-agent")
    assert (r["TotalCost"], r["MissedIfCacheIgnored"]) == (21000.00, 3360.00)
    assert r["CheckResult"].startswith("Fails")


# ----------------------------------------------------------------------------------------------- pages
@pytest.mark.parametrize("persona", ["finance_controller", "cfo", "regulatory_analyst", "affiliate_accounting",
                                     "transmission_finance", "opco_finance", "cip_compliance", "auditor"])
def test_pages_answer_for_utility_personas(client, persona):
    me = client.get("/api/me", headers=H(persona)).json()
    assert me["tenant"] == "utility" and me["copy"]["nav"]["items"]["tax"] == "Regulatory Accounting & Rates"
    for path in ("/api/command", "/api/summary", "/api/usecases", "/api/allocation", "/api/reconciliation", "/api/board",
                 "/api/risk", "/api/tax/allocation-chain", "/api/compliance/compliance"):
        r = client.get(path, headers=H(persona))
        assert r.status_code in (200, 403), (persona, path, r.status_code, r.text[:200])


def test_command_center_and_board_show_utility_sections(client):
    k = client.get("/api/command", headers=H("finance_controller")).json()
    assert k["headline"]["deduction_at_risk"] > 0 and k["headline"]["credit_unlock"] > 0
    b = client.get("/api/board", headers=H("cfo")).json()
    keys = [s["key"] for s in b["sections"]]
    assert keys[4:] == ["rate_case", "capital_om", "tdit", "cross_subsidy", "copilot"]
    assert all(s["status"] != "error" for s in b["sections"]), [s.get("gap") for s in b["sections"] if s["status"] == "error"]
    seats = next(s for s in b["sections"] if s["key"] == "copilot")
    assert {m["label"]: m["value"] for m in seats["metrics"]}["Idle beyond 30 days"] == 540
