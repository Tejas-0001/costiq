"""Add the R&D and clinical workloads and reference registers to a CostIQ sample DuckDB file.

  python data/pharma.py sample/costiq_sample.duckdb

Idempotent: removes what it added before inserting again. Generated sample: entities, filings, vendors' terms
and people are invented, and all figures are illustrative.

Adds
  * Enterprise SaaS billed by invoice file drop (SAP S/4HANA, Veeva Vault Clinical, ServiceNow, Oracle Argus Safety),
    including S/4HANA implementation services (capitalizable) and an Argus analytics line with no GL account.
  * An R&D AWS account: clinical-trial analytics, a patient registry with ePRO ingest, electronic data capture (surge
    during database lock DBL-PX301), a patient portal, a discovery GPU endpoint left idle after 20 Sep, genomics data,
    non-production EDC environments running 24x7, and Consumer Health insights.
  * Invoices for all of it (ServiceNow September is over-billed: a reconciliation exception) and refreshed budgets.
  * ref_* registers (schema/costiq_pharma_schema.sql) with seeded issues so every pharma check has findings.
"""
from __future__ import annotations
import datetime as dt, hashlib, json, os, re, sys
import duckdb

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
START, AS_OF = dt.date(2026, 7, 1), dt.date(2026, 10, 22)
MONTHS = [dt.date(2026, m, 1) for m in (7, 8, 9, 10)]
RD_ACCOUNT = ("aws-rd-8008", "R&D AWS")
RD_SUB = ("888800008008", "pharma-rd-platform")
RD_SOURCE, SAAS_SOURCE = "aws_rd_focus_export", "saas_invoice_filedrop"
STUDY_EVENT = ("DBL-PX301", dt.date(2026, 9, 10), dt.date(2026, 9, 16))
GPU_IDLE_FROM = dt.date(2026, 9, 20)


def ts(d):
    return dt.datetime(d.year, d.month, d.day)


def next_month(d):
    return dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def wobble(day, key, spread=0.15):
    h = int(hashlib.md5(f"{day}{key}".encode()).hexdigest()[:6], 16) / 0xFFFFFF
    return 1 + spread * (2 * h - 1)


# application, cost center, region, resource, service, category, unit, daily $, extra tags
RD_LINES = [
    ("trial-analytics", "Clinical Operations", "us-east-2", "trial-analytics-emr", "Amazon EMR", "Analytics", "Hours", 820, {"gxp_scope": "medium", "data_class": "phi"}),
    ("trial-analytics", "Clinical Operations", "us-east-2", "trial-data-lake", "Amazon S3", "Storage", "GB-Month", 240, {"gxp_scope": "medium", "data_class": "phi"}),
    ("patient-registry", "Patient Data", "us-east-2", "registry-aurora", "Amazon Aurora", "Databases", "Hours", 960, {}),
    ("patient-registry", "Patient Data", "us-east-2", "epro-ingest-kinesis", "Amazon Kinesis", "Analytics", "Hours", 410, {}),
    ("edc-platform", "Clinical Operations", "us-east-2", "edc-app-asg", "Amazon EC2", "Compute", "Hours", 690, {"gxp_scope": "medium", "data_class": "phi"}),
    ("patient-portal", "Patient Data", "us-east-2", "portal-eks", "Amazon EKS", "Compute", "Hours", 380, {}),
    ("discovery-gpu", "Research & Discovery", "us-east-2", "molgen-gpu-endpoint", "Amazon SageMaker", "AI and Machine Learning", "Hours", 1850, {"ai_component": "gpu_hosting"}),
    ("genomics-data", "Research & Discovery", "us-east-2", "genomics-s3", "Amazon S3", "Storage", "GB-Month", 95, {"data_class": "genomic", "gxp_scope": "low"}),
    ("edc-dev", "Clinical Operations", "us-east-2", "edc-dev-ec2", "Amazon EC2", "Compute", "Hours", 310, {"environment": "nonprod"}),
    ("edc-test", "Clinical Operations", "us-east-2", "edc-test-ec2", "Amazon EC2", "Compute", "Hours", 160, {"environment": "nonprod"}),
    ("consumer-insights", "Consumer Health", "us-west-2", "insights-ec2", "Amazon EC2", "Compute", "Hours", 540, {}),
]
EVENT_SURGE = {"edc-platform": 3.0, "patient-portal": 2.5}
# Optimizations already implemented (the Optimization Center verifies them from these run-rate changes)
RIGHTSIZED = {"insights-ec2": (dt.date(2026, 9, 1), 0.55), "epro-ingest-kinesis": (dt.date(2026, 9, 15), 0.70)}

