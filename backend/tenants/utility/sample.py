"""Utility add-on generator for a CostIQ DuckDB file (tenants/utility/pack.yaml `addons`).

  python tenants/utility/sample.py sample/utility/costiq_sample.duckdb   (data/build_sample.py runs it)

An electric utility holding company with generic names: a centralized service company (18 CFR Part 367), six operating
companies, three transmission companies and a non-utility affiliate. Idempotent: removes what it added before inserting
again. All amounts are invented.

Adds
  * The group's AWS payer (aws-payer-7001) and Azure billing profile (ms-mca-7001): the workloads by account and
    subscription, Savings Plan, RDS reserved instances (bought in July), an Azure reservation (bought in September),
    Enterprise Support spread pro rata, tax and credits by account, and an SLA credit posted after September closed.
    The September AWS invoice ties to the cent (507,065.34); the September Azure invoice carries tax that is not in the
    cost data and a late charge (2,640.55) that has not arrived yet.
  * AWS Marketplace GIS subscription, Azure Marketplace historian analytics, the M365 Copilot annual prepayment
    (amortized monthly), ITSM SaaS (September overbilled by 14,200), ERP systems integrator services (capex with no
    work order) and work-management implementation services (work order without a threshold date).
  * 3,000 M365 Copilot seats (70% group-assigned) with daily activity; AI token workloads with all four token types.
  * Registers: legal entities, cost centers, allocation factors and policies (AP-NET v2 awaiting a second approver),
    jurisdiction factors, the FERC account reference, rules R1-R17, exception codes, the first-cut account map, work
    orders, required tags, cloud accounts, CIP review requests, vendors with CIP-013 clauses, rate cases, storm events.
  * Deliberate data issues: untagged spend, invalid tag values, capex with no work order, a work order with no threshold
    date, invoice differences, non-utility affiliate spend tagged to a regulated cost center, a multi-function account
    with no function, masked resource names awaiting CIP review, a correction after close, a blocked rule to 920.
"""
from __future__ import annotations
import datetime as dt, hashlib, json, os, random, re, sys
import duckdb

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))  # mvp/backend
sys.path[:0] = [ROOT, os.path.join(ROOT, "data")]
from sample_common import refresh_budgets  # noqa: E402

START, AS_OF = dt.date(2026, 7, 1), dt.date(2026, 10, 22)
MONTHS = [dt.date(2026, m, 1) for m in (7, 8, 9, 10)]
SEP = dt.date(2026, 9, 1)
D, T = dt.date, dt.datetime
SOURCE_AWS, SOURCE_AZ, SOURCE_SAAS = "aws_focus_export", "azure_focus_export", "saas_invoice_filedrop"
MONTH_FACTOR = {7: 0.95, 8: 0.97, 9: 1.0, 10: 22 / 30 * 1.01}   # monthly totals against September (October to the 22nd)
GROWTH = {7: 0.95, 8: 0.97, 9: 1.0, 10: 1.01}                   # daily run rate against September
STORM = ("ST-2026-09A", D(2026, 9, 10), D(2026, 9, 16))
GPU_IDLE_FROM = D(2026, 9, 20)
APPROVER, SUBMITTER = "controller@company.example", "service.accounting@company.example"


def ts(d):
    return dt.datetime(d.year, d.month, d.day)


def next_month(d):
    return dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def month_days(m):
    """Days of month m inside the sample window."""
    out, d = [], m
    while d < next_month(m) and d <= AS_OF:
        out.append(d)
        d += dt.timedelta(days=1)
    return out


def wobble(day, key, spread=0.12):
    h = int(hashlib.md5(f"{day}{key}".encode()).hexdigest()[:6], 16) / 0xFFFFFF
    return 1 + spread * (2 * h - 1)


def split(total, weights):
    """Split `total` (cents exact) by weights; the rounding residual lands on the largest part."""
    s = sum(weights)
    parts = [round(total * w / s, 2) for w in weights]
    if parts:
        i = max(range(len(parts)), key=lambda k: weights[k])
        parts[i] = round(parts[i] + round(total - sum(parts), 2), 2)
    return parts


def mask(name):
    """Pseudonym for a resource name on an account flagged for CIP review (keyed hash; the key stays with the client)."""
    return "P-" + hashlib.sha256(f"cip-review|{name}".encode()).hexdigest()[:10].upper()


# ============================================================================================ legal entities
# code, entity, type, regulated, jurisdictions, accounts part, grid, retail customers, employees, workstations, pole miles
ENTITIES = [
    ("SVC", "Shared Services Company", "service_company", True, "", "Part 367", None, 0, 6800, 7400, 0),
    ("NOR", "North Electric Company", "opco", True, "State A,State B,FERC", "Part 101", "RTO-East", 880000, 1650, 1780, 6300),
    ("CEN", "Central Electric Company", "opco", True, "State C,State D,FERC", "Part 101", "RTO-East", 610000, 2150, 2300, 5200),
    ("VAL", "Valley Power Company", "opco", True, "State E,FERC", "Part 101", "RTO-West", 540000, 1150, 1240, 4100),
    ("SOU", "Southern Electric Company", "opco", True, "State F,State G,FERC", "Part 101", "RTO-West", 520000, 1400, 1500, 4800),
    ("MET", "Metro Wires Company", "opco", True, "State H,FERC", "Part 101", "RTO-East", 1320000, 1560, 1700, 3900),
    ("PLA", "Plains Wires Company", "opco", True, "State I", "Part 101", "Grid-South", 990000, 1730, 1850, 6000),
    ("TNO", "North Transmission Company", "transco", True, "FERC", "Part 101", "RTO-East", 0, 0, 0, 3100),
    ("TCE", "Central Transmission Company", "transco", True, "FERC", "Part 101", "RTO-East", 0, 0, 0, 2700),
    ("TWE", "West Transmission Company", "transco", True, "FERC", "Part 101", "RTO-West", 0, 0, 0, 1900),
    ("CES", "Competitive Energy Services", "affiliate", False, "Non-jurisdictional", "none", None, 0, 340, 360, 0),
]
E = {code: name for code, name, *_ in ENTITIES}
RECEIVING = [e for e in ENTITIES if e[0] != "SVC"]                     # companies the service company bills
OPCOS = [e for e in ENTITIES if e[2] == "opco"]
GLT = {"NOR": 410000, "CEN": 520000, "VAL": 300000, "SOU": 360000, "MET": 450000, "PLA": 470000, "TNO": 42000,
       "TCE": 36000, "TWE": 30000, "CES": 95000}                       # GL transactions a month
NET_TB = {"NOR": 182, "CEN": 236, "VAL": 121, "SOU": 148, "MET": 205, "PLA": 190, "TNO": 64, "TCE": 51, "TWE": 39, "CES": 22}
LCT = {"NOR": 41, "CEN": 33, "VAL": 22, "SOU": 27, "MET": 24, "PLA": 35, "TNO": 58, "TCE": 46, "TWE": 31}   # $M a quarter
LCD = {"NOR": 64, "CEN": 52, "VAL": 38, "SOU": 41, "MET": 77, "PLA": 69}
TAGGED_USE = {"NOR": 0.15, "CEN": 0.16, "VAL": 0.10, "SOU": 0.11, "MET": 0.17, "PLA": 0.15, "TNO": 0.05, "TCE": 0.04,
              "TWE": 0.03, "CES": 0.04}
JURISDICTION_FACTORS = [
    ("NOR", "State A", 0.84), ("NOR", "State B", 0.08), ("NOR", "FERC", 0.08),
    ("CEN", "State C", 0.68), ("CEN", "State D", 0.24), ("CEN", "FERC", 0.08),
    ("VAL", "State E", 0.92), ("VAL", "FERC", 0.08),
    ("SOU", "State F", 0.58), ("SOU", "State G", 0.34), ("SOU", "FERC", 0.08),
    ("MET", "State H", 0.85), ("MET", "FERC", 0.15),
    ("PLA", "State I", 1.0), ("TNO", "FERC", 1.0), ("TCE", "FERC", 1.0), ("TWE", "FERC", 1.0),
    ("CES", "Non-jurisdictional", 1.0), ("SVC", "Unassigned", 1.0)]

# ============================================================================================ cost centers (brief 6.3)
# cost center, business unit, FERC function, default factor; all owned by the service company except the affiliate's
COST_CENTERS = [
    ("Transmission Operations", "TRANSMISSION", "T", "F-TPM"),
    ("Transmission Planning", "TRANSMISSION", "T", "F-TPM"),
    ("Transmission Asset Management", "TRANSMISSION", "T", "F-TPM"),
    ("Distribution Operations", "DISTRIBUTION", "D", "F-CUS"),
    ("Grid Modernization (AMI)", "DISTRIBUTION", "D", "F-CUS"),
    ("Field Work Management", "DISTRIBUTION", "D", "F-EMP-OPCO"),
    ("Customer Operations", "DISTRIBUTION", "CUST", "F-CUS"),
    ("Enterprise Applications", "CORPORATE_IT", "AG", "F-GLT"),
    ("End-User Computing", "CORPORATE_IT", "AG", "F-WKS"),
    ("Data & Analytics Platform", "CORPORATE_IT", "GENERAL", "F-EMP"),
    ("Cyber Security Operations", "CORPORATE_IT", "AG", "F-WKS"),
    ("Network & Connectivity", "CORPORATE_IT", "AG", "F-EQ"),
    ("Finance & Regulatory Services", "SHARED_SERVICES", "AG", "F-EMP"),
    ("Competitive Energy Services", "NON_UTILITY", "NONUTIL", "F-DIR"),
    ("Unallocated", None, None, None),   # suspense: no policy; held at the service company in the exception queue
]

# ============================================================================================ allocation factors (6.8)
WO_T, WO_D, WO_G, WO_D2 = "WO-T-2026-0142", "WO-D-2026-0207", "WO-G-2026-0101", "WO-D-2026-0211"
DRIVERS = {  # driver name -> {entity code: value}
    "Employees": {e[0]: e[8] for e in RECEIVING if e[8]},
    "Employees (operating companies)": {e[0]: e[8] for e in OPCOS},
    "Workstations": {e[0]: e[9] for e in RECEIVING if e[9]},
    "Retail customers": {e[0]: e[7] for e in OPCOS},
    "Transmission pole miles": {e[0]: e[10] for e in RECEIVING if e[10]},
    "GL transactions": GLT,
    "Equal share": {e[0]: 1 for e in RECEIVING},
    "Network bytes by attachment (TB)": NET_TB,
    "Level of construction, Transmission": LCT,
    "Level of construction, Distribution": LCD,
    "Share of tagged cloud consumption": TAGGED_USE,
    f"{WO_T} benefiting (pole miles)": {"TNO": 3100, "TCE": 2700},
    f"{WO_D} benefiting (retail customers)": {"NOR": 880000, "CEN": 610000, "MET": 1320000},
    f"{WO_G} benefiting (GL transactions)": {k: v for k, v in GLT.items() if k != "CES"},
    f"{WO_D2} benefiting (employees)": {e[0]: e[8] for e in OPCOS},
}
FACTORS = [  # code, driver, refresh, used for, billing class, status, note
    ("F-EMP", "Employees", "Monthly", "Data & Analytics, Finance & Regulatory", "457.2", "approved", None),
    ("F-EMP-OPCO", "Employees (operating companies)", "Monthly", "Field Work Management", "457.2", "approved", None),
    ("F-WKS", "Workstations", "Monthly", "End-User Computing, Cyber, Copilot seats with no cost center", "457.2", "approved", None),
    ("F-CUS", "Retail customers", "Monthly", "Customer Operations, Distribution Operations, AMI", "457.2", "approved", None),
    ("F-TPM", "Transmission pole miles", "Annual", "Transmission cost centers", "457.2", "approved", None),
    ("F-GLT", "GL transactions", "Monthly", "Enterprise Applications", "457.2", "approved", None),
    ("F-DIR", None, "-", "Direct charges (100% to one company)", "457.1", "approved", None),
    ("F-EQ", "Equal share", "-", "Network & Connectivity until the bytes driver is approved", "457.2", "approved", None),
    ("F-NET", "Network bytes by attachment (TB)", "Monthly", "Network & Connectivity (AP-NET v2)", "457.2", "awaiting approval",
     "Submitted with AP-NET v2; applies once a second approver signs"),
    ("F-LCT", "Level of construction, Transmission", "Quarterly", "Capital overheads (Transmission)", "457.2", "approved", None),
    ("F-LCD", "Level of construction, Distribution", "Quarterly", "Capital overheads (Distribution)", "457.2", "approved", None),
    ("F-USE", "Share of tagged cloud consumption", "Monthly", "Shared platforms", "457.2", "awaiting approval",
     "New factor: needs Regulated Accounting approval and Schedule XXI disclosure"),
    ("F-WO-T-0142", f"{WO_T} benefiting (pole miles)", "Per work order", f"{WO_T} benefiting companies", "457.1", "approved",
     "Work-order benefiting location"),
    ("F-WO-D-0207", f"{WO_D} benefiting (retail customers)", "Per work order", f"{WO_D} benefiting companies", "457.1", "approved",
     "Work-order benefiting location"),
    ("F-WO-G-0101", f"{WO_G} benefiting (GL transactions)", "Per work order", f"{WO_G} benefiting companies", "457.1", "approved",
     "Work-order benefiting location"),
    ("F-WO-D-0211", f"{WO_D2} benefiting (employees)", "Per work order", f"{WO_D2} benefiting companies", "457.1", "approved",
     "Work-order benefiting location"),
]
FACTOR_OF = {f[1]: f[0] for f in FACTORS if f[1]}
POLICIES = [  # id, name, match column, match value, driver, fixed entity code, priority
    ("AP-DIRECT", "Direct charge: an opco tag naming one company bills 100% to that company (F-DIR, 457.1)", "opco", "(tag)",
     None, None, 1),
    ("AP-WO-T-0142", f"{WO_T}: transmission outage scheduling, benefiting companies by pole miles", "Application",
     "transmission-outage-scheduling", f"{WO_T} benefiting (pole miles)", None, 3),
    ("AP-WO-D-0207", f"{WO_D}: vegetation analytics, benefiting companies by retail customers", "Application",
     "vegetation-analytics", f"{WO_D} benefiting (retail customers)", None, 3),
    ("AP-WO-G-0101", f"{WO_G}: ERP upgrade, regulated companies by GL transactions", "Application", "erp-upgrade",
     f"{WO_G} benefiting (GL transactions)", None, 3),
    ("AP-WO-D-0211", f"{WO_D2}: work-management implementation, operating companies by employees", "Application", "work-management",
     f"{WO_D2} benefiting (employees)", None, 3),
    ("AP-CES", "Competitive Energy Services work to the affiliate", "CostCenter", "Competitive Energy Services", None, "CES", 10),
    ("AP-TRANS", "Transmission Operations by transmission pole miles", "CostCenter", "Transmission Operations", "Transmission pole miles", None, 20),
    ("AP-TRANS-PLAN", "Transmission Planning by transmission pole miles", "CostCenter", "Transmission Planning", "Transmission pole miles", None, 20),
    ("AP-TRANS-ASSET", "Transmission Asset Management by transmission pole miles", "CostCenter", "Transmission Asset Management",
     "Transmission pole miles", None, 20),
    ("AP-DIST", "Distribution Operations by retail customers", "CostCenter", "Distribution Operations", "Retail customers", None, 20),
    ("AP-AMI", "Grid Modernization (AMI) by retail customers", "CostCenter", "Grid Modernization (AMI)", "Retail customers", None, 20),
    ("AP-FWM", "Field Work Management by operating-company employees", "CostCenter", "Field Work Management",
     "Employees (operating companies)", None, 20),
    ("AP-CUST", "Customer Operations by retail customers", "CostCenter", "Customer Operations", "Retail customers", None, 20),
    ("AP-ERP", "Enterprise Applications by GL transactions", "CostCenter", "Enterprise Applications", "GL transactions", None, 30),
    ("AP-EUC", "End-User Computing by workstations", "CostCenter", "End-User Computing", "Workstations", None, 30),
    ("AP-CYBER", "Cyber Security Operations by workstations", "CostCenter", "Cyber Security Operations", "Workstations", None, 30),
    ("AP-NET", "Network & Connectivity by equal share", "CostCenter", "Network & Connectivity", "Equal share", None, 30),
    ("AP-DEFAULT", "Data & Analytics Platform (multi-function) by employees", "CostCenter", "Data & Analytics Platform", "Employees", None, 40),
    ("AP-FINREG", "Finance & Regulatory Services by employees", "CostCenter", "Finance & Regulatory Services", "Employees", None, 40),
]
PENDING_POLICY = ("AP-NET", 2, "Network & Connectivity by bytes by attachment", "CostCenter", "Network & Connectivity",
                  "Network bytes by attachment (TB)", None, 30)

