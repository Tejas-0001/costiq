"""Add Meridian Energy's utility workloads and reference registers to a CostIQ sample DuckDB file.

  python data/utility.py sample/costiq_sample.duckdb

Idempotent: removes what it added before inserting again. Everything is synthetic: Meridian Energy is a fictional
utility holding company; entities, dockets, vendors' terms and people are invented, and all figures are illustrative.

Adds
  * Enterprise SaaS billed by invoice file drop (SAP S/4HANA, Salesforce, ServiceNow, Oracle Utilities CIS), including
    S/4HANA implementation services (capitalizable) and a CIS analytics line with no FERC mapping.
  * A grid AWS account: ADMS analytics, AMI meter data management, outage management (surge during storm ST-2026-09A),
    customer portal, a grid-planning GPU endpoint left idle after 20 Sep, CEII grid models, non-production
    environments running 24x7, and non-regulated renewables siting analytics.
  * Invoices for all of it (ServiceNow September is over-billed: a reconciliation exception) and refreshed budgets.
  * ut_* registers (schema/costiq_utility_schema.sql) with seeded issues so every Utilities check has findings.
"""
from __future__ import annotations
import datetime as dt, hashlib, json, os, re, sys
import duckdb

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
START, AS_OF = dt.date(2026, 7, 1), dt.date(2026, 10, 22)
MONTHS = [dt.date(2026, m, 1) for m in (7, 8, 9, 10)]
GRID_ACCOUNT = ("aws-grid-8008", "Meridian Grid AWS")
GRID_SUB = ("888800008008", "meridian-grid-platform")
GRID_SOURCE, SAAS_SOURCE = "aws_grid_focus_export", "saas_invoice_filedrop"
STORM = ("ST-2026-09A", dt.date(2026, 9, 10), dt.date(2026, 9, 16))
GPU_IDLE_FROM = dt.date(2026, 9, 20)


def ts(d):
    return dt.datetime(d.year, d.month, d.day)


def next_month(d):
    return dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def wobble(day, key, spread=0.15):
    h = int(hashlib.md5(f"{day}{key}".encode()).hexdigest()[:6], 16) / 0xFFFFFF
    return 1 + spread * (2 * h - 1)


# application, cost center, region, resource, service, category, unit, daily $, extra tags
GRID_LINES = [
    ("adms-analytics", "Grid Operations", "us-east-2", "adms-analytics-emr", "Amazon EMR", "Analytics", "Hours", 820, {"cip_scope": "medium", "data_class": "bcsi"}),
    ("adms-analytics", "Grid Operations", "us-east-2", "adms-lake", "Amazon S3", "Storage", "GB-Month", 240, {"cip_scope": "medium", "data_class": "bcsi"}),
    ("ami-mdm", "Customer Operations", "us-east-2", "mdm-aurora", "Amazon Aurora", "Databases", "Hours", 960, {}),
    ("ami-mdm", "Customer Operations", "us-east-2", "ami-ingest-kinesis", "Amazon Kinesis", "Analytics", "Hours", 410, {}),
    ("outage-management", "Grid Operations", "us-east-2", "oms-app-asg", "Amazon EC2", "Compute", "Hours", 690, {"cip_scope": "medium", "data_class": "bcsi"}),
    ("customer-portal", "Customer Operations", "us-east-2", "portal-eks", "Amazon EKS", "Compute", "Hours", 380, {}),
    ("grid-planning-gpu", "Transmission Planning", "us-east-2", "gridplan-gpu-endpoint", "Amazon SageMaker", "AI and Machine Learning", "Hours", 1850, {"ai_component": "gpu_hosting"}),
    ("ceii-grid-models", "Transmission Planning", "us-east-2", "ceii-models-s3", "Amazon S3", "Storage", "GB-Month", 95, {"data_class": "ceii", "cip_scope": "low"}),
    ("grid-dev", "Grid Operations", "us-east-2", "adms-dev-ec2", "Amazon EC2", "Compute", "Hours", 310, {"environment": "nonprod"}),
    ("oms-test", "Grid Operations", "us-east-2", "oms-test-ec2", "Amazon EC2", "Compute", "Hours", 160, {"environment": "nonprod"}),
    ("renewables-siting", "Renewables Development", "us-west-2", "siting-gis-ec2", "Amazon EC2", "Compute", "Hours", 540, {}),
]
STORM_SURGE = {"outage-management": 3.0, "customer-portal": 2.5}
# Optimizations already implemented (the Optimization Center verifies them from these run-rate changes)
RIGHTSIZED = {"siting-gis-ec2": (dt.date(2026, 9, 1), 0.55), "ami-ingest-kinesis": (dt.date(2026, 9, 15), 0.70)}

