"""Write SQL once (DuckDB dialect), run it on the client's database.

Every query in the use-case registry and in the engines is authored in DuckDB SQL with named
parameters (`:name`). `translate()` turns it into the configured target dialect:

  1. Scope: references to the FOCUS cost tables are replaced by a scope-filtered subquery
     (business-unit row-level security), before any dialect work, so it applies everywhere.
  2. Portability fixes that sqlglot does not do on its own:
       - GROUP BY ALL is expanded to the explicit non-aggregate SELECT expressions
       - json_each(<array>) becomes json_array_elements(<array>) on PostgreSQL
  3. sqlglot transpiles functions, casts, intervals and JSON access to the target.
  4. Named parameters are rendered in the driver's style and returned with an ordered list.

Supported targets: duckdb, postgres (validated in CI against the catalog use cases),
tsql and clickhouse (translation available; validated per engagement).
"""
from __future__ import annotations
import functools, re
import sqlglot
from sqlglot import exp

COST_TABLES = {"costandusage", "focus_data_table"}
SEAT_TABLES = {"ai_seat_assignment"}
ACTIVITY_TABLES = {"ai_user_activity"}
BUDGET_TABLES = {"ai_budget", "cost_budget"}
# Allocated (post-allocation) tables carry a cost center and a legal entity.
ENTITY_TABLES = {"ut_allocated_cost", "prudence_evidence"}
PARAM_RE = re.compile(r"(?<![:\w]):([a-zA-Z_][a-zA-Z0-9_]*)")

# Cost-center attribution used for row-level scope: split-cost allocation tags first, then resource tags.
COST_CENTER_EXPR = ("COALESCE(json_extract_string(AllocatedTags, '$.cost_center'), "
                    "json_extract_string(Tags, '$.cost_center'))")


def _is_aggregate(node: exp.Expression) -> bool:
    for n in node.walk():
        if isinstance(n, (exp.AggFunc, exp.Window)):
            return True
    return False


def expand_group_by_all(tree: exp.Expression) -> exp.Expression:
    for select in tree.find_all(exp.Select):
        group = select.args.get("group")
        if not group:
            continue
        if not group.args.get("all") and not any(
                isinstance(e, exp.Var) and e.name.upper() == "ALL" for e in group.expressions):
            continue
        keys = []
        for proj in select.expressions:
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(inner, exp.Star) or _is_aggregate(inner) or not list(inner.find_all(exp.Column)):
                continue  # aggregates and constants are not grouping keys
            keys.append(inner.copy())
        select.set("group", exp.Group(expressions=keys) if keys else None)
    return tree