# ============================================================================================ FERC accounts (6.9)
O898 = "Order No. 898 (RM21-11-000) ¶112-113"
P101 = "18 CFR Part 101"
P367 = "18 CFR Part 367"
FROM_898, BEFORE_898 = D(2025, 1, 1), D(2024, 12, 31)
FERC_ACCOUNTS = [  # account, title, part, type, function, rules, from, to, citation, flag, subaccount, labor only, note
    ("101", "Electric plant in service", "101", "Plant", "ANY", "R2", None, None, P101, "Regulation", "10100", False, None),
    ("106", "Completed construction not classified, Electric", "101", "Plant", "ANY", "R2", None, None, P101, "Regulation", "10600", False, None),
    ("107", "Construction work in progress, Electric", "101/367", "Plant", "ANY", "R1", None, None,
     "Acct 107; Electric Plant Instructions 3 and 4; AI20-1-000", "Regulation", "10700", False, "Open work order required"),
    ("108", "Accumulated provision for depreciation of electric utility plant", "101", "Contra-plant", "ANY", "R3", None, None,
     P101, "Regulation", "10800", False, None),
    ("165", "Prepayments", "101/367", "Balance sheet", "ANY", "R9", None, None, "Acct 165", "Regulation", "16500", False,
     "Annual seats, upfront commitments; amortized monthly to the consuming account"),
    ("182.3", "Other regulatory assets", "101", "Balance sheet", "ANY", "R11", None, None, "Acct 182.3", "Regulation", "18230", False,
     "Only with a commission order on file"),
    ("186", "Miscellaneous deferred debits", "101", "Balance sheet", "ANY", "R11", None, None, "Acct 186", "POLICY", "18600", False,
     "Order pending"),
    ("303", "Miscellaneous intangible plant (software before Order No. 898)", "101", "Plant", "ANY", "Legacy", None, BEFORE_898,
     "Acct 303; Order No. 898 ¶111", "Regulation", "30300", False, "Balances without a function may stay (¶111)"),
    ("303", "Miscellaneous intangible property (service company)", "367", "Plant", "SERVICE_COMPANY", "R16", None, None,
     "Part 367 Acct 303; Order No. 898 ¶116", "Regulation", "30300", False, "Part 367 has no computer accounts"),
    ("351.1", "Computer hardware (Transmission)", "101", "Plant", "T", "R2", FROM_898, None, O898, "Regulation", "35110", False, None),
    ("351.2", "Computer software (Transmission)", "101", "Plant", "T", "R2", FROM_898, None, O898, "Regulation", "35120", False, None),
    ("351.3", "Communication equipment (Transmission)", "101", "Plant", "T", "R2", FROM_898, None, O898, "Regulation", "35130", False, None),
    ("363.1", "Computer hardware (Distribution)", "101", "Plant", "D", "R2", FROM_898, None, O898, "Regulation", "36310", False, None),
    ("363.2", "Computer software (Distribution)", "101", "Plant", "D", "R2", FROM_898, None, O898, "Regulation", "36320", False, None),
    ("363.3", "Communication equipment (Distribution)", "101", "Plant", "D", "R2", FROM_898, None, O898, "Regulation", "36330", False, None),
    ("397.1", "Computer hardware (General)", "101", "Plant", "GENERAL", "R2", FROM_898, None, O898, "Regulation", "39710", False, None),
    ("397.2", "Computer software (General or multi-function)", "101", "Plant", "GENERAL", "R2", FROM_898, None, O898, "Regulation", "39720",
     False, "Multi-function software default (¶112)"),
    ("397.3", "Communication equipment (General)", "101", "Plant", "GENERAL", "R2", FROM_898, None, O898, "Regulation", "39730", False, None),
    ("403", "Depreciation expense", "101", "Expense", "ANY", "R3", FROM_898, None, "Order No. 898 ¶112, ¶137", "Regulation", "40300", False,
     "Existing rates continue until the next depreciation case"),
    ("404", "Amortization of limited-term electric plant (software before Order No. 898)", "101", "Expense", "ANY", "R3", None, BEFORE_898,
     "Acct 404; Order No. 898 ¶112", "Regulation", "40400", False, None),
    ("408.1", "Taxes other than income taxes, utility operating income", "101", "Expense", "ANY", "-", None, None, P101, "Regulation",
     "40810", False, "Not used for cloud tax lines: tax follows the underlying line (R12)"),
    ("426.5", "Other deductions", "101", "Below the line", "ANY", "R15", None, None, "Acct 426.5", "Regulation", "42650", False,
     "Abandoned or impaired capitalized software"),
    ("457.1", "Direct costs charged", "367", "Billing", "SERVICE_COMPANY", "R16", None, None, "18 CFR 367.4570-367.4571", "Regulation",
     "45710", False, None),
    ("457.2", "Indirect costs charged", "367", "Billing", "SERVICE_COMPANY", "R16", None, None, "18 CFR 367.4572", "Regulation", "45720",
     False, None),
    ("457.3", "Compensation for use of capital", "367", "Billing", "SERVICE_COMPANY", "R16", None, None, "18 CFR 367.4573", "Regulation",
     "45730", False, None),
    ("560", "Operation supervision and engineering (Transmission)", "101", "O&M", "T", "R5", None, None, P101, "POLICY", "56000", False, None),
    ("561.1", "Load dispatch, reliability", "101", "O&M", "T", "R5", None, None, P101, "POLICY", "56110", False, None),
    ("561.2", "Load dispatch, monitor and operate transmission system", "101", "O&M", "T", "R5", None, None, P101, "POLICY", "56120", False, None),
    ("561.3", "Load dispatch, transmission service and scheduling", "101", "O&M", "T", "R5", None, None, P101, "POLICY", "56130", False, None),
    ("561.4", "Scheduling, system control and dispatch services", "101", "O&M", "T", "-", None, None, P101, "Regulation", "56140", False,
     "RTO-billed services only; never IT spend"),
    ("561.5", "Reliability, planning and standards development", "101", "O&M", "T", "R5", None, None, P101, "POLICY", "56150", False,
     "Office costs go to A&G (561.5 Note)"),
    ("561.6", "Transmission service studies", "101", "O&M", "T", "R5", None, None, P101, "POLICY", "56160", False, None),
    ("561.7", "Generation interconnection studies", "101", "O&M", "T", "R5", None, None, P101, "POLICY", "56170", False, None),
    ("561.8", "Reliability, planning and standards development services", "101", "O&M", "T", "-", None, None, P101, "Regulation", "56180",
     False, "RTO-billed services only; never IT spend"),
    ("566", "Miscellaneous transmission expenses", "101", "O&M", "T", "R5, R8", None, None, P101, "POLICY", "56600", False,
     "Includes communication service"),
    ("569.1", "Maintenance of computer hardware (Transmission)", "101", "O&M", "T", "R4", FROM_898, None, P101, "Regulation", "56910", False, None),
    ("569.2", "Maintenance of computer software (Transmission)", "101", "O&M", "T", "R4", FROM_898, None, P101, "Regulation", "56920", False, None),
    ("580", "Operation supervision and engineering (Distribution)", "101", "O&M", "D", "R6", None, None, P101, "POLICY", "58000", False, None),
    ("581", "Load dispatching (Distribution)", "101", "O&M", "D", "R6", None, None, P101, "POLICY", "58100", False, None),
    ("588", "Miscellaneous distribution expenses", "101", "O&M", "D", "R6, R8", None, None, P101, "POLICY", "58800", False,
     "Includes communication service"),
    ("592.2", "Maintenance of computer hardware (Distribution)", "101", "O&M", "D", "R4", FROM_898, None, P101, "Regulation", "59220", False, None),
    ("592.3", "Maintenance of computer software (Distribution)", "101", "O&M", "D", "R4", FROM_898, None, P101, "Regulation", "59230", False, None),
    ("903", "Customer records and collection expenses", "101", "O&M", "CUST", "R7", None, None, P101, "POLICY", "90300", False,
     "Customer accounts placement to confirm with regulatory accounting"),
    ("920", "Administrative and general salaries", "101/367", "O&M (labor only)", "AG", "R11 (own staff)", None, None, "Acct 920", "Regulation",
     "92000", True, "Compensation only: a non-labor line here is labor_account_on_non_labor"),
    ("921", "Office supplies and expenses", "101/367", "O&M", "AG", "R7, R8", None, None, "Acct 921", "POLICY", "92100", False, None),
    ("922", "Administrative expenses transferred, credit", "101/367", "O&M", "AG", "R1a", None, None, "Acct 922", "Regulation", "92200", False, None),
    ("923", "Outside services employed", "101/367", "O&M", "AG", "R7, R11", None, None, "Acct 923", "POLICY", "92300", False, None),
    ("930.2", "Miscellaneous general expenses", "101/367", "O&M", "AG", "R7, R8", None, None, "Acct 930.2", "POLICY", "93020", False, None),
    ("931", "Rents (computers and data processing)", "101/367", "O&M", "AG", "R16", None, None, "Acct 931", "POLICY", "93100", False, None),
    ("935", "Maintenance of general plant (service company)", "367", "O&M", "SERVICE_COMPANY", "R16", None, None, "Part 367 Acct 935",
     "Regulation", "93500", False, None),
    ("935.1", "Maintenance of computer hardware (General)", "101", "O&M", "AG", "R4", FROM_898, None, P101, "Regulation", "93510", False,
     "Hardware maintenance, not cloud usage"),
    ("935.2", "Maintenance of computer software (General)", "101", "O&M", "AG", "R4", FROM_898, None, P101, "Regulation", "93520", False,
     "License renewals, update services and support"),
]

# Rules R1-R17 (brief 3.3): id, step, pattern, treatment, T, D, General, service company, condition, citation, basis, policy element
RULES = [
    ("R1", 3, "Implementation: configuration, coding, integration, customization, dev-environment consumption tagged to a work order",
     "Dr 107", "107", "107", "107", "107", "Work order open; probable-to-complete date set; not training, data conversion or planning",
     "AI20-1-000 Q1; ASC 350-40-25-18; Acct 107; Electric Plant Instructions 3, 4", "Regulation", "Threshold date"),
    ("R1a", 3, "Own corporate IT or A&G staff time on R1", "Dr 107 / Cr 922", "107", "107", "107", None, "Time records (General Instruction 9)",
     "Acct 922; Electric Plant Instruction 4", "Regulation", None),
    ("R1b", 3, "Service-company labor or contract work on R1", "Operating company Dr 107; service company bills 457.1", "107", "107", "107",
     "457.1", "Work order has a benefiting location", "18 CFR 367.27", "Regulation", None),
    ("R2", 3, "Module ready for its intended use", "Dr 106 then 101", "351.2", "363.2", "397.2", "303",
     "Substantial testing complete; predominant-use reason recorded", "Order No. 898 ¶112-113; ASC 350-40-35-17", "Regulation",
     "Choice of function"),
    ("R3", 3, "Depreciation", "Dr 403 / Cr 108 (303 balances before 2025: 404 / 111)", "403", "403", "403", "404",
     "Approved rate; existing rates continue until the next depreciation case", "Order No. 898 ¶111-112, ¶137", "Regulation",
     "Life versus hosting term"),
    ("R4", 4, "License renewal, update service or vendor support", "Dr maintenance of computer software", "569.2", "592.3", "935.2", "935",
     "Cloud provider support plans counted here", "Acct 569.2, 592.3, 935.2", "Regulation", "Support plans as maintenance"),
    ("R5", 5, "Recurring usage or subscription serving Transmission", "Dr transmission O&M", "566", None, None, None,
     "561.1-561.3, 561.5-561.7 for reliability, monitoring, scheduling or studies; 560 engineering; never 561.4 or 561.8",
     "AI20-1-000; Acct 560, 561.x, 566; Operating Expense Instruction 3.A", "POLICY", None),
    ("R6", 5, "Recurring usage or subscription serving Distribution", "Dr distribution O&M", None, "588", None, None,
     "Or a specific 58x account (581 load dispatching)", "Acct 588", "POLICY", None),
    ("R7", 5, "Recurring usage or subscription serving Corporate IT or A&G (ERP, M365, Copilot seats, general AI tokens)",
     "Dr A&G O&M", None, None, "921", "921", "921 general department; 930.2 utility-wide; 923 managed service; 935.2 license renewal",
     "Acct 921, 923, 930.2, 935.2", "POLICY", None),
    ("R8", 5, "Networking and transport (Direct Connect, ExpressRoute, egress)", "Dr communication service", "566", "588", "921", "921",
     "Communication service", "Acct 566 item 9; Acct 588 item 10; Acct 921; Acct 930.2", "Regulation", "A&G account choice"),
    ("R9", 2, "Upfront commitment or annual prepayment (one-time purchase, reservation principal, annual seats)",
     "Dr 165, then monthly to the consuming account", "165", "165", "165", "165", "Amortized through EffectiveCost",
     "Acct 165", "Regulation", None),
    ("R10", 3, "Purchased license that passes ASC 350-40-15-4A", "Capitalize under R1/R2 above the property-unit threshold; else R4-R7",
     "351.2", "363.2", "397.2", "303", "Property units listing (Electric Plant Instruction 10)", "ASC 350-40-15-4A", "Regulation",
     "Threshold set by the client"),
    ("R11", 6, "Implementation costs that cannot be capitalized (planning, training, data conversion, hypercare)",
     "Functional O&M (own staff 920, consultants 923); with an order Dr 182.3", "923", "923", "923", "923",
     "Order reference required for 182.3; order pending: 186", "ASC 350-40-25-4/-5; Acct 182.3", "Regulation", None),
    ("R12", 1, "Tax lines", "Same account as the underlying line", "same as line", "same as line", "same as line", "same as line",
     "Capitalized with R1 lines; not 408.1", "Part 101 special instructions to 408.1/408.2, notes 2-3; Electric Plant Instruction 9.A",
     "Regulation", "Tax on services"),
    ("R13", 1, "Credits, refunds, SLA credits, corrections", "Reverse to the original account; after close, adjust in the next open period",
     "same as line", "same as line", "same as line", "same as line", "Sign plus ChargeClass Correction, not ChargeCategory alone",
     "FOCUS 1.0 specification", "POLICY", None),
    ("R14", 6, "Dedicated host or identified asset over 12 months that qualifies as a lease", "Optional right-of-use asset 101.1 with 227/243",
     None, None, None, None, "Lease test per contract; expense account and amount unchanged", "AI19-1-000", "Regulation", "Optional"),
    ("R15", 6, "Capitalized software abandoned or impaired", "Dr 426.5 (182.2 only if FERC authorizes)", "426.5", "426.5", "426.5", "426.5",
     "State recovery to 182.3; disallowances 426.5 or 435", "Acct 303.B; 182.2; 182.3", "Regulation", None),
    ("R16", 6, "Service company (Part 367)", "By nature (921, 923, 930.2, 931, 935); bill split 457.1 / 457.2 / 457.3", None, None, None,
     "921, 923, 930.2, 931, 935", "Each bill line carries the receiving company's Part 101 account", "18 CFR 367.25-367.30; Form 60 Sch. XXI",
     "Regulation", None),
    ("R17", 5, "Copilot seats, Copilot Credits, AI tokens", "Same as R5-R7 by the function of the user or workload; annual seats via R9",
     "566", "588", "921", "921", "Tokens inside a capital project: expensed pending policy", "ASU 2025-06", "POLICY", None),
]
EXCEPTION_CODES = [
    ("no_opco", "Operating company not resolved", "Corporate IT Finance / Cloud CoE", "Tag the account or approve a rule; history restated by rule"),
    ("no_ferc_function", "FERC function not resolved", "Regulatory accounting", "Tag ferc_function or fix the cost center"),
    ("no_work_order", "Capitalized line without a work order", "Regulatory accounting", "Open or link a work order; held out of 107 until then"),
    ("capitalizable_without_threshold_date", "Work order has no probable-to-complete date", "Regulatory accounting",
     "Record the threshold date; held out of 107 until then"),
    ("regulatory_asset_without_order", "182.3 treatment requested with no order on file", "Rates & regulatory", "Expensed until an order is on file"),
    ("multi_function_default_397.2", "Multi-function default applied", "Regulatory accounting", "Record the predominant-use reason"),
    ("unmapped_ferc_account", "No account matched", "Regulatory accounting", "Add an account map row"),
    ("labor_account_on_non_labor", "A non-labor line hit 920 (guard)", "Controller", "Blocked; map to functional O&M"),
    ("cip_review_pending", "Resource names were pseudonymized and await the client's CIP review", "NERC CIP compliance",
     "Client CIP-011 R1.1 classification"),
]

# First-cut account map (ref_gl_account_map): first match by Priority. Today's allocator matches ChargeCategory, ServiceCategory,
# ServiceName or CostCenter (plus EntityType); tax and credit lines carry their account's tags, so they land with the line they
# belong to (R12, R13). priority, column, value, entity type, function, account, title, rule, citation, flag
SVC_BY_NATURE = [("Compute", "931", "Rents (computers and data processing)"), ("Storage", "931", "Rents (computers and data processing)"),
                 ("Databases", "931", "Rents (computers and data processing)"), ("Networking", "921", "Office supplies and expenses"),
                 ("Analytics", "931", "Rents (computers and data processing)"), ("AI and Machine Learning", "923", "Outside services employed"),
                 ("Security", "921", "Office supplies and expenses"), ("Management and Governance", "921", "Office supplies and expenses"),
                 ("Business Applications", "921", "Office supplies and expenses"), ("Other", "930.2", "Miscellaneous general expenses"),
                 ("Developer Tools", "921", "Office supplies and expenses"), ("Integration", "931", "Rents (computers and data processing)"),
                 ("Web", "931", "Rents (computers and data processing)")]
