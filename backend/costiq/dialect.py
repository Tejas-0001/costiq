"""Write SQL once (DuckDB dialect), run it on the client's database.

Every query in the use-case registry and in the engines is authored in DuckDB SQL with named
parameters (`:name`). `translate()` turns it into the configured target dialect:

  1. Scope: references to the FOCUS cost tables are replaced by a scope-filtered subquery
     (business-unit row-level security), before any dialect work, so it applies everywhere.
  2. Portability fixes that sqlglot does not do on its own:
       - GROUP BY ALL is expanded to the explicit non-aggregate SELECT expressions (kept on Snowflake, which has it)
       - json_each(<array>) becomes json_array_elements(<array>) on PostgreSQL
       - Snowflake (see _snowflake): json_each becomes TABLE(FLATTEN(...)); JSON access on VARIANT columns uses
         GET_PATH/GET without PARSE_JSON; interval arithmetic becomes DATEADD; TRY_CAST only receives strings;
         timestamps cast to text use an explicit format so hash keys match DuckDB's
       - Databricks: a JSON key passed as a parameter gets the '$.' path prefix GET_JSON_OBJECT needs
  3. sqlglot transpiles functions, casts, intervals and JSON access to the target.
  4. Named parameters are rendered in the driver's style and returned with an ordered list.

Supported targets: duckdb, postgres (validated in CI against the catalog use cases),
tsql and clickhouse (translation available; validated per engagement), snowflake and databricks
(compiled for the Native Snowflake Pipeline export, dbt_export.py; validated per engagement).
"""
from __future__ import annotations
import functools, glob, os, re
import sqlglot
from sqlglot import exp

COST_TABLES = {"costandusage", "focus_data_table"}
SEAT_TABLES = {"ai_seat_assignment"}
ACTIVITY_TABLES = {"ai_user_activity"}
BUDGET_TABLES = {"ai_budget", "cost_budget"}
# Allocated (post-allocation) tables carry a cost center and a legal entity.
ENTITY_TABLES = {"allocated_cost", "filing_evidence"}
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