# vendor, account, service name, category, cost center, application, monthly $, charge frequency, extra tags
SAAS_LINES = [
    ("SAP", "sap-rise-9101", "SAP S/4HANA Cloud (RISE)", "Business Applications", "Corporate IT", "erp-s4hana", 310000, "Recurring", {}),
    ("SAP", "sap-rise-9101", "SAP S/4HANA implementation services", "Business Applications", "Corporate IT", "erp-s4hana", 120000, "One-Time", {"implementation": "true"}),
    ("Salesforce", "sfdc-9102", "Salesforce Service Cloud", "Business Applications", "Customer Operations", "crm-service", 95000, "Recurring", {}),
    ("ServiceNow", "snow-9103", "ServiceNow ITSM and ITOM", "Business Applications", "Corporate IT", "itsm", 70000, "Recurring", {"cip_scope": "low"}),
    ("Oracle", "orcl-9104", "Oracle Utilities Customer Cloud Service", "Business Applications", "Customer Operations", "cis-billing", 160000, "Recurring", {}),
    ("Oracle", "orcl-9104", "Oracle Utilities Analytics add-on", "Other", "Customer Operations", "cis-analytics", 18000, "Recurring", {}),
]
SERVICENOW_SEP_OVERBILL = 14200.0


def grid_rows():
    out, day = [], START
    while day <= AS_OF:
        storm = STORM[1] <= day <= STORM[2]
        for app, cc, region, res, svc, cat, unit, daily, extra in GRID_LINES:
            cost = daily * wobble(day, res)
            if res in RIGHTSIZED and day >= RIGHTSIZED[res][0]:
                cost *= RIGHTSIZED[res][1]
            tags = {"cost_center": cc, "application": app, "environment": "prod", **extra}
            if storm and app in STORM_SURGE:
                cost *= STORM_SURGE[app]
                tags["storm_event"] = STORM[0]
            cost = round(cost, 4)
            r = dict(ChargeDescription=f"{svc} {res}", RegionId=region, RegionName=region,
                     ResourceId=f"arn:aws:{svc.split()[-1].lower()}:{region}:{GRID_SUB[0]}:{res}", ResourceName=res,
                     ResourceType=svc, ServiceCategory=cat, ServiceName=svc, ServiceSubcategory=svc,
                     SkuId=f"{svc.replace(' ', '')}-{unit}", SkuMeter=unit, SkuPriceId=f"{res}-ondemand",
                     ConsumedQuantity=24 if unit == "Hours" else round(cost * 20, 2), ConsumedUnit=unit,
                     PricingUnit=unit, PricingQuantity=24 if unit == "Hours" else round(cost * 20, 2),
                     Tags=json.dumps(tags))
            if res == "gridplan-gpu-endpoint":  # real-time endpoint: busy on weekdays, then left running idle
                busy = day < GPU_IDLE_FROM and day.weekday() < 5
                r.update(x_AIProduct="SageMaker endpoint", x_RequestCount=int(2400 * wobble(day, "rq")) if busy else 0)
            out.append(base(day, cost, GRID_ACCOUNT, "AWS", GRID_SUB, GRID_SOURCE, **r))
        day += dt.timedelta(days=1)
    return out


