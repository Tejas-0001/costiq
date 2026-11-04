"""Client-Owned Codebase: the Native Snowflake Pipeline as a dbt Core project the client keeps in its own Git.

  python -m costiq.dbt_export --target snowflake --out <dir>        write the project tree (client-cloud-ledger/)
  python -m costiq.dbt_export --target snowflake --zip <file.zip>   the same tree as one zip

Everything is generated from the live sources of the active tenant, so the project matches what CostIQ runs:

  CostIQ                                    dbt project
  registry use cases (dialect.translate)    analyses/<module>/<id>.sql
  Allocator.sql()                           models/marts/fct_cloud_ledger.sql (incremental, delete+insert by period)
  the allocator's pooling step              models/intermediate/int_tags__resolved.sql
  ref_* registers, allocation_rule          seeds/*.csv (account numbers stay VARCHAR)
  Engines.controls() C-06, C-08, C-10       tests/ (singular tests), plus shares-sum-to-one and the 920 guard
  loader.ddl_statements("snowflake")        snowflake/02_raw_tables.sql
  access.yaml scopes                        governance/ (row access policies, masking)
  required tags                             provider-policy/ (AWS tag policy and SCP, Azure Policy assignments)

Patterns: dbt Core 1.12.3 (Apache 2.0) run by dbt Projects on Snowflake from a Task on a user-managed warehouse;
no remote packages; ledger tables are not transient; money stays NUMBER(38,10); raw rows keep METADATA$FILENAME and
METADATA$FILE_ROW_NUMBER; whole billing periods are replaced with delete+insert (Snowpipe skips a file name it has
loaded in the last 14 days, and insert_overwrite would truncate the table).

Output is deterministic: same sources, same bytes (sorted files and rows, fixed zip timestamps, no build time).
"""
from __future__ import annotations
import argparse, csv, datetime as dt, decimal, io, json, logging, os, re, sys, zipfile
import sqlglot
from sqlglot import exp
from . import dialect

PROJECT = "client_cloud_ledger"
ROOT = "client-cloud-ledger"
DBT_VERSION = "1.12.3"
DATABASE = "LEDGER"
WAREHOUSE = "LEDGER_WH"
PERIOD_TOKEN = "__LEDGER_PERIOD__"
REF, SRC = "zzref__", "zzsrc__raw__"

# Raw FOCUS landing tables: name, what lands there, file pattern, folder pattern that names one export run,
# extra provider columns, row filter applied in staging.
FOCUS_SOURCES = [
    ("aws", "AWS_FOCUS_1_2", "AWS Data Exports, FOCUS 1.2 with AWS columns, Parquet, 'Create new data export file' mode",
     r".*/data/BILLING_PERIOD=.*[.]parquet", r"BILLING_PERIOD=[^/]+/([^/]+)/", [], ""),
    ("azure", "AZURE_FOCUS_1_2P", "Azure Cost Management export, 'Cost and usage details (FOCUS)', Parquet",
     r".*[.]parquet", r"[0-9]{8}-[0-9]{8}/([^/]+)/", [("x_CostAllocationRuleName", "VARCHAR")],
     "x_CostAllocationRuleName IS NULL"),
    ("saas", "SAAS_FOCUS", "FOCUS rows from the SaaS and AI vendor invoice adapters (costiq.adapters), Parquet",
     r".*[.]parquet", r"/([^/]+)$", [], ""),
]
LINEAGE = [("_FILE_NAME", "VARCHAR"), ("_FILE_ROW_NUMBER", "NUMBER(38, 0)"), ("_FILE_CONTENT_KEY", "VARCHAR"),
           ("_FILE_LAST_MODIFIED", "TIMESTAMP_NTZ"), ("_LOADED_AT", "TIMESTAMP_LTZ")]
TAG_DENYLIST = ["aws:createdBy"]  # carries the creating principal's name; never needed for allocation
DEFAULT_REQUIRED_TAGS = ["cost_center", "application"]  # the keys the allocator reads when the tenant lists none
NOT_TAGGABLE = ("Tax", "Credit", "Adjustment")
# Azure built-in policy definitions
AZ_REQUIRE_TAG = "871b6d14-10aa-478d-b590-94f262ecfa99"           # Require a tag on resources
AZ_INHERIT_SUB_TAG = "40df99da-1232-49b1-a39a-6da8d878f469"       # Inherit a tag from the subscription if missing

# Ledger columns a tenant schema adds to allocated_cost (allocation.extra_columns(); the FERC ledger's rule, function,
# invoice and billing-class stamps): description and dbt data tests, written to schema.yml only for the columns the
# ledger has. accepted_values lets NULL through (a line held at the service company has no function or billing class).
LEDGER_COLUMN_DOCS: dict[str, tuple[str, list]] = {
    "SubAccountId": ("The charge's account or subscription id.", []),
    "InvoiceId": ("Invoice the charge was billed on.", []),
    "InvoiceDetailId": ("Invoice line the charge belongs to; joins the ledger to fct_invoice_reconciliation.", []),
    "ChargeDescription": ("The provider's charge description, for transaction listings by account.", []),
    "FunctionCode": ("Function the placing rule used: T, D, CUST, AG, GENERAL or NONUTIL; empty on lines held at the service company.",
                     [("accepted_values", ["T", "D", "CUST", "AG", "GENERAL", "NONUTIL"])]),
    "RuleId": ("Mapping rule that placed the line (docs/rule-register.md, fct_rule_register).",
               ["not_null", ("relationships", "RuleId")]),
    "Citation": ("Citation of the placing rule.", []),
    "PolicyFlag": ("Regulation (the account text decides) or POLICY (the company's choice, signed off by the controller).",
                   [("accepted_values", ["Regulation", "POLICY"])]),
    "WorkOrderId": ("Work order of a capitalized line; on a held line, the work order the charge was tagged with.", []),
    "BillingClass": ("457.1 billed directly, 457.2 billed by an allocation factor; empty when the service company keeps the line.",
                     [("accepted_values", ["457.1", "457.2"])]),
}

_BANNED = re.compile(r"\b(demo|demos|sample|samples|synthetic|prototype|preview)\b", re.I)
log = logging.getLogger("costiq.dbt_export")


# ---------------------------------------------------------------------------------------- small helpers
def _clean(text: str) -> str:
    """Product wording only: approvers' persona addresses and labels lose the 'demo' marker."""
    text = re.sub(r"\bdemo\.(?=[\w.+-]*@)", "", text, flags=re.I)
    text = re.sub(r"\s*\((?:demo|sample)\)", "", text, flags=re.I)
    text = re.sub(r"\bon a sample\b", "on a holdout set", text, flags=re.I)
    text = re.sub(r"\bsamples?\b", lambda m: "subset" + ("s" if m.group(0).lower().endswith("s") else ""), text, flags=re.I)
    text = re.sub(r"\b(demo|synthetic|prototype|preview)\b", "", text, flags=re.I)
    return text


def _slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_") or "other"


def _sf_type(t: str) -> str:
    """information_schema data type (DuckDB or PostgreSQL) -> Snowflake type."""
    u = (t or "").upper()
    m = re.match(r"(?:DECIMAL|NUMERIC)\((\d+),\s*(\d+)\)", u)
    if m:
        return f"NUMBER({m.group(1)}, {m.group(2)})"
    if u in ("INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT", "UINTEGER"):
        return "NUMBER(38, 0)"
    if u in ("DOUBLE", "FLOAT", "REAL", "DOUBLE PRECISION"):
        return "FLOAT"
    if u in ("TIMESTAMP", "TIMESTAMP WITHOUT TIME ZONE", "DATETIME"):
        return "TIMESTAMP_NTZ"
    if u in ("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ"):
        return "TIMESTAMP_TZ"
    if u == "DATE":
        return "DATE"
    if u == "BOOLEAN":
        return "BOOLEAN"
    if u in ("JSON", "JSONB"):
        return "VARIANT"
    return "VARCHAR"


def _seed_type(sf: str) -> str:
    return {"VARIANT": "varchar"}.get(sf, sf.lower().replace(" ", ""))


def _cell(v, sf: str) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, separators=(",", ":"))
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    s = str(v)
    if sf.startswith("TIMESTAMP") and re.match(r"^\d{4}-\d{2}-\d{2}T", s):
        s = s.replace("T", " ")
    if sf.startswith("NUMBER") and re.match(r"^-?\d+\.\d+$", s):
        s = s.rstrip("0").rstrip(".") if "." in s else s
    return _clean(s)


def _row_key(row: list[str]) -> tuple:
    """Rows in a stable, readable order: numbers compare as numbers, everything else as text."""
    return tuple((0, float(c), "") if re.fullmatch(r"-?\d+(?:\.\d+)?", c) else (1, 0.0, c) for c in row)


def _csv(header: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def _lit(v) -> exp.Expression:
    if v is None:
        return exp.Null()
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, (int, float, decimal.Decimal)):
        return exp.Literal.number(v)
    if isinstance(v, dt.datetime):
        return exp.Cast(this=exp.Literal.string(v.isoformat(sep=" ")), to=exp.DataType.build("TIMESTAMP"))
    if isinstance(v, dt.date):
        return exp.Cast(this=exp.Literal.string(v.isoformat()), to=exp.DataType.build("DATE"))
    return exp.Literal.string(str(v))


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(esc(h) for h in header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


# ---------------------------------------------------------------------------------------- Jinja stripping + validation
_CONFIG = re.compile(r"\{\{\s*config\(.*?\)\s*\}\}", re.S)


def strip_jinja(text: str) -> list[str]:
    """SQL left once the Jinja is taken out, for parsing: the main text plus each {% set %}...{% endset %} block.
    ref('x') -> x, source('a', 'b') -> a.b, any other {{ ... }} -> NULL; a `{% if not loop.last %}` separator is dropped
    (a loop body is kept once)."""
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    text = _CONFIG.sub("", text)
    blocks: list[str] = []

    def grab(m):
        blocks.append(m.group(1))
        return ""
    text = re.sub(r"\{%-?\s*set\s+\w+\s*-?%\}(.*?)\{%-?\s*endset\s*-?%\}", grab, text, flags=re.S)
    out = []
    for chunk in [text] + blocks:
        chunk = re.sub(r"\{%-?\s*if not loop\.last\s*-?%\}.*?\{%-?\s*endif\s*-?%\}", "", chunk, flags=re.S)
        chunk = re.sub(r"\{%.*?%\}", "", chunk, flags=re.S)
        chunk = re.sub(r"\{\{\s*ref\(\s*['\"](\w+)['\"]\s*\)\s*\}\}", r"\1", chunk)
        chunk = re.sub(r"\{\{\s*source\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)\s*\}\}", r"\1.\2", chunk)
        chunk = re.sub(r"\{\{.*?\}\}", "NULL", chunk, flags=re.S)
        if chunk.strip() and re.sub(r"--[^\n]*", "", chunk).strip():
            out.append(chunk)
    return out


def _statements(text: str) -> list[str]:
    """Split a setup script on statement-ending semicolons, keeping $$ ... $$ bodies whole."""
    out, cur, in_body = [], [], False
    for line in text.splitlines():
        cur.append(line)
        if line.count("$$") % 2:
            in_body = not in_body
        if not in_body and line.rstrip().endswith(";"):
            stmt = "\n".join(cur).strip()
            if re.sub(r"--[^\n]*", "", stmt).strip(" ;\n"):
                out.append(stmt)
            cur = []
    rest = "\n".join(cur).strip()
    if re.sub(r"--[^\n]*", "", rest).strip():
        out.append(rest)
    return out


_POLICY = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:ROW\s+ACCESS|MASKING)\s+POLICY\b.*?\bRETURNS\s+\w+\s*->(.*?)(?:\bCOMMENT\s*=.*)?;?\s*$",
                     re.S | re.I)