AFFILIATE = ("NONUTIL", "Non-utility affiliate (no FERC account)")
CC_ACCOUNTS = [  # cost center, function label, account, title, rule
    ("Transmission Operations", "Transmission", "561.2", "Load dispatch, monitor and operate transmission system", "R5"),
    ("Transmission Planning", "Transmission", "561.5", "Reliability, planning and standards development", "R5"),
    ("Transmission Asset Management", "Transmission", "566", "Miscellaneous transmission expenses", "R5"),
    ("Distribution Operations", "Distribution", "588", "Miscellaneous distribution expenses", "R6"),
    ("Grid Modernization (AMI)", "Distribution", "588", "Miscellaneous distribution expenses", "R6"),
    ("Field Work Management", "Distribution", "588", "Miscellaneous distribution expenses", "R6"),
    ("Customer Operations", "Customer accounts", "903", "Customer records and collection expenses", "R7"),
    ("Enterprise Applications", "Administrative and general", "921", "Office supplies and expenses", "R7"),
    ("End-User Computing", "Administrative and general", "921", "Office supplies and expenses", "R7"),
    ("Data & Analytics Platform", "Administrative and general", "930.2", "Miscellaneous general expenses", "R7"),
    ("Cyber Security Operations", "Administrative and general", "921", "Office supplies and expenses", "R7"),
    ("Network & Connectivity", "Administrative and general", "921", "Office supplies and expenses (communication service)", "R8"),
    ("Finance & Regulatory Services", "Administrative and general", "921", "Office supplies and expenses", "R7"),
]
SUPPORT_BY_FUNCTION = [  # R4: support plans as maintenance of computer software, by the function of the supported usage
    ("T", "Transmission", "569.2", "Maintenance of computer software (Transmission)"),
    ("D", "Distribution", "592.3", "Maintenance of computer software (Distribution)"),
]
FUNCTION_ACCOUNTS = [  # function code, label, account, title, rule: lines whose cost center has no map row (R5-R7 by function)
    ("T", "Transmission", "566", "Miscellaneous transmission expenses", "R5"),
    ("D", "Distribution", "588", "Miscellaneous distribution expenses", "R6"),
    ("CUST", "Customer accounts", "903", "Customer records and collection expenses", "R7"),
    ("AG", "Administrative and general", "921", "Office supplies and expenses", "R7"),
    ("GENERAL", "Administrative and general", "930.2", "Miscellaneous general expenses", "R7"),
]
SERVICE_ACCOUNTS = [  # service name, function label, account, title, rule, flag
    ("AWS Support (Enterprise)", "Administrative and general", "935.2", "Maintenance of computer software (support plan, multi-function)", "R4", "POLICY"),
    ("Microsoft 365 Copilot", "Administrative and general", "921", "Office supplies and expenses", "R7", "POLICY"),
    ("GitHub Copilot", "Administrative and general", "921", "Office supplies and expenses", "R7", "POLICY"),
    ("Claude Enterprise", "Administrative and general", "921", "Office supplies and expenses", "R7", "POLICY"),
    ("ERP upgrade integration services", "Administrative and general", "923", "Outside services employed", "R7", "POLICY"),
    ("ERP training and data conversion", "Administrative and general", "923", "Outside services employed", "R11", "Regulation"),
    ("Work management implementation services", "Administrative and general", "923", "Outside services employed", "R11", "Regulation"),
]
RULE_CITE = {r[0]: r[9] for r in RULES}
RULE_BASIS = {r[0]: r[10] for r in RULES}


def account_map():
    """priority, column, value, entity type, function label, account, title, rule, citation, flag, FunctionCode (NULL = any)."""
    rows = []
    for cat, acct, title in SVC_BY_NATURE:
        rows.append((1, "ServiceCategory", cat, "service_company", "Service company (by nature)", acct, title, "R16", RULE_CITE["R16"], "POLICY",
                     None))
    for cc in ("Usage", "Purchase", "Tax", "Credit", "Adjustment"):
        rows.append((2, "ChargeCategory", cc, "affiliate", "Non-utility", AFFILIATE[0], AFFILIATE[1], "R16", RULE_CITE["R16"], "Regulation",
                     "NONUTIL"))
    for code, fn, acct, title in SUPPORT_BY_FUNCTION:
        rows.append((9, "ServiceName", "AWS Support (Enterprise)", None, fn, acct, title, "R4", RULE_CITE["R4"], RULE_BASIS["R4"], code))
    for svc, fn, acct, title, rule, flag in SERVICE_ACCOUNTS:
        rows.append((10, "ServiceName", svc, None, fn, acct, title, rule, RULE_CITE[rule], flag, None))
    for cc, fn, acct, title, rule in CC_ACCOUNTS:
        rows.append((20, "CostCenter", cc, None, fn, acct, title, rule, RULE_CITE[rule], RULE_BASIS[rule], None))
    for code, fn, acct, title, rule in FUNCTION_ACCOUNTS:
        rows.append((90, "FunctionCode", code, None, fn, acct, title, rule, RULE_CITE[rule], RULE_BASIS[rule], code))
    return rows


RULE_CHANGES = [
    ("RC-2026-014", "R7", "CostCenter", "End-User Computing", "920",
     "Book End-User Computing cloud subscriptions to A&G salaries", "it.finance@company.example", T(2026, 10, 14, 15, 2),
     "blocked", "920 guard (account 920 is A&G salaries)", "labor_account_on_non_labor"),
]

# ============================================================================================ work orders (6.7)
WORK_ORDERS = [  # id, module, description, app, cost center, benefiting, basis, function, plant account, cap start, ready, share,
                 # hosting months, depreciation %, AFUDC, status, CWIP before July, owner
    (WO_G, "Module 1", "ERP upgrade led by the service company and billed to operating companies", "erp-upgrade", "Enterprise Applications",
     "NOR,CEN,VAL,SOU,MET,PLA,TNO,TCE,TWE", "GL transactions", "GENERAL", "397.2", D(2026, 3, 15), D(2026, 10, 1), 0.45, None, 10.0,
     True, "partly_in_service", 91000.0, "Enterprise Applications"),
    (WO_G, "Module 2", "ERP upgrade led by the service company and billed to operating companies", "erp-upgrade", "Enterprise Applications",
     "NOR,CEN,VAL,SOU,MET,PLA,TNO,TCE,TWE", "GL transactions", "GENERAL", "397.2", D(2026, 3, 15), D(2027, 4, 1), 0.55, None, 10.0,
     True, "open", 0.0, "Enterprise Applications"),
    (WO_T, "Module 1", "Transmission outage scheduling SaaS implementation", "transmission-outage-scheduling", "Transmission Operations",
     "TNO,TCE", "Transmission pole miles", "T", "351.2", D(2026, 2, 1), D(2026, 9, 1), 1.0, 36, 20.0, True, "in_service", 96400.0,
     "Transmission Operations"),
    (WO_D, "Module 1", "Vegetation analytics platform", "vegetation-analytics", "Distribution Operations", "NOR,CEN,MET",
     "Retail customers", "D", "363.2", D(2026, 5, 1), None, 1.0, None, 20.0, True, "open", 52000.0, "Distribution Operations"),
    (WO_D2, "Module 1", "Work-management SaaS implementation", "work-management", "Field Work Management", "NOR,CEN,VAL,SOU,MET,PLA",
     "Employees (operating companies)", "D", "363.2", None, None, 1.0, 60, None, None, "open", 0.0, "Field Work Management"),
]

# ============================================================================================ tags (6.6)
REQUIRED_TAGS = [
    ("opco", "SVC, NOR, CEN, VAL, SOU, MET, PLA, TNO, TCE, TWE, CES, SHARED", "required", "All usage",
     "Legal entity / GL business unit / affiliate code", "Account or subscription default, approved rule, suspense"),
    ("business_unit", "TRANSMISSION, DISTRIBUTION, CORPORATE_IT, SHARED_SERVICES, NON_UTILITY", "required", "All",
     "Management business unit", "Cost-center register"),
    ("cost_center", "Values in ref_cost_center", "required", "All", "Department ID", "Approved rule, suspense"),
    ("ferc_function", "T, D, CUST, AG, GENERAL, NONUTIL", "required", "All usage", "FERC function",
     "Cost-center register; multi-function: GENERAL with an exception"),
    ("work_order", "WO-<T/D/G>-YYYY-NNNN, open in ref_work_order", "conditional", "capex = true",
     "Project ID / work order and benefiting location", "None: no_work_order"),
    ("capex", "true, false", "conditional", "Build environments, implementation services", "Cost class", "false"),
    ("application", "Names on the CMDB list", "required", "All", "Activity / product", "-"),
    ("environment", "prod, nonprod, sandbox", "required", "All", "-", "-"),
    ("data_class", "public, internal, confidential, ceii", "optional", "-", "Masking", "-"),
]
DENYLIST = [("esp", "drop at ingest", "Electronic security perimeter names"), ("bcs", "drop at ingest", "BES Cyber System names"),
            ("substation", "drop at ingest", "Facility names"), ("ip", "drop at ingest", "Addresses"),
            ("hostname", "drop at ingest", "Host names"), ("topology", "drop at ingest", "Network topology"),
            ("resourceTags/, accountTag/, costCategory/, userAttribute/", "normalize", "AWS key prefixes removed; keys matched case-insensitively")]
TAG_VALUE_MAP = [
    ("cost_center", "Distrib", "Distribution Operations", "normalize", "Abbreviation"),
    ("cost_center", "T&D", None, "queue", "Two functions: cannot pick one"),
    ("cost_center", "Work Management (retired 2025)", "Field Work Management", "normalize", "Retired cost center"),
    ("opco", "nor", "NOR", "normalize", "Lowercase entity code"),
]

# ============================================================================================ rates and regulatory
FILINGS = [  # docket, jurisdiction, entity code, test start, test end, rate year start, rate year end, filing date, status, pre-tax ROR %, authority
    ("SA-26-0418-EL", "State A", "NOR", T(2026, 7, 1), T(2026, 10, 1), T(2027, 1, 1), T(2028, 1, 1), T(2026, 12, 15), "In preparation",
     None, "State A Public Utility Commission"),
    ("SF-26-0072-RC", "State F", "SOU", T(2026, 7, 1), T(2026, 10, 1), T(2027, 4, 1), T(2028, 4, 1), T(2026, 11, 20), "Filed",
     None, "State F Public Utility Commission"),
    ("SC-27-0015-MYP", "State C", "CEN", T(2026, 7, 1), T(2026, 10, 1), T(2027, 1, 1), T(2028, 1, 1), T(2027, 2, 1), "Planned",
     None, "State C Public Utility Commission"),
    ("ER27-118-000", "FERC", "TNO", T(2026, 7, 1), T(2026, 10, 1), T(2027, 1, 1), T(2028, 1, 1), T(2026, 12, 1), "In preparation",
     None, "Federal Energy Regulatory Commission"),
]
RATE_CASES = {  # docket -> type, ROE %, equity ratio, cost of debt %, rate years
    "SA-26-0418-EL": ("historical", 9.70, 0.52, 4.60, "2027"),
    "SF-26-0072-RC": ("historical", 9.65, 0.47, 4.85, "2027-2028"),
    "SC-27-0015-MYP": ("forecast", 9.80, 0.50, 4.70, "2027-2029"),
    "ER27-118-000": ("formula rate", 10.50, 0.55, 4.40, "annual update"),
}
TAX_RATE = 0.21
JURISDICTION_POLICY = [  # jurisdiction, status, deferral on file, reference
    ("State A", "claimed", True, "Order on file: cloud computing costs deferred to a regulatory asset over the contract or 62 months"),
    ("State B", "eligible", False, "No order on file; precedent Idaho PUC Order No. 34707"),
    ("State C", "eligible", False, "No order on file; request with the multi-year plan"),
    ("State D", "eligible", False, "No order on file"),
    ("State E", "ineligible", False, "Deferral request denied in 2025"),
    ("State F", "eligible", False, "No order on file"),
    ("State G", "eligible", False, "No order on file"),
    ("State H", "eligible", False, "No order on file"),
    ("State I", "eligible", False, "No order on file"),
    ("FERC", "not_applicable", False, "Formula rate: implementation costs follow AI20-1-000 to plant"),
    ("Non-jurisdictional", "not_applicable", False, "Non-utility affiliate"),
    ("Unassigned", "not_applicable", False, "Service company before billing"),
]
CLAUSES = {"vulnerability_disclosure": True, "incident_notification": True, "remote_access_controls": True, "software_integrity": True}
VENDORS = [  # vendor, CIP scoped, clauses, contract end, renewal, escalator %, annual value
    ("AWS", True, CLAUSES, T(2028, 6, 30), T(2028, 7, 1), 0.0, 6200000),
    ("Microsoft", True, {**CLAUSES, "software_integrity": False}, T(2028, 3, 31), T(2027, 3, 1), 4.0, 6900000),
    ("ServiceNow", True, {**CLAUSES, "incident_notification": False}, T(2026, 12, 31), T(2027, 1, 1), 5.0, 840000),
    ("GIS Platform Vendor", False, CLAUSES, T(2027, 5, 31), T(2027, 6, 1), 6.0, 270000),
    ("Historian Analytics Vendor", True, {**CLAUSES, "remote_access_controls": False}, T(2027, 3, 31), T(2027, 4, 1), 7.0, 180000),
    ("ERP Systems Integrator", False, CLAUSES, T(2027, 6, 30), T(2027, 7, 1), 3.0, 1400000),
    ("Work Management SaaS", False, CLAUSES, T(2027, 1, 31), T(2027, 2, 1), 8.0, 420000),
    ("Anthropic", False, CLAUSES, T(2027, 1, 1), T(2027, 1, 1), 0.0, 150000),
    ("Snowflake", False, CLAUSES, T(2027, 9, 30), T(2027, 10, 1), 6.0, 240000),
]
STORM_EVENT = (STORM[0], "September windstorm", STORM[1], STORM[2], "State A,State C,State H")
AI_USE = [  # application, use, grid-ops context, review status, approved on, owner
    ("interconnection-agent", "Agent that prepares interconnection queue inputs", True, "Approved", D(2026, 6, 15), "Transmission Planning"),
    ("loadflow-surrogate", "Load-flow surrogate model on a GPU endpoint", True, "Pending", None, "Transmission Planning"),
    ("grid-model-assistant", "Answer planners' questions over transmission models", True, "Approved", D(2026, 5, 1), "Transmission Planning"),
    ("field-dispatch-assistant", "Suggest crew assignments during restoration", True, "Pending", None, "Field Work Management"),
    ("contact-center-summarization", "Summarize customer calls for the contact center", False, "Approved", D(2026, 2, 10), "Customer Operations"),
    ("regulatory-drafting", "Drafting assistant for regulatory filings", False, "Approved", D(2026, 4, 2), "Finance & Regulatory Services"),
    ("outage-faq-agent", "Answer customers' outage questions from approved content", False, "Approved", D(2026, 1, 10), "Customer Operations"),
]
CONTACTS, WORK_ORDERS_DONE, CREW_REPORTS = 410000, 62000, 28000