def _schema_files() -> list[str]:
    """Core schema files first, then tenant schema files (backend/tenants/<tenant>/*.sql), so a core name wins."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  # mvp/backend
    core = sorted(glob.glob(os.path.join(root, "schema", "*.sql")))
    tenant = sorted(glob.glob(os.path.join(root, "tenants", "*", "*.sql")))
    return core + tenant


def _schema_columns() -> tuple[dict[str, str], dict[str, str]]:
    """(lower-case -> canonical column name, lower-case -> declared type) from the schema files."""
    names, types = {}, {}
    for f in _schema_files():
        text = open(f, encoding="utf-8").read()
        for m in re.finditer(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s+([A-Z][A-Z0-9_]*)", text, re.M):
            names.setdefault(m.group(1).lower(), m.group(1))
            types.setdefault(m.group(1).lower(), m.group(2).upper())
    return names, types


def _known_columns() -> dict[str, str]:
    """lower-case -> canonical column names from the schema files (published SQL sometimes uses `ID`
    where FOCUS says `Id`; case matters once identifiers are quoted)."""
    return _schema_columns()[0]


KNOWN_COLUMNS = _known_columns()
_TYPES = _schema_columns()[1]
# JSON columns become VARIANT on Snowflake; TIMESTAMP (without time zone) columns need an explicit text format.
JSON_COLUMNS = frozenset(k for k, v in _TYPES.items() if v == "JSON")
TIMESTAMP_COLUMNS = frozenset(k for k, v in _TYPES.items() if v in ("TIMESTAMP", "DATETIME"))


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


def _single_source(select: exp.Select) -> str | None:
    """Alias (or name) of the only FROM source of a SELECT without joins, else None."""
    src = select.args.get("from_") or select.args.get("from")
    if src is None or select.args.get("joins"):
        return None
    node = src.this
    if isinstance(node, (exp.Table, exp.Subquery)):
        return node.alias_or_name or None
    return None


def inline_order_aliases(tree: exp.Expression, keep_input_columns: bool = False) -> exp.Expression:
    """PostgreSQL allows a bare alias in ORDER BY but not an alias inside an expression (ABS(Variance)).

    keep_input_columns: when an alias shadows an input column of the same name (COALESCE(GlAccount, '(unmapped)')
    AS GlAccount), an expression such as `GlAccount IS NULL` means the input column, as it does on DuckDB. Inlining
    the alias would lose that (COALESCE(...) IS NULL is never true), so the column is qualified with its source
    instead. Used for Snowflake and Databricks."""
    for select in tree.find_all(exp.Select):
        order = select.args.get("order")
        if not order:
            continue
        aliases = {p.alias: p.this for p in select.expressions if isinstance(p, exp.Alias)}
        source = _single_source(select) if keep_input_columns else None
        for o in order.expressions:
            target = o.this
            if isinstance(target, exp.Column):
                continue
            for c in list(target.find_all(exp.Column)):
                if c.table or c.name not in aliases:
                    continue
                if keep_input_columns and any(x.name.lower() == c.name.lower() for x in aliases[c.name].find_all(exp.Column)):
                    if source:
                        c.set("table", exp.to_identifier(source))
                    continue
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


# ---------------------------------------------------------------------------------------- Snowflake
_SIMPLE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNIT = {"DAYS": "DAY", "MONTHS": "MONTH", "YEARS": "YEAR", "WEEKS": "WEEK", "HOURS": "HOUR", "MINUTES": "MINUTE",
         "SECONDS": "SECOND", "QUARTERS": "QUARTER"}


def _json_each_arg(node: exp.Expression) -> exp.Expression | None:
    """The argument of a json_each(...) table function, or None."""
    if isinstance(node, exp.Anonymous) and node.name.upper() == "JSON_EACH":
        return node.expressions[0] if node.expressions else None
    if isinstance(node, exp.Func) and not isinstance(node, exp.Anonymous) and node.sql_name().upper() == "JSON_EACH":
        return node.this
    return None


def _json_each_tables(tree: exp.Expression) -> list[exp.Table]:
    return [t for t in tree.find_all(exp.Table) if _json_each_arg(t.this) is not None]


def _sf_variant(node: exp.Expression, flat: set[str]) -> bool:
    """True when the expression is VARIANT on Snowflake: a JSON-typed column, a json_each element, or JSON access."""
    if isinstance(node, exp.Column):
        if node.table and node.table.lower() in flat:
            return node.name.lower() == "value"
        return node.name.lower() in JSON_COLUMNS
    if isinstance(node, (exp.JSONExtract, exp.ParseJSON, exp.GetExtract)):
        return True
    if isinstance(node, exp.Paren):
        return _sf_variant(node.this, flat)
    return False


def _sf_path(this: exp.Expression, path: exp.Expression) -> exp.Expression:
    """GET_PATH(this, 'a.b[0].c') for plain keys; nested GET(...) when a key has characters a path cannot hold
    (for example 'user:CostCenter'); GET(this, :param) when the key is a parameter."""
    if isinstance(path, exp.Placeholder) or (isinstance(path, exp.Literal) and path.is_string and not path.name.startswith("$")):
        return exp.GetExtract(this=this, expression=path.copy())
    if isinstance(path, exp.JSONPath):
        parts = [p for p in path.expressions if not isinstance(p, exp.JSONPathRoot)]
        simple = all(isinstance(p, exp.JSONPathSubscript) or (isinstance(p, exp.JSONPathKey) and _SIMPLE_KEY.match(str(p.this)))
                     for p in parts)
        if not simple:
            node = this
            for p in parts:
                key = exp.Literal.number(p.this) if isinstance(p, exp.JSONPathSubscript) else exp.Literal.string(str(p.this))
                node = exp.GetExtract(this=node, expression=key)
            return node
    return exp.JSONExtract(this=this, expression=path.copy(), requires_json=True)


def _sf_json(tree: exp.Expression, flat: set[str]) -> exp.Expression:
    """JSON access on VARIANT inputs: GET_PATH/GET with no PARSE_JSON, text via ::VARCHAR. Deepest first, so a
    nested access sees its (already rewritten, VARIANT) argument."""
    nodes = [n for n in tree.find_all(exp.JSONExtract, exp.JSONExtractScalar, bfs=False)]
    for node in reversed(nodes):
        if node.args.get("requires_json") or not _sf_variant(node.this, flat):
            continue
        got = _sf_path(node.this.copy(), node.expression)
        if isinstance(node, exp.JSONExtractScalar):
            parent = node.parent
            numeric_cast = isinstance(parent, (exp.Cast, exp.TryCast)) and not parent.to.is_type(*exp.DataType.TEXT_TYPES)
            if numeric_cast and isinstance(parent, exp.Cast):
                node.replace(got)  # CAST(variant AS DOUBLE) reads the JSON number directly
                continue
            got = exp.Cast(this=got, to=exp.DataType.build("VARCHAR"))
        node.replace(got)
    return tree


def _sf_flatten(arg: exp.Expression, flat: set[str]) -> exp.Expression:
    arg = arg.copy()
    if not _sf_variant(arg, flat):
        arg = exp.ParseJSON(this=arg)
    return arg


def _sf_json_each(tree: exp.Expression, flat: set[str]) -> exp.Expression:
    # EXISTS (SELECT .. FROM json_each(arr) AS a WHERE <a.value ...>) -> ARRAY_SIZE(FILTER(arr, a -> ...)) > 0:
    # a correlated FLATTEN inside EXISTS is not something every Snowflake plan accepts; FILTER is a plain function.
    for ex in list(tree.find_all(exp.Exists)):
        sel = ex.this
        if not isinstance(sel, exp.Select) or sel.args.get("joins") or sel.args.get("group") or sel.args.get("with_"):
            continue
        src = sel.args.get("from_") or sel.args.get("from")
        tbl = src.this if src else None
        if not isinstance(tbl, exp.Table) or _json_each_arg(tbl.this) is None or not tbl.alias:
            continue
        alias = tbl.alias
        refs = [c for c in sel.find_all(exp.Column) if c.table and c.table.lower() == alias.lower()]
        if any(c.name.lower() != "value" for c in refs) or any(_is_aggregate(p) for p in sel.expressions):
            continue
        where = sel.args.get("where")
        cond = where.this.copy() if where else exp.true()
        for c in [c for c in cond.find_all(exp.Column) if c.table and c.table.lower() == alias.lower()]:
            c.replace(exp.to_identifier(alias))
        arr = exp.Cast(this=_sf_flatten(_json_each_arg(tbl.this), flat), to=exp.DataType.build("ARRAY"))
        lam = exp.Lambda(this=cond, expressions=[exp.to_identifier(alias)])
        ex.replace(exp.GT(this=exp.ArraySize(this=exp.ArrayFilter(this=arr, expression=lam)), expression=exp.Literal.number(0)))
    # Everything else: json_each(arr) AS a  ->  TABLE(FLATTEN(INPUT => arr)) AS a  (a.value is FLATTEN's VALUE column)
    for tbl in _json_each_tables(tree):
        arg = _sf_flatten(_json_each_arg(tbl.this), flat)
        node = exp.TableFromRows(this=exp.Explode(this=exp.Kwarg(this=exp.Var(this="INPUT"), expression=arg)),
                                 alias=tbl.args.get("alias"))
        tbl.replace(node)
    return tree


def _interval_amount(iv: exp.Interval) -> tuple[exp.Expression, str] | None:
    """(amount, unit) of INTERVAL 7 DAY / INTERVAL '30' DAY / INTERVAL '30 days' / INTERVAL (n) MONTH."""
    amount, unit = iv.this, iv.unit.name.upper() if iv.unit else ""
    if isinstance(amount, exp.Literal) and amount.is_string:
        m = re.match(r"^\s*(-?\d+)\s*([A-Za-z]*)\s*$", amount.name)
        if not m:
            return None
        amount = exp.Literal.number(int(m.group(1)))
        unit = unit or m.group(2).upper()
    elif isinstance(amount, exp.Paren):
        amount = amount.this
    unit = _UNIT.get(unit, unit)
    if not unit:
        return None
    return amount.copy(), unit


def _negate(amount: exp.Expression) -> exp.Expression:
    if isinstance(amount, exp.Literal) and not amount.is_string:
        return exp.Literal.number(-int(amount.name)) if re.match(r"^-?\d+$", amount.name) else exp.Neg(this=amount)
    return exp.Neg(this=exp.Paren(this=amount) if not isinstance(amount, (exp.Column, exp.Placeholder)) else amount)


def _sf_intervals(tree: exp.Expression) -> exp.Expression:
    """x +/- INTERVAL n UNIT and date_add(x, INTERVAL n UNIT) -> DATEADD(UNIT, +/-n, x). Innermost first, so
    (x - INTERVAL 1 DAY) - INTERVAL 2 MONTH becomes DATEADD(MONTH, -2, (DATEADD(DAY, -1, x)))."""
    def fix(node):
        if isinstance(node, (exp.Add, exp.Sub)):
            left, right = node.this, node.expression
            if isinstance(right, exp.Interval):
                iv, other = right, left
            elif isinstance(node, exp.Add) and isinstance(left, exp.Interval):
                iv, other = left, right
            else:
                return None
            got = _interval_amount(iv)
            if not got:
                return None
            amount, unit = got
            if isinstance(node, exp.Sub):
                amount = _negate(amount)
            return exp.DateAdd(this=other.copy(), expression=amount, unit=exp.Var(this=unit))
        if isinstance(node, (exp.DateAdd, exp.DateSub)) and isinstance(node.expression, exp.Interval):
            got = _interval_amount(node.expression)
            if not got:
                return None
            amount, unit = got
            if isinstance(node, exp.DateSub):
                amount = _negate(amount)
            return exp.DateAdd(this=node.this.copy(), expression=amount, unit=exp.Var(this=unit))
        return None
    for node in reversed(list(tree.find_all(exp.Add, exp.Sub, exp.DateAdd, exp.DateSub, bfs=False))):
        new = fix(node)
        if new is not None:
            if node is tree:
                return new
            node.replace(new)
    return tree


def _sf_casts(tree: exp.Expression) -> exp.Expression:
    """TRY_CAST only on strings (Snowflake rejects other inputs, and sqlglot would silently drop the TRY_), and
    timestamps cast to text with an explicit format, so md5 keys built from them match DuckDB's text form."""
    for tc in list(tree.find_all(exp.TryCast)):
        val = tc.this
        is_text = (isinstance(val, exp.Literal) and val.is_string) or (
            isinstance(val, exp.Cast) and val.to.is_type(*exp.DataType.TEXT_TYPES))
        if not is_text:
            tc.set("this", exp.Cast(this=val.copy(), to=exp.DataType.build("VARCHAR")))
        tc.set("requires_string", True)
    for c in list(tree.find_all(exp.Cast)):
        if isinstance(c, exp.TryCast) or not c.to.is_type(*exp.DataType.TEXT_TYPES):
            continue
        if isinstance(c.this, exp.Column) and c.this.name.lower() in TIMESTAMP_COLUMNS:
            c.replace(exp.TimeToStr(this=c.this.copy(), format=exp.Literal.string("%Y-%m-%d %H:%M:%S")))
    return tree