def saas_rows():
    out = []
    for m in MONTHS:
        for vendor, acct, svc, cat, cc, app, monthly, freq, extra in SAAS_LINES:
            tags = {"cost_center": cc, "application": app, "environment": "prod", **extra}
            amount = round(monthly * wobble(m, svc, 0.02), 2)
            out.append(base(m, amount, (acct, f"Meridian {vendor}"), vendor, (acct, f"{vendor} subscription"), SAAS_SOURCE,
                            period_end=next_month(m), ChargeCategory="Purchase", ChargeFrequency=freq,
                            ChargeDescription=f"{svc} ({m:%B %Y})", ServiceCategory=cat, ServiceName=svc,
                            ServiceSubcategory="Business Applications" if cat != "Other" else "Other",
                            ResourceId=f"{acct}:{app}", ResourceName=app, ResourceType="SaaS subscription",
                            SkuId=f"{acct}-{app}", SkuMeter="Subscription", SkuPriceId=f"{acct}-{app}-contract",
                            ConsumedQuantity=1, ConsumedUnit="Subscription", PricingQuantity=1,
                            PricingUnit="Subscription", Tags=json.dumps(tags),
                            InvoiceDetailId=f"IND-{acct}-{m:%Y%m}-{app}"))
    return out


def base(day, cost, account, issuer, sub, source, period_end=None, **kw):
    r = {
        "BillingAccountId": account[0], "BillingAccountName": account[1], "BillingAccountType": "Billing Account",
        "BillingCurrency": "USD", "BillingPeriodStart": ts(dt.date(day.year, day.month, 1)),
        "BillingPeriodEnd": ts(next_month(day)), "ChargeCategory": "Usage", "ChargeFrequency": "Usage-Based",
        "ChargePeriodStart": ts(day), "ChargePeriodEnd": ts(period_end or day + dt.timedelta(days=1)),
        "BilledCost": cost, "EffectiveCost": cost, "ListCost": round(cost * 1.04, 4), "ContractedCost": cost,
        "PricingCategory": "Standard", "PricingCurrency": "USD", "PricingCurrencyEffectiveCost": cost,
        "HostProviderName": issuer, "ServiceProviderName": issuer, "InvoiceIssuerName": issuer,
        "SubAccountId": sub[0], "SubAccountName": sub[1], "SubAccountType": "Account",
        "x_CostSource": "invoiced", "x_SourceSystem": source,
        "InvoiceId": f"INV-{account[0]}-{day:%Y%m}", "InvoiceDetailId": f"IND-{account[0]}-{day:%Y%m}-Usage",
    }
    r.update(kw)
    return r


D, T = dt.date, dt.datetime
ENTITIES = [  # entity, parent, type, regulated, states
    ("Meridian Energy Services Co.", "Meridian Energy", "service_company", True, ""),
    ("Meridian Electric Ohio", "Meridian Energy", "opco", True, "OH,KY"),
    ("Meridian Electric Indiana", "Meridian Energy", "opco", True, "IN"),
    ("Meridian Electric Texas", "Meridian Energy", "opco", True, "TX"),
    ("Meridian Transmission", "Meridian Energy", "transmission", True, "FERC"),
    ("Meridian Renewables", "Meridian Energy", "affiliate", False, ""),
]
OPCOS = ["Meridian Electric Ohio", "Meridian Electric Indiana", "Meridian Electric Texas", "Meridian Transmission", "Meridian Renewables"]
DRIVERS = {  # per entity, in OPCOS order
    "Customers": [1450000, 920000, 1180000, 0, 0],
    "AmiMeters": [1410000, 880000, 1150000, 0, 0],
    "Employees": [4100, 2600, 3300, 900, 350],
    "Workstations": [4500, 2850, 3600, 990, 380],
    "PoleMiles": [9800, 6400, 8900, 14200, 0],
}
POLICY_APPROVER, POLICY_SUBMITTER = "demo.finance_controller@meridian.example", "demo.affiliate_accounting@meridian.example"
POLICIES = [  # id, name, column, value, driver, fixed entity, priority, status
    ("AP-AI-PLATFORM", "Shared Claude API platform by headcount", "ServiceName", "Claude API", "Employees", None, 5, "approved"),
    ("AP-RENEWABLES", "Renewables Development work to Meridian Renewables", "CostCenter", "Renewables Development", None, "Meridian Renewables", 10, "approved"),
    ("AP-TPLAN", "Transmission Planning to Meridian Transmission", "CostCenter", "Transmission Planning", None, "Meridian Transmission", 20, "approved"),
    ("AP-CUSTOMER", "Customer Operations by retail customers", "CostCenter", "Customer Operations", "Customers", None, 20, "approved"),
    ("AP-GRID", "Grid Operations by distribution and transmission pole miles", "CostCenter", "Grid Operations", "PoleMiles", None, 20, "approved"),
    ("AP-ANALYTICS", "Data & Analytics by workstations", "CostCenter", "Data & Analytics", "Workstations", None, 30, "approved"),
    ("AP-DEFAULT", "Everything else by headcount", "*", "*", "Employees", None, 99, "approved"),
]
PENDING_POLICY = ("AP-ANALYTICS", 2, "Data & Analytics by AMI meters (meter-data platform is the main consumer)",
                  "CostCenter", "Data & Analytics", "AmiMeters", None, 30)
