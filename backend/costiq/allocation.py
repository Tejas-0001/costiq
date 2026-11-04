"""Driver-based, multi-step allocation for a multi-entity group.

Every charge is followed through five steps and written to allocated_cost with its lineage:

  1. Pool         cost center from split-cost tags, resource tags or approved rules (Engines.cost_center_expr)
  2. Entity split first approved ref_allocation_policy by Priority: an allocation key's shares (the tenant's drivers)
                  or 100% to a fixed entity
  3. Jurisdiction ref_jurisdiction_factor per entity
  4. Cost class   the tenant pack's allocation.classes, first match wins (pharma: capital for implementation services
                  under ASC 350-40, qre for recurring fees claimed as qualifying research), else the default class
  5. GL account   a class with its own account books there; the default class takes the first matching
                  ref_gl_account_map row by Priority (matched on ChargeCategory, ServiceCategory, ServiceName or
                  CostCenter, and the entity type)

Charges with no matching policy stay on the shared-services entity with Exception 'no_opco_policy'; default-class lines
with no account map get the pack's unmapped exception (pharma: 'no_gl_account'). Each period is replaced as a whole
(idempotent), so totals always tie back to the source.

A tenant whose pack sets `allocation.engine: ferc` (utility) runs Allocator.ferc_sql() instead: the FERC ledger (direct charge
first, rule register R1-R17, work orders, the 920 guard, one exception code per line); see tenants/utility/pack.yaml.
"""
from __future__ import annotations
from .engines import CHARGE_KEY
from . import tenant

COLUMNS = ["ChargeKey", "PeriodStart", "ChargePeriodStart", "ServiceProviderName", "ServiceName", "ServiceCategory",
           "ChargeCategory", "ChargeFrequency", "Application", "CostCenter", "PoolMethod", "LegalEntity", "Regulated", "OpcoPolicy",
           "OpcoPolicyVersion", "DriverName", "OpcoShare", "Jurisdiction", "JurisdictionFactor", "PnlFunction",
           "GlAccount", "GlTitle", "CostClass", "Share", "SourceCost", "AllocatedCost", "Exception"]



def classes() -> dict:
    """The tenant's cost classes: {"classes": [{class, when, function, account, title}], "default_class",
    "unmapped_exception", "expense_classes"} (tenants/<t>/pack.yaml `allocation`)."""
    a = dict(tenant.pack().get("allocation") or {})
    a.setdefault("classes", [])
    a.setdefault("default_class", "opex")
    a.setdefault("unmapped_exception", "no_gl_account")
    a.setdefault("expense_classes", [a["default_class"]])
    return a


def extra_columns() -> list[str]:
    """allocated_cost columns the tenant adds after the shared COLUMNS (pack.yaml allocation.ledger_columns; the tenant's
    schema appends them). Empty for tenants without them (pharma): check it before selecting one of these columns."""
    return [str(c) for c in ((tenant.pack().get("allocation") or {}).get("ledger_columns") or [])]