# ============================================================================================ the group's cloud estate
AWS_PAYER = ("aws-payer-7001", "Group AWS")
AZ_PROFILE = ("ms-mca-7001", "Group Azure")
M365_ACCOUNT = ("m365-7002", "Microsoft 365 annual")
SP_ARN = "arn:aws:savingsplans::410000000100:savingsplan/sp-group-compute-2026"
RI_ARN = "arn:aws:rds:us-east-1:410000000100:ri:ri-aurora-ami-2026"
AZ_RES = "/providers/Microsoft.Capacity/reservationOrders/ro-group-erp/reservations/res-erp-esv5-2026"
AWS_ACCOUNTS = {  # key -> (id, name, workload, cost center, owning entity code, function, environment, sensitivity, names masked)
    "payer": ("410000000100", "aws-payer", "Payer: commitments and support", None, "SVC", None, "prod", None, False),
    "aws-tplan": ("410000000101", "aws-tplan", "Power-flow and contingency analysis HPC", "Transmission Planning", "SVC", "T", "prod", "ceii", False),
    "aws-tops-hist": ("410000000102", "aws-tops-hist", "Read-only analytics replica of the transmission historian", "Transmission Operations",
                      "SVC", "T", "prod", "cip_related_workload", True),
    "aws-ix-studies": ("410000000103", "aws-ix-studies", "Interconnection agent and load-flow surrogate model", "Transmission Planning",
                       "SVC", "T", "prod", None, False),
    "aws-ami": ("410000000104", "aws-ami", "AMI meter data management", "Grid Modernization (AMI)", "SVC", "D", "prod", None, False),
    "aws-oms": ("410000000105", "aws-oms", "Outage management cloud tier and outage map", "Distribution Operations", "SVC", "D", "prod", None, False),
    "aws-veg-dev": ("410000000106", "aws-veg-dev", "Build of the vegetation analytics platform", "Distribution Operations", "SVC", "D", "nonprod", None, False),
    "aws-portal": ("410000000107", "aws-portal", "Customer self-service portal", "Customer Operations", "SVC", "CUST", "prod", None, False),
    "aws-data-landing": ("410000000108", "aws-data-landing", "Landing bucket for billing exports", "Data & Analytics Platform", "SVC", None,
                         "prod", None, False),
    "aws-network": ("410000000109", "aws-network", "Shared Direct Connect and Transit Gateway", "Network & Connectivity", "SVC", "AG", "prod",
                    "potential_bcsi_context", False),
    "aws-security": ("410000000110", "aws-security", "Central logging and SIEM, GuardDuty, Security Hub", "Cyber Security Operations", "SVC",
                     "AG", "prod", "potential_bcsi_context", False),
    "aws-sandbox-shared": ("410000000111", "aws-sandbox-shared", "Shared sandbox", None, "SVC", None, "sandbox", None, False),
    "aws-ces-analytics": ("410000000112", "aws-ces-analytics", "Trading analytics for the non-utility affiliate", None, "CES", None, "prod",
                          None, False),
}
AZ_SUBS = {  # key -> (subscription id, name, workload, cost center, owning entity code, function, environment, sensitivity)
    "az-erp": ("sub-7001-erp", "az-erp", "ERP production on Azure VMs", "Enterprise Applications", "SVC", "AG", "prod", None),
    "az-erp-upgrade": ("sub-7001-erpupg", "az-erp-upgrade", "ERP upgrade build and test environments", "Enterprise Applications", "SVC",
                       "GENERAL", "nonprod", None),
    "az-gis": ("sub-7001-gis", "az-gis", "GIS platform", "Distribution Operations", "SVC", "D", "prod", None),
    "az-wms": ("sub-7001-wms", "az-wms", "Work management integration", "Field Work Management", "SVC", "D", "prod", None),
    "az-tasset": ("sub-7001-tasset", "az-tasset", "Transmission asset-health analytics", "Transmission Asset Management", "SVC", "T", "prod", None),
    "az-toutage": ("sub-7001-toutage", "az-toutage", "Transmission outage scheduling app", "Transmission Operations", "SVC", "T", "prod", None),
    "az-aoai-contact": ("sub-7001-aoai-cc", "az-aoai-contact", "Contact-center call summarization", "Customer Operations", "SVC", "CUST", "prod", None),
    "az-aoai-reg": ("sub-7001-aoai-reg", "az-aoai-reg", "Drafting assistant for regulatory filings", "Finance & Regulatory Services", "SVC",
                    "AG", "prod", None),
    "az-copilot-studio": ("sub-7001-cps", "az-copilot-studio", "Copilot Studio pay-as-you-go credits", "Customer Operations", "SVC", "CUST",
                          "prod", None),
    "az-network": ("sub-7001-net", "az-network", "ExpressRoute", "Network & Connectivity", "SVC", "AG", "prod", "potential_bcsi_context"),
    "az-security": ("sub-7001-sec", "az-security", "Defender for Cloud and Sentinel", "Cyber Security Operations", "SVC", "AG", "prod",
                    "potential_bcsi_context"),
    "az-devtest": ("sub-7001-devtest", "az-devtest", "Non-production", None, "SVC", None, "nonprod", None),
    "az-legacy-migration": ("sub-7001-legacy", "az-legacy-migration", "Lift-and-shift landing zone", None, "SVC", None, "prod", None),
}
CC_FN = {cc: (bu, fn) for cc, bu, fn, _ in COST_CENTERS}


def tags(cc, app, env="prod", opco="SHARED", fn=None, extra=None, bu=None):
    t = {}
    if opco:
        t["opco"] = opco
    if cc:
        b, f = CC_FN.get(cc, (None, None))
        if bu or b:
            t["business_unit"] = bu or b
        t["cost_center"] = cc
        if fn is not False and (fn or f):
            t["ferc_function"] = fn or f
    t["application"] = app
    t["environment"] = env
    t["capex"] = "false"
    if extra:
        t.update(extra)
    return t


WO_TAGS = lambda wo: {"capex": "true", "work_order": wo, "implementation": "true"}   # noqa: E731  (valid work order: R1, class cwip)
# Components: (account key, resource, service, category, subcategory, unit, September effective $, SP covered $, RI covered $,
#              tags, flags)   flags: storm, rightsized date, gpu, nonprod hours, masked, idle_test
AWS_COMPONENTS = [
    ("aws-tplan", "tplan-hpc-fleet", "Amazon EC2", "Compute", "Virtual Machines", "Hours", 48000.00, 44000.00, 0,
     tags("Transmission Planning", "powerflow-hpc", extra={"data_class": "confidential"}), {}),
    ("aws-tplan", "tplan-spot-workers", "Amazon EC2", "Compute", "Virtual Machines", "Hours", 30000.00, 0, 0,
     tags("Transmission Planning", "powerflow-hpc", extra={"data_class": "confidential"}), {"spot": True}),
    ("aws-tplan", "tplan-fsx-lustre", "Amazon FSx", "Storage", "File Storage", "GB-Month", 21000.00, 0, 0,
     tags("Transmission Planning", "powerflow-hpc", extra={"data_class": "confidential"}), {}),
    ("aws-tplan", "tplan-case-archive", "Amazon S3", "Storage", "Object Storage", "GB-Month", 11000.00, 0, 0,
     tags("Transmission Planning", "powerflow-hpc", extra={"data_class": "confidential"}), {}),
    ("aws-tops-hist", "historian-replica-store", "Amazon S3", "Storage", "Object Storage", "GB-Month", 15200.00, 0, 0,
     tags("Transmission Operations", "historian-analytics", extra={"data_class": "confidential"}), {"masked": True}),
    ("aws-tops-hist", "historian-replica-queries", "Amazon Athena", "Analytics", "Analytics Platforms", "TB", 13300.00, 0, 0,
     tags("Transmission Operations", "historian-analytics", extra={"data_class": "confidential"}), {"masked": True}),
    ("aws-tops-hist", "historian-replica-etl", "AWS Glue", "Analytics", "Data Processing", "DPU-Hours", 9500.00, 0, 0,
     tags("Transmission Operations", "historian-analytics", extra={"data_class": "confidential"}), {"masked": True}),
    ("aws-ix-studies", "loadflow-gpu-endpoint", "Amazon SageMaker", "AI and Machine Learning", "Machine Learning", "Hours", 44000.00, 0, 0,
     tags("Transmission Planning", "loadflow-surrogate", extra={"ai_component": "gpu_hosting"}), {"gpu": True}),
    ("aws-ami", "ami-mdm-aurora", "Amazon Aurora", "Databases", "Relational Databases", "Hours", 25000.00, 0, 20000.00,
     tags("Grid Modernization (AMI)", "ami-mdm"), {}),
    ("aws-ami", "ami-ingest-kinesis", "Amazon Kinesis", "Analytics", "Streaming Analytics", "Hours", 12500.00, 0, 0,
     tags("Grid Modernization (AMI)", "ami-mdm"), {"rightsized": (D(2026, 9, 15), 0.70)}),
    ("aws-ami", "ami-interval-lake", "Amazon S3", "Storage", "Object Storage", "GB-Month", 12500.00, 0, 0,
     tags("Grid Modernization (AMI)", "ami-mdm"), {}),
    ("aws-oms", "oms-app-eks", "Amazon Elastic Kubernetes Service", "Compute", "Containers", "Hours", 14600.00, 6000.00, 0,
     tags("Distribution Operations", "outage-management"), {"storm": 3.0}),
    ("aws-oms", "outage-map-cdn", "Amazon CloudFront", "Networking", "Content Delivery", "GB", 6000.00, 0, 0,
     tags("Distribution Operations", "outage-map"), {"storm": 3.0}),
    ("aws-oms", "oms-reporting-ec2", "Amazon EC2", "Compute", "Virtual Machines", "Hours", 3000.00, 0, 0,
     tags("Distribution Operations", "outage-management"), {"rightsized": (D(2026, 9, 1), 0.55)}),
    ("aws-oms", "oms-batch-ec2", "Amazon EC2", "Compute", "Virtual Machines", "Hours", 3400.00, 0, 0,
     {**tags("Distribution Operations", "outage-management"), "cost_center": "Distrib"}, {"storm": 2.0}),
    ("aws-veg-dev", "veg-dev-ec2", "Amazon EC2", "Compute", "Virtual Machines", "Hours", 16000.00, 13000.00, 0,
     tags("Distribution Operations", "vegetation-analytics", env="nonprod", extra=WO_TAGS(WO_D)), {"hours": 24}),
    ("aws-veg-dev", "veg-test-ec2", "Amazon EC2", "Compute", "Virtual Machines", "Hours", 4000.00, 0, 0,
     tags("Distribution Operations", "vegetation-analytics", env="nonprod", extra=WO_TAGS(WO_D)), {"hours": 24}),
    ("aws-veg-dev", "veg-imagery-pipeline", "Amazon EMR", "Analytics", "Data Processing", "Hours", 6000.00, 0, 0,
     tags("Distribution Operations", "vegetation-analytics", env="nonprod", extra=WO_TAGS(WO_D)), {}),
    ("aws-veg-dev", "veg-imagery-raw", "Amazon S3", "Storage", "Object Storage", "GB-Month", 3000.00, 0, 0,
     tags("Distribution Operations", "vegetation-analytics", env="nonprod", extra=WO_TAGS(WO_D)), {}),
    ("aws-portal", "portal-eks", "Amazon Elastic Kubernetes Service", "Compute", "Containers", "Hours", 15000.00, 11000.00, 0,
     tags("Customer Operations", "customer-portal"), {}),
    ("aws-portal", "portal-db", "Amazon RDS", "Databases", "Relational Databases", "Hours", 7000.00, 0, 0,
     tags("Customer Operations", "customer-portal"), {}),
    ("aws-portal", "portal-search-ec2", "Amazon EC2", "Compute", "Virtual Machines", "Hours", 2000.00, 0, 0,
     {**tags("Customer Operations", "customer-portal"), "opco": "nor"}, {}),
    ("aws-data-landing", "billing-export-landing", "Amazon S3", "Storage", "Object Storage", "GB-Month", 5000.00, 0, 0,
     tags("Data & Analytics Platform", "billing-export-landing", fn=False), {}),
    ("aws-network", "dx-connection-01", "AWS Direct Connect", "Networking", "Network Connectivity", "Port-Hours", 21000.00, 0, 0,
     tags("Network & Connectivity", "core-network"), {}),
    ("aws-network", "tgw-core", "AWS Transit Gateway", "Networking", "Network Connectivity", "Attachment-Hours", 13000.00, 0, 0,
     tags("Network & Connectivity", "core-network"), {}),
    ("aws-security", "central-logging", "Amazon Security Lake", "Security", "Security Posture Management", "GB", 12000.00, 0, 0,
     tags("Cyber Security Operations", "security-monitoring"), {}),
    ("aws-security", "threat-detection", "Amazon GuardDuty", "Security", "Threat Detection", "Events", 9000.00, 0, 0,
     tags("Cyber Security Operations", "security-monitoring"), {}),
    ("aws-security", "security-hub", "AWS Security Hub", "Security", "Security Posture Management", "Checks", 6000.00, 0, 0,
     tags("Cyber Security Operations", "security-monitoring"), {}),
    ("aws-sandbox-shared", "sandbox-ec2-fleet", "Amazon EC2", "Compute", "Virtual Machines", "Hours", 34000.00, 21519.45, 0, None, {}),
    ("aws-sandbox-shared", "sandbox-scratch", "Amazon S3", "Storage", "Object Storage", "GB-Month", 8000.00, 0, 0, None, {}),
    ("aws-sandbox-shared", "sandbox-db", "Amazon RDS", "Databases", "Relational Databases", "Hours", 6000.00, 0, 0, None, {}),
    ("aws-ces-analytics", "ces-trading-emr", "Amazon EMR", "Analytics", "Data Processing", "Hours", 18000.00, 0, 0,
     {"cost_center": "Data & Analytics Platform", "application": "trading-analytics", "environment": "prod", "capex": "false"}, {}),
    ("aws-ces-analytics", "ces-market-data", "Amazon S3", "Storage", "Object Storage", "GB-Month", 7000.00, 0, 0,
     {"cost_center": "Data & Analytics Platform", "application": "trading-analytics", "environment": "prod", "capex": "false"}, {}),
    ("aws-ces-analytics", "ces-trading-queries", "Amazon Athena", "Analytics", "Analytics Platforms", "TB", 6000.00, 0, 0,
     {"cost_center": "Data & Analytics Platform", "application": "trading-analytics", "environment": "prod", "capex": "false"}, {}),
]
# Bedrock interconnection agent, September cost by token type (input 53%, output 31%, cache read 9%, cache write 7%)
AGENT_TOKENS = {"input": 11130.00, "output": 6510.00, "cache_read": 1890.00, "cache_write": 1470.00}
AGENT_PRICE = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75}   # $ per 1M tokens, list
TOKEN_COL = {"input": "x_InputTokens", "output": "x_OutputTokens", "cache_read": "x_CacheReadTokens", "cache_write": "x_CacheWriteTokens"}
SP_FEE_MONTH = 96000.00
RI_PURCHASE, RI_MONTHLY = 240000.00, 20000.00
SUPPORT_SEP = 41870.12
TAX_SEP = 9214.67
CREDITS_MONTH = -12500.00
SLA_CREDIT = -3180.00
AWS_USAGE_SEP = 372480.55
MARKETPLACE_GIS = 22500.00

AZ_COMPONENTS = [  # sub key, resource, service, category, subcategory, unit, September $, tags, flags
    ("az-erp", "erp-app-vms", "Virtual Machines", "Compute", "Virtual Machines", "Hours", 87000.00, tags("Enterprise Applications", "erp-production"),
     {}),
    ("az-erp", "erp-db-vm", "Virtual Machines", "Compute", "Virtual Machines", "Hours", 15000.00, tags("Enterprise Applications", "erp-production"),
     {"reserved_from": D(2026, 10, 1)}),
    ("az-erp", "erp-managed-disks", "Storage", "Storage", "Disk Storage", "GB-Month", 22000.00, tags("Enterprise Applications", "erp-production"), {}),
    ("az-erp", "erp-backup", "Backup", "Storage", "Backup Storage", "GB-Month", 15358.07, tags("Enterprise Applications", "erp-production"), {}),
    ("az-erp-upgrade", "erpupg-build-vms", "Virtual Machines", "Compute", "Virtual Machines", "Hours", 18000.00,
     tags("Enterprise Applications", "erp-upgrade", env="nonprod", fn="GENERAL", extra=WO_TAGS(WO_G)), {"hours": 24}),
    ("az-erp-upgrade", "erpupg-test-vms", "Virtual Machines", "Compute", "Virtual Machines", "Hours", 8000.00,
     tags("Enterprise Applications", "erp-upgrade", env="nonprod", fn="GENERAL", extra=WO_TAGS(WO_G)), {"hours": 24}),
    ("az-gis", "gis-app-vms", "Virtual Machines", "Compute", "Virtual Machines", "Hours", 20000.00, tags("Distribution Operations", "gis-platform"), {}),
    ("az-gis", "gis-sql", "Azure SQL Database", "Databases", "Relational Databases", "Hours", 9800.00, tags("Distribution Operations", "gis-platform"), {}),
    ("az-gis", "gis-blob", "Storage", "Storage", "Object Storage", "GB-Month", 4000.00, tags("Distribution Operations", "gis-platform"), {}),
    ("az-gis", "gis-tile-cache", "Storage", "Storage", "Object Storage", "GB-Month", 4200.00,
     {**tags("Distribution Operations", "gis-platform"), "cost_center": "T&D"}, {}),
    ("az-wms", "wms-logic-apps", "Logic Apps", "Integration", "Workflow Orchestration", "Executions", 9000.00,
     tags("Field Work Management", "work-management-integration"), {}),
    ("az-wms", "wms-service-bus", "Service Bus", "Integration", "Messaging", "Operations", 5200.00, tags("Field Work Management", "work-management-integration"), {}),
    ("az-wms", "wms-legacy-connector", "Logic Apps", "Integration", "Workflow Orchestration", "Executions", 2800.00,
     {**tags("Field Work Management", "work-management-integration"), "cost_center": "Work Management (retired 2025)"}, {}),
    ("az-tasset", "tasset-databricks", "Azure Databricks", "Analytics", "Data Processing", "DBU-Hours", 52000.00,
     tags("Transmission Asset Management", "asset-health-analytics"), {}),
    ("az-tasset", "tasset-lake", "Storage", "Storage", "Object Storage", "GB-Month", 12000.00, tags("Transmission Asset Management", "asset-health-analytics"), {}),
    ("az-toutage", "toutage-app", "Azure App Service", "Compute", "Web Apps", "Hours", 12000.00,
     tags("Transmission Operations", "transmission-outage-scheduling"), {"build_until": D(2026, 9, 1), "wo": WO_T}),
    ("az-toutage", "toutage-sql", "Azure SQL Database", "Databases", "Relational Databases", "Hours", 7000.00,
     tags("Transmission Operations", "transmission-outage-scheduling"), {"build_until": D(2026, 9, 1), "wo": WO_T}),
    ("az-network", "expressroute-core", "ExpressRoute", "Networking", "Network Connectivity", "Port-Hours", 22000.00,
     tags("Network & Connectivity", "core-network"), {}),
    ("az-security", "defender-for-cloud", "Microsoft Defender for Cloud", "Security", "Security Posture Management", "Nodes", 17000.00,
     tags("Cyber Security Operations", "security-monitoring"), {}),
    ("az-security", "sentinel-workspace", "Microsoft Sentinel", "Security", "Security Information and Event Management", "GB", 14000.00,
     tags("Cyber Security Operations", "security-monitoring"), {}),
    ("az-devtest", "devtest-erp-vms", "Virtual Machines", "Compute", "Virtual Machines", "Hours", 12000.00,
     tags("Enterprise Applications", "erp-test", env="nonprod"), {"hours": 24}),
    ("az-devtest", "devtest-gis-vms", "Virtual Machines", "Compute", "Virtual Machines", "Hours", 7000.00,
     tags("Distribution Operations", "gis-test", env="nonprod"), {"hours": 24}),
    ("az-devtest", "devtest-portal-vms", "Virtual Machines", "Compute", "Virtual Machines", "Hours", 5000.00,
     tags("Customer Operations", "portal-test", env="nonprod"), {"hours": 24}),
    ("az-legacy-migration", "legacy-lift-vms", "Virtual Machines", "Compute", "Virtual Machines", "Hours", 20000.00, None, {}),
    ("az-legacy-migration", "legacy-lift-disks", "Storage", "Storage", "Disk Storage", "GB-Month", 7000.00, None, {}),
    ("az-legacy-migration", "legacy-lift-sql", "Azure SQL Database", "Databases", "Relational Databases", "Hours", 4000.00, None, {}),
]
AOAI = [  # sub key, deployment, model, cost center, app, September $, token mix (input, cache read, output) cost shares
    ("az-aoai-contact", "aoai-contact-summarize", "gpt-4.1", "Customer Operations", "contact-center-summarization", 28900.00, (0.62, 0.08, 0.30)),
    ("az-aoai-reg", "aoai-reg-drafting", "gpt-5.4", "Finance & Regulatory Services", "regulatory-drafting", 10000.00, (0.45, 0.10, 0.45)),
]
AOAI_PRICE = {"gpt-4.1": (2.00, 0.50, 8.00), "gpt-5.4": (2.50, 0.25, 15.00)}   # input, cache read, output per 1M
COPILOT_STUDIO_CREDITS, COPILOT_CREDIT_PRICE = 615000, 0.01
AZ_RESERVATION = 120000.00
AZ_MARKETPLACE = 15000.00
AZ_ROUNDING = {7: 0.21, 8: 0.44, 9: 0.37}
AZ_TAX = {7: 10671.95, 8: 10903.62, 9: 11212.40}
AZ_LATE_CHARGE = 2640.55
AZ_INVOICE_USAGE_SEP = 459048.62
ALLOCATION_RULE_SHIFT = 22000.00