FACTORS = [("Meridian Electric Ohio", "OH", 0.93), ("Meridian Electric Ohio", "KY", 0.07),
           ("Meridian Electric Indiana", "IN", 1.0), ("Meridian Electric Texas", "TX", 1.0),
           ("Meridian Transmission", "FERC", 1.0), ("Meridian Renewables", "Non-jurisdictional", 1.0),
           ("Meridian Energy Services Co.", "Unassigned", 1.0)]
FERC_MAP = [  # priority, column, value, entity type, function, account, title
    (1, "ChargeCategory", "Tax", None, "Taxes", "408.1", "Taxes other than income taxes"),
    (2, "ChargeCategory", "Credit", None, "Administrative and General", "923", "Outside services employed"),
    (10, "ServiceCategory", "Business Applications", "transmission", "Transmission", "569.2", "Maintenance of computer software (transmission)"),
    (10, "ServiceCategory", "Compute", "transmission", "Transmission", "569.1", "Maintenance of computer hardware (transmission)"),
    (10, "ServiceCategory", "Storage", "transmission", "Transmission", "569.1", "Maintenance of computer hardware (transmission)"),
    (10, "ServiceCategory", "Analytics", "transmission", "Transmission", "569.1", "Maintenance of computer hardware (transmission)"),
    (20, "ServiceCategory", "Business Applications", None, "Administrative and General", "935.2", "Maintenance of computer software"),
    (20, "ServiceCategory", "Management and Governance", None, "Administrative and General", "935.2", "Maintenance of computer software"),
    (20, "ServiceCategory", "Security", None, "Administrative and General", "935.2", "Maintenance of computer software"),
    (20, "ServiceCategory", "AI and Machine Learning", None, "Administrative and General", "923", "Outside services employed"),
    (30, "ServiceCategory", "Compute", None, "Administrative and General", "935.1", "Maintenance of computer hardware"),
    (30, "ServiceCategory", "Storage", None, "Administrative and General", "935.1", "Maintenance of computer hardware"),
    (30, "ServiceCategory", "Networking", None, "Administrative and General", "935.1", "Maintenance of computer hardware"),
    (30, "ServiceCategory", "Databases", None, "Administrative and General", "935.1", "Maintenance of computer hardware"),
    (30, "ServiceCategory", "Analytics", None, "Administrative and General", "935.1", "Maintenance of computer hardware"),
]
JURISDICTION_POLICY = [
    ("OH", "allowed", True, 62, "PUCO deferral order (illustrative), modeled on Idaho PUC Order No. 34707"),
    ("KY", "untested", False, 62, "No order on file"),
    ("IN", "rejected", False, 62, "Request denied 2025 (illustrative), cf. Illinois ICC 2020"),
    ("TX", "untested", False, 62, "No order on file"),
    ("FERC", "not_applicable", False, 62, "Formula rate: implementation costs follow AI20-1-000"),
    ("Non-jurisdictional", "not_applicable", False, 62, "Non-regulated affiliate"),
    ("Unassigned", "not_applicable", False, 62, "Service company before allocation"),
]
RATE_CASES = [  # docket, jurisdiction, entity, base start, base end, test start, test end, filing, status, roe, equity, debt
    ("OH-26-1105-EL-AIR", "OH", "Meridian Electric Ohio", T(2026, 7, 1), T(2026, 10, 1), T(2027, 1, 1), T(2028, 1, 1),
     T(2026, 12, 15), "In preparation", 9.70, 0.52, 4.60),
    ("TX-PUC-57210", "TX", "Meridian Electric Texas", T(2026, 7, 1), T(2026, 10, 1), T(2027, 4, 1), T(2028, 4, 1),
     T(2027, 3, 31), "Planned", 9.65, 0.425, 4.85),
    ("ER27-118-000", "FERC", "Meridian Transmission", T(2026, 7, 1), T(2026, 10, 1), T(2027, 1, 1), T(2028, 1, 1),
     T(2026, 12, 1), "In preparation", 10.50, 0.55, 4.40),
]
TAX_RATE = 0.21
CLAUSES_FULL = {"vulnerability_disclosure": True, "incident_notification": True, "remote_access_controls": True, "software_integrity": True}
VENDORS = [  # vendor, cip scoped, clauses, contract end, renewal, escalator %, annual value
    ("SAP", False, CLAUSES_FULL, T(2029, 3, 31), T(2027, 4, 1), 7.0, 3720000),
    ("Salesforce", False, CLAUSES_FULL, T(2027, 1, 31), T(2027, 2, 1), 9.0, 1140000),
    ("ServiceNow", True, {**CLAUSES_FULL, "incident_notification": False}, T(2026, 12, 31), T(2027, 1, 1), 5.0, 840000),
    ("Oracle", False, CLAUSES_FULL, T(2028, 6, 30), T(2028, 7, 1), 3.0, 2136000),
    ("AWS", True, CLAUSES_FULL, T(2028, 1, 1), T(2028, 1, 1), 0.0, 3400000),
    ("Microsoft", True, {**CLAUSES_FULL, "software_integrity": False}, T(2028, 7, 1), T(2027, 7, 1), 4.0, 1600000),
    ("Anthropic", False, CLAUSES_FULL, T(2027, 1, 1), T(2027, 1, 1), 0.0, 150000),
    ("Snowflake", False, CLAUSES_FULL, T(2027, 9, 30), T(2027, 10, 1), 6.0, 240000),
]
CIP_SYSTEMS = [  # application, system, impact, holds BCSI, key ownership, owner
    ("outage-management", "Outage management system (cloud tier)", "Medium", True, "provider", "Grid Operations IT"),
    ("adms-analytics", "ADMS analytics replica", "Medium", True, "customer", "Grid Operations IT"),
    ("ceii-grid-models", "Transmission planning models (CEII)", "Low", True, "customer", "Transmission Planning"),
    ("itsm", "ServiceNow change management for CIP assets", "Low", True, "provider", "Corporate IT"),
    ("grid-planning-gpu", "Grid-planning model endpoint", "None", False, "customer", "Transmission Planning"),
]
AI_USE = [
    ("grid-model-assistant", "Answer planners' questions over transmission models", True, "Approved", D(2026, 5, 1)),
    ("grid-planning-gpu", "Load-flow surrogate model for interconnection studies", True, "Approved", D(2026, 6, 15)),
    ("interconnection-study-agent", "Agent that prepares interconnection study inputs", True, "Pending", None),
    ("outage-summary-bot", "Summarize outage tickets for control-room shift handover", True, "Pending", None),
    ("customer-outage-assistant", "Answer customers' outage questions from approved content", False, "Approved", D(2026, 1, 10)),
    ("site-research", "Research candidate renewable sites", False, "Approved", D(2026, 2, 3)),
]


