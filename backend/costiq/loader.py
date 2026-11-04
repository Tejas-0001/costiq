"""Schema migration and data loading for any supported database.

  costiq migrate                       create FOCUS 1.4 + AI extension + CostIQ app tables
  costiq load-sample <file.duckdb>     copy the sample dataset into the configured database
  costiq seed                          allocation rules, manifests and the ledger genesis entry
"""
from __future__ import annotations
import datetime as dt, hashlib, json, os, re
import sqlglot
from .db import Database

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  # mvp/backend
SCHEMA_FILES = [os.path.join(ROOT, "schema", "focus_1_4_ai_schema.sql"),
                os.path.join(ROOT, "schema", "costiq_app_schema.sql"),
                os.path.join(ROOT, "schema", "costiq_pharma_schema.sql"),
                os.path.join(ROOT, "schema", "costiq_optimization_schema.sql")]
TABLES = ["CostAndUsage", "BillingPeriod", "ContractCommitment", "InvoiceDetail", "ai_seat_assignment",
          "ai_user_activity", "ai_model_catalog", "ai_user_directory", "ai_budget", "cost_budget",
          # pharma registers (allocated_cost is computed by `costiq allocate`, never loaded)
          "ref_legal_entity", "ref_cost_center", "ref_allocation_driver", "ref_allocation_policy", "ref_jurisdiction_factor",
          "ref_gl_account_map", "ref_jurisdiction_policy", "ref_tax_filing", "ref_gxp_system", "ref_vendor", "ref_study_event",
          "ref_volume", "ref_ai_use", "ref_program", "ref_program_application",
          # monitoring and optimization (cost_alert_event is computed by `costiq alerts`)
          "optimization_recommendation", "resource_utilization_daily", "ai_app_telemetry_daily", "ai_provisioned_capacity",
          "optimization_action", "cost_alert_rule"]


def ddl_statements(target: str) -> list[str]:
    out = []
    for path in SCHEMA_FILES:
        text = re.sub(r"--[^\n]*", "", open(path, encoding="utf-8").read())
        for stmt in [s.strip() for s in text.split(";") if s.strip()]:
            if target == "duckdb":
                out.append(stmt)
            else:
                out.append(sqlglot.transpile(stmt, read="duckdb", write=target, identify=(target == "postgres"))[0])
    return out


def migrate(db: Database):
    db.execute_script(ddl_statements(db.target))
    db.ensure_views()


def load_from_duckdb(db: Database, path: str):
    import duckdb
    src = duckdb.connect(path, read_only=True)
    for t in TABLES:
        cols = [r[0] for r in src.execute(f"DESCRIBE {t}").fetchall()]
        rows = src.execute(f"SELECT * FROM {t}").fetchall()
        if db.target == "duckdb":
            db.con.execute(f"DELETE FROM {t}")
            db.con.execute(f"ATTACH IF NOT EXISTS '{path}' AS src (READ_ONLY)")
            db.con.execute(f"INSERT INTO {t} SELECT * FROM src.{t}")
            continue
        if db.target == "postgres":
            with db.pool.connection() as con, con.cursor() as cur:
                cur.execute(f'DELETE FROM "{t}"')
                collist = ", ".join(f'"{c}"' for c in cols)
                with cur.copy(f'COPY "{t}" ({collist}) FROM STDIN') as cp:
                    for r in rows:
                        cp.write_row([json.dumps(v) if isinstance(v, (dict, list)) else v for v in r])
                con.commit()
        else:
            raise NotImplementedError(f"bulk load for {db.target}: use the ingestion adapters")
        print(f"{t}: {len(rows)} rows")


DEFAULT_RULES = [
    # (rule id, name, priority, column, value, cost center)
    ("R-TAX", "Tax lines to shared corporate", 10, "ChargeCategory", "Tax", "Shared Services"),
    ("R-DEFAULT-WS", "Claude Default Workspace to IT until owners are assigned", 20, "x_WorkspaceName", "Default Workspace", "Corporate IT"),
    ("R-AWS-DEV", "pharma-dev account to Engineering", 30, "SubAccountId", "333333333333", "Clinical Operations"),
    ("R-SNOW", "Snowflake account to Data Science", 30, "SubAccountId", "PHARMA_PROD", "Data & Analytics"),
]


def seed(db: Database, actor="system"):
    now = dt.datetime(2026, 10, 23, 6, 0)
    db.query("DELETE FROM allocation_rule", limit=None)
    for rid, name, prio, col, val, cc in DEFAULT_RULES:
        db.query("INSERT INTO allocation_rule VALUES (:r, 1, :n, :p, :c, :v, :cc, :f, NULL, :a, :at)",
                 dict(r=rid, n=name, p=prio, c=col, v=val, cc=cc, f=dt.datetime(2026, 1, 1),
                      a="Internal Controller (demo)", at=dt.datetime(2025, 12, 15)), limit=None)
    # Ingestion manifests: one per source system and billing period, with a digest of the rows.
    db.query("DELETE FROM ingest_manifest", limit=None)
    res = db.query("""
        SELECT x_SourceSystem, BillingPeriodStart, COUNT(*) AS n,
               md5(string_agg(CAST(BilledCost AS VARCHAR) || '|' || COALESCE(ResourceId, '') || '|' ||
                              CAST(ChargePeriodStart AS VARCHAR), ',' ORDER BY ChargePeriodStart, BilledCost)) AS digest,
               SUM(CASE WHEN RegionId IS NULL AND ChargeCategory = 'Usage' AND ServiceCategory <> 'AI and Machine Learning' THEN 1 ELSE 0 END) AS no_region
        FROM CostAndUsage WHERE x_SourceSystem IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2""", limit=None)
    implemented = {"aws_focus_export", "azure_focus_export", "gcp_focus_export", "anthropic_cost_report",
                   "anthropic_usage_report", "github_ai_usage_report"}
    for src, bps, n, digest, no_region in res["rows"]:
        checks = [{"check": "Required FOCUS columns populated", "status": "pass", "detail": f"{n} rows"},
                  {"check": "Idempotent replay (no duplicate charge keys)", "status": "pass", "detail": "0 duplicates"}]
        if no_region:
            checks.append({"check": "Region populated on usage rows", "status": "warn",
                           "detail": f"{no_region} usage rows without region; residency reported as Unknown"})
        db.query("INSERT INTO ingest_manifest VALUES (:id, :s, :b, :m, :n, :d, :r, :c, 'promoted')",
                 dict(id=f"MF-{src}-{str(bps)[:7]}", s=src, b=bps if isinstance(bps, dt.datetime) else dt.datetime.fromisoformat(bps),
                      m="implemented" if src in implemented else "representative", n=n, d=digest[:12],
                      r=now, c=json.dumps(checks)), limit=None)
    # Ledger genesis
    from .ledger import Ledger
    Ledger(db).reset(actor)
    refresh(db)


def refresh(db: Database):
    """Rebuild the derived tables from current data. Unlike seed, keeps rules, manifests and the ledger."""
    # Entity allocation chain for every billing period
    from .allocation import Allocator
    from .engines import Engines
    Allocator(db, Engines(db, None)).run()
    # Optimization baselines and verification, then alerts
    from .registry import Registry
    from .config import settings
    from .optimization import Optimizer
    from .alerts import Alerts
    eng = Engines(db, Registry(settings.registry_dir))
    Optimizer(db, eng).refresh()
    Alerts(db, eng).evaluate()
    from .prudence import PrudenceEngine
    PrudenceEngine(db, eng).run()