# ============================================================================================ SaaS and services
ITSM = ("itsm-7101", "ServiceNow", "ServiceNow IT service management", 70000.00, 14200.00)
SI = ("si-7102", "ERP Systems Integrator")
WMS = ("wms-7103", "Work Management SaaS")

# ============================================================================================ Copilot seats (6.11)
SEAT_BU = [  # business unit, cost center, seats
    ("CORPORATE_IT", "End-User Computing", 195), ("CORPORATE_IT", None, 25), ("CORPORATE_IT", "Enterprise Applications", 150),
    ("CORPORATE_IT", "Data & Analytics Platform", 90), ("CORPORATE_IT", "Cyber Security Operations", 80),
    ("CORPORATE_IT", "Network & Connectivity", 60), ("SHARED_SERVICES", "Finance & Regulatory Services", 900),
    ("TRANSMISSION", "Transmission Operations", 200), ("TRANSMISSION", "Transmission Planning", 150),
    ("TRANSMISSION", "Transmission Asset Management", 100), ("DISTRIBUTION", "Distribution Operations", 250),
    ("DISTRIBUTION", "Grid Modernization (AMI)", 150), ("DISTRIBUTION", "Field Work Management", 250),
    ("CUSTOMER_OPERATIONS", "Customer Operations", 400)]
SEAT_GROUP = {"CORPORATE_IT": "sg-m365copilot-corporate-it", "SHARED_SERVICES": "sg-m365copilot-shared-services",
              "TRANSMISSION": "sg-m365copilot-transmission", "DISTRIBUTION": "sg-m365copilot-distribution",
              "CUSTOMER_OPERATIONS": "sg-m365copilot-customer-operations"}
COPILOT_SKU = "639dec6b-bb19-468b-871c-c5c441c4b0cb"
SEATS, SEAT_PRICE = 3000, 30.00
IDLE_GROUP, IDLE_DIRECT, GRACE = 380, 160, 45
SNAPSHOT = AS_OF


# ============================================================================================ row builders
def base_row(day, cost, billed, account, issuer, sub, source, provider=None, period_end=None, bp=None, **kw):
    bps = bp or dt.date(day.year, day.month, 1)
    r = {
        "BillingAccountId": account[0], "BillingAccountName": account[1], "BillingAccountType": "Billing Account",
        "BillingCurrency": "USD", "BillingPeriodStart": ts(bps), "BillingPeriodEnd": ts(next_month(bps)),
        "ChargeCategory": "Usage", "ChargeFrequency": "Usage-Based", "ChargePeriodStart": ts(day),
        "ChargePeriodEnd": ts(period_end or day + dt.timedelta(days=1)),
        "BilledCost": billed, "EffectiveCost": cost, "ListCost": round(cost * 1.06, 2), "ContractedCost": round(max(cost, billed), 2),
        "PricingCategory": "Standard", "PricingCurrency": "USD", "PricingCurrencyEffectiveCost": cost,
        "HostProviderName": provider or issuer, "ServiceProviderName": provider or issuer, "InvoiceIssuerName": issuer,
        "SubAccountId": sub[0], "SubAccountName": sub[1],
        "SubAccountType": "AWS Account" if issuer in ("AWS", "AWS Marketplace") else "Subscription",
        "x_CostSource": "invoiced", "x_SourceSystem": source,
    }
    r.update(kw)
    return r


def daily_shape(m, key, flags):
    """Relative weight of each day of month m for one component (storm surge, rightsizing, wobble)."""
    w = []
    for d in month_days(m):
        x = wobble(d, key)
        if flags.get("storm") and STORM[1] <= d <= STORM[2]:
            x *= flags["storm"]
        if flags.get("rightsized") and d >= flags["rightsized"][0]:
            x *= flags["rightsized"][1]
        w.append(x)
    return w


def month_target(sep, m):
    return round(sep * MONTH_FACTOR[m.month], 2)


def component_total(sep, key, flags, m):
    """Month m total of a component whose September total is `sep`: September's daily run rate x growth x the month's
    day weights (storm days in September, rightsizing after its date). September is exact."""
    w_sep = daily_shape(SEP, key, flags)
    if m == SEP:
        return sep
    return round(sep / sum(w_sep) * GROWTH[m.month] * sum(daily_shape(m, key, flags)), 2)


def sp_covered_month(m):
    """Savings Plan coverage (effective cost of covered usage) for month m: 99.5% of the month's fee."""
    fee = sp_fee_month(m)
    return round(fee * (95519.45 / SP_FEE_MONTH), 2)


def sp_fee_month(m):
    days = month_days(m)
    full = (next_month(m) - m).days
    return SP_FEE_MONTH if len(days) == full else round(SP_FEE_MONTH / full * len(days), 2)


def aws_rows():
    out = []
    sp_total_sep = sum(c[7] for c in AWS_COMPONENTS)
    for m in MONTHS:
        days = month_days(m)
        cov_scale = sp_covered_month(m) / sp_total_sep
        ri_cov = RI_MONTHLY if len(days) == (next_month(m) - m).days else round(RI_MONTHLY / (next_month(m) - m).days * len(days), 2)
        for acct_key, res, svc, cat, subcat, unit, sep, sp, ri, tg, flags in AWS_COMPONENTS:
            acct = AWS_ACCOUNTS[acct_key]
            total = component_total(sep, res, flags, m)
            cov = round(sp * cov_scale, 2) if sp else 0.0
            ricov = ri_cov if ri else 0.0
            ondemand = round(total - cov - ricov, 2)
            shape = daily_shape(m, res, flags)
            name = mask(res) if flags.get("masked") else res
            region = "us-east-1" if acct_key not in ("aws-tplan", "aws-ix-studies") else "us-east-2"
            arn = f"arn:aws:{svc.split()[-1].lower()}:{region}:{acct[0]}:{name}"
            t = dict(tg) if tg else None
            for part, amount, kind in ((0, ondemand, "ondemand"), (1, cov, "sp"), (2, ricov, "ri")):
                if amount <= 0:
                    continue
                for d, v in zip(days, split(amount, shape)):
                    hrs = flags.get("hours")
                    gpu = flags.get("gpu")
                    kw = dict(ChargeDescription=f"{svc} {'Spot ' if flags.get('spot') else ''}usage" if flags.get("masked") else
                              f"{svc} {res}" + (" covered by Compute Savings Plan" if kind == "sp" else
                                                " covered by reserved instance" if kind == "ri" else ""),
                              RegionId=region, RegionName=region, ResourceId=arn, ResourceName=name, ResourceType=svc,
                              ServiceCategory=cat, ServiceName=svc, ServiceSubcategory=subcat,
                              SkuId=f"{svc.replace(' ', '')}-{unit}", SkuMeter=unit,
                              SkuPriceId=f"{res}-{'spot' if flags.get('spot') else kind}",
                              ConsumedQuantity=(hrs if hrs else 24) if unit == "Hours" else round(v * 7.5, 3), ConsumedUnit=unit,
                              PricingUnit=unit, PricingQuantity=(hrs if hrs else 24) if unit == "Hours" else round(v * 7.5, 3),
                              Tags=json.dumps(t) if t else None)
                    if flags.get("spot"):
                        kw["PricingCategory"] = "Dynamic"
                    if kind == "sp":
                        kw.update(PricingCategory="Committed", CommitmentDiscountId=SP_ARN, CommitmentDiscountName="Compute Savings Plan 1yr No Upfront",
                                  CommitmentDiscountType="Compute Savings Plan", CommitmentDiscountCategory="Spend",
                                  CommitmentDiscountStatus="Used", CommitmentDiscountQuantity=v, CommitmentDiscountUnit="USD")
                    if kind == "ri":
                        kw.update(PricingCategory="Committed", CommitmentDiscountId=RI_ARN, CommitmentDiscountName="Aurora RI 1yr All Upfront",
                                  CommitmentDiscountType="Reserved Instance", CommitmentDiscountCategory="Usage",
                                  CommitmentDiscountStatus="Used", CommitmentDiscountQuantity=24, CommitmentDiscountUnit="Hours")
                    if kind == "ondemand" and cat == "Compute" and not flags.get("spot"):
                        kw["CommitmentProgramEligibilityDetails"] = json.dumps({"CommitmentPrograms": [
                            {"ProgramType": "FlexibleSpendPlan", "ProgramName": "Compute Savings Plans"}]})
                    if gpu:
                        busy = d < GPU_IDLE_FROM and d.weekday() < 5
                        kw.update(x_AIProduct="SageMaker endpoint", x_RequestCount=int(2600 * wobble(d, "rq")) if busy else 0)
                    if flags.get("storm") and m.month == 9 and STORM[1] <= d <= STORM[2] and t:
                        kw["Tags"] = json.dumps({**t, "storm_event": STORM[0]})
                    out.append(base_row(d, v, 0.0 if kind != "ondemand" else v, AWS_PAYER, "AWS", acct[:2], SOURCE_AWS, **kw))
        # Bedrock interconnection agent: four token types
        acct = AWS_ACCOUNTS["aws-ix-studies"]
        t = tags("Transmission Planning", "interconnection-agent", extra={"data_class": "internal"})
        for tt, sep in AGENT_TOKENS.items():
            total = month_target(sep, m)
            weights = [wobble(d, "ix" + tt) * (0.4 if d.weekday() >= 5 else 1.0) for d in days]
            for d, v in zip(days, split(total, weights)):
                n = int(round(v / AGENT_PRICE[tt] * 1e6))
                cols = {c: 0 for c in TOKEN_COL.values()}
                cols[TOKEN_COL[tt]] = n
                out.append(base_row(d, v, v, AWS_PAYER, "AWS", acct[:2], SOURCE_AWS, provider="AWS",
                                    ChargeDescription=f"claude-sonnet-5-5 {tt.replace('_', ' ')} tokens (standard)",
                                    RegionId="us-east-2", RegionName="us-east-2", ServiceName="Claude Sonnet 5.5 (Amazon Bedrock Edition)",
                                    ServiceCategory="AI and Machine Learning", ServiceSubcategory="Generative AI",
                                    SkuId=f"claude-sonnet-5-5-{tt}", SkuMeter="Tokens", SkuPriceId=f"claude-sonnet-5-5-{tt}-std",
                                    ConsumedQuantity=n, ConsumedUnit="Tokens", PricingQuantity=round(n / 1e6, 6), PricingUnit="1M Tokens",
                                    ListUnitPrice=AGENT_PRICE[tt], ContractedUnitPrice=AGENT_PRICE[tt], PublisherName="Anthropic",
                                    Tags=json.dumps(t), x_AIProduct="claude_bedrock", x_ModelName="claude-sonnet-5-5",
                                    x_ModelVendor="Anthropic", x_TokenType=tt, x_ServiceTier="standard", x_InferenceGeo="us",
                                    x_WorkspaceId=acct[0], x_WorkspaceName="interconnection-agent",
                                    x_RequestCount=int(1200 * wobble(d, "ixr")) if tt == "input" else None, **cols))
        # Payer: Savings Plan fee (daily), unused commitment (month end), RI purchase (July), Enterprise Support (split pro rata)
        payer = AWS_ACCOUNTS["payer"]
        fee = sp_fee_month(m)
        for d, v in zip(days, split(fee, [1.0] * len(days))):
            out.append(base_row(d, 0.0, v, AWS_PAYER, "AWS", payer[:2], SOURCE_AWS, ChargeCategory="Purchase", ChargeFrequency="Recurring",
                                ChargeDescription="Compute Savings Plan recurring fee", ServiceName="Savings Plans for AWS Compute usage",
                                ServiceCategory="Compute", ServiceSubcategory="Virtual Machines", ResourceId=SP_ARN,
                                ResourceName="Compute Savings Plan", ResourceType="Savings Plan", SkuId="SavingsPlanRecurringFee",
                                PricingCategory="Committed", PricingQuantity=v, PricingUnit="USD", CommitmentDiscountId=SP_ARN,
                                CommitmentDiscountName="Compute Savings Plan 1yr No Upfront", CommitmentDiscountType="Compute Savings Plan",
                                CommitmentDiscountCategory="Spend", CommitmentDiscountQuantity=v, CommitmentDiscountUnit="USD",
                                ContractedCost=v, ListCost=v))
        unused = round(fee - sp_covered_month(m), 2)
        if unused > 0:
            out.append(base_row(days[-1], unused, 0.0, AWS_PAYER, "AWS", payer[:2], SOURCE_AWS,
                                ChargeDescription="Compute Savings Plan unused commitment", ServiceName="Savings Plans for AWS Compute usage",
                                ServiceCategory="Compute", ServiceSubcategory="Virtual Machines", ResourceId=SP_ARN,
                                ResourceName="Compute Savings Plan", ResourceType="Savings Plan", SkuId="SavingsPlanUnused",
                                PricingCategory="Committed", PricingQuantity=unused, PricingUnit="USD", CommitmentDiscountId=SP_ARN,
                                CommitmentDiscountName="Compute Savings Plan 1yr No Upfront", CommitmentDiscountType="Compute Savings Plan",
                                CommitmentDiscountCategory="Spend", CommitmentDiscountStatus="Unused", CommitmentDiscountQuantity=unused,
                                CommitmentDiscountUnit="USD", ListCost=0, ContractedCost=0,
                                Tags=json.dumps(tags("End-User Computing", "shared-commitments"))))
        if m.month == 7:
            out.append(base_row(m, 0.0, RI_PURCHASE, AWS_PAYER, "AWS", payer[:2], SOURCE_AWS, ChargeCategory="Purchase",
                                ChargeFrequency="One-Time", ChargeDescription="Aurora reserved instances, 1 year, all upfront",
                                ServiceName="Amazon Aurora", ServiceCategory="Databases", ServiceSubcategory="Relational Databases",
                                ResourceId=RI_ARN, ResourceName="Aurora RI 1yr All Upfront", ResourceType="Reserved Instance",
                                SkuId="Aurora-RI-AllUpfront", PricingCategory="Committed", PricingQuantity=1, PricingUnit="Reservation",
                                CommitmentDiscountId=RI_ARN, CommitmentDiscountName="Aurora RI 1yr All Upfront",
                                CommitmentDiscountType="Reserved Instance", CommitmentDiscountCategory="Usage",
                                ListCost=RI_PURCHASE, ContractedCost=RI_PURCHASE))
    return out


