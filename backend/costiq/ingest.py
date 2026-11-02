"""FOCUS file ingestion (AWS Data Exports FOCUS, Azure Cost Management FOCUS export, GCP FOCUS view
exports, OCI FOCUS reports, or any FOCUS 1.x CSV/Parquet).

  python -m costiq.ingest --source aws_focus_export --period 2026-10 --path '/landing/aws/2026-10/*.parquet'

Behaviour
  * Reads CSV or Parquet with DuckDB (no cloud SDKs needed; files come from the landing zone).
  * Maps columns by name to FOCUS 1.4 + x_ columns (case-insensitive); unknown columns are reported.
  * Idempotent: rows for (source, billing period) are replaced, never appended twice.
  * Writes an ingest_manifest row: record count, sha256 digest, schema checks, maturity.
  * Quarantines the load (no promotion) if a mandatory FOCUS column is missing.
AI vendors that do not publish FOCUS (Anthropic, OpenAI, GitHub, Microsoft 365 Copilot) get their
own adapters that first produce a FOCUS-shaped file; they then load through this same path.
"""
from __future__ import annotations
import argparse, datetime as dt, glob, hashlib, json, sys
import duckdb
from .config import settings
from .db import connect

MANDATORY = ["BilledCost", "BillingAccountId", "BillingCurrency", "BillingPeriodStart", "BillingPeriodEnd",
             "ChargeCategory", "ChargePeriodStart", "ChargePeriodEnd", "ContractedCost", "EffectiveCost", "ListCost",
             "ServiceName", "ServiceCategory", "ServiceProviderName", "InvoiceIssuerName"]


def ingest(db, source: str, period: str, path: str, maturity: str = "implemented"):
    files = sorted(glob.glob(path))
    if not files:
        raise SystemExit(f"no files match {path}")
    src = duckdb.connect()
    reader = "read_parquet" if files[0].endswith(".parquet") else "read_csv_auto"
    src.execute(f"CREATE TABLE f AS SELECT * FROM {reader}(?)", [files])
    file_cols = [r[0] for r in src.execute("DESCRIBE f").fetchall()]
    target_cols = db.query("SELECT * FROM CostAndUsage WHERE 1 = 0")["columns"]
    by_lower = {c.lower(): c for c in file_cols}
    mapped = {t: by_lower.get(t.lower()) for t in target_cols}
    missing = [c for c in MANDATORY if not mapped.get(c)]
    extra = [c for c in file_cols if c.lower() not in {t.lower() for t in target_cols}]
    checks = [{"check": "Mandatory FOCUS columns present", "status": "fail" if missing else "pass",
               "detail": f"missing: {', '.join(missing)}" if missing else f"{len(MANDATORY)} present"}]
    drift = [c for c in file_cols if c in by_lower.values() and c.lower() in {t.lower() for t in target_cols}
             and c not in target_cols]
    if drift:
        checks.append({"check": "Column names match expected schema", "status": "warn",
                       "detail": f"case drift mapped: {', '.join(drift[:5])}"})
    if extra:
        checks.append({"check": "Unrecognised columns", "status": "warn", "detail": ", ".join(extra[:8])})
    select = ", ".join(f'"{mapped[t]}" AS "{t}"' if mapped.get(t) else f'NULL AS "{t}"' for t in target_cols
                       if t != "x_SourceSystem")
    rows = src.execute(f"SELECT {select}, ? AS x_SourceSystem FROM f "
                       f"WHERE strftime(CAST(\"{mapped['BillingPeriodStart']}\" AS TIMESTAMP), '%Y-%m') = ?",
                       [source, period]).fetchall() if not missing else []
    digest = hashlib.sha256("\n".join(map(str, rows)).encode()).hexdigest()
    start = dt.datetime.strptime(period, "%Y-%m")
    status = "quarantined" if missing else "promoted"
    if not missing:
        db.query("DELETE FROM CostAndUsage WHERE x_SourceSystem = :s AND BillingPeriodStart = :b",
                 {"s": source, "b": start}, limit=None)
        cols = ", ".join(target_cols)
        ph = ", ".join(f":c{i}" for i in range(len(target_cols)))
        for r in rows:  # simple and portable; bulk COPY is used by the Postgres loader for large backfills
            db.query(f"INSERT INTO CostAndUsage ({cols}) VALUES ({ph})", {f"c{i}": v for i, v in enumerate(r)}, limit=None)
    db.query("DELETE FROM ingest_manifest WHERE SourceSystem = :s AND BillingPeriodStart = :b", {"s": source, "b": start}, limit=None)
    db.query("INSERT INTO ingest_manifest VALUES (:id, :s, :b, :m, :n, :d, :r, :c, :st)",
             dict(id=f"MF-{source}-{period}", s=source, b=start, m=maturity, n=len(rows), d=digest[:12],
                  r=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0), c=json.dumps(checks), st=status), limit=None)
    return {"source": source, "period": period, "files": len(files), "rows": len(rows), "status": status, "checks": checks}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="costiq-ingest")
    ap.add_argument("--source", required=True, help="e.g. aws_focus_export, azure_focus_export, gcp_focus_export")
    ap.add_argument("--period", required=True, help="billing period YYYY-MM")
    ap.add_argument("--path", required=True, help="glob of CSV or Parquet files in the landing zone")
    ap.add_argument("--maturity", default="implemented", choices=["implemented", "representative"])
    a = ap.parse_args(argv)
    out = ingest(connect(settings.db_url), a.source, a.period, a.path, a.maturity)
    print(json.dumps(out, indent=2))
    return 0 if out["status"] == "promoted" else 2


if __name__ == "__main__":
    sys.exit(main())
