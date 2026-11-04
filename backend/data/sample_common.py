"""Helpers shared by the sample generators: the active tenant's name tables and the technology-budget rule.

The generators run as scripts (data/build_sample.py calls them with COSTIQ_TENANT set); they read cost-center,
workspace, account and application names from tenants/<tenant>/names.py so the seeded scenarios stay shared.
"""
from __future__ import annotations
import functools, importlib, os, re, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def tenant() -> str:
    name = (os.environ.get("COSTIQ_TENANT") or "pharma").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or not os.path.isdir(os.path.join(ROOT, "tenants", name)):
        raise SystemExit(f"Unknown tenant {name!r} (COSTIQ_TENANT)")
    return name


@functools.lru_cache(maxsize=None)
def names():
    """tenants/<tenant>/names.py as a module."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return importlib.import_module(f"tenants.{tenant()}.names")


def budget_sql(factors: dict, default: float, delete: bool = True) -> list[str]:
    """Technology budgets by cost center: September actuals x a planning factor per cost center, for every month."""
    n = names()
    q = lambda v: str(v).replace("'", "''")
    case = " ".join(f"WHEN '{q(cc)}' THEN {f}" for cc, f in factors.items())
    out = ["DELETE FROM cost_budget"] if delete else []
    out.append(f"""
        INSERT INTO cost_budget
        WITH base AS (
          SELECT COALESCE(json_extract_string(AllocatedTags, '$.cost_center'), json_extract_string(Tags, '$.cost_center'),
                          CASE WHEN ChargeCategory = 'Tax' THEN '{q(n.SHARED_CC)}' END, '{q(n.IT_CC)}') AS CostCenter,
                 SUM(EffectiveCost) AS Sep
          FROM CostAndUsage WHERE BillingPeriodStart = TIMESTAMP '2026-09-01' GROUP BY 1
        )
        SELECT m.BudgetPeriodStart, b.CostCenter,
               ROUND(b.Sep * CASE b.CostCenter {case} ELSE {default} END, -2), 'USD'
        FROM base b CROSS JOIN (SELECT DISTINCT BillingPeriodStart AS BudgetPeriodStart FROM CostAndUsage
                                WHERE BillingPeriodStart >= TIMESTAMP '2026-07-01') m""")
    return out


def refresh_budgets(con):
    """Recompute the technology budgets after each add-on so they cover every stack the sample holds."""
    n = names()
    for stmt in budget_sql(n.BUDGET_FACTORS, n.BUDGET_DEFAULT):
        con.execute(stmt)