def _parse_statement(stmt: str) -> str:
    """'parsed' or 'command' (sqlglot keeps the statement as text); raises on a syntax error. Policy statements are
    checked through their body (sqlglot 30 cannot parse Snowflake's CREATE ROW ACCESS POLICY grammar); a pipe or task
    is also checked through the statement after AS."""
    stmt = re.sub(r"^(?:\s*--[^\n]*\n)+", "", stmt.strip() + "\n").strip()
    m = _POLICY.match(stmt)
    if m:
        sqlglot.parse_one("SELECT " + m.group(1).strip().rstrip(";"), read="snowflake")
        return "parsed"
    tree = sqlglot.parse_one(stmt.rstrip().rstrip(";"), read="snowflake")
    if isinstance(tree, exp.Command):
        tail = re.search(r"\bAS\s+((?:COPY|SELECT|CALL|INSERT|DELETE|MERGE)\b.*)$", stmt.rstrip().rstrip(";"), re.S | re.I)
        if tail:
            inner = sqlglot.parse_one(tail.group(1), read="snowflake")
            if not isinstance(inner, exp.Command):
                return "parsed"
        return "command"
    return "parsed"


def validate(files: dict[str, str]) -> dict:
    """Parse every .sql file with sqlglot's Snowflake dialect (Jinja stripped). Models, analyses and tests must be
    queries; setup scripts are parsed statement by statement. Also checks the wording rules."""
    report = {"files": 0, "queries": 0, "statements": 0, "commands": [], "errors": [], "wording": []}
    sg = logging.getLogger("sqlglot")
    level = sg.level
    sg.setLevel(logging.ERROR)
    try:
        for path, text in sorted(files.items()):
            m = _BANNED.search(text)
            if m:
                report["wording"].append(f"{path}: '{m.group(0)}'")
            if not path.endswith(".sql"):
                continue
            report["files"] += 1
            rel = path.split("/", 1)[1] if "/" in path else path
            try:
                if rel.split("/")[0] in ("models", "analyses", "tests"):
                    for chunk in strip_jinja(text):
                        tree = sqlglot.parse_one(chunk, read="snowflake")
                        if not isinstance(tree, exp.Query):
                            raise ValueError(f"not a query: {type(tree).__name__}")
                        report["queries"] += 1
                else:
                    for stmt in _statements(text):
                        kind = _parse_statement(stmt)
                        report["statements"] += 1
                        if kind == "command":
                            report["commands"].append(f"{path}: {stmt.split(chr(10))[0][:80]}")
            except Exception as e:  # noqa: BLE001 - reported, not raised
                report["errors"].append(f"{path}: {str(e).splitlines()[0][:300]}")
    finally:
        sg.setLevel(level)
    return report