def aws_payer_level(usage):
    """Support (split pro rata to tagged usage by cost center), tax and credits (by account) and the SLA credit."""
    out = []
    by_month = {}
    for r in usage:
        if r["BillingAccountId"] != AWS_PAYER[0] or r["ChargeCategory"] != "Usage":
            continue
        k = r["BillingPeriodStart"].date()
        t = json.loads(r["Tags"]) if r["Tags"] else {}
        cc = t.get("cost_center")
        b = by_month.setdefault(k, {"cc": {}, "acct": {}, "acct_billed": {}, "acct_tags": {}})
        if cc in CC_FN:
            b["cc"][cc] = b["cc"].get(cc, 0) + r["EffectiveCost"]
        a = (r["SubAccountId"], r["SubAccountName"])
        b["acct"][a] = b["acct"].get(a, 0) + r["EffectiveCost"]
        b["acct_billed"][a] = b["acct_billed"].get(a, 0) + r["BilledCost"]
        if a not in b["acct_tags"] or (t.get("cost_center") and not b["acct_tags"][a].get("cost_center")):
            b["acct_tags"][a] = {k2: v for k2, v in t.items() if k2 not in ("storm_event",)}
    payer = AWS_ACCOUNTS["payer"]
    for m in MONTHS:
        b = by_month[m]
        f = MONTH_FACTOR[m.month]
        support = SUPPORT_SEP if m.month == 9 else round(SUPPORT_SEP * f, 2)
        ccs = sorted(b["cc"])
        for cc, v in zip(ccs, split(support, [b["cc"][c] for c in ccs])):
            out.append(base_row(m, v, v, AWS_PAYER, "AWS", payer[:2], SOURCE_AWS, ChargeCategory="Purchase", ChargeFrequency="Recurring",
                                period_end=next_month(m), ChargeDescription="AWS Enterprise Support (share of eligible usage)",
                                ServiceName="AWS Support (Enterprise)", ServiceCategory="Other", ServiceSubcategory="Support",
                                SkuId="AWSSupportEnterprise", PricingQuantity=1, PricingUnit="Month",
                                AllocatedMethodId="aws-support-pro-rata", AllocatedResourceId=f"support:{cc}", AllocatedResourceName=cc,
                                AllocatedTags=json.dumps({"cost_center": cc}),
                                AllocatedMethodDetails=json.dumps({"Elements": [{"AllocatedRatio": round(b["cc"][cc] / sum(b["cc"].values()), 6),
                                                                                  "UsageUnit": "EffectiveCost"}]})))
        if m.month == 10:
            continue  # tax and credits arrive with the invoice; October is open
        accts = sorted(b["acct"])
        billed_usage = sum(b["acct_billed"].values())
        tax = TAX_SEP if m.month == 9 else round(billed_usage * TAX_SEP / AWS_USAGE_SEP, 2)
        for a, v in zip(accts, split(tax, [b["acct_billed"][x] for x in accts])):
            t = b["acct_tags"].get(a)
            out.append(base_row(m, v, v, AWS_PAYER, "AWS", a, SOURCE_AWS, ChargeCategory="Tax", ChargeFrequency="Recurring",
                                period_end=next_month(m), ChargeDescription="Sales and use tax", ServiceName="Tax", ServiceCategory="Other",
                                ServiceSubcategory="Other", ListCost=0, ContractedCost=v, Tags=json.dumps(t) if t else None))
        for a, v in zip(accts, split(CREDITS_MONTH, [b["acct"][x] for x in accts])):
            t = b["acct_tags"].get(a)
            out.append(base_row(m, v, v, AWS_PAYER, "AWS", a, SOURCE_AWS, ChargeCategory="Credit", ChargeFrequency="One-Time",
                                period_end=next_month(m), ChargeDescription="Migration acceleration credit", ServiceName="AWS Credits",
                                ServiceCategory="Other", ServiceSubcategory="Other", ListCost=0, ContractedCost=0,
                                Tags=json.dumps(t) if t else None))
    # SLA credit for September, received 2026-10-09 after September closed: posted in October (R13)
    oms = AWS_ACCOUNTS["aws-oms"]
    out.append(base_row(D(2026, 9, 1), SLA_CREDIT, SLA_CREDIT, AWS_PAYER, "AWS", oms[:2], SOURCE_AWS, bp=D(2026, 10, 1),
                        period_end=D(2026, 10, 1), ChargeCategory="Credit", ChargeClass="Correction", ChargeFrequency="One-Time",
                        ChargeDescription="SLA credit for September (outage map availability), received 2026-10-09",
                        ServiceName="Amazon CloudFront", ServiceCategory="Networking", ServiceSubcategory="Content Delivery",
                        ResourceName="outage-map-cdn", ListCost=0, ContractedCost=0,
                        Tags=json.dumps(tags("Distribution Operations", "outage-map"))))
    return out


def marketplace_rows():
    out = []
    acct = AWS_ACCOUNTS["aws-oms"]
    for m in MONTHS:
        out.append(base_row(m, MARKETPLACE_GIS, MARKETPLACE_GIS, AWS_PAYER, "AWS Marketplace", acct[:2], SOURCE_AWS,
                            provider="GIS Platform Vendor", period_end=next_month(m), ChargeCategory="Purchase",
                            ChargeFrequency="Recurring", ChargeDescription=f"GIS SaaS subscription ({m:%B %Y})",
                            ServiceName="GIS Platform (SaaS)", ServiceCategory="Business Applications", ServiceSubcategory="Business Applications",
                            HostProviderName="AWS", ResourceId="aws-marketplace:gis-platform", ResourceName="gis-platform-saas",
                            ResourceType="SaaS subscription", SkuId="gis-platform-enterprise", PricingQuantity=1, PricingUnit="Month",
                            ConsumedQuantity=1, ConsumedUnit="Subscription", PublisherName="GIS Platform Vendor",
                            Tags=json.dumps(tags("Distribution Operations", "gis-platform"))))
    return out


def azure_rows():
    out = []
    usage_sep = sum(c[6] for c in AZ_COMPONENTS) + sum(a[5] for a in AOAI) + COPILOT_STUDIO_CREDITS * COPILOT_CREDIT_PRICE
    assert round(usage_sep, 2) == round(AZ_INVOICE_USAGE_SEP - AZ_LATE_CHARGE, 2), usage_sep
    for m in MONTHS:
        days = month_days(m)
        for sub_key, res, svc, cat, subcat, unit, sep, tg, flags in AZ_COMPONENTS:
            sub = AZ_SUBS[sub_key]
            total = component_total(sep, res, {}, m)
            shape = daily_shape(m, res, {})
            for d, v in zip(days, split(total, shape)):
                t = dict(tg) if tg else None
                if flags.get("build_until") and d < flags["build_until"] and t:
                    t.update(WO_TAGS(flags["wo"]))
                    t["environment"] = "nonprod"
                hrs = flags.get("hours")
                reserved = flags.get("reserved_from") and d >= flags["reserved_from"]
                kw = dict(ChargeDescription=f"{svc} {res}" + (" covered by reservation" if reserved else ""), RegionId="eastus2",
                          RegionName="eastus2", ResourceId=f"/subscriptions/{sub[0]}/resourceGroups/rg-{sub_key}/{res}", ResourceName=res,
                          ResourceType=svc, ServiceCategory=cat, ServiceName=svc, ServiceSubcategory=subcat,
                          SkuId=f"AZ-{svc.replace(' ', '')}-{unit}", SkuMeter=unit, SkuPriceId=f"{res}-payg",
                          ConsumedQuantity=(hrs or 24) if unit == "Hours" else round(v * 6.2, 3), ConsumedUnit=unit, PricingUnit=unit,
                          PricingQuantity=(hrs or 24) if unit == "Hours" else round(v * 6.2, 3), Tags=json.dumps(t) if t else None)
                billed = v
                if reserved:  # the reservation bought in September covers the ERP VMs from October
                    v = round(AZ_RESERVATION / 12 / (next_month(m) - m).days, 2)
                    billed = 0.0
                    kw.update(PricingCategory="Committed", CommitmentDiscountId=AZ_RES, CommitmentDiscountName="ERP Esv5 1yr reservation",
                              CommitmentDiscountType="Reservation", CommitmentDiscountCategory="Usage", CommitmentDiscountStatus="Used",
                              CommitmentDiscountQuantity=24, CommitmentDiscountUnit="Hours")
                out.append(base_row(d, v, billed, AZ_PROFILE, "Microsoft", sub[:2], SOURCE_AZ, **kw))
        for sub_key, dep, model, cc, app, sep, mix in AOAI:
            sub = AZ_SUBS[sub_key]
            total = month_target(sep, m)
            t = tags(cc, app, extra={"data_class": "internal"})
            pin, pcache, pout = AOAI_PRICE[model]
            for tt, share, price in (("input", mix[0], pin), ("cache_read", mix[1], pcache), ("output", mix[2], pout)):
                weights = [wobble(d, dep + tt) * (0.5 if d.weekday() >= 5 else 1.0) for d in days]
                for d, v in zip(days, split(round(total * share, 2) if tt != "output" else round(total - round(total * mix[0], 2)
                                                                                                    - round(total * mix[1], 2), 2), weights)):
                    n = int(round(v / price * 1e6))
                    cols = {c: 0 for c in TOKEN_COL.values()}
                    cols[TOKEN_COL[tt]] = n
                    out.append(base_row(d, v, v, AZ_PROFILE, "Microsoft", sub[:2], SOURCE_AZ, ServiceName="Azure OpenAI",
                                        ServiceCategory="AI and Machine Learning", ServiceSubcategory="Generative AI",
                                        ChargeDescription=f"{model} {tt.replace('_', ' ')} tokens (global standard)",
                                        RegionId="eastus2", RegionName="eastus2", ResourceId=f"/subscriptions/{sub[0]}/deployments/{dep}",
                                        ResourceName=dep, ResourceType="Model deployment", SkuId=f"{model}-{tt}", SkuMeter="Tokens",
                                        SkuPriceId=f"{model}-{tt}-global", ConsumedQuantity=n, ConsumedUnit="Tokens",
                                        PricingQuantity=round(n / 1e6, 6), PricingUnit="1M Tokens", ListUnitPrice=price,
                                        ContractedUnitPrice=price, PublisherName="OpenAI", Tags=json.dumps(t), x_AIProduct="azure_openai",
                                        x_ModelName=model, x_ModelVendor="OpenAI", x_TokenType=tt, x_ServiceTier="standard",
                                        x_InferenceGeo="global", x_WorkspaceId=dep, x_WorkspaceName=dep,
                                        x_RequestCount=int(13500 * wobble(d, dep)) if tt == "input" else None, **cols))
        # Copilot Studio: customer-facing outage FAQ agent, pay-as-you-go credits (employee agents for licensed staff are not billed)
        sub = AZ_SUBS["az-copilot-studio"]
        credits_m = int(round(COPILOT_STUDIO_CREDITS * MONTH_FACTOR[m.month]))
        weights = [wobble(d, "cps") * (2.5 if STORM[1] <= d <= STORM[2] else 1.0) for d in days]
        for d, v in zip(days, split(round(credits_m * COPILOT_CREDIT_PRICE, 2), weights)):
            q = int(round(v / COPILOT_CREDIT_PRICE))
            out.append(base_row(d, v, v, AZ_PROFILE, "Microsoft", sub[:2], SOURCE_AZ, ServiceName="Microsoft Copilot Studio",
                                ServiceCategory="AI and Machine Learning", ServiceSubcategory="Generative AI",
                                ChargeDescription="Copilot Studio generative answers (pay-as-you-go)", SkuId="copilot-credit-payg",
                                SkuMeter="Copilot Credits", ConsumedQuantity=q, ConsumedUnit="Copilot Credits", PricingQuantity=q,
                                PricingUnit="Copilot Credits", ListUnitPrice=COPILOT_CREDIT_PRICE, ContractedUnitPrice=COPILOT_CREDIT_PRICE,
                                ResourceId=f"/subscriptions/{sub[0]}/copilot/outage-faq-agent", ResourceName="outage-faq-agent",
                                Tags=json.dumps(tags("Customer Operations", "outage-faq-agent")), x_AIProduct="copilot_studio",
                                x_AgentId="agt-cust-outage", x_AgentName="Outage-FAQ-Agent", x_ActionType="generative_answer",
                                x_CreditQuantity=q, x_CreditUnit="Copilot Credits", x_TokenType="n/a",
                                x_RequestCount=int(q / 2), x_WorkspaceId="env-customer-prod", x_WorkspaceName="Customer environment"))
        # Azure cost-allocation rule rows (ExpressRoute shared to the ERP subscription): net zero, excluded from the ledger
        net, erp = AZ_SUBS["az-network"], AZ_SUBS["az-erp"]
        shift = month_target(ALLOCATION_RULE_SHIFT, m)
        for sub, amt in ((net, -shift), (erp, shift)):
            out.append(base_row(days[-1], amt, 0.0, AZ_PROFILE, "Microsoft", sub[:2], SOURCE_AZ, ServiceName="ExpressRoute",
                                ChargeCategory="Adjustment", ChargeFrequency="Recurring",
                                ServiceCategory="Networking", ServiceSubcategory="Network Connectivity", ChargeClass=None,
                                ChargeDescription="Cost allocation rule: ExpressRoute shared (x_CostAllocationRuleName)",
                                ResourceName="expressroute-core", ListCost=0, ContractedCost=0,
                                Tags=json.dumps({**tags("Network & Connectivity", "core-network"),
                                                 "cost_allocation_rule": "ExpressRoute shared"})))
        if m.month in AZ_ROUNDING:
            out.append(base_row(days[-1], AZ_ROUNDING[m.month], AZ_ROUNDING[m.month], AZ_PROFILE, "Microsoft", AZ_SUBS["az-erp"][:2],
                                SOURCE_AZ, ChargeCategory="Adjustment", ChargeFrequency="One-Time", ChargeDescription="Rounding adjustment",
                                ServiceName="Rounding adjustment", ServiceCategory="Other", ServiceSubcategory="Other", ListCost=0,
                                ContractedCost=AZ_ROUNDING[m.month], Tags=json.dumps(tags("End-User Computing", "billing-rounding"))))
        # Marketplace ISV: historian analytics SaaS for transmission operations
        out.append(base_row(m, AZ_MARKETPLACE, AZ_MARKETPLACE, AZ_PROFILE, "Microsoft", AZ_SUBS["az-toutage"][:2], SOURCE_AZ,
                            provider="Historian Analytics Vendor", period_end=next_month(m), ChargeCategory="Purchase",
                            ChargeFrequency="Recurring", ChargeDescription=f"Historian analytics SaaS ({m:%B %Y})",
                            ServiceName="Historian Analytics (Marketplace)", ServiceCategory="Business Applications",
                            ServiceSubcategory="Business Applications", HostProviderName="Microsoft", ResourceId="azure-marketplace:historian-analytics",
                            ResourceName="historian-analytics-saas", ResourceType="SaaS subscription", SkuId="historian-analytics-std",
                            PricingQuantity=1, PricingUnit="Month", ConsumedQuantity=1, ConsumedUnit="Subscription",
                            PublisherName="Historian Analytics Vendor", Tags=json.dumps(tags("Transmission Operations", "historian-analytics-saas"))))
    # Reservation bought in September (principal): one-time purchase to 165, amortized through covered usage from October
    out.append(base_row(D(2026, 9, 28), 0.0, AZ_RESERVATION, AZ_PROFILE, "Microsoft", AZ_SUBS["az-erp"][:2], SOURCE_AZ,
                        ChargeCategory="Purchase", ChargeFrequency="One-Time",
                        ChargeDescription="Reservation purchase, 1 year (x_AmortizationClass = Principal)", ServiceName="Virtual Machines",
                        ServiceCategory="Compute", ServiceSubcategory="Virtual Machines", ResourceId=AZ_RES, ResourceName="res-erp-esv5-2026",
                        ResourceType="Reservation", SkuId="Reservation-Purchase", PricingCategory="Committed", PricingQuantity=1,
                        PricingUnit="Reservation", CommitmentDiscountId=AZ_RES, CommitmentDiscountName="ERP Esv5 1yr reservation",
                        CommitmentDiscountType="Reservation", CommitmentDiscountCategory="Usage", ListCost=AZ_RESERVATION,
                        ContractedCost=AZ_RESERVATION))
    return out


def m365_rows():
    """Annual M365 Copilot invoice (2026-03-01, 3,000 seats) amortized monthly from prepayments (165) by the holder's cost center."""
    out = []
    for m in MONTHS:
        for bu, cc, n in SEAT_BU:
            v = round(n * SEAT_PRICE, 2)
            t = tags(cc, "m365-copilot") if cc else None
            out.append(base_row(m, v, 0.0, M365_ACCOUNT, "Microsoft", ("m365-tenant", "tenant.onmicrosoft.com"), SOURCE_AZ,
                                period_end=next_month(m), ChargeCategory="Purchase", ChargeFrequency="Recurring",
                                ChargeDescription="Microsoft 365 Copilot seats: monthly amortization of the annual invoice of 2026-03-01",
                                ServiceName="Microsoft 365 Copilot", ServiceCategory="AI and Machine Learning",
                                ServiceSubcategory="Generative AI", SkuId=COPILOT_SKU, SkuMeter="Seats", ConsumedQuantity=n,
                                ConsumedUnit="Seats", PricingQuantity=n, PricingUnit="Seats", ListUnitPrice=SEAT_PRICE,
                                ContractedUnitPrice=SEAT_PRICE, PricingCategory="Committed", Tags=json.dumps(t) if t else None,
                                x_AIProduct="m365_copilot", x_TokenType="n/a", InvoiceId="INV-M365-2026-03-ANNUAL",
                                InvoiceDetailId="IND-M365-2026-03-ANNUAL"))
    return out