def _known_columns() -> dict[str, str]:
    """lower-case -> canonical column names from the schema files (published SQL sometimes uses `ID`
    where FOCUS says `Id`; case matters once identifiers are quoted)."""
    import os
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  # mvp/backend
    names = {}
    for f in sorted(x for x in os.listdir(os.path.join(root, "schema")) if x.endswith(".sql")):
        for m in re.finditer(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s+[A-Z]", open(os.path.join(root, "schema", f), encoding="utf-8").read(), re.M):
            names.setdefault(m.group(1).lower(), m.group(1))
    return names


KNOWN_COLUMNS = _known_columns()


def canonical_case(tree: exp.Expression) -> exp.Expression:
    for col in tree.find_all(exp.Column):
        ident = col.this
        if isinstance(ident, exp.Identifier) and not ident.quoted:
            canon = KNOWN_COLUMNS.get(ident.name.lower())
            if canon and canon != ident.name:
                col.set("this", exp.to_identifier(canon))
    return tree


def round_numeric(tree: exp.Expression) -> exp.Expression:
    """PostgreSQL only has ROUND(numeric, int); cast the argument."""
    for r in list(tree.find_all(exp.Round)):
        if r.args.get("decimals") is not None and not isinstance(r.this, exp.Cast):
            r.set("this", exp.Cast(this=r.this.copy(), to=exp.DataType.build("DECIMAL")))
    return tree


def inline_order_aliases(tree: exp.Expression) -> exp.Expression:
    """PostgreSQL allows a bare alias in ORDER BY but not an alias inside an expression (ABS(Variance))."""
    for select in tree.find_all(exp.Select):
        order = select.args.get("order")
        if not order:
            continue
        aliases = {p.alias: p.this for p in select.expressions if isinstance(p, exp.Alias)}
        for o in order.expressions:
            target = o.this
            if isinstance(target, exp.Column):
                continue
            for c in list(target.find_all(exp.Column)):
                if not c.table and c.name in aliases:
                    c.replace(aliases[c.name].copy())
    return tree


def _json_each_to_pg(tree: exp.Expression) -> exp.Expression:
    def fix(node):
        if isinstance(node, exp.Anonymous) and node.name.upper() == "JSON_EACH":
            return exp.Anonymous(this="JSON_ARRAY_ELEMENTS", expressions=node.expressions)
        if isinstance(node, exp.Func) and node.sql_name().upper() == "JSON_EACH" and not isinstance(node, exp.Anonymous):
            return exp.Anonymous(this="JSON_ARRAY_ELEMENTS", expressions=[node.this] + list(node.expressions))
        return node
    return tree.transform(fix)


def _scope_subquery(table: exp.Table, kind: str, n: int, n_entity: int = 0) -> exp.Expression:
    ph = ", ".join(f":__scope_{i}" for i in range(n))
    eph = ", ".join(f":__entity_{i}" for i in range(n_entity))
    name = table.name
    if n_entity and kind != "entity":
        pred = "1 = 0"  # affiliate wall: entity-scoped users never see shared, pre-allocation rows
    elif kind == "entity":
        preds = ([f"CostCenter IN ({ph})"] if n else []) + ([f"LegalEntity IN ({eph})"] if n_entity else [])
        pred = " AND ".join(preds)
    elif kind == "cost":
        pred = f"{COST_CENTER_EXPR} IN ({ph})"
    elif kind == "seat":
        pred = f"CostCenter IN ({ph})"
    elif kind == "activity":
        pred = f"UserPrincipal IN (SELECT UserPrincipal FROM ai_user_directory WHERE CostCenter IN ({ph}))"
    else:
        pred = f"CostCenter IN ({ph})"
    sub = sqlglot.parse_one(f"(SELECT * FROM {name} WHERE {pred})", read="duckdb")
    alias = table.alias or name
    return exp.Subquery(this=sub.this if isinstance(sub, exp.Subquery) else sub,
                        alias=exp.TableAlias(this=exp.to_identifier(alias)))


def apply_scope(tree: exp.Expression, n_scope: int, n_entity: int = 0) -> exp.Expression:
    if n_scope <= 0 and n_entity <= 0:
        return tree
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}

    def fix(node):
        if isinstance(node, exp.Table) and not node.args.get("db") and node.name.lower() not in ctes:
            nm = node.name.lower()
            kind = ("cost" if nm in COST_TABLES else "seat" if nm in SEAT_TABLES else
                    "activity" if nm in ACTIVITY_TABLES else "budget" if nm in BUDGET_TABLES else
                    "entity" if nm in ENTITY_TABLES else None)
            if kind:
                return _scope_subquery(node, kind, n_scope, n_entity)
        return node
    return tree.transform(fix)


@functools.lru_cache(maxsize=4096)
def _translate_cached(sql: str, target: str, n_scope: int, n_entity: int = 0) -> tuple[str, tuple[str, ...]]:
    tree = sqlglot.parse_one(sql, read="duckdb")
    tree = apply_scope(tree, n_scope, n_entity)
    if target != "duckdb":
        tree = expand_group_by_all(tree)
    if target != "duckdb":
        tree = canonical_case(tree)
        tree = inline_order_aliases(tree)
    if target == "postgres":
        tree = _json_each_to_pg(tree)
        tree = round_numeric(tree)
    if target == "duckdb":
        out = tree.sql(dialect="duckdb")
    else:
        out = tree.sql(dialect=target, identify=(target == "postgres"))
    # sqlglot renders :name placeholders per dialect; normalise back to :name then apply driver style.
    out = re.sub(r"%\((\w+)\)s", r":\1", out)
    out = re.sub(r"\{(\w+): ?[^}]*\}", r":\1", out)
    out = re.sub(r"(?<![:\w])\$([a-zA-Z_]\w*)", r":\1", out)
    order = tuple(PARAM_RE.findall(out))
    return out, order


def translate(sql: str, target: str = "duckdb", n_scope: int = 0, n_entity: int = 0) -> tuple[str, list[str]]:
    """Return (sql in target dialect with :name params, ordered parameter names)."""
    out, order = _translate_cached(sql, target, n_scope, n_entity)
    return out, list(order)


def bind(sql: str, order: list[str], params: dict, style: str):
    """Render :name placeholders in the driver's parameter style."""
    if style == "named_dollar":   # duckdb
        return PARAM_RE.sub(lambda m: "$" + m.group(1), sql), {k: params[k] for k in set(order)}
    if style == "pyformat":       # psycopg
        return PARAM_RE.sub(lambda m: f"%({m.group(1)})s", sql.replace("%", "%%")), {k: params[k] for k in set(order)}
    if style == "qmark":          # pyodbc / SQL Server
        return PARAM_RE.sub("?", sql), [params[k] for k in order]
    raise ValueError(style)
