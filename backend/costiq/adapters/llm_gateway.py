"""LLM gateway telemetry adapter -> ai_app_telemetry_daily.

  python -m costiq.adapters.llm_gateway --path /landing/llm-gateway/2026-11-*.csv

Accepts a daily per-application, per-model CSV from an API gateway, an LLM proxy, or Bedrock / Azure OpenAI diagnostic
logs aggregated by the client (template: data/templates/llm_gateway_template.csv). Rows for the dates in the file are
replaced (idempotent) and an ingest_manifest row records the load.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, glob, hashlib, json, sys
from ..config import settings
from ..db import connect

COLUMNS = {  # template column -> table column
    "usage_date": "UsageDate", "application": "Application", "model": "ModelName", "requests": "Requests",
    "failed_requests": "FailedRequests", "retried_requests": "RetriedRequests", "avg_input_tokens": "AvgInputTokens",
    "avg_output_tokens": "AvgOutputTokens", "max_tokens_setting": "MaxTokensSetting",
    "truncated_responses": "TruncatedResponses", "accepted_outcomes": "AcceptedOutcomes", "latency_p95_ms": "LatencyP95Ms",
}
REQUIRED = ["usage_date", "application", "model", "requests"]
SOURCE = "llm_gateway_telemetry"


def load(db, paths):
    recs = []
    for p in paths:
        with open(p, newline="", encoding="utf-8-sig") as fh:
            recs += list(csv.DictReader(fh))
    cols = set(recs[0]) if recs else set()
    missing = [c for c in REQUIRED if c not in cols]
    checks = [{"check": "Required columns present", "status": "fail" if missing else "pass",
               "detail": f"missing: {', '.join(missing)}" if missing else f"{len(recs)} rows"}]
    unknown = sorted(cols - set(COLUMNS))
    if unknown:
        checks.append({"check": "Unrecognised columns", "status": "warn", "detail": ", ".join(unknown[:8])})
    status = "quarantined" if missing or not recs else "promoted"
    days = sorted({r["usage_date"] for r in recs}) if not missing else []
    if status == "promoted":
        for d in days:
            db.query("DELETE FROM ai_app_telemetry_daily WHERE UsageDate = :d", {"d": dt.date.fromisoformat(d)}, limit=None)
        names = list(COLUMNS.values())
        for r in recs:
            vals = {COLUMNS[k]: (v if v != "" else None) for k, v in r.items() if k in COLUMNS}
            vals["UsageDate"] = dt.date.fromisoformat(vals["UsageDate"])
            db.query(f"INSERT INTO ai_app_telemetry_daily ({', '.join(names)}) VALUES ({', '.join(':' + n for n in names)})",
                     {n: vals.get(n) for n in names}, limit=None)
    period = dt.datetime.fromisoformat(days[0][:7] + "-01") if days else dt.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    digest = hashlib.sha256(json.dumps(recs, sort_keys=True).encode()).hexdigest()
    db.query("DELETE FROM ingest_manifest WHERE SourceSystem = :s AND BillingPeriodStart = :b", {"s": SOURCE, "b": period}, limit=None)
    db.query("INSERT INTO ingest_manifest VALUES (:id, :s, :b, 'implemented', :n, :d, :r, :c, :st)",
             dict(id=f"MF-{SOURCE}-{period:%Y-%m}", s=SOURCE, b=period, n=len(recs), d=digest[:12],
                  r=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0), c=json.dumps(checks), st=status), limit=None)
    return {"source": SOURCE, "rows": len(recs), "days": len(days), "status": status, "checks": checks}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="costiq.adapters.llm_gateway")
    ap.add_argument("--path", required=True)
    a = ap.parse_args(argv)
    out = load(connect(settings.db_url), sorted(glob.glob(a.path)))
    print(json.dumps(out, indent=2))
    return 0 if out["status"] == "promoted" else 2


if __name__ == "__main__":
    sys.exit(main())
