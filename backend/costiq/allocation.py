"""Driver-based, multi-step allocation for a multi-entity pharmaceutical group.

Every charge is followed through five steps and written to allocated_cost with its lineage:

  1. Pool         cost center from split-cost tags, resource tags or approved rules (Engines.cost_center_expr)
  2. Entity split first approved ref_allocation_policy by Priority: an allocation key's shares
                  (enrolled patients, trial sites, active studies, headcount ...) or 100% to a fixed entity
  3. Jurisdiction ref_jurisdiction_factor per entity (e.g. US 93% / Puerto Rico 7%)
  4. Cost class   capital (implementation services, ASC 350-40), qre (recurring subscription in a jurisdiction where
                  cloud fees are claimed as qualifying R&D expenditure) or opex
  5. GL account   first matching ref_gl_account_map row by Priority; capital -> 1750, qre -> 6150

Charges with no matching policy stay on the shared-services entity with Exception 'no_opco_policy'; opex with no GL map
gets 'no_gl_account'. Each period is replaced as a whole (idempotent), so totals always tie back to the source.
"""
from __future__ import annotations
from .engines import CHARGE_KEY

COLUMNS = ["ChargeKey", "PeriodStart", "ChargePeriodStart", "ServiceProviderName", "ServiceName", "ServiceCategory",
           "ChargeCategory", "ChargeFrequency", "Application", "CostCenter", "PoolMethod", "LegalEntity", "Regulated", "OpcoPolicy",
           "OpcoPolicyVersion", "DriverName", "OpcoShare", "Jurisdiction", "JurisdictionFactor", "PnlFunction",
           "GlAccount", "GlTitle", "CostClass", "Share", "SourceCost", "AllocatedCost", "Exception"]

CAPITAL = ("Capitalized software", "1750", "Capitalized software (ASC 350-40)")
QRE = ("Research & Development", "6150", "Qualified research: cloud & software (credit claimed)")


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
        cc, cc_params = self.eng.cost_center_expr()
        method = ("CASE WHEN json_extract_string(AllocatedTags, '$.cost_center') IS NOT NULL THEN 'Split-cost allocation' "
                  "WHEN json_extract_string(Tags, '$.cost_center') IS NOT NULL THEN 'Resource tag' "
                  f"WHEN {cc} <> 'Unallocated' THEN 'Approved rule' ELSE 'Unallocated' END")
        cap_f, cap_a, cap_t = CAPITAL
        qre_f, qre_a, qre_t = QRE
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
         CASE WHEN j.Impl = 'true' THEN 'capital'
              WHEN j.ChargeCategory = 'Purchase' AND j.ChargeFrequency = 'Recurring' AND jp.CreditClaimed THEN 'qre'
              ELSE 'opex' END AS Cls
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
     OR (m.MatchColumn = 'ServiceName' AND x.ServiceName = m.MatchValue))
)
SELECT x.ChargeKey, x.PeriodStart, x.ChargePeriodStart, x.ServiceProviderName, x.ServiceName, x.ServiceCategory,
       x.ChargeCategory, x.ChargeFrequency, x.Application, x.CostCenter, x.PoolMethod, x.LegalEntity, x.Regulated, x.PolicyId, x.Version,
       x.DriverName, x.OpcoShare, x.Jurisdiction, x.JFactor,
       CASE x.Cls WHEN 'capital' THEN '{cap_f}' WHEN 'qre' THEN '{qre_f}' ELSE f.PnlFunction END,
       CASE x.Cls WHEN 'capital' THEN '{cap_a}' WHEN 'qre' THEN '{qre_a}' ELSE f.GlAccount END,
       CASE x.Cls WHEN 'capital' THEN '{cap_t}' WHEN 'qre' THEN '{qre_t}' ELSE f.GlTitle END,
       x.Cls, x.OpcoShare * x.JFactor, x.SourceCost, x.SourceCost * x.OpcoShare * x.JFactor,
       COALESCE(x.Exc, CASE WHEN x.Cls = 'opex' AND f.GlAccount IS NULL THEN 'no_gl_account' END)
FROM ent x
LEFT JOIN fm f ON f.ChargeKey = x.ChargeKey AND f.LegalEntity = x.LegalEntity AND f.Jurisdiction = x.Jurisdiction AND f.rn = 1
""", cc_params

    # ------------------------------------------------------------------ read side
    def chain(self, scope, period_start=None):
        """Dollars at each allocation step for one period (default: all periods), plus exceptions."""
        where, p = ("WHERE PeriodStart = :ps", {"ps": period_start}) if period_start else ("", {})
        q = lambda sql: [dict(zip(r["columns"], row)) for r in [self.db.query(sql, p, scope, limit=None)] for row in r["rows"]]
        src_where = "WHERE BillingPeriodStart = :ps" if period_start else ""
        source = q(f"SELECT ROUND(SUM(EffectiveCost), 2) AS Total FROM CostAndUsage {src_where}")
        return {
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