def _lit(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


class Allocator:
    def __init__(self, db, engines):
        self.db, self.eng = db, engines

    def periods(self):
        return [r[0] for r in self.db.query("SELECT DISTINCT BillingPeriodStart FROM CostAndUsage ORDER BY 1", limit=None)["rows"]]

    def run(self, period_start=None):
        """Allocate one billing period (a datetime) or, by default, every period. Returns rows written per period."""
        import datetime as dt
        done = {}
        for p in ([period_start] if period_start else self.periods()):
            ps = p if isinstance(p, dt.datetime) else dt.datetime.fromisoformat(str(p))
            sql, params = self.sql()
            self.db.query("DELETE FROM allocated_cost WHERE PeriodStart = :ps", {"ps": ps}, limit=None)
            self.db.query(sql, {**params, "ps": ps}, limit=None)
            done[ps.isoformat()[:7]] = self.db.query("SELECT COUNT(*) FROM allocated_cost WHERE PeriodStart = :ps",
                                                     {"ps": ps})["rows"][0][0]
        return done

    def sql(self):
        """(INSERT ... SELECT for one period with :ps, parameters of the pooling rules)."""
        if classes().get("engine") == "ferc":
            return self.ferc_sql()
        cc, cc_params = self.eng.cost_center_expr()
        method = ("CASE WHEN json_extract_string(AllocatedTags, '$.cost_center') IS NOT NULL THEN 'Split-cost allocation' "
                  "WHEN json_extract_string(Tags, '$.cost_center') IS NOT NULL THEN 'Resource tag' "
                  f"WHEN {cc} <> 'Unallocated' THEN 'Approved rule' ELSE 'Unallocated' END")
        cfg = classes()
        fixed = [c for c in cfg["classes"] if c.get("account")]
        default = _lit(cfg["default_class"])
        cls = ("CASE " + " ".join(f"WHEN {c['when']} THEN {_lit(c['class'])}" for c in cfg["classes"])
               + f" ELSE {default} END") if cfg["classes"] else default

        def pick(field, fallback):
            if not fixed:
                return fallback
            return ("CASE x.Cls " + " ".join(f"WHEN {_lit(c['class'])} THEN {_lit(c[field])}" for c in fixed)
                    + f" ELSE {fallback} END")
        return f"""
INSERT INTO allocated_cost ({', '.join(COLUMNS)})
WITH base AS (
  SELECT {CHARGE_KEY} AS ChargeKey, BillingPeriodStart AS PeriodStart, ChargePeriodStart, ServiceProviderName, ServiceName,
         ServiceCategory, ChargeCategory, ChargeFrequency, SubAccountId,
         json_extract_string(Tags, '$.application') AS Application,
         json_extract_string(Tags, '$.implementation') AS Impl,
         {cc} AS CostCenter, {method} AS PoolMethod, EffectiveCost AS SourceCost
  FROM CostAndUsage WHERE BillingPeriodStart = :ps
),
pol AS (
  SELECT * FROM ref_allocation_policy
  WHERE Step = 'opco' AND Status = 'approved' AND EffectiveFrom <= :ps AND (EffectiveTo IS NULL OR EffectiveTo > :ps)
),
matched AS (
  SELECT b.ChargeKey, p.PolicyId, p.Version, p.DriverName, p.FixedEntity,
         ROW_NUMBER() OVER (PARTITION BY b.ChargeKey ORDER BY p.Priority, p.PolicyId) AS rn
  FROM base b JOIN pol p ON p.MatchColumn = '*'
       OR (p.MatchColumn = 'CostCenter' AND b.CostCenter = p.MatchValue)
       OR (p.MatchColumn = 'ServiceName' AND b.ServiceName = p.MatchValue)
       OR (p.MatchColumn = 'ServiceProviderName' AND b.ServiceProviderName = p.MatchValue)
       OR (p.MatchColumn = 'SubAccountId' AND b.SubAccountId = p.MatchValue)
       OR (p.MatchColumn = 'Application' AND b.Application = p.MatchValue)
),
pick AS (SELECT * FROM matched WHERE rn = 1),
drv AS (
  SELECT DriverName, LegalEntity, DriverValue / SUM(DriverValue) OVER (PARTITION BY DriverName) AS DShare
  FROM ref_allocation_driver WHERE PeriodStart = :ps
),
opco AS (
  SELECT b.*, k.PolicyId, k.Version, k.DriverName,
         COALESCE(k.FixedEntity, d.LegalEntity,
                  (SELECT MIN(LegalEntity) FROM ref_legal_entity WHERE EntityType = 'service_company')) AS LegalEntity,
         CASE WHEN k.PolicyId IS NULL OR k.FixedEntity IS NOT NULL THEN 1 ELSE d.DShare END AS OpcoShare,
         CASE WHEN k.PolicyId IS NULL THEN 'no_opco_policy' END AS Exc
  FROM base b
  LEFT JOIN pick k ON k.ChargeKey = b.ChargeKey
  LEFT JOIN drv d ON k.FixedEntity IS NULL AND d.DriverName = k.DriverName
),
jur AS (
  SELECT o.*, COALESCE(f.Jurisdiction, 'Unassigned') AS Jurisdiction, COALESCE(f.Factor, 1) AS JFactor
  FROM opco o LEFT JOIN ref_jurisdiction_factor f ON f.LegalEntity = o.LegalEntity AND f.PeriodStart = :ps
),
ent AS (
  SELECT j.*, e.EntityType, e.Regulated,
         {cls} AS Cls
  FROM jur j
  LEFT JOIN ref_legal_entity e ON e.LegalEntity = j.LegalEntity
  LEFT JOIN ref_jurisdiction_policy jp ON jp.Jurisdiction = j.Jurisdiction
),
fm AS (
  SELECT x.ChargeKey, x.LegalEntity, x.Jurisdiction, m.PnlFunction, m.GlAccount, m.GlTitle,
         ROW_NUMBER() OVER (PARTITION BY x.ChargeKey, x.LegalEntity, x.Jurisdiction ORDER BY m.Priority) AS rn
  FROM ent x JOIN ref_gl_account_map m
    ON (m.EntityType IS NULL OR m.EntityType = x.EntityType)
   AND ((m.MatchColumn = 'ChargeCategory' AND x.ChargeCategory = m.MatchValue)
     OR (m.MatchColumn = 'ServiceCategory' AND x.ServiceCategory = m.MatchValue)
     OR (m.MatchColumn = 'ServiceName' AND x.ServiceName = m.MatchValue)
     OR (m.MatchColumn = 'CostCenter' AND x.CostCenter = m.MatchValue))
)
SELECT x.ChargeKey, x.PeriodStart, x.ChargePeriodStart, x.ServiceProviderName, x.ServiceName, x.ServiceCategory,
       x.ChargeCategory, x.ChargeFrequency, x.Application, x.CostCenter, x.PoolMethod, x.LegalEntity, x.Regulated, x.PolicyId, x.Version,
       x.DriverName, x.OpcoShare, x.Jurisdiction, x.JFactor,
       {pick("function", "f.PnlFunction")},
       {pick("account", "f.GlAccount")},
       {pick("title", "f.GlTitle")},
       x.Cls, x.OpcoShare * x.JFactor, x.SourceCost, x.SourceCost * x.OpcoShare * x.JFactor,
       COALESCE(x.Exc, CASE WHEN x.Cls = {default} AND f.GlAccount IS NULL THEN {_lit(cfg["unmapped_exception"])} END)
FROM ent x
LEFT JOIN fm f ON f.ChargeKey = x.ChargeKey AND f.LegalEntity = x.LegalEntity AND f.Jurisdiction = x.Jurisdiction AND f.rn = 1
""", cc_params

    # ------------------------------------------------------------------ the FERC ledger (pack allocation.engine: ferc)
    def ferc_sql(self):
        """(INSERT ... SELECT for one period with :ps, parameters) for the FERC ledger. Every charge, in rule order:
        entity (direct opco tag 457.1 > approved policy: fixed entity 457.1, factor 457.2 or the factor's own class > held at
        the service company), jurisdiction, function (tag > cost center > account > multi-function default), then the rule
        that places it (R12 tax, R13 credits, R9 commitments, a requested class when valid, else the account map), the 920
        guard and one exception per line (pack `allocation.ferc.exception_order`)."""
        cc, cc_params = self.eng.cost_center_expr()
        method = ("CASE WHEN json_extract_string(AllocatedTags, '$.cost_center') IS NOT NULL THEN 'Split-cost allocation' "
                  "WHEN json_extract_string(Tags, '$.cost_center') IS NOT NULL THEN 'Resource tag' "
                  f"WHEN {cc} <> 'Unallocated' THEN 'Approved rule' ELSE 'Unallocated' END")
        cfg = classes()
        f = dict(cfg.get("ferc") or {})
        rules = dict(f.get("rules") or {})
        exc = dict(f.get("exceptions") or {})
        t = {"rule": "ref_ferc_rule", "account": "ref_ferc_account", "work_order": "ref_work_order",
             "value_map": "ref_tag_value_map", "cloud_account": "ref_cloud_account", "factor": "ref_allocation_factor",
             "cip_review": "ref_cip_review", **(f.get("tables") or {})}
        default = _lit(cfg["default_class"])
        multi = _lit(f.get("multi_function", "GENERAL"))
        nonutil = dict(f.get("nonutility") or {})
        nu_types = ", ".join(_lit(x) for x in (nonutil.get("entity_types") or ["affiliate"]))
        nu_fn = _lit(nonutil.get("function", "NONUTIL"))
        commit = dict(f.get("commitment") or {})
        functions = ", ".join(_lit(x) for x in ("T", "D", "CUST", "AG", "GENERAL", "NONUTIL"))
        by_fn = dict(rules.get("by_function") or {})
        by_fn_sql = ("CASE x.FunctionCode " + " ".join(f"WHEN {_lit(k)} THEN {_lit(v)}" for k, v in by_fn.items())
                     + f" ELSE {_lit(by_fn.get('GENERAL', 'R7'))} END") if by_fn else _lit("R7")
        ai_replaces = ", ".join(_lit(x) for x in (rules.get("ai_replaces") or ["R5", "R6", "R7"]))
        cls_list = cfg["classes"]
        valid = lambda c: c.get("valid") or "TRUE"  # noqa: E731
        cls = ("CASE " + " ".join(f"WHEN ({c['when']}) THEN CASE WHEN ({valid(c)}) THEN {_lit(c['class'])} ELSE {default} END"
                                  for c in cls_list) + f" ELSE {default} END") if cls_list else default
        req = ("CASE " + " ".join(f"WHEN ({c['when']}) THEN {_lit(c['class'])}" for c in cls_list) + " END") if cls_list else "NULL"
        held = ("CASE " + " ".join(f"WHEN ({c['when']}) AND NOT COALESCE(({valid(c)}), FALSE) THEN {c.get('held') or 'NULL'}"
                                   for c in cls_list) + " END") if cls_list else "NULL"
        fixed = [c for c in cls_list if c.get("account")]

        def pick(field, fallback):
            if not fixed:
                return fallback
            return ("CASE x.Cls " + " ".join(f"WHEN {_lit(c['class'])} THEN {_lit(c[field])}" for c in fixed)
                    + f" ELSE {fallback} END")
        cls_rule = ("CASE x.Cls " + " ".join(f"WHEN {_lit(c['class'])} THEN {_lit(c.get('rule') or 'R1')}" for c in fixed)
                    + " END") if fixed else "NULL"
        stamps = {exc.get("no_opco", "no_opco"): "x.Suspense = 1",
                  exc.get("labor_account", "labor_account_on_non_labor"): "x.LaborHit = 1",
                  cfg["unmapped_exception"]: f"x.Cls = {default} AND x.MapAccount IS NULL AND x.IsCommit = 0",
                  exc.get("no_function", "no_ferc_function"): "x.Suspense = 0 AND x.IsCommit = 0 AND x.FunctionCode IS NULL",
                  exc.get("multi_function", "multi_function_default_397.2"): "x.MultiFn = 1",
                  exc.get("cip_review", "cip_review_pending"): "x.CipPending = 1"}
        branches = [f"WHEN {stamps[code]} THEN {_lit(code)}" if code in stamps else f"WHEN x.HeldExc = {_lit(code)} THEN {_lit(code)}"
                    for code in (f.get("exception_order") or list(stamps))]
        exception = "CASE " + " ".join(branches) + " END"
        commit_acct, commit_title = _lit(commit.get("account", "165")), _lit(commit.get("title", "Prepayments"))
        commit_fn, commit_cls = _lit(commit.get("function", "Prepayment")), _lit(commit.get("class", "prepayment"))
        values = {
            "SubAccountId": "x.SubAccountId", "InvoiceId": "x.InvoiceId", "InvoiceDetailId": "x.InvoiceDetailId",
            "ChargeDescription": "x.ChargeDescription", "FunctionCode": "x.FunctionCode", "RuleId": "x.RuleId",
            "Citation": "r.Citation", "PolicyFlag": "r.Basis", "WorkOrderId": "x.WorkOrderId",
            "BillingClass": "CASE WHEN x.EntityType = 'service_company' THEN NULL ELSE x.Bill END"}
        extra = extra_columns()
        extra_sql = "".join(f",\n       {values.get(c, 'NULL')}" for c in extra)
        return f"""
INSERT INTO allocated_cost ({', '.join(COLUMNS + extra)})
WITH base AS (
  SELECT {CHARGE_KEY} AS ChargeKey, BillingPeriodStart AS PeriodStart, ChargePeriodStart, ServiceProviderName, ServiceName,
         ServiceCategory, ChargeCategory, ChargeClass, ChargeFrequency, SubAccountId, InvoiceId, InvoiceDetailId, ChargeDescription,
         CommitmentDiscountId, x_AIProduct AS AiProduct,
         json_extract_string(Tags, '$.application') AS Application,
         json_extract_string(Tags, '$.implementation') AS Impl,
         json_extract_string(Tags, '$.capex') AS Capex,
         json_extract_string(Tags, '$.work_order') AS WoTag,
         json_extract_string(Tags, '$.opco') AS OpcoTag,
         json_extract_string(Tags, '$.ferc_function') AS FnTag,
         {cc} AS RawCostCenter, {method} AS PoolMethod, EffectiveCost AS SourceCost
  FROM CostAndUsage WHERE BillingPeriodStart = :ps
),
vm AS (
  SELECT TagKey, RawValue, MIN(NormalizedValue) AS NormalizedValue FROM {t['value_map']}
  WHERE Action = 'normalize' AND NormalizedValue IS NOT NULL GROUP BY TagKey, RawValue
),
nb AS (
  SELECT b.*, COALESCE(vc.NormalizedValue, b.RawCostCenter) AS CostCenter, COALESCE(vo.NormalizedValue, b.OpcoTag) AS OpcoCode,
         COALESCE(vf.NormalizedValue, b.FnTag) AS FnCode,
         CASE WHEN b.ChargeCategory = 'Tax' THEN 1 ELSE 0 END AS IsTax,
         CASE WHEN b.ChargeCategory <> 'Tax' AND (b.ChargeCategory = 'Credit' OR b.ChargeClass = 'Correction' OR b.SourceCost < 0)
              THEN 1 ELSE 0 END AS IsCredit,
         CASE WHEN b.ChargeCategory = 'Purchase' AND b.CommitmentDiscountId IS NOT NULL THEN 1 ELSE 0 END AS IsCommit
  FROM base b
  LEFT JOIN vm vc ON vc.TagKey = 'cost_center' AND vc.RawValue = b.RawCostCenter
  LEFT JOIN vm vo ON vo.TagKey = 'opco' AND vo.RawValue = b.OpcoTag
  LEFT JOIN vm vf ON vf.TagKey = 'ferc_function' AND vf.RawValue = b.FnTag
),
svc AS (SELECT MIN(LegalEntity) AS LegalEntity FROM ref_legal_entity WHERE EntityType = 'service_company'),
pol AS (
  SELECT * FROM ref_allocation_policy
  WHERE Step = 'opco' AND Status = 'approved' AND EffectiveFrom <= :ps AND (EffectiveTo IS NULL OR EffectiveTo > :ps)
),
dpol AS (
  SELECT PolicyId, MAX(Version) AS Version FROM pol WHERE PolicyId = {_lit(f.get('direct_policy', 'AP-DIRECT'))} GROUP BY PolicyId
),
direct AS (
  SELECT b.ChargeKey, e.LegalEntity, d.PolicyId, d.Version
  FROM nb b
  JOIN ref_legal_entity e ON e.EntityCode = b.OpcoCode
  JOIN dpol d ON 1 = 1
  WHERE b.IsCommit = 0 AND b.OpcoCode <> {_lit(f.get('shared_tag_value', 'SHARED'))}
),
matched AS (
  SELECT b.ChargeKey, p.PolicyId, p.Version, p.DriverName, p.FixedEntity,
         ROW_NUMBER() OVER (PARTITION BY b.ChargeKey ORDER BY p.Priority, p.PolicyId) AS rn
  FROM nb b JOIN pol p ON p.MatchColumn = '*'
       OR (p.MatchColumn = 'CostCenter' AND b.CostCenter = p.MatchValue)
       OR (p.MatchColumn = 'ServiceName' AND b.ServiceName = p.MatchValue)
       OR (p.MatchColumn = 'ServiceProviderName' AND b.ServiceProviderName = p.MatchValue)
       OR (p.MatchColumn = 'SubAccountId' AND b.SubAccountId = p.MatchValue)
       OR (p.MatchColumn = 'Application' AND b.Application = p.MatchValue)
),
pick AS (SELECT * FROM matched WHERE rn = 1),
drv AS (
  SELECT DriverName, LegalEntity, DriverValue / SUM(DriverValue) OVER (PARTITION BY DriverName) AS DShare
  FROM ref_allocation_driver WHERE PeriodStart = :ps
),
fac AS (SELECT DriverName, MIN(BillingClass) AS BillingClass FROM {t['factor']} WHERE DriverName IS NOT NULL GROUP BY DriverName),
opco AS (
  SELECT b.*,
         CASE WHEN b.IsCommit = 1 THEN NULL WHEN d.ChargeKey IS NOT NULL THEN d.PolicyId ELSE k.PolicyId END AS PolicyId,
         CASE WHEN b.IsCommit = 1 THEN NULL WHEN d.ChargeKey IS NOT NULL THEN d.Version ELSE k.Version END AS Version,
         CASE WHEN b.IsCommit = 1 OR d.ChargeKey IS NOT NULL THEN NULL ELSE k.DriverName END AS DriverName,
         CASE WHEN b.IsCommit = 1 THEN s.LegalEntity ELSE COALESCE(d.LegalEntity, k.FixedEntity, r.LegalEntity, s.LegalEntity) END
           AS LegalEntity,
         CASE WHEN b.IsCommit = 1 OR d.ChargeKey IS NOT NULL OR k.PolicyId IS NULL OR k.FixedEntity IS NOT NULL THEN 1
              ELSE r.DShare END AS OpcoShare,
         CASE WHEN b.IsCommit = 1 THEN NULL WHEN d.ChargeKey IS NOT NULL OR k.FixedEntity IS NOT NULL THEN '457.1'
              WHEN k.PolicyId IS NULL THEN NULL WHEN fa.BillingClass = '457.1' THEN '457.1' ELSE '457.2' END AS Bill,
         CASE WHEN b.IsCommit = 0 AND d.ChargeKey IS NULL AND k.PolicyId IS NULL THEN 1 ELSE 0 END AS Suspense
  FROM nb b
  CROSS JOIN svc s
  LEFT JOIN direct d ON d.ChargeKey = b.ChargeKey
  LEFT JOIN pick k ON k.ChargeKey = b.ChargeKey AND d.ChargeKey IS NULL AND b.IsCommit = 0
  LEFT JOIN drv r ON k.FixedEntity IS NULL AND r.DriverName = k.DriverName
  LEFT JOIN fac fa ON fa.DriverName = k.DriverName
),
wo AS (
  SELECT WorkOrderId, MIN(CapitalizationStartDate) AS CapStart, MAX(COALESCE(ReadyForUseDate, DATE '9999-12-31')) AS LastReady
  FROM {t['work_order']} GROUP BY WorkOrderId
),
cip AS (
  SELECT DISTINCT c.SubAccountId FROM {t['cip_review']} c JOIN {t['cloud_account']} a ON a.SubAccountId = c.SubAccountId
  WHERE c.ReviewStatus = 'pending' AND a.NamesMasked
),
jur AS (
  SELECT o.*, COALESCE(jf.Jurisdiction, 'Unassigned') AS Jurisdiction, COALESCE(jf.Factor, 1) AS JFactor,
         e.EntityType, e.Regulated,
         CASE WHEN o.WoTag IS NULL OR w.WorkOrderId IS NULL THEN 'missing'
              WHEN w.CapStart IS NULL OR w.CapStart > CAST(o.ChargePeriodStart AS DATE) THEN 'no_threshold'
              WHEN w.LastReady <= CAST(o.ChargePeriodStart AS DATE) THEN 'in_service'
              ELSE 'valid' END AS WoStatus,
         c.FercFunction AS CcFn, a.FercFunction AS AcctFn,
         CASE WHEN o.FnCode IN ({functions}) THEN o.FnCode END AS TagFn,
         CASE WHEN p.SubAccountId IS NOT NULL THEN 1 ELSE 0 END AS CipPending
  FROM opco o
  LEFT JOIN ref_jurisdiction_factor jf ON jf.LegalEntity = o.LegalEntity AND jf.PeriodStart = :ps
  LEFT JOIN ref_legal_entity e ON e.LegalEntity = o.LegalEntity
  LEFT JOIN wo w ON w.WorkOrderId = o.WoTag
  LEFT JOIN ref_cost_center c ON c.CostCenter = o.CostCenter
  LEFT JOIN {t['cloud_account']} a ON a.SubAccountId = o.SubAccountId
  LEFT JOIN cip p ON p.SubAccountId = o.SubAccountId
),
fn AS (
  SELECT j.*,
         CASE WHEN j.Suspense = 1 OR j.IsCommit = 1 THEN NULL
              WHEN j.EntityType IN ({nu_types}) THEN {nu_fn}
              ELSE COALESCE(j.TagFn, NULLIF(j.CcFn, {multi}), j.AcctFn, CASE WHEN j.CcFn = {multi} THEN {multi} END) END
           AS FunctionCode,
         CASE WHEN j.Suspense = 0 AND j.IsCommit = 0 AND j.EntityType NOT IN ({nu_types}) AND j.TagFn IS NULL
                   AND j.CcFn = {multi} AND j.AcctFn IS NULL THEN 1 ELSE 0 END AS MultiFn,
         CASE WHEN j.IsTax = 1 OR j.IsCredit = 1 OR j.IsCommit = 1 THEN NULL ELSE {req} END AS ReqCls,
         CASE WHEN j.IsTax = 1 OR j.IsCredit = 1 OR j.IsCommit = 1 THEN {default} ELSE {cls} END AS Cls,
         CASE WHEN j.IsTax = 1 OR j.IsCredit = 1 OR j.IsCommit = 1 THEN NULL ELSE {held} END AS HeldExc
  FROM jur j
  LEFT JOIN ref_jurisdiction_policy jp ON jp.Jurisdiction = j.Jurisdiction
),
fm AS (
  SELECT x.ChargeKey, x.LegalEntity, x.Jurisdiction, m.PnlFunction, m.GlAccount, m.GlTitle, m.RuleId,
         ROW_NUMBER() OVER (PARTITION BY x.ChargeKey, x.LegalEntity, x.Jurisdiction ORDER BY m.Priority) AS rn
  FROM fn x JOIN ref_gl_account_map m
    ON (m.EntityType IS NULL OR m.EntityType = x.EntityType)
   AND (m.FunctionCode IS NULL OR m.FunctionCode = x.FunctionCode)
   AND ((m.MatchColumn = 'ChargeCategory' AND x.ChargeCategory = m.MatchValue)
     OR (m.MatchColumn = 'ServiceCategory' AND x.ServiceCategory = m.MatchValue)
     OR (m.MatchColumn = 'ServiceName' AND x.ServiceName = m.MatchValue)
     OR (m.MatchColumn = 'CostCenter' AND x.CostCenter = m.MatchValue)
     OR (m.MatchColumn = 'FunctionCode' AND x.FunctionCode = m.MatchValue))
),
lab AS (SELECT DISTINCT FercAccount FROM {t['account']} WHERE LaborOnly),
placed AS (
  SELECT x.*, m.GlAccount AS MapAccount,
         CASE WHEN x.IsCommit = 1 THEN {commit_acct} ELSE {pick("account", "m.GlAccount")} END AS Acct,
         CASE WHEN x.IsCommit = 1 THEN {commit_title} ELSE {pick("title", "m.GlTitle")} END AS AcctTitle,
         CASE WHEN x.IsCommit = 1 THEN {commit_fn} ELSE {pick("function", "m.PnlFunction")} END AS AcctFunction,
         CASE WHEN x.IsTax = 1 THEN {_lit(rules.get('tax', 'R12'))}
              WHEN x.IsCredit = 1 THEN {_lit(rules.get('credit', 'R13'))}
              WHEN x.IsCommit = 1 THEN {_lit(commit.get('rule', 'R9'))}
              WHEN {cls_rule} IS NOT NULL THEN {cls_rule}
              WHEN x.AiProduct IS NOT NULL AND m.RuleId IN ({ai_replaces}) THEN {_lit(rules.get('ai', 'R17'))}
              WHEN m.RuleId IS NOT NULL THEN m.RuleId
              WHEN x.Suspense = 1 THEN {_lit(rules.get('service_company', 'R16'))}
              ELSE {by_fn_sql} END AS RuleId,
         CASE WHEN x.ReqCls IS NOT NULL THEN x.WoTag END AS WorkOrderId
  FROM fn x
  LEFT JOIN fm m ON m.ChargeKey = x.ChargeKey AND m.LegalEntity = x.LegalEntity AND m.Jurisdiction = x.Jurisdiction AND m.rn = 1
),
guarded AS (
  SELECT p.*, CASE WHEN l.FercAccount IS NOT NULL THEN 1 ELSE 0 END AS LaborHit
  FROM placed p LEFT JOIN lab l ON l.FercAccount = p.Acct
)
SELECT x.ChargeKey, x.PeriodStart, x.ChargePeriodStart, x.ServiceProviderName, x.ServiceName, x.ServiceCategory,
       x.ChargeCategory, x.ChargeFrequency, x.Application, x.CostCenter, x.PoolMethod, x.LegalEntity, x.Regulated, x.PolicyId, x.Version,
       x.DriverName, x.OpcoShare, x.Jurisdiction, x.JFactor,
       x.AcctFunction,
       CASE WHEN x.LaborHit = 1 THEN NULL ELSE x.Acct END,
       CASE WHEN x.LaborHit = 1 THEN NULL ELSE x.AcctTitle END,
       CASE WHEN x.IsCommit = 1 THEN {commit_cls} ELSE x.Cls END, x.OpcoShare * x.JFactor, x.SourceCost,
       x.SourceCost * x.OpcoShare * x.JFactor,
       {exception}{extra_sql}
FROM guarded x
LEFT JOIN {t['rule']} r ON r.RuleId = x.RuleId
""", cc_params

    # ------------------------------------------------------------------ read side
    def chain(self, scope, period_start=None):
        """Dollars at each allocation step for one period (default: all periods), plus exceptions."""
        where, p = ("WHERE PeriodStart = :ps", {"ps": period_start}) if period_start else ("", {})
        q = lambda sql: [dict(zip(r["columns"], row)) for r in [self.db.query(sql, p, scope, limit=None)] for row in r["rows"]]
        src_where = "WHERE BillingPeriodStart = :ps" if period_start else ""
        source = q(f"SELECT ROUND(SUM(EffectiveCost), 2) AS Total FROM CostAndUsage {src_where}")
        out = {
            "source_total": source[0]["Total"] if source else None,
            "pool": q(f"SELECT PoolMethod, ROUND(SUM(AllocatedCost), 2) AS AllocatedCost FROM allocated_cost {where} GROUP BY 1 ORDER BY 2 DESC"),
            "opco": q(f"""SELECT LegalEntity, Regulated, ROUND(SUM(AllocatedCost), 2) AS AllocatedCost
                          FROM allocated_cost {where} GROUP BY 1, 2 ORDER BY 3 DESC"""),
            "policies": q(f"""SELECT COALESCE(OpcoPolicy, '(none)') AS Policy, MAX(OpcoPolicyVersion) AS Version,
                                     COALESCE(MAX(DriverName), 'fixed entity') AS Basis, ROUND(SUM(AllocatedCost), 2) AS AllocatedCost
                              FROM allocated_cost {where} GROUP BY 1 ORDER BY 4 DESC"""),
            "jurisdiction": q(f"SELECT Jurisdiction, ROUND(SUM(AllocatedCost), 2) AS AllocatedCost FROM allocated_cost {where} GROUP BY 1 ORDER BY 2 DESC"),
            "gl": q(f"""SELECT COALESCE(GlAccount, '(unmapped)') AS GlAccount, COALESCE(MAX(GlTitle), 'No GL account') AS GlTitle,
                                 CostClass, ROUND(SUM(AllocatedCost), 2) AS AllocatedCost
                          FROM allocated_cost {where} GROUP BY 1, 3 ORDER BY 4 DESC"""),
            "exceptions": q(f"""SELECT Exception, COUNT(*) AS Lines, ROUND(SUM(AllocatedCost), 2) AS AllocatedCost
                                FROM allocated_cost {"WHERE" if not where else where + " AND"} Exception IS NOT NULL
                                GROUP BY 1 ORDER BY 3 DESC"""),
            "formula": "Each charge's effective cost × entity share (allocation key) × jurisdiction factor. Shares sum to 1 per "
                       "charge, so allocated totals tie to the source (control C-10).",
        }
        cols = set(extra_columns())
        if {"RuleId", "Citation", "PolicyFlag"} <= cols:  # the FERC ledger: dollars by placing rule and by billing class
            out["rules"] = q(f"""SELECT RuleId, MAX(Citation) AS Citation, MAX(PolicyFlag) AS PolicyFlag, COUNT(*) AS Lines,
                                        ROUND(SUM(AllocatedCost), 2) AS AllocatedCost
                                 FROM allocated_cost {where} GROUP BY 1 ORDER BY 5 DESC""")
        if "BillingClass" in cols:
            out["billing"] = q(f"""SELECT BillingClass, ROUND(SUM(AllocatedCost), 2) AS AllocatedCost
                                   FROM allocated_cost {where} GROUP BY 1 ORDER BY 1 NULLS LAST""")
        return out
