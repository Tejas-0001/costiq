"""Pluggable database access. The client chooses the engine; CostIQ speaks one SQL dialect (DuckDB)
and translates per request (see dialect.py).

COSTIQ_DB_URL examples
  duckdb:////data/costiq.duckdb                         embedded, evaluation and small installs
  postgresql://costiq:***@pg.internal:5432/costiq       recommended production default
  mssql+pyodbc://...  /  clickhouse://...               translation targets (validated per engagement)
"""
from __future__ import annotations
import datetime as dt, decimal, threading, time
from urllib.parse import urlparse
from . import dialect

VIEWS_SQL = [
    "CREATE OR REPLACE VIEW focus_data_table AS SELECT * FROM CostAndUsage",
    "CREATE OR REPLACE VIEW contract_commitment AS SELECT * FROM ContractCommitment",
]


def _jsonable(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


class EntityScope(list):
    """Scope of an affiliate-walled user: the legal entities it may see. It behaves as a non-enterprise scope
    everywhere (it is not None), sees only its entities' rows in allocated tables, and never sees the shared,
    pre-allocation charges in the FOCUS cost tables."""


class Database:
    target = "duckdb"
    style = "named_dollar"

    def __init__(self, url: str):
        self.url = url
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def query(self, sql: str, params: dict | None = None, scope: list[str] | None = None, limit: int | None = 5000):
        """Run DuckDB-dialect SQL with :named params on the configured engine.
        `scope` (list of cost centers) applies row-level security; None means enterprise scope."""
        params = dict(params or {})
        entities = list(scope) if isinstance(scope, EntityScope) else []
        if entities:
            scope = None
        if scope is not None and len(scope) == 0:
            scope = ["__no_access__"]  # scoped user with no cost centers sees nothing, never everything
        n = len(scope) if scope else 0
        for i, s in enumerate(scope or []):
            params[f"__scope_{i}"] = s
        for i, e in enumerate(entities):
            params[f"__entity_{i}"] = e
        target_sql, order = dialect.translate(sql, self.target, n, len(entities))
        missing = [p for p in set(order) if p not in params]
        if missing:
            raise ValueError(f"missing parameters: {', '.join(sorted(missing))}")
        bound_sql, bound = dialect.bind(target_sql, order, params, self.style)
        t0 = time.perf_counter()
        cols, rows = self._execute(bound_sql, bound, limit)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return {"columns": cols, "rows": [[_jsonable(v) for v in r] for r in rows], "elapsed_ms": ms,
                "truncated": limit is not None and len(rows) >= limit, "dialect": self.target}

    def ensure_views(self):
        """FinOps Foundation SQL reads `focus_data_table` / `contract_commitment`; map them to FOCUS tables."""
        try:
            self.execute_script([dialect.translate(v, self.target)[0] for v in VIEWS_SQL])
        except Exception:
            pass  # tables not migrated yet; `costiq migrate` calls this again

    def execute_script(self, statements: list[str]):
        for s in statements:
            self._execute(s, {} if self.style != "qmark" else [], None, fetch=False)

    # ------------------------------------------------------------------ engine specific
    def _execute(self, sql, params, limit, fetch=True):
        raise NotImplementedError


class DuckDB(Database):
    target, style = "duckdb", "named_dollar"

    def __init__(self, url):
        super().__init__(url)
        path = urlparse(url).path or ":memory:"
        if url.startswith("duckdb:////"):
            path = "/" + url[len("duckdb:////"):]
        elif url.startswith("duckdb:///"):
            path = url[len("duckdb:///"):]
        self.path = path
        self._con = None
        self._open = threading.Lock()

    @property
    def con(self):
        """Opened on first use, not at import. Only one process may open a DuckDB file, and a dev server with reload
        (`vercel dev`, `uvicorn --reload`) imports the app in its watcher process as well as in the worker."""
        if self._con is None:
            with self._open:
                if self._con is None:
                    import duckdb
                    self._con = duckdb.connect(self.path)
                    self.ensure_views()
        return self._con

    def _execute(self, sql, params, limit, fetch=True):
        con = self.con  # outside the lock: opening runs ensure_views, which comes back through here
        with self._lock:
            cur = con.cursor()
            cur.execute(sql, params or None)
            if not fetch or cur.description is None:
                return [], []
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(limit) if limit else cur.fetchall()
            return cols, rows


class Postgres(Database):
    target, style = "postgres", "pyformat"

    def __init__(self, url):
        super().__init__(url)
        import psycopg_pool
        self.pool = psycopg_pool.ConnectionPool(url.replace("postgresql+psycopg://", "postgresql://"),
                                                min_size=1, max_size=8, open=True)
        self.ensure_views()

    def _execute(self, sql, params, limit, fetch=True):
        with self.pool.connection() as con, con.cursor() as cur:
            cur.execute(sql, params or None)
            if not fetch or cur.description is None:
                con.commit()
                return [], []
            cols = [d.name for d in cur.description]
            rows = cur.fetchmany(limit) if limit else cur.fetchall()
            return cols, rows


class SqlAlchemyDB(Database):
    """SQL Server / ClickHouse through SQLAlchemy drivers supplied by the client image."""

    def __init__(self, url, target):
        super().__init__(url)
        import sqlalchemy
        self.target, self.style = target, "qmark" if target == "tsql" else "named_dollar"
        self.engine = sqlalchemy.create_engine(url)

    def _execute(self, sql, params, limit, fetch=True):
        with self.engine.connect() as con:
            res = con.exec_driver_sql(sql, tuple(params) if isinstance(params, list) else params)
            if not fetch or not res.returns_rows:
                con.commit()
                return [], []
            cols = list(res.keys())
            rows = res.fetchmany(limit) if limit else res.fetchall()
            return cols, rows


def connect(url: str) -> Database:
    scheme = url.split(":", 1)[0]
    if scheme == "duckdb":
        return DuckDB(url)
    if scheme.startswith("postgres"):
        return Postgres(url)
    if scheme.startswith("mssql"):
        return SqlAlchemyDB(url, "tsql")
    if scheme.startswith("clickhouse"):
        return SqlAlchemyDB(url, "clickhouse")
    raise ValueError(f"unsupported database url scheme: {scheme}")