# vendor, account, service name, category, cost center, application, monthly $, charge frequency, extra tags
SAAS_LINES = [
    ("SAP", "sap-rise-9101", "SAP S/4HANA Cloud (RISE)", "Business Applications", "Corporate IT", "erp-s4hana", 310000, "Recurring", {}),
    ("SAP", "sap-rise-9101", "SAP S/4HANA implementation services", "Business Applications", "Corporate IT", "erp-s4hana", 120000, "One-Time", {"implementation": "true"}),
    ("Veeva", "veeva-9102", "Veeva Vault Clinical", "Business Applications", "Clinical Operations", "etmf-ctms", 95000, "Recurring", {"gxp_scope": "high"}),
    ("ServiceNow", "snow-9103", "ServiceNow ITSM and ITOM", "Business Applications", "Corporate IT", "itsm", 70000, "Recurring", {"gxp_scope": "low"}),
    ("Oracle", "orcl-9104", "Oracle Argus Safety", "Business Applications", "Patient Data", "pv-safety", 160000, "Recurring", {"gxp_scope": "high"}),
    ("Oracle", "orcl-9104", "Oracle Argus Analytics add-on", "Other", "Patient Data", "pv-analytics", 18000, "Recurring", {}),
]
SERVICENOW_SEP_OVERBILL = 14200.0


def rd_rows():
    out, day = [], START
    while day <= AS_OF:
        surge = STUDY_EVENT[1] <= day <= STUDY_EVENT[2]
        for app, cc, region, res, svc, cat, unit, daily, extra in RD_LINES:
            cost = daily * wobble(day, res)
            if res in RIGHTSIZED and day >= RIGHTSIZED[res][0]:
                cost *= RIGHTSIZED[res][1]
            tags = {"cost_center": cc, "application": app, "environment": "prod", **extra}
            if surge and app in EVENT_SURGE:
                cost *= EVENT_SURGE[app]
                tags["study_event"] = STUDY_EVENT[0]
            cost = round(cost, 4)
            r = dict(ChargeDescription=f"{svc} {res}", RegionId=region, RegionName=region,
                     ResourceId=f"arn:aws:{svc.split()[-1].lower()}:{region}:{RD_SUB[0]}:{res}", ResourceName=res,
                     ResourceType=svc, ServiceCategory=cat, ServiceName=svc, ServiceSubcategory=svc,
                     SkuId=f"{svc.replace(' ', '')}-{unit}", SkuMeter=unit, SkuPriceId=f"{res}-ondemand",
                     ConsumedQuantity=24 if unit == "Hours" else round(cost * 20, 2), ConsumedUnit=unit,
                     PricingUnit=unit, PricingQuantity=24 if unit == "Hours" else round(cost * 20, 2),
                     Tags=json.dumps(tags))
            if res == "molgen-gpu-endpoint":  # real-time endpoint: busy on weekdays, then left running idle
                busy = day < GPU_IDLE_FROM and day.weekday() < 5
                r.update(x_AIProduct="SageMaker endpoint", x_RequestCount=int(2400 * wobble(day, "rq")) if busy else 0)
            out.append(base(day, cost, RD_ACCOUNT, "AWS", RD_SUB, RD_SOURCE, **r))
        day += dt.timedelta(days=1)
    return out