def saas_rows():
    out = []
    acct, vendor, svc, monthly, _ = ITSM
    for m in MONTHS:
        out.append(base_row(m, monthly, monthly, (acct, vendor), vendor, (acct, f"{vendor} subscription"), SOURCE_SAAS,
                            period_end=next_month(m), ChargeCategory="Purchase", ChargeFrequency="Recurring",
                            ChargeDescription=f"{svc} ({m:%B %Y})", ServiceName=svc, ServiceCategory="Business Applications",
                            ServiceSubcategory="Business Applications", ResourceId=f"{acct}:itsm", ResourceName="itsm",
                            ResourceType="SaaS subscription", SkuId=f"{acct}-itsm", SkuMeter="Subscription", ConsumedQuantity=1,
                            ConsumedUnit="Subscription", PricingQuantity=1, PricingUnit="Subscription",
                            Tags=json.dumps(tags("End-User Computing", "itsm"))))
    si_acct, si_vendor = SI
    out.append(base_row(SEP, 118000.00, 118000.00, (si_acct, si_vendor), si_vendor, (si_acct, "ERP upgrade services"), SOURCE_SAAS,
                        period_end=next_month(SEP), ChargeCategory="Purchase", ChargeFrequency="One-Time",
                        ChargeDescription="ERP upgrade integration services, September milestone", ServiceName="ERP upgrade integration services",
                        ServiceCategory="Business Applications", ServiceSubcategory="Professional Services", ResourceId=f"{si_acct}:erp-upgrade",
                        ResourceName="erp-upgrade-integration", ResourceType="Professional services", SkuId=f"{si_acct}-milestone",
                        ConsumedQuantity=1, ConsumedUnit="Milestone", PricingQuantity=1, PricingUnit="Milestone",
                        Tags=json.dumps({**tags("Enterprise Applications", "erp", fn="GENERAL"), "capex": "true"})))
    out.append(base_row(SEP, 9600.00, 9600.00, (si_acct, si_vendor), si_vendor, (si_acct, "ERP upgrade services"), SOURCE_SAAS,
                        period_end=next_month(SEP), ChargeCategory="Purchase", ChargeFrequency="One-Time",
                        ChargeDescription="ERP training and data conversion (deferral requested)", ServiceName="ERP training and data conversion",
                        ServiceCategory="Business Applications", ServiceSubcategory="Professional Services", ResourceId=f"{si_acct}:erp-training",
                        ResourceName="erp-training-conversion", ResourceType="Professional services", SkuId=f"{si_acct}-training",
                        ConsumedQuantity=1, ConsumedUnit="Milestone", PricingQuantity=1, PricingUnit="Milestone",
                        Tags=json.dumps({**tags("Enterprise Applications", "erp", fn="GENERAL"), "implementation": "deferral_requested"})))
    wacct, wvendor = WMS
    out.append(base_row(SEP, 85000.00, 85000.00, (wacct, wvendor), wvendor, (wacct, "Work management implementation"), SOURCE_SAAS,
                        period_end=next_month(SEP), ChargeCategory="Purchase", ChargeFrequency="One-Time",
                        ChargeDescription="Work management SaaS implementation services", ServiceName="Work management implementation services",
                        ServiceCategory="Business Applications", ServiceSubcategory="Professional Services",
                        ResourceId=f"{wacct}:implementation", ResourceName="wms-implementation", ResourceType="Professional services",
                        SkuId=f"{wacct}-implementation", ConsumedQuantity=1, ConsumedUnit="Milestone", PricingQuantity=1,
                        PricingUnit="Milestone", Tags=json.dumps({**tags("Field Work Management", "work-management"), "capex": "true",
                                                                  "work_order": WO_D2})))
    return out


# ============================================================================================ direct charges (457.1)
# Workloads deployed once per company carry that company's opco tag, so the service company bills them directly (457.1)
# instead of by factor. Each daily usage row splits into one row per company (cents exact; invoice totals do not move).
CUSTOMERS = {e[0]: e[7] for e in OPCOS}
POLE_MILES = {e[0]: e[10] for e in ENTITIES if e[2] == "transco"}
PER_COMPANY = {  # resource name -> {entity code: weight}
    "outage-map-cdn": CUSTOMERS, "portal-db": CUSTOMERS, "gis-app-vms": CUSTOMERS, "gis-sql": CUSTOMERS, "gis-blob": CUSTOMERS,
    "wms-logic-apps": CUSTOMERS, "wms-service-bus": CUSTOMERS,
    "tplan-spot-workers": POLE_MILES, "tplan-fsx-lustre": POLE_MILES, "tplan-case-archive": POLE_MILES,
    "tasset-databricks": POLE_MILES, "tasset-lake": POLE_MILES,
}
ONE_ACCOUNT = {  # accounts that serve one company (usage, tax and credits): sub account id -> entity code
    AWS_ACCOUNTS["aws-tops-hist"][0]: "TNO",       # replica of North Transmission's historian
    AWS_ACCOUNTS["aws-ix-studies"][0]: "TWE",      # West Transmission's interconnection studies
    AWS_ACCOUNTS["aws-ami"][0]: "MET",             # Metro Wires' AMI head-end
    AWS_ACCOUNTS["aws-oms"][0]: "NOR",             # North Electric's outage management (the outage map tier is per company)
}
ONE_RESOURCE = {"tplan-hpc-fleet": "TCE"}      # usage of one resource that serves one company: resource name -> entity code
SPLIT_COST = ("BilledCost", "EffectiveCost", "ListCost", "ContractedCost", "PricingCurrencyEffectiveCost")
SPLIT_QTY = ("ConsumedQuantity", "PricingQuantity", "CommitmentDiscountQuantity")


def direct_charges(rows):
    out = []
    for r in rows:
        one = ONE_ACCOUNT.get(r.get("SubAccountId")) if r.get("Tags") else None
        if one:
            r = dict(r, Tags=json.dumps({**json.loads(r["Tags"]), "opco": one}))
        if r.get("ChargeCategory") != "Usage" or not r.get("Tags"):
            out.append(r)
            continue
        one = ONE_RESOURCE.get(r.get("ResourceName"))
        if one:
            r = dict(r, Tags=json.dumps({**json.loads(r["Tags"]), "opco": one}))
        w = PER_COMPANY.get(r.get("ResourceName"))
        if not w:
            out.append(r)
            continue
        codes = sorted(w)
        weights = [w[c] for c in codes]
        parts = {f: split(r[f], weights) for f in SPLIT_COST if r.get(f) is not None}
        for i, code in enumerate(codes):
            x = dict(r)
            for f, vals in parts.items():
                x[f] = vals[i]
            for f in SPLIT_QTY:
                if isinstance(x.get(f), (int, float)) and x[f]:
                    x[f] = round(x[f] * weights[i] / sum(weights), 6)
            x["ResourceId"] = f"{r['ResourceId']}-{code.lower()}"
            x["ResourceName"] = f"{r['ResourceName']}-{code.lower()}"
            x["Tags"] = json.dumps({**json.loads(r["Tags"]), "opco": code})
            out.append(x)
    return out


# ============================================================================================ invoices
def invoice_detail(inv, det, ps, cat, issuer, acct, amount, desc, issued=True, created=None):
    pe = T(ps.year + (ps.month == 12), ps.month % 12 + 1, 1)
    c = created or pe
    return {"BilledCost": round(amount, 2), "BillingAccountId": acct, "BillingCurrency": "USD", "BillingPeriodEnd": pe,
            "BillingPeriodStart": ps, "ChargeCategory": cat, "InvoiceDetailCreated": c, "InvoiceDetailDescription": desc,
            "InvoiceDetailGrain": json.dumps({"ChargeCategory": cat}), "InvoiceDetailId": det,
            "InvoiceDetailLastUpdated": c + dt.timedelta(days=3), "InvoiceId": inv,
            "InvoiceIssueDate": c + dt.timedelta(days=2) if issued else None, "InvoiceIssueStatus": "Issued" if issued else "Open",
            "InvoiceIssuerName": issuer, "PaymentCurrency": "USD", "PaymentCurrencyBilledCost": round(amount, 2),
            "PaymentCurrencyInvoiceDetailId": "PAY-" + det, "PaymentDueDate": c + dt.timedelta(days=32) if issued else None,
            "PaymentTerms": "Net 30", "PurchaseOrderNumber": f"PO-IT-2026-{issuer.upper().replace(' ', '')[:6]}", "ReferenceInvoiceId": None}


def aws_line(r):
    if r["InvoiceIssuerName"] == "AWS Marketplace":
        return "Marketplace", "Purchase", "AWS Marketplace: GIS SaaS subscription"
    if r["ChargeCategory"] == "Usage":
        return "Usage", "Usage", "Usage (on-demand)"
    if r["ServiceName"] == "Savings Plans for AWS Compute usage":
        return "SavingsPlan", "Purchase", "Savings Plan recurring fee"
    if r["ServiceName"] == "AWS Support (Enterprise)":
        return "Support", "Purchase", "Enterprise Support"
    if r["ChargeCategory"] == "Purchase":
        return "RIPurchase", "Purchase", "RDS reserved instances, 1 year, all upfront"
    if r["ChargeCategory"] == "Tax":
        return "Tax", "Tax", "Tax"
    return "Credit", "Credit", "Credits"


def az_line(r):
    if r["ServiceName"] == "Rounding adjustment":
        return "Rounding", "Adjustment", "Rounding adjustment"
    if r["ServiceName"] == "Historian Analytics (Marketplace)":
        return "Marketplace", "Purchase", "Marketplace ISV: historian analytics SaaS"
    if r["ChargeFrequency"] == "One-Time" and r["ChargeCategory"] == "Purchase":
        return "Reservation", "Purchase", "Reservation purchase"
    return "Usage", "Usage", "Usage"


def assign_invoices(rows):
    """InvoiceId / InvoiceDetailId for the group's rows, and the InvoiceDetail lines (billed amounts) for each."""
    lines = {}
    for r in rows:
        if r.get("InvoiceDetailId") == "IND-M365-2026-03-ANNUAL":
            continue
        ps = r["BillingPeriodStart"]
        acct, issuer = r["BillingAccountId"], r["InvoiceIssuerName"]
        if acct == AWS_PAYER[0]:
            key, cat, desc = aws_line(r)
        elif acct == AZ_PROFILE[0]:
            key, cat, desc = az_line(r)
        else:
            key, cat, desc = r["ChargeCategory"], r["ChargeCategory"], f"{issuer} {r['ChargeCategory'].lower()} charges"
        tag = issuer.upper().replace(" ", "")
        inv = f"INV-{tag}-{acct}-{ps:%Y%m}"
        det = f"IND-{tag}-{acct}-{ps:%Y%m}-{key}"
        r["InvoiceId"], r["InvoiceDetailId"] = inv, det
        lines.setdefault(det, {"inv": inv, "ps": ps, "cat": cat, "issuer": issuer, "acct": acct, "desc": desc, "amt": 0.0})
        lines[det]["amt"] += r["BilledCost"]
    out = []
    for det, x in sorted(lines.items()):
        saas = x["acct"] in (ITSM[0], SI[0], WMS[0]) or x["issuer"] == "AWS Marketplace"
        pe = T(x["ps"].year + (x["ps"].month == 12), x["ps"].month % 12 + 1, 1)
        issued = saas or pe.date() <= D(2026, 10, 1)
        amt = round(x["amt"], 2)
        if x["acct"] == ITSM[0] and x["ps"].month == 9:
            amt = round(amt + ITSM[4], 2)                        # September ITSM invoice overbilled
        if x["acct"] == AZ_PROFILE[0] and x["ps"].month == 9 and det.endswith("-Usage"):
            amt = AZ_INVOICE_USAGE_SEP                           # includes a late charge not yet in the cost data
        created = x["ps"] if saas else pe                        # cloud invoices issue on the 3rd of the next month
        out.append(invoice_detail(x["inv"], det, x["ps"], x["cat"], x["issuer"], x["acct"], amt, x["desc"], issued, created))
    # Azure tax is on the invoice but not in the cost data (by design)
    for mth, tax in AZ_TAX.items():
        ps = T(2026, mth, 1)
        tag = "MICROSOFT"
        inv = f"INV-{tag}-{AZ_PROFILE[0]}-{ps:%Y%m}"
        out.append(invoice_detail(inv, f"IND-{tag}-{AZ_PROFILE[0]}-{ps:%Y%m}-Tax", ps, "Tax", "Microsoft", AZ_PROFILE[0], tax, "Tax",
                                  True, T(2026, mth % 12 + 1, 1)))
    return out


RECONCILING_ITEMS = [  # invoice id, issuer, period, item, amount, category, explained, treatment, resolution, invoice line
    (f"INV-MICROSOFT-{AZ_PROFILE[0]}-202607", "Microsoft", T(2026, 7, 1), "Tax not in the cost data", AZ_TAX[7], "tax_not_in_cost_data",
     True, "Follows the underlying lines (R12); booked from the invoice", "By design: the Azure cost export carries no tax",
     f"IND-MICROSOFT-{AZ_PROFILE[0]}-202607-Tax"),
    (f"INV-MICROSOFT-{AZ_PROFILE[0]}-202608", "Microsoft", T(2026, 8, 1), "Tax not in the cost data", AZ_TAX[8], "tax_not_in_cost_data",
     True, "Follows the underlying lines (R12); booked from the invoice", "By design: the Azure cost export carries no tax",
     f"IND-MICROSOFT-{AZ_PROFILE[0]}-202608-Tax"),
    (f"INV-MICROSOFT-{AZ_PROFILE[0]}-202609", "Microsoft", T(2026, 9, 1), "Tax not in the cost data", AZ_TAX[9], "tax_not_in_cost_data",
     True, "Follows the underlying lines (R12); booked from the invoice", "By design: the Azure cost export carries no tax",
     f"IND-MICROSOFT-{AZ_PROFILE[0]}-202609-Tax"),
    (f"INV-MICROSOFT-{AZ_PROFILE[0]}-202609", "Microsoft", T(2026, 9, 1), "Late charge not yet in the cost data", AZ_LATE_CHARGE,
     "late_charge", False, "Held as a reconciling item", "Expected in the day-5 re-run of the cost export",
     f"IND-MICROSOFT-{AZ_PROFILE[0]}-202609-Usage"),
    (f"INV-SERVICENOW-{ITSM[0]}-202609", "ServiceNow", T(2026, 9, 1), "Invoice exceeds usage rows", ITSM[4], "overbilling", True,
     "Raised with the vendor; not allocated", "Credit memo requested", f"IND-SERVICENOW-{ITSM[0]}-202609-Purchase"),
    (f"INV-AWS-{AWS_PAYER[0]}-202610", "AWS", T(2026, 10, 1), "SLA credit for September received after close", SLA_CREDIT,
     "correction_next_period", True, "Posted in October to the account of the original charge (R13)", "September stays closed",
     f"IND-AWS-{AWS_PAYER[0]}-202610-Credit"),
    (f"INV-MICROSOFT-{AZ_PROFILE[0]}-202609", "Microsoft", T(2026, 9, 1), "Rounding adjustment", AZ_ROUNDING[9], "rounding", True,
     "Spread by weighted average", "In the cost data", f"IND-MICROSOFT-{AZ_PROFILE[0]}-202609-Rounding"),
]