def reference_rows():
    rows = {"ut_legal_entity": ENTITIES,
            "ut_cost_center": [(cc, "Meridian Renewables" if cc == "Renewables Development" else "Meridian Energy Services Co.",
                                cc != "Renewables Development")
                               for cc in ("Grid Operations", "Customer Operations", "Transmission Planning", "Data & Analytics",
                                          "Corporate IT", "Finance", "HR", "Legal", "Shared Services", "Renewables Development",
                                          "Unallocated")],
            "ut_allocation_driver": [(name, ts(m), ent, round(vals[i] * (1 + 0.002 * k), 0))
                                     for name, vals in DRIVERS.items() for k, m in enumerate(MONTHS)
                                     for i, ent in enumerate(OPCOS) if vals[i]],
            "ut_jurisdiction_factor": [(e, j, ts(m), f) for m in MONTHS for e, j, f in FACTORS],
            "ut_ferc_account_map": FERC_MAP,
            "ut_jurisdiction_policy": JURISDICTION_POLICY,
            "ut_cip_system": CIP_SYSTEMS,
            "ut_storm_event": [(STORM[0], "Remnants of Hurricane Nora (synthetic)", STORM[1], STORM[2], "OH,KY,IN")],
            "ut_volume": [(ts(m), ent, DRIVERS["Customers"][i], DRIVERS["AmiMeters"][i], (38 if ent == "Meridian Transmission" else 6) + k)
                          for k, m in enumerate(MONTHS) for i, ent in enumerate(OPCOS) if ent != "Meridian Renewables"],
            "ut_ai_use": AI_USE}
    rows["ut_allocation_policy"] = [
        (pid, 1, "opco", name, col, val, drv, fixed, prio, T(2026, 1, 1), None, status, POLICY_SUBMITTER, T(2025, 12, 1),
         POLICY_APPROVER, T(2025, 12, 15)) for pid, name, col, val, drv, fixed, prio, status in POLICIES]
    pid, ver, name, col, val, drv, fixed, prio = PENDING_POLICY
    rows["ut_allocation_policy"].append((pid, ver, "opco", name, col, val, drv, fixed, prio, T(2026, 10, 1), None,
                                         "submitted", POLICY_SUBMITTER, T(2026, 10, 20), None, None))
    rows["ut_rate_case"] = [
        (d, j, e, bs, be, tys, tye, f, s, roe, eq, debt, round(eq * roe / (1 - TAX_RATE) + (1 - eq) * debt, 4))
        for d, j, e, bs, be, tys, tye, f, s, roe, eq, debt in RATE_CASES]
    rows["ut_vendor"] = [(v, cip, json.dumps(cl), end, ren, esc, acv) for v, cip, cl, end, ren, esc, acv in VENDORS]
    return rows