def saas_rows():
    out = []
    for m in MONTHS:
        for vendor, acct, svc, cat, cc, app, monthly, freq, extra in SAAS_LINES:
            tags = {"cost_center": cc, "application": app, "environment": "prod", **extra}
            amount = round(monthly * wobble(m, svc, 0.02), 2)
            out.append(base(m, amount, (acct, f"{vendor} account"), vendor, (acct, f"{vendor} subscription"), SAAS_SOURCE,
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
ENTITIES = [  # entity, parent, type, regulated (GxP), jurisdictions
    ("Global Services", "Group", "service_company", True, ""),
    ("US Operations", "Group", "opco", True, "US,PR"),
    ("Germany GmbH", "Group", "opco", True, "DE"),
    ("UK Operations", "Group", "opco", True, "UK"),
    ("Research Ireland", "Group", "research", True, "IE"),
    ("Consumer Health Affiliate", "Group", "affiliate", False, ""),
]
OPCOS = ["US Operations", "Germany GmbH", "UK Operations", "Research Ireland", "Consumer Health Affiliate"]
DRIVERS = {  # per entity, in OPCOS order
    "EnrolledPatients": [14500, 9200, 11800, 0, 0],
    "TrialSites": [141, 88, 115, 0, 0],
    "Employees": [4100, 2600, 3300, 900, 350],
    "Workstations": [4500, 2850, 3600, 990, 380],
    "ActiveStudies": [98, 64, 89, 142, 0],
}
POLICY_APPROVER, POLICY_SUBMITTER = "demo.finance_controller@company.example", "demo.transfer_pricing@company.example"
POLICIES = [  # id, name, column, value, driver, fixed entity, priority, status
    ("AP-AI-PLATFORM", "Shared Claude API platform by headcount", "ServiceName", "Claude API", "Employees", None, 5, "approved"),
    ("AP-CONSUMER", "Consumer Health work to Consumer Health Affiliate", "CostCenter", "Consumer Health", None, "Consumer Health Affiliate", 10, "approved"),
    ("AP-RESEARCH", "Research & Discovery to Research Ireland", "CostCenter", "Research & Discovery", None, "Research Ireland", 20, "approved"),
    ("AP-PATIENT", "Patient Data by enrolled patients", "CostCenter", "Patient Data", "EnrolledPatients", None, 20, "approved"),
    ("AP-CLINICAL", "Clinical Operations by active studies", "CostCenter", "Clinical Operations", "ActiveStudies", None, 20, "approved"),
    ("AP-ANALYTICS", "Data & Analytics by workstations", "CostCenter", "Data & Analytics", "Workstations", None, 30, "approved"),
    ("AP-DEFAULT", "Everything else by headcount", "*", "*", "Employees", None, 99, "approved"),
]
PENDING_POLICY = ("AP-ANALYTICS", 2, "Data & Analytics by trial sites (the site-data platform is the main consumer)",
                  "CostCenter", "Data & Analytics", "TrialSites", None, 30)
FACTORS = [("US Operations", "US", 0.93), ("US Operations", "PR", 0.07),
           ("Germany GmbH", "DE", 1.0), ("UK Operations", "UK", 1.0),
           ("Research Ireland", "IE", 1.0), ("Consumer Health Affiliate", "Non-GxP", 1.0),
           ("Global Services", "Unassigned", 1.0)]
GL_MAP = [  # priority, column, value, entity type, P&L function, GL account, title
    (1, "ChargeCategory", "Tax", None, "SG&A", "7810", "Non-income taxes"),
    (2, "ChargeCategory", "Credit", None, "SG&A", "7400", "Outside services"),
    (10, "ServiceCategory", "Business Applications", "research", "Research & Development", "6120", "Research software"),
    (10, "ServiceCategory", "Compute", "research", "Research & Development", "6110", "Research computing"),
    (10, "ServiceCategory", "Storage", "research", "Research & Development", "6110", "Research computing"),
    (10, "ServiceCategory", "Analytics", "research", "Research & Development", "6110", "Research computing"),
    (20, "ServiceCategory", "Business Applications", None, "SG&A", "7220", "Software subscriptions & maintenance"),
    (20, "ServiceCategory", "Management and Governance", None, "SG&A", "7220", "Software subscriptions & maintenance"),
    (20, "ServiceCategory", "Security", None, "SG&A", "7220", "Software subscriptions & maintenance"),
    (20, "ServiceCategory", "AI and Machine Learning", None, "SG&A", "7400", "Outside services"),
    (30, "ServiceCategory", "Compute", None, "SG&A", "7210", "Cloud infrastructure"),
    (30, "ServiceCategory", "Storage", None, "SG&A", "7210", "Cloud infrastructure"),
    (30, "ServiceCategory", "Networking", None, "SG&A", "7210", "Cloud infrastructure"),
    (30, "ServiceCategory", "Databases", None, "SG&A", "7210", "Cloud infrastructure"),
    (30, "ServiceCategory", "Analytics", None, "SG&A", "7210", "Cloud infrastructure"),
]
JURISDICTION_POLICY = [  # jurisdiction, credit status, claimed, reference (cloud fees as qualifying R&D expenditure)
    ("US", "claimed", True, "IRC §41 computer-use QRE (illustrative)"),
    ("PR", "eligible", False, "Puerto Rico R&D credit: not yet claimed"),
    ("DE", "ineligible", False, "Forschungszulage: cloud fees not eligible (illustrative)"),
    ("UK", "eligible", False, "Cloud and data costs qualify since Apr 2023; not yet claimed"),
    ("IE", "not_applicable", False, "Claimed in a separate R&D credit computation"),
    ("Non-GxP", "not_applicable", False, "Consumer Health: no qualifying research"),
    ("Unassigned", "not_applicable", False, "Shared services before allocation"),
]
FILINGS = [  # filing, jurisdiction, entity, review start, review end, forecast start, forecast end, filing date, status, credit %, authority
    ("US-1120-FY2026", "US", "US Operations", T(2026, 7, 1), T(2026, 10, 1), T(2027, 1, 1), T(2028, 1, 1),
     T(2026, 12, 15), "In preparation", 6.5, "IRS"),
    ("UK-CT600-FY2026", "UK", "UK Operations", T(2026, 7, 1), T(2026, 10, 1), T(2027, 4, 1), T(2028, 4, 1),
     T(2027, 3, 31), "Planned", 20.0, "HMRC"),
    ("IE-CT1-FY2026", "IE", "Research Ireland", T(2026, 7, 1), T(2026, 10, 1), T(2027, 1, 1), T(2028, 1, 1),
     T(2026, 12, 1), "In preparation", 30.0, "Revenue"),
]
CLAUSES_FULL = {"quality_agreement": True, "audit_rights": True, "change_notification": True, "data_integrity": True}
VENDORS = [  # vendor, GxP scoped, quality clauses, contract end, renewal, escalator %, annual value
    ("SAP", False, CLAUSES_FULL, T(2029, 3, 31), T(2027, 4, 1), 7.0, 3720000),
    ("Veeva", True, CLAUSES_FULL, T(2027, 1, 31), T(2027, 2, 1), 9.0, 1140000),
    ("ServiceNow", True, {**CLAUSES_FULL, "change_notification": False}, T(2026, 12, 31), T(2027, 1, 1), 5.0, 840000),
    ("Oracle", True, CLAUSES_FULL, T(2028, 6, 30), T(2028, 7, 1), 3.0, 2136000),
    ("AWS", True, CLAUSES_FULL, T(2028, 1, 1), T(2028, 1, 1), 0.0, 3400000),
    ("Microsoft", True, {**CLAUSES_FULL, "data_integrity": False}, T(2028, 7, 1), T(2027, 7, 1), 4.0, 1600000),
    ("Anthropic", False, CLAUSES_FULL, T(2027, 1, 1), T(2027, 1, 1), 0.0, 150000),
    ("Snowflake", False, CLAUSES_FULL, T(2027, 9, 30), T(2027, 10, 1), 6.0, 240000),
]
GXP_SYSTEMS = [  # application, system, GxP impact, holds patient data, key ownership, owner, next validation review
    ("edc-platform", "Electronic data capture (EDC)", "Medium", True, "provider", "Clinical Operations IT", D(2026, 12, 15)),
    ("trial-analytics", "Clinical trial analytics replica", "Medium", True, "customer", "Clinical Operations IT", D(2026, 10, 1)),
    ("genomics-data", "Genomics data store", "Low", True, "customer", "Research & Discovery", D(2027, 6, 30)),
    ("itsm", "ServiceNow change control for GxP systems", "Low", True, "provider", "Corporate IT", D(2026, 11, 30)),
    ("discovery-gpu", "Molecule-generation model endpoint", "None", False, "customer", "Research & Discovery", None),
]
AI_USE = [  # application, use case, GxP context, review status, approved on, internal risk tier, EU AI Act class, GMP context
    ("protocol-assistant", "Answer study teams' questions over approved protocols", True, "Approved", D(2026, 5, 1), "High", None, False),
    ("discovery-gpu", "Generative chemistry model for lead optimization", False, "Approved", D(2026, 6, 15), "Limited", None, False),
    ("csr-drafting-agent", "Agent that drafts clinical study report sections", True, "Pending", None, "High", None, False),
    ("ae-intake-bot", "Summarize adverse-event intake for case processing", True, "Pending", None, "High", None, False),
    ("patient-support-assistant", "Answer patients' program questions from approved content", False, "Approved", D(2026, 1, 10), "High", None, False),
    ("consumer-research", "Research consumer health product trends", False, "Approved", D(2026, 2, 3), "Minimal", None, False),
]
PROGRAMS = [  # program, name, phase, annual technology budget
    ("PX-100", "Discovery chemistry platform", "Discovery", 600000),
    ("PX-214", "Oncology, first-in-human", "Phase 1", 60000),
    ("PX-227", "Immunology, dose ranging", "Phase 2", 420000),
    ("PX-301", "Cardiometabolic, pivotal", "Phase 3", 2100000),
    ("PX-M01", "Marketed portfolio: safety and registries", "Marketed", 3000000),
]
PROGRAM_APPS = {"PX-100": ["discovery-gpu", "experiments"], "PX-214": ["genomics-data"], "PX-227": ["trial-analytics"],
                "PX-301": ["edc-platform", "edc-dev", "edc-test", "etmf-ctms", "csr-drafting-agent", "protocol-assistant", "patient-portal"],
                "PX-M01": ["pv-safety", "pv-analytics", "ae-intake-bot", "patient-registry"]}


def reference_rows():
    rows = {"ref_legal_entity": ENTITIES,
            "ref_cost_center": [(cc, "Consumer Health Affiliate" if cc == "Consumer Health" else "Global Services",
                                cc != "Consumer Health")
                               for cc in ("Clinical Operations", "Patient Data", "Research & Discovery", "Data & Analytics",
                                          "Corporate IT", "Finance", "HR", "Legal", "Shared Services", "Consumer Health",
                                          "Unallocated")],
            "ref_allocation_driver": [(name, ts(m), ent, round(vals[i] * (1 + 0.002 * k), 0))
                                      for name, vals in DRIVERS.items() for k, m in enumerate(MONTHS)
                                      for i, ent in enumerate(OPCOS) if vals[i]],
            "ref_jurisdiction_factor": [(e, j, ts(m), f) for m in MONTHS for e, j, f in FACTORS],
            "ref_gl_account_map": GL_MAP,
            "ref_jurisdiction_policy": JURISDICTION_POLICY,
            "ref_gxp_system": GXP_SYSTEMS,
            "ref_study_event": [(STUDY_EVENT[0], "Database lock, study PX-301", STUDY_EVENT[1], STUDY_EVENT[2], "US,DE,UK")],
            "ref_volume": [(ts(m), ent, DRIVERS["EnrolledPatients"][i], DRIVERS["TrialSites"][i],
                            (38 if ent == "Research Ireland" else 6) + k)
                           for k, m in enumerate(MONTHS) for i, ent in enumerate(OPCOS) if ent != "Consumer Health Affiliate"],
            "ref_ai_use": AI_USE,
            "ref_program": PROGRAMS,
            "ref_program_application": [(app, pid) for pid, apps in PROGRAM_APPS.items() for app in apps]}
    rows["ref_allocation_policy"] = [
        (pid, 1, "opco", name, col, val, drv, fixed, prio, T(2026, 1, 1), None, status, POLICY_SUBMITTER, T(2025, 12, 1),
         POLICY_APPROVER, T(2025, 12, 15)) for pid, name, col, val, drv, fixed, prio, status in POLICIES]
    pid, ver, name, col, val, drv, fixed, prio = PENDING_POLICY
    rows["ref_allocation_policy"].append((pid, ver, "opco", name, col, val, drv, fixed, prio, T(2026, 10, 1), None,
                                          "submitted", POLICY_SUBMITTER, T(2026, 10, 20), None, None))
    rows["ref_tax_filing"] = FILINGS
    rows["ref_vendor"] = [(v, gxp, json.dumps(cl), end, ren, esc, acv) for v, gxp, cl, end, ren, esc, acv in VENDORS]
    return rows


def insert_dicts(con, table, dicts):
    cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})", [[d.get(c) for c in cols] for d in dicts])