# ============================================================================================ Copilot seats and activity
def seats_and_activity():
    rnd = random.Random(2026)
    seats, activity, directory = [], [], []
    n = 0
    holders = []
    for bu, cc, k in SEAT_BU:
        for _ in range(k):
            n += 1
            holders.append((f"emp{n:05d}@company.example", bu, cc))
    # 70% group-assigned overall; idle 380 group + 160 direct; 45 assigned in the last 14 days (grace)
    idx = list(range(SEATS))
    rnd.shuffle(idx)
    group = set(idx[:2100])
    gl, dl = [i for i in idx if i in group], [i for i in idx if i not in group]
    idle = set(gl[:IDLE_GROUP] + dl[:IDLE_DIRECT])
    grace = set(gl[IDLE_GROUP:IDLE_GROUP + 30] + dl[IDLE_DIRECT:IDLE_DIRECT + 15])
    cutoff = SNAPSHOT - dt.timedelta(days=2) - dt.timedelta(days=30)        # last activity before this is idle
    weekdays = [d for d in (START + dt.timedelta(days=i) for i in range((AS_OF - START).days + 1)) if d.weekday() < 5]
    surfaces = ["teams", "outlook", "word", "excel", "powerpoint", "chat"]
    for i, (upn, bu, cc) in enumerate(holders):
        if i in grace:
            assigned = SNAPSHOT - dt.timedelta(days=rnd.randint(0, 13))
        else:
            assigned = D(2026, 3, 1) + dt.timedelta(days=rnd.randint(0, 100))
        directory.append(dict(UserPrincipal=upn, DisplayName=f"Employee {i + 1:05d}", Department=cc or "Unassigned",
                              CostCenter=cc, IsActiveEmployee=True))
        seats.append(dict(SnapshotDate=SNAPSHOT, x_AIProduct="m365_copilot", ServiceProviderName="Microsoft", UserPrincipal=upn,
                          Department=cc or "Unassigned", CostCenter=cc, LicenseSku="Microsoft 365 Copilot", AssignedDate=assigned,
                          MonthlySeatCost=SEAT_PRICE, BillingCurrency="USD", SkuId=COPILOT_SKU,
                          AssignedByGroupId=SEAT_GROUP[bu] if i in group else None, AssignmentState="Active"))
        if i in grace:
            continue
        if i in idle:
            if rnd.random() < 0.2:
                continue                                     # never used
            last = START + dt.timedelta(days=rnd.randint(0, (cutoff - START).days - 1))
            days = [d for d in weekdays if max(assigned, START) <= d <= last]
            days = sorted(set(rnd.sample(days, min(len(days), rnd.randint(1, 6))) + [last])) if days else [last]
        else:
            p = 0.65 if i % 5 else 0.3
            days = [d for d in weekdays if d >= assigned and rnd.random() < p]
            recent = [d for d in weekdays if d >= max(assigned, SNAPSHOT - dt.timedelta(days=13))]
            if recent:
                days = sorted(set(days + [rnd.choice(recent)]))
        for d in days:
            activity.append(dict(ActivityDate=d, x_AIProduct="m365_copilot", UserPrincipal=upn, Surface=rnd.choice(surfaces),
                                 IsActive=True, Prompts=max(1, int(rnd.lognormvariate(2.0, 0.6)))))
    return seats, activity, directory


# ============================================================================================ registers
def reference_rows():
    pol = [(pid, 1, "opco", name, col, val, drv, E[fx] if fx else None, prio, T(2026, 1, 1), None, "approved", SUBMITTER,
            T(2025, 12, 1), APPROVER, T(2025, 12, 15)) for pid, name, col, val, drv, fx, prio in POLICIES]
    pid, ver, name, col, val, drv, fx, prio = PENDING_POLICY
    pol.append((pid, ver, "opco", name, col, val, drv, fx, prio, T(2026, 10, 1), None, "submitted", SUBMITTER, T(2026, 10, 20), None, None))
    drivers = [(name, ts(m), E[code], round(v * (1 + 0.002 * k), 0) if v > 1 else v)
               for name, vals in DRIVERS.items() for k, m in enumerate(MONTHS) for code, v in vals.items() if v]
    filings = []
    rates = []
    for docket, j, code, a, b, c, d2, fd, st, _, auth in FILINGS:
        typ, roe, eq, debt, years = RATE_CASES[docket]
        ror = round(eq * roe / (1 - TAX_RATE) + (1 - eq) * debt, 4)
        filings.append((docket, j, E[code], a, b, c, d2, fd, st, ror, auth))
        rates.append((docket, typ, roe, eq, debt, ror, years))
    cloud = [(a[0], a[1], "AWS", AWS_PAYER[0], a[2], a[3], E[a[4]], a[5], a[6], a[7], a[8],
              "Cloud CoE" if a[3] is None else a[3], app)
             for k, a in AWS_ACCOUNTS.items()
             for app in [{"aws-tplan": "powerflow-hpc", "aws-tops-hist": "historian-analytics", "aws-ix-studies": "interconnection-agent",
                          "aws-ami": "ami-mdm", "aws-oms": "outage-management", "aws-veg-dev": "vegetation-analytics",
                          "aws-portal": "customer-portal", "aws-data-landing": "billing-export-landing", "aws-network": "core-network",
                          "aws-security": "security-monitoring", "aws-ces-analytics": "trading-analytics"}.get(k)]]
    cloud += [(s[0], s[1], "Microsoft", AZ_PROFILE[0], s[2], s[3], E[s[4]], s[5], s[6], s[7], False,
               "Cloud CoE" if s[3] is None else s[3], app)
              for k, s in AZ_SUBS.items()
              for app in [{"az-erp": "erp-production", "az-erp-upgrade": "erp-upgrade", "az-gis": "gis-platform",
                           "az-wms": "work-management-integration", "az-tasset": "asset-health-analytics",
                           "az-toutage": "transmission-outage-scheduling", "az-aoai-contact": "contact-center-summarization",
                           "az-aoai-reg": "regulatory-drafting", "az-copilot-studio": "outage-faq-agent", "az-network": "core-network",
                           "az-security": "security-monitoring"}.get(k)]]
    cip = [("historian-analytics", AWS_ACCOUNTS["aws-tops-hist"][0], "Read-only analytics replica of the transmission historian",
            "cip_related_workload", None, "customer", "pending", D(2026, 10, 2), "Transmission Operations"),
           ("powerflow-hpc", AWS_ACCOUNTS["aws-tplan"][0], "Power-flow and contingency analysis (planning data)", "ceii", False,
            "customer", "classified", D(2026, 6, 12), "Transmission Planning"),
           ("core-network", AWS_ACCOUNTS["aws-network"][0], "Direct Connect circuits and Transit Gateway", "potential_bcsi_context", None,
            "customer", "pending", D(2026, 10, 2), "Network & Connectivity"),
           ("security-monitoring", AWS_ACCOUNTS["aws-security"][0], "Central logging and SIEM (log sources under review)",
            "potential_bcsi_context", None, "customer", "pending", D(2026, 10, 2), "Cyber Security Operations"),
           ("core-network", AZ_SUBS["az-network"][0], "ExpressRoute circuits", "potential_bcsi_context", None, "provider", "pending",
            D(2026, 10, 2), "Network & Connectivity"),
           ("security-monitoring", AZ_SUBS["az-security"][0], "Defender for Cloud and Sentinel", "potential_bcsi_context", None, "provider",
            "pending", D(2026, 10, 2), "Cyber Security Operations")]
    volumes = []
    total_cust = sum(e[7] for e in OPCOS)
    total_emp = sum(e[8] for e in OPCOS)
    for k, m in enumerate(MONTHS):
        f = MONTH_FACTOR[m.month] if m.month != 10 else 22 / 31
        for e in OPCOS:
            volumes.append((ts(m), e[1], e[7], int(round(CONTACTS * f * e[7] / total_cust)), int(round(WORK_ORDERS_DONE * f * e[8] / total_emp)),
                            int(round(CREW_REPORTS * f * e[8] / total_emp))))
    prepay = [
        ("PP-AWS-RI-2026-07", "AWS", f"INV-AWS-{AWS_PAYER[0]}-202607", D(2026, 7, 1), "Aurora reserved instances, 1 year, all upfront",
         RI_PURCHASE, "165", D(2026, 7, 1), 12, RI_MONTHLY, "R9 then the account of the covered usage", RI_ARN),
        ("PP-AZ-RES-2026-09", "Microsoft", f"INV-MICROSOFT-{AZ_PROFILE[0]}-202609", D(2026, 9, 28),
         "Azure VM reservation, 1 year (x_AmortizationClass = Principal)", AZ_RESERVATION, "165", D(2026, 10, 1), 12,
         round(AZ_RESERVATION / 12, 2), "R9 then the account of the covered usage", AZ_RES),
        ("PP-M365-2026-03", "Microsoft", "INV-M365-2026-03-ANNUAL", D(2026, 3, 1), "Microsoft 365 Copilot, 3,000 seats x $30 x 12",
         SEATS * SEAT_PRICE * 12, "165", D(2026, 3, 1), 12, SEATS * SEAT_PRICE, "R9 then R7 by the seat holder's cost center", None),
    ]
    rows = {
        "ref_legal_entity": [(name, "Group" if code != "TCE" else "Transmission holding (80.1%; 19.9% outside investor)", typ, reg,
                              jur, code, part, grid) for code, name, typ, reg, jur, part, grid, *_ in ENTITIES],
        "ref_cost_center": [(cc, E["CES"] if cc == "Competitive Energy Services" else E["SVC"], cc != "Competitive Energy Services",
                             bu, fn, fac) for cc, bu, fn, fac in COST_CENTERS],
        "ref_allocation_driver": drivers,
        "ref_allocation_policy": pol,
        "ref_jurisdiction_factor": [(E[code], j, ts(m), f) for m in MONTHS for code, j, f in JURISDICTION_FACTORS],
        "ref_gl_account_map": account_map(),
        "ref_jurisdiction_policy": JURISDICTION_POLICY,
        "ref_tax_filing": filings,
        "ref_rate_case": rates,
        "ref_ferc_account": FERC_ACCOUNTS,
        "ref_ferc_rule": [(rid, step, pat, trt, ta, da, ga, sa, cond, cite, basis, pe,
                           "approved" if basis == "Regulation" else "awaiting sign-off",
                           APPROVER if basis == "Regulation" else None, T(2026, 10, 15, 9, 0) if basis == "Regulation" else None)
                          for rid, step, pat, trt, ta, da, ga, sa, cond, cite, basis, pe in RULES],
        "ref_exception_code": EXCEPTION_CODES,
        "ref_rule_change": RULE_CHANGES,
        "ref_work_order": WORK_ORDERS,
        "ref_allocation_factor": FACTORS,
        "ref_required_tag": REQUIRED_TAGS,
        "ref_tag_denylist": DENYLIST,
        "ref_tag_value_map": TAG_VALUE_MAP,
        "ref_cloud_account": cloud,
        "ref_invoice_reconciling_item": RECONCILING_ITEMS,
        "ref_prepayment": prepay,
        "ref_vendor": [(v, cip_, json.dumps(cl), end, ren, esc, acv) for v, cip_, cl, end, ren, esc, acv in VENDORS],
        "ref_cip_review": cip,
        "ref_storm_event": [STORM_EVENT],
        "ref_volume": volumes,
        "ref_ai_use": AI_USE,
    }
    return rows


# ============================================================================================ apply
def insert_dicts(con, table, dicts):
    """Bulk insert (a DataFrame scan; row-by-row inserts are too slow for the activity table)."""
    import pandas as pd
    if not dicts:
        return
    cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    df = pd.DataFrame(dicts).reindex(columns=cols)
    con.register("__rows", df)
    con.execute(f"INSERT INTO {table} SELECT * FROM __rows")
    con.unregister("__rows")


def tidy_base(con):
    """Shape the shared base data to the utility estate: resource names, tax that follows its lines, M365 seats replaced."""
    con.execute("""UPDATE CostAndUsage SET ResourceName = replace(ResourceName, 'web-store', 'outage-web'),
                                           ResourceId = replace(ResourceId, 'web-store', 'outage-web')
                   WHERE ResourceName LIKE '%web-store%' OR ResourceId LIKE '%web-store%'""")
    # tax lines carry the cost center of the largest line on the same invoice (R12: tax follows the underlying line)
    con.execute("""
        UPDATE CostAndUsage AS t SET Tags = (
          SELECT json_object('cost_center', x.cc, 'application', 'tax-on-' || lower(t.InvoiceIssuerName))
          FROM (SELECT json_extract_string(Tags, '$.cost_center') AS cc, SUM(BilledCost) AS amt
                FROM CostAndUsage c WHERE c.BillingAccountId = t.BillingAccountId AND c.BillingPeriodStart = t.BillingPeriodStart
                  AND c.ChargeCategory <> 'Tax' AND json_extract_string(c.Tags, '$.cost_center') IS NOT NULL
                GROUP BY 1 ORDER BY 2 DESC LIMIT 1) x)
        WHERE t.ChargeCategory = 'Tax' AND t.Tags IS NULL AND t.BillingAccountId NOT IN (?, ?)""", [AWS_PAYER[0], AZ_PROFILE[0]])
    # the base generator's M365 Copilot seats are replaced by the 3,000-seat annual subscription
    con.execute("DELETE FROM CostAndUsage WHERE x_AIProduct = 'm365_copilot'")
    con.execute("DELETE FROM ai_seat_assignment WHERE x_AIProduct = 'm365_copilot'")
    con.execute("DELETE FROM ai_user_activity WHERE x_AIProduct = 'm365_copilot'")
    con.execute("""UPDATE ContractCommitment SET ContractCommitmentQuantity = 3000, ContractCommitmentCost = 1080000,
                   PricingCurrencyContractCommitmentCost = 1080000, ContractCommitmentPaymentModel = 'All Upfront',
                   ContractCommitmentPaymentInterval = 'Annual', ContractCommitmentPeriodStart = TIMESTAMP '2026-03-01',
                   ContractCommitmentPeriodEnd = TIMESTAMP '2027-03-01', ContractPeriodStart = TIMESTAMP '2026-03-01',
                   ContractPeriodEnd = TIMESTAMP '2027-03-01',
                   ContractCommitmentDescription = 'Microsoft 365 Copilot, 3,000 seats, annual, invoiced 2026-03-01 (renewal 2027-03-01)'
                   WHERE ContractCommitmentId = 'cc-m365-copilot-seats'""")
    # rebuild the base Microsoft invoice lines without the removed seat rows
    con.execute("""UPDATE InvoiceDetail AS i SET BilledCost = x.amt, PaymentCurrencyBilledCost = x.amt
                   FROM (SELECT InvoiceDetailId, ROUND(SUM(BilledCost), 2) AS amt FROM CostAndUsage GROUP BY 1) x
                   WHERE x.InvoiceDetailId = i.InvoiceDetailId AND i.InvoiceIssuerName = 'Microsoft' AND i.ChargeCategory = 'Purchase'
                     AND i.BillingAccountId = 'ms-mca-1001'""")
    if not con.execute("SELECT COUNT(*) FROM ai_model_catalog WHERE ModelName = 'gpt-4.1'").fetchone()[0]:
        con.execute("INSERT INTO ai_model_catalog VALUES ('gpt-4.1', 'OpenAI', 'balanced', TRUE, 2.00, 8.00, 0.50, 0.00, 'USD')")


def apply(path):
    con = duckdb.connect(path)
    for schema in (os.path.join(ROOT, "schema", "costiq_regulatory_schema.sql"), os.path.join(ROOT, "tenants", "utility", "schema.sql")):
        text = re.sub(r"--[^\n]*", "", open(schema, encoding="utf-8").read())
        for stmt in [s.strip() for s in text.split(";") if s.strip()]:
            con.execute(stmt)
    ref = reference_rows()
    for t, rows in ref.items():
        con.execute(f"DELETE FROM {t}")
        con.executemany(f"INSERT INTO {t} VALUES ({', '.join('?' * len(rows[0]))})", rows)

    accounts = [AWS_PAYER[0], AZ_PROFILE[0], M365_ACCOUNT[0], ITSM[0], SI[0], WMS[0]]
    marks = ", ".join("?" * len(accounts))
    con.execute(f"DELETE FROM CostAndUsage WHERE BillingAccountId IN ({marks})", accounts)
    con.execute(f"DELETE FROM InvoiceDetail WHERE BillingAccountId IN ({marks})", accounts)
    tidy_base(con)

    aws = aws_rows()
    charges = direct_charges(aws + aws_payer_level(aws) + marketplace_rows() + azure_rows() + m365_rows() + saas_rows())
    invoices = assign_invoices(charges)
    insert_dicts(con, "CostAndUsage", charges)
    insert_dicts(con, "InvoiceDetail", invoices)

    issuers = ["AWS Marketplace", "ServiceNow", SI[1], WMS[1]]
    con.execute(f"DELETE FROM BillingPeriod WHERE InvoiceIssuerName IN ({', '.join('?' * len(issuers))})", issuers)
    insert_dicts(con, "BillingPeriod", [
        {"BillingPeriodCreated": ts(m), "BillingPeriodStart": ts(m), "BillingPeriodEnd": ts(next_month(m)),
         "BillingPeriodLastUpdated": ts(m) + dt.timedelta(days=3),
         "BillingPeriodStatus": "Closed" if m.month < 10 or i != "AWS Marketplace" else "Open", "InvoiceIssuerName": i}
        for i in issuers for m in MONTHS])

    seats, activity, directory = seats_and_activity()
    con.execute("DELETE FROM ai_user_directory WHERE UserPrincipal LIKE 'emp%@company.example'")
    insert_dicts(con, "ai_seat_assignment", seats)
    insert_dicts(con, "ai_user_activity", activity)
    insert_dicts(con, "ai_user_directory", directory)

    refresh_budgets(con)
    con.execute("CHECKPOINT")
    sep = con.execute(f"SELECT ROUND(SUM(BilledCost), 2) FROM CostAndUsage WHERE InvoiceId = 'INV-AWS-{AWS_PAYER[0]}-202609'").fetchone()[0]
    con.close()
    print(f"{path}: {len(charges)} utility charge rows, {len(invoices)} invoice lines, {len(seats)} Copilot seats, "
          f"{len(activity)} activity rows, {sum(len(v) for v in ref.values())} register rows; AWS September invoice {sep}")


if __name__ == "__main__":
    for p in sys.argv[1:] or [os.path.join(ROOT, "sample", "utility", "costiq_sample.duckdb")]:
        apply(p)