# ---------------------------------------------------------------------------------------- the generator
class Export:
    """Builds {path: text} for the active tenant from the live registry, allocation engine, database and access model."""

    def __init__(self, db=None, reg=None, eng=None, acc=None):
        if db is None or reg is None or eng is None:
            from . import context
            db, reg, eng = db or context.db, reg or context.reg, eng or context.eng
        if acc is None:
            from .auth import access
            acc = access()
        self.db, self.reg, self.eng, self.acc = db, reg, eng, acc
        self.files: dict[str, str] = {}
        self.notes: list[str] = []
        self._columns = self._schema_columns()
        self.tables = {t.lower(): t for t in self._columns}
        self.seeds = sorted(t for t in self._columns if t.lower().startswith("ref_")) + (
            ["allocation_rule"] if "allocation_rule" in self.tables else [])
        self.sources: set[str] = set()

    # ---------------------------------------------------------------- live database
    def _schema_columns(self) -> dict[str, list[tuple[str, str]]]:
        res = self.db.query("""SELECT table_name, column_name, data_type FROM information_schema.columns
                               WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                               ORDER BY table_name, ordinal_position""", {}, None, limit=None)
        out: dict[str, list[tuple[str, str]]] = {}
        for t, c, typ in res["rows"]:
            out.setdefault(t, []).append((c, _sf_type(typ)))
        return out

    def _rows(self, table: str) -> tuple[list[str], list[list[str]]]:
        cols = self._columns[table]
        sel = ", ".join(f"CAST({c} AS VARCHAR) AS {c}" if t.startswith("NUMBER(") and not t.endswith(", 0)") or t == "VARIANT"
                        else c for c, t in cols)
        res = self.db.query(f"SELECT {sel} FROM {table}", {}, None, limit=None)
        rows = [[_cell(v, t) for v, (_, t) in zip(r, cols)] for r in res["rows"]]
        return [c for c, _ in cols], sorted(rows, key=_row_key)

    # ---------------------------------------------------------------- SQL plumbing
    def _node(self, name: str) -> str | None:
        """dbt node for a CostIQ table name: ref(model or seed), source('raw', table), or None (leave as is)."""
        n = name.lower()
        if n in ("costandusage", "focus_data_table"):
            return REF + "int_focus__v14"
        if n == "allocated_cost":
            return REF + "fct_cloud_ledger"
        if n in {s.lower() for s in self.seeds}:
            return REF + next(s for s in self.seeds if s.lower() == n)
        if n == "contract_commitment":
            n = "contractcommitment"
        if n in self.tables:
            self.sources.add(self.tables[n])
            return SRC + self.tables[n]
        return None

    def _map_tables(self, tree: exp.Expression) -> exp.Expression:
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        for t in list(tree.find_all(exp.Table)):
            if not isinstance(t.this, exp.Identifier) or t.args.get("db") or t.name.lower() in ctes:
                continue
            node = self._node(t.name)
            if not node:
                continue
            if not t.alias:
                t.set("alias", exp.TableAlias(this=exp.to_identifier(t.name)))
            t.set("this", exp.to_identifier(node))
        return tree

    @staticmethod
    def _jinja_names(text: str) -> str:
        text = re.sub(rf"\b{SRC}(\w+)\b", lambda m: "{{ source('raw', '" + m.group(1) + "') }}", text)
        return re.sub(rf"\b{REF}(\w+)\b", lambda m: "{{ ref('" + m.group(1) + "') }}", text)

    def _snowflake(self, tree: exp.Expression) -> str:
        """DuckDB tree (tables already mapped) -> pretty Snowflake SQL with dbt refs; :params stay."""
        sql, _ = dialect.translate(tree.sql(dialect="duckdb"), "snowflake", pretty=True)
        return self._jinja_names(sql)

    # ---------------------------------------------------------------- build
    def build(self) -> dict[str, str]:
        self.ledger_cols = self._ledger()
        self._analyses()
        self._staging()
        self._intermediate_extra()
        self._marts()
        self._seeds()
        self._tests()
        self._snowflake_setup()
        self._governance()
        self._provider_policy()
        self._workflows()
        self._project()
        self._docs()
        self._readme()
        out = {}
        for path, text in self.files.items():
            text = _clean(text)
            out[f"{ROOT}/{path}"] = text if text.endswith("\n") else text + "\n"
        return dict(sorted(out.items()))

    def add(self, path: str, text: str):
        self.files[path] = text.strip("\n") + "\n"

    # ---------------------------------------------------------------- ledger (Allocator.sql)
    def _ledger(self) -> list[str]:
        from .allocation import Allocator, COLUMNS
        sql, params = Allocator(self.db, self.eng).sql()
        ins = sqlglot.parse_one(sql, read="duckdb")
        query = ins.expression if isinstance(ins, exp.Insert) else ins
        cols = [c.name for c in ins.this.expressions] if isinstance(ins, exp.Insert) and isinstance(ins.this, exp.Schema) else list(COLUMNS)
        for ph in list(query.find_all(exp.Placeholder)):
            if ph.name == "ps":
                ph.replace(exp.Cast(this=exp.Literal.string(PERIOD_TOKEN), to=exp.DataType.build("TIMESTAMP")))
            elif ph.name in params:
                ph.replace(_lit(params[ph.name]))
        self.source_cost = "EffectiveCost"
        tags = self._split_pool_step(query)
        # name every output column, money as NUMBER(38,10)
        sel = query if isinstance(query, exp.Select) else query.find(exp.Select)
        projections = []
        for name, proj in zip(cols, sel.expressions):
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            if name.endswith("Cost"):
                inner = exp.Cast(this=inner.copy(), to=exp.DataType.build("DECIMAL(38, 10)"))
            projections.append(exp.alias_(inner.copy(), name))
        sel.set("expressions", projections)
        self._map_tables(query)
        body = self._snowflake(query).replace(f"'{PERIOD_TOKEN}'", "'{{ ps }}'")
        body = "\n".join("  " + line if line else line for line in body.splitlines())
        acct = self._account_col(cols)
        self.add("models/marts/fct_cloud_ledger.sql", f"""
{{{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='PeriodStart',
    on_schema_change='fail',
    full_refresh=false,
    transient=false,
    row_access_policy=(var('governance_schema') ~ '.LEDGER_SCOPE on (CostCenter, LegalEntity)') if var('apply_row_access_policies') else none
) }}}}
-- The cloud ledger: every charge through pool -> legal entity -> jurisdiction -> cost class -> {acct}, one billing
-- period at a time, exactly as CostIQ's allocation engine (Allocator.sql) runs it. A run replaces whole periods
-- (delete+insert on PeriodStart): the open periods on an incremental run, every period on the first build. Periods
-- listed in the ledger_period_close seed are never rewritten; full_refresh=false protects history from --full-refresh.
{{%- set period_sql -%}}
SELECT DISTINCT TO_CHAR(PeriodStart, 'YYYY-MM-DD HH24:MI:SS') AS PeriodStart
FROM {{{{ ref('int_tags__resolved') }}}}
WHERE PeriodStart IS NOT NULL
{{%- if is_incremental() %}}
  AND PeriodStart >= COALESCE(TRY_TO_TIMESTAMP_NTZ('{{{{ var("rebuild_from") }}}}'),
                              DATEADD(MONTH, 1 - {{{{ var("open_periods") }}}}, DATE_TRUNC('MONTH', CURRENT_DATE())))
  AND PeriodStart NOT IN (SELECT PeriodStart FROM {{{{ ref('ledger_period_close') }}}} WHERE PeriodStart IS NOT NULL)
{{%- endif %}}
ORDER BY 1
{{%- endset -%}}
{{%- set periods = (run_query(period_sql).columns[0].values() | list) if execute else [] -%}}
{{%- if periods | length == 0 -%}}{{%- set periods = ['1900-01-01 00:00:00'] -%}}{{%- endif -%}}
{{%- for ps in periods %}}
SELECT * FROM (
{body}
) AS ledger_period
{{%- if not loop.last %}}
UNION ALL
{{%- endif %}}
{{%- endfor %}}
""")
        if tags is not None:
            tags = self._snowflake(tags)
            self.add("models/intermediate/int_tags__resolved.sql", f"""
{{{{ config(materialized='table', transient=false) }}}}
-- Pool step of the ledger, for every billing period: the charge key, the cost center from split-cost allocation tags,
-- then resource tags, then the approved pooling rules (seeds/allocation_rule.csv, compiled in the order CostIQ applies
-- them), else 'Unallocated'. File lineage columns let any ledger line be traced to its source file and row.
{tags}
""")
        return cols

    def _split_pool_step(self, query: exp.Expression) -> exp.Expression | None:
        """Move the allocator's pool CTE (the one that reads CostAndUsage for :ps) into int_tags__resolved for every
        period, and point the ledger at it. Returns the int_tags__resolved query, or None when the engine's SQL does not
        have that shape (the ledger then keeps the step inline)."""
        with_ = query.args.get("with_") or query.args.get("with")
        if not with_:
            return None
        for cte in with_.expressions:
            q = cte.this
            if not isinstance(q, exp.Select) or not any(t.name.lower() == "costandusage" for t in q.find_all(exp.Table)):
                continue
            pred = next((e for e in q.find_all(exp.EQ) if any(isinstance(c, exp.Column) and c.name.lower() == "billingperiodstart"
                                                             for c in (e.this, e.expression))
                         and PERIOD_TOKEN in e.sql()), None)
            alias = next((p.alias for p in q.expressions if isinstance(p, exp.Alias) and isinstance(p.this, exp.Column)
                          and p.this.name.lower() == "billingperiodstart"), None)
            if pred is None or alias is None:
                return None
            for p in q.expressions:
                if isinstance(p, exp.Alias) and p.alias == "SourceCost":
                    self.source_cost = p.this.sql(dialect="duckdb")
            tags = q.copy()
            tpred = next(e for e in tags.find_all(exp.EQ) if PERIOD_TOKEN in e.sql())
            where = tags.args.get("where")
            if where is not None and where.this is tpred:
                tags.set("where", None)
            else:
                tpred.replace(exp.true())
            for c, _ in LINEAGE[:2] + [("_EXPORT_RUN_ID", "")]:
                tags.append("expressions", exp.column(c))
            self._map_tables(tags)
            cte.set("this", sqlglot.parse_one(
                f"SELECT * FROM {REF}int_tags__resolved WHERE {alias} = CAST('{PERIOD_TOKEN}' AS TIMESTAMP)", read="duckdb"))
            return tags
        return None

    @staticmethod
    def _account_col(cols: list[str]) -> str:
        return next((c for c in ("FercAccount", "GlAccount", "Account") if c in cols), "GlAccount")

    # ---------------------------------------------------------------- analyses (registry)
    def _analyses(self):
        labels = self.acc.get("modules", {})
        self.analysis_count = 0
        for uid in self.reg.order:
            uc = self.reg.get(uid)
            if "{{" in uc["sql"] or "{%" in uc["sql"]:
                self.notes.append(f"{uid}: template markers in SQL; not exported")
                continue
            tree = self._map_tables(sqlglot.parse_one(uc["sql"], read="duckdb"))
            text = self._snowflake(tree)
            params = {p["name"]: p for p in uc.get("params", [])}

            def var(m):
                p = params.get(m.group(1), {})
                default = str(p.get("default", "") if p.get("default") is not None else "").replace('"', '\\"')
                return "'{{ var(\"" + m.group(1) + "\", \"" + default + "\") }}'"
            text = dialect.PARAM_RE.sub(var, text)
            folder = _slug(labels.get(uc["module"], uc["module"]))
            name = uid.lower().replace("-", "_")
            head = [f"-- {uid} {uc['title']}", f"-- {uc.get('business_question', '')}".rstrip()]
            if params:
                head.append("-- dbt vars: " + ", ".join(f"{k} (default {p.get('default')})" for k, p in params.items()))
            self.add(f"analyses/{folder}/{name}.sql", "\n".join(head) + "\n" + text)
            self.analysis_count += 1

    # ---------------------------------------------------------------- staging + int_focus__v14
    def _focus_columns(self) -> list[tuple[str, str]]:
        """CostAndUsage columns and Snowflake types, from CostIQ's schema DDL (loader) or else the live table."""
        ddl = self._ddl().get("costandusage")
        if ddl:
            tree = sqlglot.parse_one(ddl, read="snowflake")
            return [(c.name, c.args["kind"].sql(dialect="snowflake")) for c in tree.find_all(exp.ColumnDef)]
        return list(self._columns.get(self.tables.get("costandusage", "CostAndUsage"), []))

    def _ddl(self) -> dict[str, str]:
        if not hasattr(self, "_ddl_cache"):
            out = {}
            try:
                from .loader import ddl_statements
                for s in ddl_statements("snowflake"):
                    m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", s, re.I)
                    if m:
                        out[m.group(1).lower()] = s
            except Exception as e:  # noqa: BLE001 - fall back to the live tables
                self.notes.append(f"schema DDL from the live tables ({e.__class__.__name__})")
            self._ddl_cache = out
        return self._ddl_cache

    def _staging(self):
        self.focus_cols = self._focus_columns()
        names = [c for c, _ in self.focus_cols]
        lineage = ["_FILE_NAME", "_FILE_ROW_NUMBER", "_FILE_LAST_MODIFIED", "_LOADED_AT", "_EXPORT_RUN_ID"]
        deny = ", ".join(f"'{k}'" for k in TAG_DENYLIST)
        norm = ("LOWER(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(t.KEY, '^user:', ''), '([a-z0-9])([A-Z])', '\\\\1_\\\\2'), "
                "'[^A-Za-z0-9_]+', '_'))")
        for key, table, what, _pattern, run_re, extra, row_filter in FOCUS_SOURCES:
            select = []
            for c in names:
                if c == "Tags":
                    select.append("g.Tags AS Tags")
                elif c == "AllocatedTags":
                    select.append("IFF(IS_VARCHAR(k.AllocatedTags), TRY_PARSE_JSON(AS_VARCHAR(k.AllocatedTags)), k.AllocatedTags) AS AllocatedTags")
                elif c == "x_SourceSystem":
                    select.append(f"COALESCE(k.x_SourceSystem, '{key}_focus_export') AS x_SourceSystem")
                else:
                    select.append(f"k.{c}")
            select += [f"k.{c}" for c in lineage]
            cols = ",\n  ".join(select)
            where = f"\n  WHERE {row_filter}" if row_filter else ""
            self.add(f"models/staging/{key}/stg_{key}__focus.sql", f"""
-- {what}.
-- Keeps the latest export run per billing account and billing period (providers restate open periods; a re-delivered
-- period arrives as a new run), normalises tag keys (user:CostCenter -> cost_center) and drops denylisted keys.
WITH src AS (
  SELECT r.*, COALESCE(REGEXP_SUBSTR(r._FILE_NAME, '{run_re}', 1, 1, 'e', 1), 'single') AS _EXPORT_RUN_ID
  FROM {{{{ source('raw', '{table}') }}}} AS r{where}
),
runs AS (
  SELECT BillingAccountId, BillingPeriodStart, _EXPORT_RUN_ID, MAX(_FILE_LAST_MODIFIED) AS RunAt
  FROM src
  GROUP BY BillingAccountId, BillingPeriodStart, _EXPORT_RUN_ID
),
latest AS (
  SELECT BillingAccountId, BillingPeriodStart, _EXPORT_RUN_ID
  FROM runs
  QUALIFY ROW_NUMBER() OVER (PARTITION BY BillingAccountId, BillingPeriodStart ORDER BY RunAt DESC, _EXPORT_RUN_ID DESC) = 1
),
k AS (
  SELECT s.*
  FROM src AS s
  JOIN latest AS l
    ON l.BillingAccountId = s.BillingAccountId
   AND l.BillingPeriodStart = s.BillingPeriodStart
   AND l._EXPORT_RUN_ID = s._EXPORT_RUN_ID
),
tag_pairs AS (
  SELECT k._FILE_NAME, k._FILE_ROW_NUMBER, {norm} AS TagKey, t.VALUE AS TagValue
  FROM k, LATERAL FLATTEN(INPUT => IFF(IS_VARCHAR(k.Tags), TRY_PARSE_JSON(AS_VARCHAR(k.Tags)), k.Tags)) AS t
  WHERE t.KEY NOT IN ({deny})
  QUALIFY ROW_NUMBER() OVER (PARTITION BY k._FILE_NAME, k._FILE_ROW_NUMBER, TagKey ORDER BY t.KEY) = 1
),
g AS (
  SELECT _FILE_NAME, _FILE_ROW_NUMBER, OBJECT_AGG(TagKey, TagValue) AS Tags
  FROM tag_pairs
  GROUP BY _FILE_NAME, _FILE_ROW_NUMBER
)
SELECT
  {cols}
FROM k
LEFT JOIN g
  ON g._FILE_NAME = k._FILE_NAME AND g._FILE_ROW_NUMBER = k._FILE_ROW_NUMBER
""")
        # Snowflake's own spend (FOCUS 1.3 view), off until the column list is checked against the account
        core = {"BilledCost", "EffectiveCost", "ListCost", "ContractedCost", "BillingAccountId", "BillingAccountName",
                "BillingCurrency", "BillingPeriodStart", "BillingPeriodEnd", "ChargeCategory", "ChargeClass",
                "ChargeDescription", "ChargeFrequency", "ChargePeriodStart", "ChargePeriodEnd", "ServiceCategory",
                "ServiceName", "ServiceProviderName", "InvoiceIssuerName", "SubAccountId", "SubAccountName",
                "PricingQuantity", "PricingUnit", "ConsumedQuantity", "ConsumedUnit", "RegionId", "RegionName"}
        sel = [f"v.{c}" if c in core else
               ("'snowflake_focus_view' AS x_SourceSystem" if c == "x_SourceSystem" else f"CAST(NULL AS {t}) AS {c}")
               for c, t in self.focus_cols]
        sel += ["'SNOWFLAKE.BILLING.FOCUS_COST_USAGE_V1_3' AS _FILE_NAME", "CAST(NULL AS NUMBER(38, 0)) AS _FILE_ROW_NUMBER",
                "CAST(NULL AS TIMESTAMP_NTZ) AS _FILE_LAST_MODIFIED", "CAST(NULL AS TIMESTAMP_LTZ) AS _LOADED_AT",
                "'view' AS _EXPORT_RUN_ID"]
        sel_sql = ",\n  ".join(sel)
        self.add("models/staging/snowflake/stg_snowflake__focus.sql", f"""
{{{{ config(enabled=var('include_snowflake_spend')) }}}}
-- Snowflake's own spend, FOCUS 1.3 (SNOWFLAKE.BILLING.FOCUS_COST_USAGE_V1_3 through RAW.SNOWFLAKE_FOCUS_1_3), so the
-- ledger carries the platform it runs on. No Tags column in that view. Enable with include_snowflake_spend: true.
SELECT
  {sel_sql}
FROM {{{{ source('raw', 'SNOWFLAKE_FOCUS_1_3') }}}} AS v
""")
        all_cols = ",\n  ".join(names + lineage)
        unions = "\nUNION ALL\n".join(f"SELECT\n  {all_cols}\nFROM {{{{ ref('stg_{k}__focus') }}}}" for k, *_ in FOCUS_SOURCES)
        self.add("models/intermediate/int_focus__v14.sql", f"""
{{{{ config(
    materialized='table',
    transient=false,
    row_access_policy=(var('governance_schema') ~ '.FOCUS_SCOPE on (AllocatedTags, Tags)') if var('apply_row_access_policies') else none
) }}}}
-- Every source conformed to the FOCUS 1.4 + AI extension superset CostIQ reads (CostAndUsage), with file lineage.
-- Columns a source does not have are NULL.
{unions}
{{% if var('include_snowflake_spend') %}}
UNION ALL
SELECT
  {all_cols}
FROM {{{{ ref('stg_snowflake__focus') }}}}
{{% endif %}}
""")

    def _intermediate_extra(self):
        drv = self.tables.get("ref_allocation_driver")
        if drv and {"DriverName", "PeriodStart", "LegalEntity", "DriverValue"} <= {c for c, _ in self._columns[drv]}:
            self.add("models/intermediate/int_allocation__opco.sql", f"""
{{{{ config(materialized='table', transient=false) }}}}
-- Allocation keys: each legal entity's share of every driver, per period (the shares the ledger applies to
-- driver-based policies). Each driver's shares sum to 1 per period (tests/assert_driver_shares_sum_to_one.sql).
SELECT
  PeriodStart,
  DriverName,
  LegalEntity,
  DriverValue,
  DriverValue / NULLIF(SUM(DriverValue) OVER (PARTITION BY PeriodStart, DriverName), 0) AS Share
FROM {{{{ ref('{drv}') }}}}
""")
            self.has_driver_model = True
        else:
            self.has_driver_model = False

    # ---------------------------------------------------------------- marts
    def _required_tags(self) -> tuple[str, str]:
        """(seed name, key column) of the required-tag register; written from the defaults when the tenant has none."""
        t = self.tables.get("ref_required_tag")
        if t:
            cols = [c for c, _ in self._columns[t]]
            key = next((c for c in cols if re.search(r"tag.?key", c, re.I)), next((c for c in cols if "key" in c.lower()), cols[0]))
            return t, key
        self.add("seeds/ref_required_tag.csv", _csv(["TagKey"], [[k] for k in DEFAULT_REQUIRED_TAGS]))
        self.extra_seed_types = {"ref_required_tag": {"TagKey": "varchar"}}
        return "ref_required_tag", "TagKey"

    def required_tag_keys(self) -> list[str]:
        t = self.tables.get("ref_required_tag")
        if not t:
            return list(DEFAULT_REQUIRED_TAGS)
        _, key = self._required_tags()
        header, rows = self._rows(t)
        i = header.index(key)
        return sorted({r[i] for r in rows if r[i]})

    def _marts(self):
        cols = self.ledger_cols
        acct = self._account_col(cols)
        seed, key = self._required_tags()
        nt = ", ".join(f"'{c}'" for c in NOT_TAGGABLE)
        self.add("models/marts/fct_tag_compliance.sql", f"""
{{{{ config(materialized='table', transient=false) }}}}
-- Tag compliance weighted by cost: for each required tag key, the share of taggable spend that carries it, by month,
-- provider and account. Tax, credits, adjustments and lines without a resource cannot be tagged and are reported
-- apart, not counted as violations.
WITH charges AS (
  SELECT
    DATE_TRUNC('MONTH', c.BillingPeriodStart) AS PeriodStart,
    c.ServiceProviderName,
    c.SubAccountId,
    c.EffectiveCost,
    c.Tags,
    c.ChargeCategory IN ({nt}) OR c.ResourceId IS NULL AS NotTaggable
  FROM {{{{ ref('int_focus__v14') }}}} AS c
),
required AS (
  SELECT DISTINCT {key} AS TagKey
  FROM {{{{ ref('{seed}') }}}}
  WHERE {key} IS NOT NULL
)
SELECT
  ch.PeriodStart,
  ch.ServiceProviderName,
  ch.SubAccountId,
  r.TagKey,
  CAST(SUM(IFF(ch.NotTaggable, 0, ch.EffectiveCost)) AS NUMBER(38, 10)) AS TaggableCost,
  CAST(SUM(IFF(NOT ch.NotTaggable AND CAST(GET(ch.Tags, r.TagKey) AS VARCHAR) IS NOT NULL, ch.EffectiveCost, 0)) AS NUMBER(38, 10)) AS TaggedCost,
  CAST(SUM(IFF(ch.NotTaggable, ch.EffectiveCost, 0)) AS NUMBER(38, 10)) AS NotTaggableCost,
  ROUND(100 * SUM(IFF(NOT ch.NotTaggable AND CAST(GET(ch.Tags, r.TagKey) AS VARCHAR) IS NOT NULL, ch.EffectiveCost, 0))
        / NULLIF(SUM(IFF(ch.NotTaggable, 0, ch.EffectiveCost)), 0), 2) AS CoveragePct
FROM charges AS ch
CROSS JOIN required AS r
GROUP BY ch.PeriodStart, ch.ServiceProviderName, ch.SubAccountId, r.TagKey
""")
        if "InvoiceDetail" in self._columns or "invoicedetail" in self.tables:
            self.sources.add(self.tables.get("invoicedetail", "InvoiceDetail"))
            inv = self.tables.get("invoicedetail", "InvoiceDetail")
            self.add("models/marts/fct_invoice_reconciliation.sql", f"""
{{{{ config(materialized='table', transient=false) }}}}
-- Bill unbundling, tied out: each invoice line against the FOCUS charges that carry its InvoiceDetailId. A difference
-- above $1 is an exception (the same rule as CostIQ's reconciliation, control C-01).
WITH charges AS (
  SELECT InvoiceDetailId, SUM(BilledCost) AS ChargeTotal, COUNT(*) AS ChargeLines
  FROM {{{{ ref('int_focus__v14') }}}}
  WHERE InvoiceDetailId IS NOT NULL
  GROUP BY InvoiceDetailId
)
SELECT
  i.InvoiceIssuerName AS Issuer,
  i.InvoiceId,
  i.InvoiceDetailId,
  i.BillingPeriodStart,
  i.ChargeCategory,
  i.BillingCurrency AS Currency,
  i.InvoiceIssueStatus AS IssueStatus,
  CAST(i.BilledCost AS NUMBER(38, 10)) AS Invoiced,
  CAST(c.ChargeTotal AS NUMBER(38, 10)) AS ChargeTotal,
  c.ChargeLines,
  CAST(i.BilledCost - COALESCE(c.ChargeTotal, 0) AS NUMBER(38, 10)) AS Difference,
  IFF(ABS(i.BilledCost - COALESCE(c.ChargeTotal, 0)) <= 1, 'matched', 'exception') AS Status,
  CASE
    WHEN ABS(i.BilledCost - COALESCE(c.ChargeTotal, 0)) <= 1 THEN NULL
    WHEN i.ChargeCategory = 'Tax' THEN 'Tax rate differs from expected'
    WHEN i.BilledCost - COALESCE(c.ChargeTotal, 0) > 0 THEN 'Invoice amount exceeds charge detail'
    ELSE 'Charges exceed invoice'
  END AS Reason
FROM {{{{ source('raw', '{inv}') }}}} AS i
LEFT JOIN charges AS c
  ON c.InvoiceDetailId = i.InvoiceDetailId
""")
        utility = "ref_ferc_account" in self.tables
        direct, shared = ("Direct (457.1)", "Indirect (457.2)") if utility else ("Direct", "Allocated by driver")
        if "BillingClass" in cols:
            # the ledger stamps the service-company billing class on every line (457.1 direct, 457.2 by factor, NULL
            # when the service company keeps it), so the bill groups by that column instead of inferring it
            group = [c for c in ("PeriodStart", "LegalEntity", acct, "CostClass", "BillingClass") if c in cols]
            basis = (f"CASE BillingClass WHEN '457.1' THEN '{direct}' WHEN '457.2' THEN '{shared}' "
                     "ELSE 'Retained at the service company' END")
            how = ("billed directly (457.1), billed by an approved allocation factor (457.2) or retained at the service\n"
                   "-- company, as the ledger's BillingClass records it")
        else:
            group = [c for c in ("PeriodStart", "LegalEntity", acct, "CostClass") if c in cols]
            basis = f"IFF(DriverName IS NULL, '{direct}', '{shared}')" if "DriverName" in cols else f"'{direct}'"
            how = "charged directly (a fixed\n-- entity) or spread by an approved allocation key"
        group_sql, group_by = ",\n  ".join(group), ", ".join(group)
        self.add("models/marts/fct_service_company_billing.sql", f"""
{{{{ config(materialized='table', transient=false) }}}}
-- What each receiving entity is billed for shared cloud cost, by period and account: {how}, with the number of ledger lines behind each amount.
SELECT
  {group_sql},
  {basis} AS BillingBasis,
  COUNT(DISTINCT ChargeKey) AS ChargeLines,
  CAST(SUM(AllocatedCost) AS NUMBER(38, 10)) AS BilledAmount
FROM {{{{ ref('fct_cloud_ledger') }}}}
GROUP BY {group_by}, BillingBasis
""")
        rules = self._rule_register_model(cols)
        self.add("models/marts/schema.yml", f"""
version: 2

models:
  - name: fct_cloud_ledger
    description: "Allocated cloud ledger: one row per charge, receiving entity and jurisdiction, with policy, cost class and account."
    columns:
      - name: ChargeKey
        data_tests: [not_null]
      - name: PeriodStart
        data_tests: [not_null]
      - name: LegalEntity
        data_tests: [not_null]
      - name: AllocatedCost
        data_tests: [not_null]
{self._ledger_column_docs(cols)}  - name: fct_tag_compliance
    description: "Cost-weighted coverage of each required tag key."
  - name: fct_invoice_reconciliation
    description: "Invoice lines tied to the FOCUS charges that carry their InvoiceDetailId."
  - name: fct_service_company_billing
    description: "Shared cost billed to each receiving entity, direct or by allocation key."
{rules}""")

    def _rule_seed(self) -> str | None:
        """dbt name of the mapping-rule register (seed, or its parsed model when it holds JSON), if the tenant has one."""
        return self.tables.get("ref_ferc_rule")

    def _ledger_column_docs(self, cols: list[str]) -> str:
        """schema.yml entries (with data tests) for the ledger columns in LEDGER_COLUMN_DOCS that the ledger has."""
        out = []
        for c in cols:
            if c not in LEDGER_COLUMN_DOCS:
                continue
            text, tests = LEDGER_COLUMN_DOCS[c]
            entry = [f"      - name: {c}", f"        description: {_yaml_str(text)}"]
            lines = []
            for t in tests:
                if t == "not_null":
                    lines.append("          - not_null")
                elif t[0] == "accepted_values":
                    lines += ["          - accepted_values:", "              arguments:",
                              "                values: [" + ", ".join(_yaml_str(v) for v in t[1]) + "]"]
                elif t[0] == "relationships" and self._rule_seed():
                    lines += ["          - relationships:", "              arguments:",
                              f"                to: ref('{self._rule_seed()}')", f"                field: {t[1]}"]
            if lines:
                entry += ["        data_tests:"] + lines
            out += entry
        return "\n".join(out) + "\n" if out else ""

    def _rule_register_model(self, cols: list[str]) -> str:
        """fct_rule_register (each mapping rule with the lines and dollars it placed per period) and its tie-out test,
        when the ledger stamps RuleId and the tenant has a rule register. Returns its schema.yml entry."""
        seed = self._rule_seed()
        if "RuleId" not in cols or not seed:
            return ""
        self.add("models/marts/fct_rule_register.sql", f"""
{{{{ config(materialized='table', transient=false) }}}}
-- The rule register with the dollars each rule placed: one row per rule and billing period (a rule that placed nothing
-- in any period has one row with no period). Rules marked POLICY are the company's own accounting choices and wait
-- for the controller's sign-off (SignedOffBy, SignedOffAt in the register).
WITH placed AS (
  SELECT
    RuleId,
    PeriodStart,
    COUNT(*) AS AllocatedLines,
    SUM(AllocatedCost) AS AllocatedCost
  FROM {{{{ ref('fct_cloud_ledger') }}}}
  GROUP BY RuleId, PeriodStart
)
SELECT
  r.*,
  p.PeriodStart,
  COALESCE(p.AllocatedLines, 0) AS AllocatedLines,
  CAST(COALESCE(p.AllocatedCost, 0) AS NUMBER(38, 10)) AS AllocatedCost
FROM {{{{ ref('{seed}') }}}} AS r
LEFT JOIN placed AS p
  ON p.RuleId = r.RuleId
""")
        self.add("tests/assert_rule_register_ties_to_ledger.sql", """
-- Every ledger dollar was placed by a rule in the register: dollars by rule sum to the ledger total for every billing
-- period (within $0.01).
WITH ledger AS (
  SELECT PeriodStart, SUM(AllocatedCost) AS AllocatedCost
  FROM {{ ref('fct_cloud_ledger') }}
  GROUP BY PeriodStart
),
by_rule AS (
  SELECT PeriodStart, SUM(AllocatedCost) AS AllocatedCost
  FROM {{ ref('fct_rule_register') }}
  WHERE PeriodStart IS NOT NULL
  GROUP BY PeriodStart
)
SELECT l.PeriodStart, l.AllocatedCost, COALESCE(r.AllocatedCost, 0) AS RuleTotal
FROM ledger AS l
LEFT JOIN by_rule AS r
  ON r.PeriodStart = l.PeriodStart
WHERE ABS(l.AllocatedCost - COALESCE(r.AllocatedCost, 0)) > 0.01
""")
        return ("  - name: fct_rule_register\n"
                "    description: \"Each mapping rule with its citation, basis and the ledger lines and dollars it placed per period.\"\n")

    # ---------------------------------------------------------------- seeds
    def _seeds(self):
        types: dict[str, dict[str, str]] = dict(getattr(self, "extra_seed_types", {}))
        json_seeds = []
        for t in self.seeds:
            header, rows = self._rows(t)
            cols = self._columns[t]
            has_json = any(sf == "VARIANT" for _, sf in cols)
            name = f"{t}__text" if has_json else t
            self.add(f"seeds/{name}.csv", _csv(header, rows))
            types[name] = {c: _seed_type(sf) for c, sf in cols}
            if has_json:
                json_seeds.append((t, name, cols))
        for t, name, cols in json_seeds:
            sel = ",\n  ".join(f"TRY_PARSE_JSON({c}) AS {c}" if sf == "VARIANT" else c for c, sf in cols)
            self.add(f"models/staging/reference/{t}.sql", f"""
-- {t} with its JSON columns parsed (the seed holds them as text).
SELECT
  {sel}
FROM {{{{ ref('{name}') }}}}
""")
        self.add("seeds/ledger_period_close.csv", _csv(["PeriodStart", "ClosedOn", "ClosedBy"], []))
        types["ledger_period_close"] = {"PeriodStart": "timestamp_ntz", "ClosedOn": "date", "ClosedBy": "varchar"}
        lines = ["version: 2", "", "seeds:"]
        for name in sorted(types):
            lines += [f"  - name: {name}", "    config:", "      column_types:"]
            lines += [f"        {c}: {typ}" for c, typ in types[name].items()]
        self.add("seeds/properties.yml", "\n".join(lines))
        self.seed_files = sorted(types)

    # ---------------------------------------------------------------- singular tests (controls)
    def _tests(self):
        cols = self.ledger_cols
        acct = self._account_col(cols)
        self.add("tests/assert_every_dollar_mapped.sql", f"""
{{{{ config(severity='warn') }}}}
-- C-08 Every allocated dollar has an account. Returns the unmapped amount per period (an exception queue, so warn).
SELECT PeriodStart, CAST(SUM(AllocatedCost) AS NUMBER(38, 2)) AS UnmappedCost
FROM {{{{ ref('fct_cloud_ledger') }}}}
WHERE {acct} IS NULL
GROUP BY PeriodStart
HAVING ABS(SUM(AllocatedCost)) >= 0.01
""")
        src = self.source_cost if re.fullmatch(r"\w+", self.source_cost or "") else "EffectiveCost"
        self.add("tests/assert_ledger_ties_to_source.sql", f"""
-- C-10 Allocated total ties to the source total for every billing period (within $0.01).
WITH source_total AS (
  SELECT BillingPeriodStart AS PeriodStart, SUM({src}) AS SourceCost
  FROM {{{{ ref('int_focus__v14') }}}}
  GROUP BY BillingPeriodStart
),
ledger_total AS (
  SELECT PeriodStart, SUM(AllocatedCost) AS AllocatedCost
  FROM {{{{ ref('fct_cloud_ledger') }}}}
  GROUP BY PeriodStart
)
SELECT s.PeriodStart, s.SourceCost, COALESCE(l.AllocatedCost, 0) AS AllocatedCost
FROM source_total AS s
LEFT JOIN ledger_total AS l
  ON l.PeriodStart = s.PeriodStart
WHERE ABS(s.SourceCost - COALESCE(l.AllocatedCost, 0)) > 0.01
""")
        if "Share" in cols:
            self.add("tests/assert_shares_sum_to_one.sql", """
-- Each charge's entity x jurisdiction shares sum to 1, so no dollar is created or lost by allocation.
SELECT PeriodStart, ChargeKey, SUM(Share) AS Shares
FROM {{ ref('fct_cloud_ledger') }}
GROUP BY PeriodStart, ChargeKey
HAVING ABS(SUM(Share) - 1) > 0.000001
""")
        if getattr(self, "has_driver_model", False):
            self.add("tests/assert_driver_shares_sum_to_one.sql", """
-- Each allocation key's shares sum to 1 in every period.
SELECT PeriodStart, DriverName, SUM(Share) AS Shares
FROM {{ ref('int_allocation__opco') }}
GROUP BY PeriodStart, DriverName
HAVING ABS(SUM(Share) - 1) > 0.000001
""")
        labor = "\n  AND COALESCE(CostClass, '') <> 'labor'" if "CostClass" in cols else ""
        if "ref_ferc_account" in self.tables:  # FERC chart of accounts only (920 means nothing in other tenants' charts)
            self.add("tests/assert_no_non_labor_in_920.sql", f"""
-- Guard: account 920 is A&G salaries. A cloud or software invoice line never lands there (exception
-- labor_account_on_non_labor). Recurring cloud fees go to functional O&M.
SELECT PeriodStart, LegalEntity, {acct}, COUNT(*) AS Lines, SUM(AllocatedCost) AS AllocatedCost
FROM {{{{ ref('fct_cloud_ledger') }}}}
WHERE ({acct} = '920' OR {acct} LIKE '920.%'){labor}
GROUP BY PeriodStart, LegalEntity, {acct}
""")
        if (self.files.get("models/intermediate/int_tags__resolved.sql")):
            self.add("tests/assert_charge_keys_unique.sql", """
-- C-06 Replay idempotency: no charge key appears twice in the latest export runs.
SELECT ChargeKey, COUNT(*) AS Copies
FROM {{ ref('int_tags__resolved') }}
GROUP BY ChargeKey
HAVING COUNT(*) > 1
""")

    # ---------------------------------------------------------------- snowflake/ setup
    def _snowflake_setup(self):
        self.add("snowflake/00_account_objects.sql", f"""
-- Database, schemas, warehouse and the deploy role. Run once as an administrator; the deploy role then owns the rest.
CREATE DATABASE IF NOT EXISTS {DATABASE};
CREATE SCHEMA IF NOT EXISTS {DATABASE}.RAW;
CREATE SCHEMA IF NOT EXISTS {DATABASE}.GOVERNANCE;
CREATE SCHEMA IF NOT EXISTS {DATABASE}.PIPELINE;

-- dbt runs need a user-managed warehouse: serverless tasks cannot run a dbt project.
CREATE WAREHOUSE IF NOT EXISTS {WAREHOUSE}
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;

CREATE ROLE IF NOT EXISTS LEDGER_DEPLOYER;
GRANT USAGE ON WAREHOUSE {WAREHOUSE} TO ROLE LEDGER_DEPLOYER;
GRANT ALL ON DATABASE {DATABASE} TO ROLE LEDGER_DEPLOYER;
GRANT ALL ON ALL SCHEMAS IN DATABASE {DATABASE} TO ROLE LEDGER_DEPLOYER;

-- CI deploys with workload identity federation (OIDC from the Git provider): no password or key is stored anywhere.
CREATE USER IF NOT EXISTS LEDGER_CI
  TYPE = SERVICE
  DEFAULT_ROLE = LEDGER_DEPLOYER
  WORKLOAD_IDENTITY = (TYPE = OIDC ISSUER = 'https://token.actions.githubusercontent.com' SUBJECT = 'repo:<org>/<repository>:environment:prod');
GRANT ROLE LEDGER_DEPLOYER TO USER LEDGER_CI;
""")
        self.add("snowflake/01_storage_integrations_and_stages.sql", f"""
-- Where the providers' billing exports land. Snowflake reads the buckets through storage integrations (a role or an
-- app consent the cloud team grants); no access key is stored. Replace the <placeholders> with your locations.
CREATE STORAGE INTEGRATION IF NOT EXISTS BILLING_S3
  TYPE = EXTERNAL_STAGE
  STORAGE_PROVIDER = 'S3'
  ENABLED = TRUE
  STORAGE_AWS_ROLE_ARN = 'arn:aws:iam::<billing-account-id>:role/<snowflake-billing-reader>'
  STORAGE_ALLOWED_LOCATIONS = ('s3://<billing-export-bucket>/<prefix>/');

CREATE STORAGE INTEGRATION IF NOT EXISTS BILLING_AZURE
  TYPE = EXTERNAL_STAGE
  STORAGE_PROVIDER = 'AZURE'
  ENABLED = TRUE
  AZURE_TENANT_ID = '<entra-tenant-id>'
  STORAGE_ALLOWED_LOCATIONS = ('azure://<storage-account>.blob.core.windows.net/<billing-export-container>/');

CREATE NOTIFICATION INTEGRATION IF NOT EXISTS BILLING_AZURE_EVENTS
  ENABLED = TRUE
  TYPE = QUEUE
  NOTIFICATION_PROVIDER = AZURE_STORAGE_QUEUE
  AZURE_STORAGE_QUEUE_PRIMARY_URI = 'https://<storage-account>.queue.core.windows.net/<billing-export-queue>'
  AZURE_TENANT_ID = '<entra-tenant-id>';

CREATE FILE FORMAT IF NOT EXISTS {DATABASE}.RAW.FF_PARQUET TYPE = PARQUET USE_LOGICAL_TYPE = TRUE;
CREATE FILE FORMAT IF NOT EXISTS {DATABASE}.RAW.FF_JSON TYPE = JSON STRIP_OUTER_ARRAY = FALSE;

CREATE STAGE IF NOT EXISTS {DATABASE}.RAW.AWS_FOCUS_STAGE
  URL = 's3://<billing-export-bucket>/<prefix>/<export-name>/'
  STORAGE_INTEGRATION = BILLING_S3
  FILE_FORMAT = {DATABASE}.RAW.FF_PARQUET;

CREATE STAGE IF NOT EXISTS {DATABASE}.RAW.AZURE_FOCUS_STAGE
  URL = 'azure://<storage-account>.blob.core.windows.net/<billing-export-container>/<export-name>/'
  STORAGE_INTEGRATION = BILLING_AZURE
  FILE_FORMAT = {DATABASE}.RAW.FF_PARQUET;

CREATE STAGE IF NOT EXISTS {DATABASE}.RAW.SAAS_FOCUS_STAGE
  URL = 's3://<billing-export-bucket>/<prefix>/saas-focus/'
  STORAGE_INTEGRATION = BILLING_S3
  FILE_FORMAT = {DATABASE}.RAW.FF_PARQUET;
""")
        # raw tables: FOCUS landings with file lineage, the export manifest, Snowflake's FOCUS view, CostIQ inputs
        ddl = self._ddl()
        cu = ddl.get("costandusage")
        parts = [f"-- Raw landing tables. FOCUS rows keep their file lineage (METADATA$FILENAME, METADATA$FILE_ROW_NUMBER ...);\n"
                 f"-- money stays NUMBER(38,10). Column names follow CostIQ's FOCUS 1.4 + AI extension superset, so Parquet\n"
                 f"-- loads by name and columns a provider does not send stay NULL.\nUSE SCHEMA {DATABASE}.RAW;"]
        for _key, table, what, _p, _r, extra, _f in FOCUS_SOURCES:
            cols = self.focus_cols + extra + LINEAGE
            body = ",\n  ".join(f"{c} {t}" for c, t in cols)
            parts.append(f"-- {what}\nCREATE TABLE IF NOT EXISTS {DATABASE}.RAW.{table} (\n  {body}\n);")
        parts.append(f"""-- Export manifests (AWS Manifest.json, Azure _manifest.json): which run is complete for which billing period.
CREATE TABLE IF NOT EXISTS {DATABASE}.RAW.EXPORT_MANIFEST (
  PROVIDER VARCHAR,
  MANIFEST VARIANT,
  _FILE_NAME VARCHAR,
  _FILE_LAST_MODIFIED TIMESTAMP_NTZ,
  _LOADED_AT TIMESTAMP_LTZ
);""")
        parts.append(f"""-- Snowflake's own spend in FOCUS 1.3 (needs the ORGANIZATION_BILLING_VIEWER database role).
CREATE VIEW IF NOT EXISTS {DATABASE}.RAW.SNOWFLAKE_FOCUS_1_3 AS
SELECT * FROM SNOWFLAKE.BILLING.FOCUS_COST_USAGE_V1_3;""")
        inputs = sorted(self.sources, key=str.lower)
        for t in inputs:
            stmt = ddl.get(t.lower())
            if stmt:
                stmt = re.sub(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+", f"CREATE TABLE IF NOT EXISTS {DATABASE}.RAW.{t}", stmt, count=1, flags=re.I)
                stmt = sqlglot.parse_one(stmt, read="snowflake").sql(dialect="snowflake", pretty=True)
            else:
                cols = self._columns.get(t, [])
                stmt = f"CREATE TABLE IF NOT EXISTS {DATABASE}.RAW.{t} (\n  " + ",\n  ".join(f"{c} {ty}" for c, ty in cols) + "\n)"
            parts.append(f"-- CostIQ input: {t}\n{stmt};")
        self.add("snowflake/02_raw_tables.sql", "\n\n".join(parts))
        meta = ("INCLUDE_METADATA = (_FILE_NAME = METADATA$FILENAME, _FILE_ROW_NUMBER = METADATA$FILE_ROW_NUMBER, "
                "_FILE_CONTENT_KEY = METADATA$FILE_CONTENT_KEY, _FILE_LAST_MODIFIED = METADATA$FILE_LAST_MODIFIED, "
                "_LOADED_AT = METADATA$START_SCAN_TIME)")
        pipes = [f"""-- Snowpipe auto-ingest: each new export file is loaded once, by column name, with its file lineage.
-- Snowpipe skips any file path it loaded in the last 14 days, even if the file changed: configure AWS exports in
-- 'Create new data export file' mode (every run in its own folder). The staging models keep the latest run per period.
-- After creating the AWS pipe, point the bucket's event notification at the queue shown by DESC PIPE (notification_channel)."""]
        stages = {"aws": "AWS_FOCUS_STAGE", "azure": "AZURE_FOCUS_STAGE", "saas": "SAAS_FOCUS_STAGE"}
        for key, table, _w, pattern, _r, _e, _f in FOCUS_SOURCES:
            integ = "\n  INTEGRATION = 'BILLING_AZURE_EVENTS'" if key == "azure" else ""
            pipes.append(f"""CREATE PIPE IF NOT EXISTS {DATABASE}.RAW.{table}_PIPE
  AUTO_INGEST = TRUE{integ}
AS
COPY INTO {DATABASE}.RAW.{table}
FROM @{DATABASE}.RAW.{stages[key]}
PATTERN = '{pattern}'
FILE_FORMAT = (FORMAT_NAME = '{DATABASE}.RAW.FF_PARQUET')
MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
{meta};""")
        for key, provider, stage, pattern in (("aws", "aws", "AWS_FOCUS_STAGE", ".*/metadata/.*Manifest[.]json"),
                                              ("azure", "azure", "AZURE_FOCUS_STAGE", ".*_manifest[.]json")):
            integ = "\n  INTEGRATION = 'BILLING_AZURE_EVENTS'" if key == "azure" else ""
            pipes.append(f"""CREATE PIPE IF NOT EXISTS {DATABASE}.RAW.{provider.upper()}_MANIFEST_PIPE
  AUTO_INGEST = TRUE{integ}
AS
COPY INTO {DATABASE}.RAW.EXPORT_MANIFEST (PROVIDER, MANIFEST, _FILE_NAME, _FILE_LAST_MODIFIED, _LOADED_AT)
FROM (
  SELECT '{provider}', $1, METADATA$FILENAME, METADATA$FILE_LAST_MODIFIED, METADATA$START_SCAN_TIME
  FROM @{DATABASE}.RAW.{stage}
)
PATTERN = '{pattern}'
FILE_FORMAT = (FORMAT_NAME = '{DATABASE}.RAW.FF_JSON');""")
        self.add("snowflake/03_pipes.sql", "\n\n".join(pipes))
        meta_sql = meta.replace("'", "''")
        self.add("snowflake/04_reload_period.sql", f"""
-- Only for an export that must stay in OVERWRITE mode: the same file names come back with new content, which Snowpipe
-- will not reload for 14 days. This procedure replaces one billing period whole: delete the period, then COPY its
-- folder with FORCE. Never used for 'Create new data export file' exports.
CREATE OR REPLACE PROCEDURE {DATABASE}.PIPELINE.RELOAD_BILLING_PERIOD(RAW_TABLE VARCHAR, STAGE_PATH VARCHAR, PERIOD VARCHAR)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN
  DELETE FROM IDENTIFIER(:RAW_TABLE) WHERE TO_CHAR(BillingPeriodStart, 'YYYY-MM') = :PERIOD;
  EXECUTE IMMEDIATE 'COPY INTO ' || RAW_TABLE || ' FROM @' || STAGE_PATH || '/BILLING_PERIOD=' || PERIOD || '/'
    || ' FILE_FORMAT = (FORMAT_NAME = ''{DATABASE}.RAW.FF_PARQUET'') MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE'
    || ' {meta_sql} FORCE = TRUE';
  RETURN 'reloaded ' || PERIOD;
END;
$$;
""")
        self.add("snowflake/05_dbt_project_and_task.sql", f"""
-- The dbt project runs inside Snowflake (dbt Projects on Snowflake, dbt Core {DBT_VERSION}). CI deploys it from Git with
-- `snow dbt deploy`; this task builds the ledger every morning on the user-managed warehouse.
CREATE DBT PROJECT IF NOT EXISTS {DATABASE}.PIPELINE.{PROJECT.upper()}
  FROM '@{DATABASE}.PIPELINE.LEDGER_REPOSITORY/branches/main'
  DBT_VERSION = '{DBT_VERSION}'
  DEFAULT_TARGET = 'prod';

CREATE TASK IF NOT EXISTS {DATABASE}.PIPELINE.BUILD_CLOUD_LEDGER
  WAREHOUSE = {WAREHOUSE}
  SCHEDULE = 'USING CRON 0 7 * * * UTC'
AS
EXECUTE DBT PROJECT {DATABASE}.PIPELINE.{PROJECT.upper()} ARGS = 'build --target prod';

ALTER TASK {DATABASE}.PIPELINE.BUILD_CLOUD_LEDGER RESUME;
""")

    # ---------------------------------------------------------------- governance/
    def _governance(self):
        acc = self.acc
        personas = acc.get("personas", {})
        roles = [f"-- One role per CostIQ role (access.yaml personas). Enterprise roles see every cost center and entity.",
                 "CREATE ROLE IF NOT EXISTS COSTIQ_ENTERPRISE;", "CREATE ROLE IF NOT EXISTS COSTIQ_NAME_REVIEWER;"]
        for key in sorted(personas):
            p = personas[key]
            r = f"COSTIQ_{key.upper()}"
            roles.append(f"CREATE ROLE IF NOT EXISTS {r} COMMENT = {_sql_str(p.get('label', key))};")
            if p.get("enterprise"):
                roles.append(f"GRANT ROLE COSTIQ_ENTERPRISE TO ROLE {r};")
        self.add("governance/01_roles.sql", "\n".join(roles))
        scope_rows = []
        for g, ccs in sorted((acc.get("scopeGroups") or {}).items()):
            scope_rows += [(g, "cost_center", cc) for cc in ccs]
        for g, ents in sorted((acc.get("entityScopeGroups") or {}).items()):
            scope_rows += [(g, "legal_entity", e) for e in ents]
        values = ",\n  ".join(f"({_sql_str(g)}, '{k}', {_sql_str(v)})" for g, k, v in scope_rows)
        gov = f"{DATABASE}.GOVERNANCE"
        insert = (f"INSERT INTO {gov}.SCOPE_MAP (ROLE_NAME, SCOPE_TYPE, SCOPE_VALUE) VALUES\n  {values};" if scope_rows else "")
        self.add("governance/02_row_access.sql", f"""
-- The cost-center and entity wall, from access.yaml: scopeGroups (group -> cost centers) and entityScopeGroups
-- (group -> legal entities). Groups arrive as roles through SCIM from the identity provider. Enterprise roles see
-- everything; an entity-scoped role sees only its entities' allocated rows and never the shared, pre-allocation charges.
CREATE TABLE IF NOT EXISTS {gov}.SCOPE_MAP (
  ROLE_NAME VARCHAR,
  SCOPE_TYPE VARCHAR,
  SCOPE_VALUE VARCHAR
);

DELETE FROM {gov}.SCOPE_MAP;
{insert}

CREATE ROW ACCESS POLICY IF NOT EXISTS {gov}.LEDGER_SCOPE
AS (cost_center VARCHAR, legal_entity VARCHAR) RETURNS BOOLEAN ->
  IS_ROLE_IN_SESSION('COSTIQ_ENTERPRISE')
  OR EXISTS (
    SELECT 1
    FROM {gov}.SCOPE_MAP AS m
    WHERE IS_ROLE_IN_SESSION(m.ROLE_NAME)
      AND ((m.SCOPE_TYPE = 'cost_center' AND m.SCOPE_VALUE = cost_center)
        OR (m.SCOPE_TYPE = 'legal_entity' AND m.SCOPE_VALUE = legal_entity))
  );

CREATE ROW ACCESS POLICY IF NOT EXISTS {gov}.FOCUS_SCOPE
AS (allocated_tags VARIANT, tags VARIANT) RETURNS BOOLEAN ->
  IS_ROLE_IN_SESSION('COSTIQ_ENTERPRISE')
  OR EXISTS (
    SELECT 1
    FROM {gov}.SCOPE_MAP AS m
    WHERE IS_ROLE_IN_SESSION(m.ROLE_NAME)
      AND m.SCOPE_TYPE = 'cost_center'
      AND m.SCOPE_VALUE = COALESCE(CAST(GET(allocated_tags, 'cost_center') AS VARCHAR), CAST(GET(tags, 'cost_center') AS VARCHAR))
  );

-- The dbt models attach these on creation (config row_access_policy); the raw landing tables get them here.
ALTER TABLE {DATABASE}.RAW.AWS_FOCUS_1_2 ADD ROW ACCESS POLICY {gov}.FOCUS_SCOPE ON (AllocatedTags, Tags);
ALTER TABLE {DATABASE}.RAW.AZURE_FOCUS_1_2P ADD ROW ACCESS POLICY {gov}.FOCUS_SCOPE ON (AllocatedTags, Tags);
ALTER TABLE {DATABASE}.RAW.SAAS_FOCUS ADD ROW ACCESS POLICY {gov}.FOCUS_SCOPE ON (AllocatedTags, Tags);
""")
        mask_cols = [c for c in ("ResourceName", "ResourceId", "x_UserPrincipal") if c in {n for n, _ in self.focus_cols}]
        alters = "\n".join(f"ALTER TABLE {DATABASE}.RAW.{t} MODIFY COLUMN {c} SET MASKING POLICY {gov}.MASK_FLAGGED_NAME USING ({c}, SubAccountId);"
                           for _k, t, *_ in FOCUS_SOURCES for c in mask_cols)
        self.add("governance/03_masking.sql", f"""
-- Names on flagged accounts are shown as keyed pseudonyms (P-XXXXXXXXXX) to everyone outside COSTIQ_NAME_REVIEWER.
-- Your security team lists the accounts in FLAGGED_ACCOUNT (for example accounts whose resource names need review
-- under your information-protection program) and sets the key in PSEUDONYM_KEY; neither is kept in this repository.
CREATE TABLE IF NOT EXISTS {gov}.FLAGGED_ACCOUNT (
  SUB_ACCOUNT_ID VARCHAR,
  REASON VARCHAR,
  FLAGGED_BY VARCHAR,
  FLAGGED_ON DATE
);

CREATE TABLE IF NOT EXISTS {gov}.PSEUDONYM_KEY (
  KEY_VALUE VARCHAR
);

CREATE MASKING POLICY IF NOT EXISTS {gov}.MASK_FLAGGED_NAME
AS (val VARCHAR, sub_account VARCHAR) RETURNS VARCHAR ->
  CASE
    WHEN val IS NULL THEN NULL
    WHEN NOT EXISTS (SELECT 1 FROM {gov}.FLAGGED_ACCOUNT AS f WHERE f.SUB_ACCOUNT_ID = sub_account) THEN val
    WHEN IS_ROLE_IN_SESSION('COSTIQ_NAME_REVIEWER') THEN val
    ELSE 'P-' || UPPER(LEFT(SHA2(CONCAT((SELECT MAX(KEY_VALUE) FROM {gov}.PSEUDONYM_KEY), LOWER(val)), 256), 10))
  END;

{alters}
""")
        tag_values = {"cost_center": self._values("ref_cost_center", "CostCenter"),
                      "legal_entity": self._values("ref_legal_entity", "LegalEntity")}
        tags = [f"-- Object tags for the pipeline's own objects and columns (ALLOWED_VALUES from the reference seeds).",
                f"CREATE TAG IF NOT EXISTS {gov}.DATA_CLASS ALLOWED_VALUES 'internal', 'restricted' COMMENT = 'Information class of a table or column';"]
        for name, vals in tag_values.items():
            if vals and len(vals) <= 300:
                allowed = ", ".join(_sql_str(v) for v in vals)
                tags.append(f"CREATE TAG IF NOT EXISTS {gov}.{name.upper()} ALLOWED_VALUES {allowed};")
        tags.append(f"ALTER WAREHOUSE {WAREHOUSE} SET TAG {gov}.DATA_CLASS = 'internal';")
        self.add("governance/04_tags.sql", "\n".join(tags))

    def _values(self, table: str, col: str) -> list[str]:
        t = self.tables.get(table)
        if not t or col not in {c for c, _ in self._columns[t]}:
            return []
        header, rows = self._rows(t)
        i = header.index(col)
        return sorted({r[i] for r in rows if r[i]})

    # ---------------------------------------------------------------- provider-policy/
    def _provider_policy(self):
        keys = self.required_tag_keys()
        values = {"cost_center": self._values("ref_cost_center", "CostCenter")}
        for k in keys:
            if re.search(r"opco|entity|company", k):
                values[k] = self._values("ref_legal_entity", "LegalEntity")
            if re.search(r"business_unit|function", k):
                bu = next((c for c in ("BusinessUnit", "FercFunction") if c in {n for n, _ in self._columns.get(self.tables.get("ref_cost_center", ""), [])}), None)
                if bu:
                    values[k] = self._values("ref_cost_center", bu)
        policy = {"tags": {}}
        for k in keys:
            entry = {"tag_key": {"@@assign": k},
                     "enforced_for": {"@@assign": ["ec2:instance", "ec2:volume", "rds:db", "lambda:function"]}}
            if values.get(k):
                entry["tag_value"] = {"@@assign": values[k]}
            policy["tags"][k] = entry
        self.add("provider-policy/aws/tag-policy.json", json.dumps(policy, indent=2, sort_keys=True))
        stmts = []
        for k in keys:
            sid = "Require" + "".join(p.title() for p in re.split(r"[^A-Za-z0-9]+", k) if p)
            stmts.append({"Sid": sid + "OnCreate", "Effect": "Deny",
                          "Action": ["ec2:RunInstances", "ec2:CreateVolume", "rds:CreateDBInstance", "lambda:CreateFunction"],
                          "Resource": "*", "Condition": {"Null": {f"aws:RequestTag/{k}": "true"}}})
        self.add("provider-policy/aws/scp-require-tags.json",
                 json.dumps({"Version": "2012-10-17", "Statement": stmts}, indent=2, sort_keys=True))
        assignments = []
        for k in keys:
            assignments.append({"name": f"require-tag-{_slug(k).replace('_', '-')}", "displayName": f"Require tag {k} on resources",
                                "policyDefinitionId": f"/providers/Microsoft.Authorization/policyDefinitions/{AZ_REQUIRE_TAG}",
                                "parameters": {"tagName": {"value": k}}, "enforcementMode": "Default"})
            assignments.append({"name": f"inherit-tag-{_slug(k).replace('_', '-')}", "displayName": f"Inherit tag {k} from the subscription if missing",
                                "policyDefinitionId": f"/providers/Microsoft.Authorization/policyDefinitions/{AZ_INHERIT_SUB_TAG}",
                                "parameters": {"tagName": {"value": k}}, "enforcementMode": "Default",
                                "identity": {"type": "SystemAssigned"}, "roleDefinition": "Tag Contributor"})
        self.add("provider-policy/azure/policy-assignments.json",
                 json.dumps({"scope": "/providers/Microsoft.Management/managementGroups/<management-group-id>",
                             "assignments": assignments}, indent=2, sort_keys=True))
        key_list = ", ".join("`" + k + "`" for k in keys)
        self.add("provider-policy/README.md", f"""
# Provider tag enforcement

Required tag keys: {key_list}.

| File | What it does | Apply with |
|---|---|---|
| `aws/tag-policy.json` | Organizations tag policy: key spelling and allowed values, enforced on EC2, EBS, RDS and Lambda | `aws organizations create-policy --type TAG_POLICY --content file://aws/tag-policy.json --name required-cost-tags` |
| `aws/scp-require-tags.json` | Service control policy: denies creating those resources without each required tag (`aws:RequestTag`) | `aws organizations create-policy --type SERVICE_CONTROL_POLICY --content file://aws/scp-require-tags.json --name require-cost-tags` |
| `azure/policy-assignments.json` | Built-in policies {AZ_REQUIRE_TAG} (require a tag on resources) and {AZ_INHERIT_SUB_TAG} (inherit a tag from the subscription if missing), one pair per key | `az policy assignment create --name <name> --policy <policyDefinitionId> --scope <scope> --params '{{"tagName": {{"value": "<key>"}}}}'`; the inherit assignments need `--mi-system-assigned` and the Tag Contributor role |

Start in audit mode (`enforcementMode: DoNotEnforce`, a tag policy without `enforced_for`) and compare with
`fct_tag_compliance` before enforcing.
""")

    # ---------------------------------------------------------------- .github/workflows/
    def _workflows(self):
        common = f"""permissions:
  id-token: write
  contents: read

env:
  SNOWFLAKE_ACCOUNT: ${{{{ vars.SNOWFLAKE_ACCOUNT }}}}
  SNOWFLAKE_USER: ${{{{ vars.SNOWFLAKE_CI_USER }}}}
  SNOWFLAKE_ROLE: LEDGER_DEPLOYER
  SNOWFLAKE_WAREHOUSE: {WAREHOUSE}
  SNOWFLAKE_DATABASE: {DATABASE}
  SNOWFLAKE_SCHEMA: PIPELINE
"""
        self.add(".github/workflows/dbt-pull-request.yml", f"""
# Pull request: deploy the branch to its own test object and build it against the dev target. OIDC workload identity:
# no Snowflake password or key is stored in the repository or in CI secrets.
name: ledger-pull-request

on:
  pull_request:
    branches: [main]

{common}
jobs:
  build:
    runs-on: ubuntu-latest
    environment: test
    steps:
      - uses: actions/checkout@v4
      - uses: snowflakedb/snowflake-actions@v3
        with:
          use-oidc: true
      - name: Deploy test object
        run: snow dbt deploy {PROJECT.upper()}_PR_${{{{ github.event.pull_request.number }}}} --source . --dbt-version {DBT_VERSION} -x
      - name: Build and test
        run: snow dbt execute -x {PROJECT.upper()}_PR_${{{{ github.event.pull_request.number }}}} build --target dev
""")
        self.add(".github/workflows/dbt-deploy.yml", f"""
# Merge to main: deploy the project object the production task runs. Never --force (it replaces the object and drops
# its run history).
name: ledger-deploy

on:
  push:
    branches: [main]

{common}
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: prod
    steps:
      - uses: actions/checkout@v4
      - uses: snowflakedb/snowflake-actions@v3
        with:
          use-oidc: true
      - name: Deploy
        run: snow dbt deploy {PROJECT.upper()} --source . --dbt-version {DBT_VERSION} --default-target prod -x
""")

    # ---------------------------------------------------------------- dbt_project.yml, profiles.yml, packages
    def _sources_yml(self):
        lines = ["version: 2", "", "sources:", "  - name: raw", f"    database: {DATABASE}", "    schema: RAW",
                 "    description: \"Landing tables loaded by Snowpipe (snowflake/03_pipes.sql) and the CostIQ inputs.\"",
                 "    tables:"]
        for _k, table, what, *_ in FOCUS_SOURCES:
            lines += [f"      - name: {table}", f"        description: {_yaml_str(what)}",
                      "        loaded_at_field: _LOADED_AT",
                      "        freshness:", "          warn_after: {count: 2, period: day}",
                      "          error_after: {count: 5, period: day}"]
        lines += ["      - name: SNOWFLAKE_FOCUS_1_3",
                  "        description: \"View over SNOWFLAKE.BILLING.FOCUS_COST_USAGE_V1_3 (Snowflake's own spend).\"",
                  "      - name: EXPORT_MANIFEST",
                  "        description: \"Provider export manifests: the runs delivered for each billing period.\""]
        for t in sorted(self.sources, key=str.lower):
            lines += [f"      - name: {t}"]
        self.add("models/sources.yml", "\n".join(lines))

    def _project(self):
        self._sources_yml()
        self.add("dbt_project.yml", f"""
name: {PROJECT}
version: "1.0.0"
config-version: 2
require-dbt-version: "{DBT_VERSION}"
profile: {PROJECT}

model-paths: ["models"]
analysis-paths: ["analyses"]
test-paths: ["tests"]
seed-paths: ["seeds"]
target-path: "target"
log-path: "logs"
clean-targets: ["target", "logs"]

vars:
  open_periods: 2                   # billing periods an incremental run rebuilds: this month and last
  rebuild_from: ""                  # YYYY-MM-DD: rebuild every open period from this date
  include_snowflake_spend: false    # add Snowflake's own FOCUS 1.3 spend to the ledger
  apply_row_access_policies: true   # attach governance/ policies to the ledger and FOCUS tables
  governance_schema: {DATABASE}.GOVERNANCE

models:
  {PROJECT}:
    +transient: false               # ledger tables keep Time Travel and Fail-safe
    staging:
      +materialized: view
      +schema: staging
    intermediate:
      +materialized: table
      +schema: intermediate
    marts:
      +materialized: table
      +schema: marts

seeds:
  {PROJECT}:
    +schema: reference
    +transient: false
    +quote_columns: false
""")
        self.add("profiles.yml", f"""
# dbt Projects on Snowflake runs as the calling role inside the account: account and user stay empty, and there is no
# password or key here.
{PROJECT}:
  target: dev
  outputs:
    dev:
      type: snowflake
      account: ""
      user: ""
      role: LEDGER_DEPLOYER
      database: {DATABASE}
      warehouse: {WAREHOUSE}
      schema: DEV
      threads: 4
    prod:
      type: snowflake
      account: ""
      user: ""
      role: LEDGER_DEPLOYER
      database: {DATABASE}
      warehouse: {WAREHOUSE}
      schema: LEDGER
      threads: 8
""")
        self.add("packages/README.md", """
# Packages

The project uses no dbt packages, so `dbt deps` makes no network call and the dbt project object needs no External
Access Integration. To add one (for example dbt_utils, Apache 2.0), copy its released source into this folder and
reference it in `packages.yml` as a local package:

```yaml
packages:
  - local: packages/dbt_utils
```
""")

    # ---------------------------------------------------------------- docs/
    def _docs(self):
        out = ["# Rule register", ""]
        rule = self.tables.get("ref_ferc_rule")
        if rule:
            header, rows = self._rows(rule)
            out += ["Mapping rules applied by the ledger, in order of evaluation, with their citations. Rules marked [POLICY]",
                    "are the company's own accounting choices and need the controller's sign-off before they go live.", "",
                    _md_table(header, rows), ""]
            out += self._dollars_by_rule()
        amap = self.tables.get("ref_gl_account_map")
        if amap:
            header, rows = self._rows(amap)
            out += ["## Account map", "", "First matching row by Priority sets the account of a default-class line "
                    "(`seeds/" + amap + ".csv`).", "", _md_table(header, rows), ""]
        acct = self.tables.get("ref_ferc_account")
        if acct:
            header, rows = self._rows(acct)
            out += ["## Accounts", "", _md_table(header, rows), ""]
        try:
            from .allocation import classes
            cfg = classes()
            if cfg.get("classes"):
                out += ["## Cost classes", "", "Evaluated in this order; the first class whose condition holds sets the "
                        "account. Anything else is `" + str(cfg.get("default_class")) + "` and goes through the account map.", "",
                        _md_table(["Class", "Condition", "Account", "Title"],
                                  [[c.get("class", ""), c.get("when", ""), c.get("account", "") or "", c.get("title", "") or ""]
                                   for c in cfg["classes"]]), ""]
        except Exception:  # noqa: BLE001 - optional section
            pass
        rules = self.tables.get("allocation_rule")
        if rules:
            header, rows = self._rows(rules)
            out += ["## Pooling rules", "", "Applied after split-cost and resource tags, by Priority (`seeds/allocation_rule.csv`).", "",
                    _md_table(header, rows), ""]
        self.add("docs/rule-register.md", "\n".join(out))

    def _dollars_by_rule(self) -> list[str]:
        """Markdown section: lines and dollars each rule placed in every billing period of the ledger at export time
        (fct_rule_register computes the same in Snowflake). Empty when the ledger does not stamp RuleId."""
        ledger = self.tables.get("allocated_cost")
        if not ledger or "RuleId" not in getattr(self, "ledger_cols", []) or \
                "RuleId" not in {c for c, _ in self._columns[ledger]}:
            return []
        flag = "PolicyFlag" if "PolicyFlag" in {c for c, _ in self._columns[ledger]} else "NULL"
        res = self.db.query(f"""SELECT COALESCE(RuleId, '(none)') AS RuleId, MAX({flag}) AS PolicyFlag,
                                       STRFTIME(PeriodStart, '%Y-%m') AS Period, COUNT(*) AS Lines,
                                       SUM(AllocatedCost) AS AllocatedCost
                                FROM {ledger} GROUP BY 1, 3""", {}, None, limit=None)
        if not res["rows"]:
            return []
        periods = sorted({r[2] for r in res["rows"]})
        by_rule: dict[str, dict] = {}
        for rid, pf, period, lines, cost in res["rows"]:
            d = by_rule.setdefault(rid, {"flag": pf or "", "lines": 0, "cost": {}})
            d["lines"] += int(lines)
            d["cost"][period] = float(cost or 0)

        def order(rid: str):  # R1, R1a, R1b, R2 ... R17, then anything else
            m = re.fullmatch(r"R(\d+)([a-z]?)", rid)
            return (0, int(m.group(1)), m.group(2)) if m else (1, 0, rid)
        money = lambda v: f"{v:,.2f}"
        rows = [[rid, d["flag"], f"{d['lines']:,}"] + [money(d["cost"].get(p, 0.0)) for p in periods]
                + [money(sum(d["cost"].values()))] for rid, d in sorted(by_rule.items(), key=lambda kv: order(kv[0]))]
        totals = [sum(d["cost"].get(p, 0.0) for d in by_rule.values()) for p in periods]
        rows.append(["Total", "", f"{sum(d['lines'] for d in by_rule.values()):,}"] + [money(t) for t in totals]
                    + [money(sum(totals))])
        return ["## Dollars placed by each rule", "",
                "Ledger lines and allocated dollars per rule and billing period when this project was exported. The total is "
                "the ledger total; `fct_rule_register` recomputes it on every build and "
                "`tests/assert_rule_register_ties_to_ledger.sql` checks that the two agree.", "",
                _md_table(["Rule", "Basis", "Lines"] + periods + ["Total"], rows), ""]

    # ---------------------------------------------------------------- README.md
    def _readme(self):
        paths = sorted(set(self.files) | {"README.md"})
        tree = _tree(paths)
        counts = {"analyses": sum(p.startswith("analyses/") for p in paths),
                  "models": sum(p.startswith("models/") and p.endswith(".sql") for p in paths),
                  "seeds": sum(p.startswith("seeds/") and p.endswith(".csv") for p in paths),
                  "tests": sum(p.startswith("tests/") for p in paths)}
        n_models, n_seeds, n_tests, n_analyses = counts["models"], counts["seeds"], counts["tests"], counts["analyses"]
        guard920 = ("| `assert_no_non_labor_in_920` | No cloud or software line lands in 920 (A&G salaries) |\n"
                    if "tests/assert_no_non_labor_in_920.sql" in self.files else "")
        rule_tie = ("| `assert_rule_register_ties_to_ledger` | Every ledger dollar was placed by a rule in the register |\n"
                    if "tests/assert_rule_register_ties_to_ledger.sql" in self.files else "")
        rule_mart = ("| Mart | `fct_rule_register` | Each mapping rule with its citation and the lines and dollars it placed per period |\n"
                     if "models/marts/fct_rule_register.sql" in self.files else "")
        stamps = [c for c in ("RuleId", "Citation", "PolicyFlag", "FunctionCode", "WorkOrderId", "BillingClass", "InvoiceDetailId")
                  if c in getattr(self, "ledger_cols", [])]
        stamped = (f"\nLedger lines also carry {', '.join('`' + c + '`' for c in stamps)} (`models/marts/schema.yml` describes and tests them).\n"
                   if stamps else "")
        self.add("README.md", f"""
# Cloud ledger: Native Snowflake Pipeline

This repository is the Client-Owned Codebase for the cloud ledger: plain SQL, YAML and CSV that turn AWS and Azure
billing exports into an allocated ledger by legal entity, jurisdiction, cost class and account, inside your own
Snowflake account. Every ledger line keeps its charge key, its source file and row, the policy and rule that placed it.

- dbt Core {DBT_VERSION} (Apache 2.0), run by dbt Projects on Snowflake. No dbt platform account, no remote packages.
- Data, code and credentials stay in your Snowflake account and your Git provider.
- {n_models} models, {n_seeds} seeds, {n_tests} control tests, {n_analyses} analyses.

## Layout

```
{tree}
```

## Deploy

1. `snowflake/00_account_objects.sql` to `03_pipes.sql`: database, warehouse, deploy role, CI user (OIDC), storage
   integrations, stages, raw tables and Snowpipe. Replace the `<placeholders>`.
2. `governance/`: roles, the row access policies built from your access model, masking for flagged names, tags.
3. Connect this repository as a Git repository object (`LEDGER.PIPELINE.LEDGER_REPOSITORY`), then run
   `snowflake/05_dbt_project_and_task.sql`. The first `build` loads the seeds and builds every period.
4. `.github/workflows/`: pull requests deploy a test object and run `dbt build`; a merge to main deploys production.
5. `provider-policy/`: tag enforcement at the source (AWS Organizations, Azure Policy).

## How the ledger is built

| Layer | Model | What it does |
|---|---|---|
| Staging | `stg_aws__focus`, `stg_azure__focus`, `stg_saas__focus` | Latest export run per billing period; tag keys normalised; denylisted keys dropped |
| Intermediate | `int_focus__v14` | All sources in one FOCUS 1.4 + AI extension shape, with file lineage |
| Intermediate | `int_tags__resolved` | Pool: cost center from split-cost tags, resource tags, then approved pooling rules |
| Intermediate | `int_allocation__opco` | Allocation keys: each entity's share of each driver per period |
| Mart | `fct_cloud_ledger` | Entity split, jurisdiction, cost class and account per charge; incremental, delete+insert by billing period |
| Mart | `fct_invoice_reconciliation`, `fct_tag_compliance`, `fct_service_company_billing` | Tie-out, tag coverage by cost, entity billing |
{rule_mart}{stamped}
Rules this project follows:
- Whole billing periods are replaced (delete+insert on `PeriodStart`); `insert_overwrite` is never used, because on
  Snowflake it truncates the table. Closed periods (`seeds/ledger_period_close.csv`) are never rewritten.
- AWS exports use 'Create new data export file' mode. Snowpipe skips a file path it loaded in the last 14 days, so an
  export that must overwrite uses `snowflake/04_reload_period.sql` to replace the period.
- Ledger tables are not transient (`+transient: false`): Time Travel and Fail-safe apply.
- Money stays `NUMBER(38,10)`; account numbers stay text (`seeds/properties.yml`).
- Raw rows keep `METADATA$FILENAME` and `METADATA$FILE_ROW_NUMBER` as `_FILE_NAME` and `_FILE_ROW_NUMBER`.
- The build task runs on a user-managed warehouse; serverless tasks cannot run a dbt project.

## Controls

| Test | Control |
|---|---|
| `assert_every_dollar_mapped` | C-08 every allocated dollar has an account (warns: unmapped lines are an exception queue) |
| `assert_ledger_ties_to_source` | C-10 allocated total equals source total per period, within $0.01 |
| `assert_shares_sum_to_one` | Each charge's shares sum to 1 |
{guard920}{rule_tie}| `assert_charge_keys_unique` | C-06 no duplicate charge keys |

Change a policy, rule or account map with a pull request: the review, approval and merge are the evidence trail.
`docs/rule-register.md` lists the rules the ledger applies (with citations where the rule table carries them).
""")


def _sql_str(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _tree(paths: list[str]) -> str:
    """An indented tree of the files, folders first."""
    root: dict = {}
    for p in paths:
        node = root
        for part in p.split("/")[:-1]:
            node = node.setdefault(part + "/", {})
        node[p.split("/")[-1]] = None
    lines = [ROOT + "/"]

    def walk(node, depth):
        folders = sorted(k for k in node if k.endswith("/"))
        files = sorted(k for k in node if not k.endswith("/"))
        for k in folders:
            sub = node[k]
            n = sum(1 for _ in _leaves(sub))
            if k == "analyses/" or (depth >= 1 and n > 6):
                lines.append("  " * (depth + 1) + f"{k}  ({n} files)")
                continue
            lines.append("  " * (depth + 1) + k)
            walk(sub, depth + 1)
        for k in files:
            lines.append("  " * (depth + 1) + k)
    walk(root, 0)
    return "\n".join(lines)


def _leaves(node):
    for k, v in node.items():
        if v is None:
            yield k
        else:
            yield from _leaves(v)


# ---------------------------------------------------------------------------------------- public API
def build(target: str = "snowflake", db=None, reg=None, eng=None, acc=None) -> dict[str, str]:
    """{path: text} of the dbt project for the active tenant."""
    if target != "snowflake":
        raise ValueError("target must be snowflake")
    return Export(db, reg, eng, acc).build()


def to_zip(files: dict[str, str]) -> bytes:
    """Deterministic zip: sorted entries, fixed timestamps and permissions."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, files[path].encode("utf-8"))
    return buf.getvalue()


def write(files: dict[str, str], out_dir: str) -> list[str]:
    written = []
    for path, text in sorted(files.items()):
        full = os.path.join(out_dir, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        written.append(full)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m costiq.dbt_export", description="Export the cloud ledger as a dbt project.")
    ap.add_argument("--target", default="snowflake", choices=["snowflake"])
    ap.add_argument("--out", help="directory to write client-cloud-ledger/ into")
    ap.add_argument("--zip", help="write the project as one zip file")
    ap.add_argument("--no-validate", action="store_true", help="skip the sqlglot parse of every .sql file")
    a = ap.parse_args(argv)
    if not a.out and not a.zip:
        ap.error("give --out <dir> or --zip <file>")
    files = build(a.target)
    if not a.no_validate:
        rep = validate(files)
        print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in rep.items()}))
        for e in rep["errors"] + rep["wording"]:
            print("  ", e, file=sys.stderr)
        if rep["errors"] or rep["wording"]:
            return 1
    if a.out:
        write(files, a.out)
        print(f"wrote {len(files)} files to {os.path.join(a.out, ROOT)}")
    if a.zip:
        with open(a.zip, "wb") as fh:
            fh.write(to_zip(files))
        print(f"wrote {a.zip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
