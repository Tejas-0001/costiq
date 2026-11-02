"""Provider recommendation adapters -> optimization_recommendation.

  python -m costiq.adapters.recommendations --source aws_cost_optimization_hub --path /landing/coh/*.csv
  python -m costiq.adapters.recommendations --source gcp_recommender --path /landing/gcp/recs.csv
  python -m costiq.adapters.recommendations --source azure_advisor --path /landing/advisor/*.json

Inputs (file drop; no cloud SDKs, no outbound calls from CostIQ):
  aws_cost_optimization_hub  AWS Data Exports "Cost Optimization Recommendations" table (CSV or Parquet)
  gcp_recommender            Recommender BigQuery export, flattened to CSV (one row per recommendation)
  azure_advisor              Advisor REST API response saved as JSON ({"value": [...]}, cost category)
Column names follow each provider's export; templates are in data/templates/. A load replaces the source's previous
recommendations (they are point-in-time) and writes an ingest_manifest row with schema checks.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, glob, hashlib, json, sys
from ..config import settings
from ..db import connect

AWS_ACTIONS = {"rightsize": "rightsize", "stop": "stop_idle", "delete": "delete_unattached", "upgrade": "upgrade_generation",
               "migratetograviton": "upgrade_generation", "purchasesavingsplans": "purchase_commitment",
               "purchasereservedinstances": "purchase_commitment", "scheduling": "schedule_offhours"}
GCP_SUBTYPES = {"change_machine_type": "rightsize", "stop_vm": "stop_idle", "delete_disk": "delete_unattached",
                "delete_address": "delete_unattached", "change_storage_class": "storage_tier",
                "commitment_use": "purchase_commitment"}
REQUIRED = {"aws_cost_optimization_hub": ["recommendation_id", "action_type", "resource_id", "estimated_monthly_savings_after_discount"],
            "gcp_recommender": ["name", "recommender_subtype", "target_resource", "cost_units"],
            "azure_advisor": ["value"]}


def _read(path):
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    if path.endswith(".parquet"):
        import duckdb
        con = duckdb.connect()
        cur = con.execute("SELECT * FROM read_parquet(?)", [path])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _ts(v):
    if not v:
        return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    return dt.datetime.fromisoformat(str(v).replace("Z", "").split("+")[0][:19])


def map_aws(r):
    name = r.get("resource_id") or str(r.get("resource_arn", "")).split("/")[-1]
    return {"RecommendationId": r["recommendation_id"], "Source": "aws_cost_optimization_hub", "ServiceProviderName": "AWS",
            "SubAccountId": r.get("account_id"), "ResourceId": r.get("resource_arn") or name, "ResourceName": name,
            "ActionType": AWS_ACTIONS.get(str(r["action_type"]).lower().replace(" ", ""), "rightsize"),
            "CurrentResourceSummary": r.get("current_resource_summary"), "RecommendedResourceSummary": r.get("recommended_resource_summary"),
            "CurrentMonthlyCost": _num(r.get("estimated_monthly_cost_before_discount") or r.get("estimated_monthly_cost_after_discount")),
            "EstimatedMonthlySavings": _num(r["estimated_monthly_savings_after_discount"]),
            "Effort": str(r.get("implementation_effort") or "medium").lower(),
            "RestartNeeded": str(r.get("restart_needed", "")).lower() in ("true", "1", "yes"), "RefreshedAt": _ts(r.get("last_refresh_timestamp"))}


def map_gcp(r):
    savings = -(_num(r.get("cost_units")) + _num(r.get("cost_nanos")) / 1e9)  # negative cost projection = savings
    name = str(r["target_resource"]).rstrip("/").split("/")[-1]
    return {"RecommendationId": str(r["name"]).split("/")[-1], "Source": "gcp_recommender", "ServiceProviderName": "Google Cloud",
            "SubAccountId": r.get("project_id"), "ResourceId": r["target_resource"], "ResourceName": name,
            "ActionType": GCP_SUBTYPES.get(str(r["recommender_subtype"]).lower(), "rightsize"),
            "CurrentResourceSummary": r.get("description"), "RecommendedResourceSummary": r.get("description"),
            "CurrentMonthlyCost": None, "EstimatedMonthlySavings": round(savings, 2), "Effort": "medium",
            "RestartNeeded": None, "RefreshedAt": _ts(r.get("last_refresh_time"))}


def map_azure(item):
    pr = item.get("properties", {})
    ext = pr.get("extendedProperties", {}) or {}
    monthly = _num(ext.get("savingsAmount")) or _num(ext.get("annualSavingsAmount")) / 12
    rid = (pr.get("resourceMetadata") or {}).get("resourceId") or pr.get("impactedValue")
    text = (pr.get("shortDescription") or {}).get("solution", "")
    action = ("rightsize" if "right-size" in text.lower() or "resize" in text.lower() else
              "stop_idle" if "shut down" in text.lower() or "idle" in text.lower() else
              "purchase_commitment" if "reserv" in text.lower() or "savings plan" in text.lower() else "rightsize")
    return {"RecommendationId": item.get("name") or hashlib.sha1(str(rid).encode()).hexdigest()[:12], "Source": "azure_advisor",
            "ServiceProviderName": "Microsoft", "SubAccountId": str(item.get("id", "")).split("/")[2] if item.get("id") else None,
            "ResourceId": rid, "ResourceName": pr.get("impactedValue"), "ActionType": action,
            "CurrentResourceSummary": ext.get("currentSku") or pr.get("impactedField"), "RecommendedResourceSummary": text,
            "CurrentMonthlyCost": None, "EstimatedMonthlySavings": round(monthly, 2), "Effort": "medium",
            "RestartNeeded": None, "RefreshedAt": _ts(pr.get("lastUpdated"))}


def load(db, source, paths):
    raw = [x for p in paths for x in ([_read(p)] if p.endswith(".json") else _read(p))]
    if source == "azure_advisor":
        records = [it for doc in raw for it in doc.get("value", []) if (it.get("properties") or {}).get("category") == "Cost"]
        missing = [] if records or raw else ["value"]
        mapped = [map_azure(r) for r in records]
    else:
        cols = set(raw[0]) if raw else set()
        missing = [c for c in REQUIRED[source] if c not in cols]
        mapped = [] if missing else [map_aws(r) if source == "aws_cost_optimization_hub" else map_gcp(r) for r in raw]
    checks = [{"check": "Required columns present", "status": "fail" if missing else "pass",
               "detail": f"missing: {', '.join(missing)}" if missing else f"{len(mapped)} recommendations"}]
    status = "quarantined" if missing else "promoted"
    if not missing:
        db.query("DELETE FROM optimization_recommendation WHERE Source = :s", {"s": source}, limit=None)
        cols = db.query("SELECT * FROM optimization_recommendation WHERE 1 = 0")["columns"]
        for m in mapped:
            db.query(f"INSERT INTO optimization_recommendation ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})",
                     {c: m.get(c) for c in cols}, limit=None)
    period = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, day=1, hour=0, minute=0, second=0, microsecond=0)
    digest = hashlib.sha256(json.dumps(mapped, default=str, sort_keys=True).encode()).hexdigest()
    db.query("DELETE FROM ingest_manifest WHERE SourceSystem = :s AND BillingPeriodStart = :b", {"s": source, "b": period}, limit=None)
    db.query("INSERT INTO ingest_manifest VALUES (:id, :s, :b, 'implemented', :n, :d, :r, :c, :st)",
             dict(id=f"MF-{source}-{period:%Y-%m}", s=source, b=period, n=len(mapped), d=digest[:12],
                  r=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0), c=json.dumps(checks), st=status), limit=None)
    return {"source": source, "rows": len(mapped), "status": status, "checks": checks}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="costiq.adapters.recommendations")
    ap.add_argument("--source", required=True, choices=sorted(REQUIRED))
    ap.add_argument("--path", required=True)
    a = ap.parse_args(argv)
    out = load(connect(settings.db_url), a.source, sorted(glob.glob(a.path)))
    print(json.dumps(out, indent=2, default=str))
    return 0 if out["status"] == "promoted" else 2


if __name__ == "__main__":
    sys.exit(main())