def _snowflake(tree: exp.Expression) -> exp.Expression:
    flat = {t.alias.lower() for t in _json_each_tables(tree) if t.alias}
    tree = _sf_json(tree, flat)
    tree = _sf_json_each(tree, flat)
    tree = _sf_intervals(tree)
    tree = _sf_casts(tree)
    return tree


def _databricks(tree: exp.Expression) -> exp.Expression:
    """GET_JSON_OBJECT needs a JSONPath; a key passed as a parameter ('cost_center') gets the '$.' prefix."""
    for js in list(tree.find_all(exp.JSONExtractScalar, exp.JSONExtract)):
        if isinstance(js.expression, exp.Placeholder):
            js.set("expression", exp.DPipe(this=exp.Literal.string("$."), expression=js.expression.copy()))
    return tree


# ---------------------------------------------------------------------------------------- scope
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
def _translate_cached(sql: str, target: str, n_scope: int, n_entity: int = 0,
                      pretty: bool = False) -> tuple[str, tuple[str, ...]]:
    tree = sqlglot.parse_one(sql, read="duckdb")
    tree = apply_scope(tree, n_scope, n_entity)
    if target not in ("duckdb", "snowflake"):  # Snowflake has GROUP BY ALL
        tree = expand_group_by_all(tree)
    if target != "duckdb":
        tree = canonical_case(tree)
        tree = inline_order_aliases(tree, keep_input_columns=target in ("snowflake", "databricks"))
    if target == "postgres":
        tree = _json_each_to_pg(tree)
        tree = round_numeric(tree)
    if target == "snowflake":
        tree = _snowflake(tree)
    if target == "databricks":
        tree = _databricks(tree)
    if target == "duckdb":
        out = tree.sql(dialect="duckdb", pretty=pretty)
    else:
        out = tree.sql(dialect=target, identify=(target == "postgres"), pretty=pretty)
    # sqlglot renders :name placeholders per dialect; normalise back to :name then apply driver style.
    out = re.sub(r"%\((\w+)\)s", r":\1", out)
    out = re.sub(r"\{(\w+): ?[^}]*\}", r":\1", out)
    out = re.sub(r"(?<![:\w])\$([a-zA-Z_]\w*)", r":\1", out)
    order = tuple(PARAM_RE.findall(out))
    return out, order


def translate(sql: str, target: str = "duckdb", n_scope: int = 0, n_entity: int = 0,
              pretty: bool = False) -> tuple[str, list[str]]:
    """Return (sql in target dialect with :name params, ordered parameter names). pretty=True lays the SQL out on
    indented lines (for people reading it: the catalog's SQL views and the dbt export)."""
    out, order = _translate_cached(sql, target, n_scope, n_entity, pretty)
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