def insert_dicts(con, table, dicts):
    cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})", [[d.get(c) for c in cols] for d in dicts])


def refresh_budgets(con):
    """Technology budgets by cost center: September actuals x a planning factor (same rule as generate_sample.py),
    recomputed after each add-on so the budgets cover the utility and AI stacks."""
    con.execute("DELETE FROM cost_budget")
    con.execute("""
        INSERT INTO cost_budget
        WITH base AS (
          SELECT COALESCE(json_extract_string(AllocatedTags, '$.cost_center'), json_extract_string(Tags, '$.cost_center'),
                          CASE WHEN ChargeCategory = 'Tax' THEN 'Shared Services' END, 'Corporate IT') AS CostCenter,
                 SUM(EffectiveCost) AS Sep
          FROM CostAndUsage WHERE BillingPeriodStart = TIMESTAMP '2026-09-01' GROUP BY 1
        )
        SELECT m.BudgetPeriodStart, b.CostCenter,
               ROUND(b.Sep * CASE b.CostCenter WHEN 'Data & Analytics' THEN 0.97 WHEN 'Customer Operations' THEN 1.0
                                               WHEN 'Transmission Planning' THEN 0.98 ELSE 1.08 END, -2), 'USD'
        FROM base b CROSS JOIN (SELECT DISTINCT BillingPeriodStart AS BudgetPeriodStart FROM CostAndUsage
                                WHERE BillingPeriodStart >= TIMESTAMP '2026-07-01') m""")