def refresh_budgets(con):
    """Technology budgets by cost center: September actuals x a planning factor (same rule as generate_sample.py),
    recomputed after each add-on so the budgets cover the R&D, clinical and AI stacks."""
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
               ROUND(b.Sep * CASE b.CostCenter WHEN 'Data & Analytics' THEN 0.97 WHEN 'Patient Data' THEN 1.0
                                               WHEN 'Research & Discovery' THEN 0.98 ELSE 1.08 END, -2), 'USD'
        FROM base b CROSS JOIN (SELECT DISTINCT BillingPeriodStart AS BudgetPeriodStart FROM CostAndUsage
                                WHERE BillingPeriodStart >= TIMESTAMP '2026-07-01') m""")


def apply(path):
    con = duckdb.connect(path)
    text = re.sub(r"--[^\n]*", "", open(os.path.join(ROOT, "schema", "costiq_pharma_schema.sql")).read())
    for stmt in [s.strip() for s in text.split(";") if s.strip()]:
        con.execute(stmt)
    ref = reference_rows()
    for t, rows in ref.items():
        con.execute(f"DELETE FROM {t}")
        con.executemany(f"INSERT INTO {t} VALUES ({', '.join('?' * len(rows[0]))})", rows)

    accounts = [RD_ACCOUNT[0]] + sorted({a for _, a, *_ in SAAS_LINES})
    for a in accounts:
        con.execute("DELETE FROM CostAndUsage WHERE BillingAccountId = ?", [a])
        con.execute("DELETE FROM InvoiceDetail WHERE BillingAccountId = ?", [a])
    charges = rd_rows() + saas_rows()
    insert_dicts(con, "CostAndUsage", charges)

    invoices = []
    for inv, det, ps, pe, cat, issuer, acct, amt in con.execute(f"""
            SELECT InvoiceId, InvoiceDetailId, BillingPeriodStart, BillingPeriodEnd, ChargeCategory, InvoiceIssuerName,
                   BillingAccountId, SUM(BilledCost)
            FROM CostAndUsage WHERE BillingAccountId IN ({', '.join('?' * len(accounts))}) GROUP BY ALL""", accounts).fetchall():
        saas = acct != RD_ACCOUNT[0]
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
    print(f"{path}: {len(charges)} R&D and SaaS charge rows, {len(invoices)} invoice lines, "
          f"{sum(len(v) for v in ref.values())} register rows")


if __name__ == "__main__":
    for p in sys.argv[1:] or [os.path.join(ROOT, "sample", "costiq_sample.duckdb")]:
        apply(p)