def apply(path):
    con = duckdb.connect(path)
    text = re.sub(r"--[^\n]*", "", open(os.path.join(ROOT, "schema", "costiq_utility_schema.sql")).read())
    for stmt in [s.strip() for s in text.split(";") if s.strip()]:
        con.execute(stmt)
    ref = reference_rows()
    for t, rows in ref.items():
        con.execute(f"DELETE FROM {t}")
        con.executemany(f"INSERT INTO {t} VALUES ({', '.join('?' * len(rows[0]))})", rows)

    accounts = [GRID_ACCOUNT[0]] + sorted({a for _, a, *_ in SAAS_LINES})
    for a in accounts:
        con.execute("DELETE FROM CostAndUsage WHERE BillingAccountId = ?", [a])
        con.execute("DELETE FROM InvoiceDetail WHERE BillingAccountId = ?", [a])
    charges = grid_rows() + saas_rows()
    insert_dicts(con, "CostAndUsage", charges)

    invoices = []
    for inv, det, ps, pe, cat, issuer, acct, amt in con.execute(f"""
            SELECT InvoiceId, InvoiceDetailId, BillingPeriodStart, BillingPeriodEnd, ChargeCategory, InvoiceIssuerName,
                   BillingAccountId, SUM(BilledCost)
            FROM CostAndUsage WHERE BillingAccountId IN ({', '.join('?' * len(accounts))}) GROUP BY ALL""", accounts).fetchall():
        saas = acct != GRID_ACCOUNT[0]
        issued = saas or pe.date() <= AS_OF  # SaaS bills in advance; cloud usage after month end
        if issuer == "ServiceNow" and ps.month == 9:
            amt = float(amt) + SERVICENOW_SEP_OVERBILL
        invoices.append({"BilledCost": amt, "BillingAccountId": acct, "BillingCurrency": "USD", "BillingPeriodEnd": pe,
                         "BillingPeriodStart": ps, "ChargeCategory": cat, "InvoiceDetailCreated": ps if saas else pe,
                         "InvoiceDetailDescription": f"{issuer} {cat} charges", "InvoiceDetailGrain": json.dumps({"ChargeCategory": cat}),
                         "InvoiceDetailId": det, "InvoiceDetailLastUpdated": (ps if saas else pe) + dt.timedelta(days=3),
                         "InvoiceId": inv, "InvoiceIssueDate": (ps if saas else pe) + dt.timedelta(days=2) if issued else None,
                         "InvoiceIssueStatus": "Issued" if issued else "Open", "InvoiceIssuerName": issuer,
                         "PaymentCurrency": "USD", "PaymentCurrencyBilledCost": amt, "PaymentCurrencyInvoiceDetailId": "PAY-" + det,
                         "PaymentDueDate": (ps if saas else pe) + dt.timedelta(days=32) if issued else None,
                         "PaymentTerms": "Net 30", "PurchaseOrderNumber": f"PO-IT-2026-{issuer.upper()[:4]}", "ReferenceInvoiceId": None})
    insert_dicts(con, "InvoiceDetail", invoices)
    # Billing periods for the new SaaS issuers (AWS periods already exist from the base generator).
    saas_issuers = sorted({vendor for vendor, *_ in SAAS_LINES})
    con.execute(f"DELETE FROM BillingPeriod WHERE InvoiceIssuerName IN ({', '.join('?' * len(saas_issuers))})", saas_issuers)
    insert_dicts(con, "BillingPeriod", [
        {"BillingPeriodCreated": ts(m), "BillingPeriodStart": ts(m), "BillingPeriodEnd": ts(next_month(m)),
         "BillingPeriodLastUpdated": ts(m) + dt.timedelta(days=3), "BillingPeriodStatus": "Closed", "InvoiceIssuerName": vendor}
        for vendor in saas_issuers for m in MONTHS])

    refresh_budgets(con)
    con.execute("CHECKPOINT")
    con.close()
    print(f"{path}: {len(charges)} utility charge rows, {len(invoices)} invoice lines, "
          f"{sum(len(v) for v in ref.values())} register rows")


if __name__ == "__main__":
    for p in sys.argv[1:] or [os.path.join(ROOT, "sample", "costiq_sample.duckdb")]:
        apply(p)
