"""Add the monitoring and optimization data to a CostIQ sample DuckDB file.

  python data/optimization.py sample/costiq_sample.duckdb

Run after the tenant's add-on (tenants/<tenant>/pack.yaml `addons`). Idempotent. Everything is generated and
illustrative. Accounts, applications, resources and cost centers are named by the tenant (tenants/<tenant>/names.py,
OPT_*); the scenarios are shared.

Adds
  * FOCUS charges (with matching invoice lines) for AI applications the telemetry describes:
      - the PTU application (OPT_PTU) on an Azure OpenAI PTU deployment that runs at ~25-40% utilization
      - the RAG application (OPT_RAG): Bedrock inference + embeddings + an OpenSearch Serverless vector store
      - a frontier model used for short summaries (OPT_BEDROCK_APPS, a model-routing candidate)
    plus shared-EKS idle capacity and three unattached EBS volumes.
  * resource_utilization_daily, ai_app_telemetry_daily, ai_provisioned_capacity, optimization_recommendation
    (AWS Cost Optimization Hub, Azure Advisor), optimization_action (a backlog at every lifecycle stage) and
    cost_alert_rule.
"""
from __future__ import annotations
import datetime as dt, hashlib, json, os, re, sys
import duckdb

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sample_common import names, refresh_budgets  # noqa: E402

N = names()
START, AS_OF = dt.date(2026, 7, 1), dt.date(2026, 10, 22)
AI_ACCOUNT = N.OPT_AI_ACCOUNT
AI_SUB = N.OPT_AI_SUB
RD_ACCOUNT, RD_SUB = N.OPT_RD_ACCOUNT, N.OPT_RD_SUB
RD_SOURCE = getattr(N, "OPT_RD_SOURCE", "aws_rd_focus_export")   # source system of the workload account rows
PTU_SUB = N.OPT_PTU_SUB
MS_ACCOUNT = ("ms-mca-1001", "MCA")
SOURCE_AI, SOURCE_PTU = "aws_ai_focus_export", "azure_ptu_focus_export"
PRICES = {"claude-opus-5-5": (5.0, 25.0), "claude-sonnet-5-5": (3.0, 15.0), "claude-haiku-5-5": (1.0, 5.0),
          "gpt-5.4": (2.5, 15.0)}  # illustrative list $ per 1M input / output tokens
PTU = N.OPT_PTU


def ts(d):
    return dt.datetime(d.year, d.month, d.day)


def next_month(d):
    return dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def h(day, key, spread=0.15):
    x = int(hashlib.md5(f"{day}{key}".encode()).hexdigest()[:6], 16) / 0xFFFFFF
    return 1 + spread * (2 * x - 1)


def days():
    d = START
    while d <= AS_OF:
        yield d
        d += dt.timedelta(days=1)


# ------------------------------------------------------------------------------------------- LLM app telemetry
# app, model, cost center, requests/day (weekday), avg input, avg output, max_tokens, failed %, retried %, accepted %,
# input growth over the window (context bloat), truncation %
APPS = N.OPT_APPS


def telemetry():
    out = []
    span = (AS_OF - START).days
    for d in days():
        wk = 0.35 if d.weekday() >= 5 else 1.0
        for app, model, cc, req, ain, aout, mx, fail, retry, acc, growth, trunc in APPS:
            n = int(req * wk * h(d, app))
            ain_d = ain * (1 + growth * (d - START).days / span) * h(d, app + "in", 0.08)
            out.append({"UsageDate": d, "Application": app, "ModelName": model, "Requests": n,
                        "FailedRequests": int(n * fail), "RetriedRequests": int(n * retry), "AvgInputTokens": round(ain_d, 1),
                        "AvgOutputTokens": round(aout * h(d, app + "out", 0.1), 1), "MaxTokensSetting": mx,
                        "TruncatedResponses": int(n * trunc), "AcceptedOutcomes": int(n * acc * h(d, app + "acc", 0.05)),
                        "LatencyP95Ms": int((2200 if "opus" in model else 1400) * h(d, app + "lat"))})
    return out


def charge(day, cost, account, sub, provider, source, detail, **kw):
    r = {"BillingAccountId": account[0], "BillingAccountName": account[1], "BillingAccountType": "Billing Account",
         "BillingCurrency": "USD", "BillingPeriodStart": ts(dt.date(day.year, day.month, 1)), "BillingPeriodEnd": ts(next_month(day)),
         "ChargeCategory": "Usage", "ChargeFrequency": "Usage-Based", "ChargePeriodStart": ts(day),
         "ChargePeriodEnd": ts(day + dt.timedelta(days=1)), "BilledCost": round(cost, 4), "EffectiveCost": round(cost, 4),
         "ListCost": round(cost, 4), "ContractedCost": round(cost, 4), "PricingCategory": "Standard", "PricingCurrency": "USD",
         "PricingCurrencyEffectiveCost": round(cost, 4), "HostProviderName": provider, "ServiceProviderName": provider,
         "InvoiceIssuerName": provider, "SubAccountId": sub[0], "SubAccountName": sub[1], "SubAccountType": "Account",
         "x_CostSource": "invoiced", "x_SourceSystem": source,
         "InvoiceId": f"INV-{account[0]}-{day:%Y%m}" + ("-AI" if account == MS_ACCOUNT else ""),
         "InvoiceDetailId": f"IND-{account[0]}-{day:%Y%m}-{detail}"}
    r.update(kw)
    return r


def ai_charges(tel):
    out = []
    by = {(t["UsageDate"], t["Application"]): t for t in tel}
    for d in days():
        # Azure OpenAI PTU: a fixed daily cost whatever the traffic
        t = by[(d, PTU["app"])]
        tok_in, tok_out = int(t["Requests"] * t["AvgInputTokens"]), int(t["Requests"] * t["AvgOutputTokens"])
        out.append(charge(d, PTU["daily"], MS_ACCOUNT, PTU_SUB, "Microsoft", SOURCE_PTU, "PTU",
                          ServiceName="Azure OpenAI", ServiceCategory="AI and Machine Learning", ServiceSubcategory="Generative AI",
                          ChargeDescription=f"Azure OpenAI provisioned throughput {PTU['units']} PTU ({PTU['model']})",
                          RegionId="eastus2", RegionName="eastus2", ResourceId=f"/subscriptions/ai/{PTU['deployment']}",
                          ResourceName=PTU["deployment"], ResourceType="Provisioned deployment", SkuId="aoai-ptu",
                          SkuMeter="PTU-hours", ConsumedQuantity=PTU["units"] * 24, ConsumedUnit="PTU-hours",
                          PricingQuantity=PTU["units"] * 24, PricingUnit="PTU-hours", x_AIProduct="azure_openai",
                          x_ModelName=PTU["model"], x_ModelVendor="OpenAI", x_ServiceTier="provisioned", x_TokenType="mixed",
                          x_InputTokens=tok_in, x_OutputTokens=tok_out, x_CacheReadTokens=0, x_CacheWriteTokens=0,
                          x_RequestCount=t["Requests"], x_InferenceGeo="us",
                          Tags=json.dumps({"cost_center": PTU["cost_center"], "application": PTU["app"],
                                           "environment": "prod", "ai_component": "inference"})))
        # Bedrock token workloads priced from telemetry
        for app, cc in N.OPT_BEDROCK_APPS:
            t = by[(d, app)]
            pin, pout = PRICES[t["ModelName"]]
            tin, tout = t["Requests"] * t["AvgInputTokens"], t["Requests"] * t["AvgOutputTokens"]
            cost = (tin * pin + tout * pout) / 1e6
            out.append(charge(d, cost, AI_ACCOUNT, AI_SUB, "AWS", SOURCE_AI, "Usage",
                              ServiceName="Amazon Bedrock", ServiceCategory="AI and Machine Learning", ServiceSubcategory="Generative AI",
                              ChargeDescription=f"Amazon Bedrock {t['ModelName']} tokens", RegionId="us-east-2", RegionName="us-east-2",
                              ResourceId=f"arn:aws:bedrock:us-east-2:{AI_SUB[0]}:{app}", ResourceName=app, ResourceType="Model invocation",
                              SkuId=f"bedrock-{t['ModelName']}", SkuMeter="Tokens", ConsumedQuantity=int(tin + tout), ConsumedUnit="Tokens",
                              PricingQuantity=(tin + tout) / 1e6, PricingUnit="1M Tokens", x_AIProduct="claude_bedrock",
                              x_ModelName=t["ModelName"], x_ModelVendor="Anthropic", x_ServiceTier="standard", x_TokenType="mixed",
                              x_InputTokens=int(tin), x_OutputTokens=int(tout), x_CacheReadTokens=0, x_CacheWriteTokens=0,
                              x_RequestCount=t["Requests"], x_InferenceGeo="us",
                              Tags=json.dumps({"cost_center": cc, "application": app, "environment": "prod", "ai_component": "inference"})))
        # RAG supporting components: embeddings and the vector store
        for svc, cat, comp, daily, res in (("Amazon Bedrock", "AI and Machine Learning", "embedding", 35, "rag-embeddings"),
                                           ("Amazon OpenSearch Service", "Databases", "vector_db", 260, "rag-vector-store")):
            out.append(charge(d, daily * h(d, res), AI_ACCOUNT, AI_SUB, "AWS", SOURCE_AI, "Usage",
                              ServiceName=svc, ServiceCategory=cat, ServiceSubcategory="Generative AI" if comp == "embedding" else "Search",
                              ChargeDescription=f"{svc} {res}", RegionId="us-east-2", RegionName="us-east-2",
                              ResourceId=f"arn:aws:{comp}:us-east-2:{AI_SUB[0]}:{res}", ResourceName=res, ResourceType=svc,
                              SkuId=f"{res}-sku", SkuMeter="Units", ConsumedQuantity=24, ConsumedUnit="Hours", PricingQuantity=24,
                              PricingUnit="Hours", x_AIProduct="claude_bedrock" if comp == "embedding" else None,
                              Tags=json.dumps({"cost_center": N.OPT_RAG["cost_center"], "application": N.OPT_RAG["app"],
                                               "environment": "prod", "ai_component": comp})))
        # Unattached EBS volumes left behind in the tenant's workload account (OPT_ORPHANS)
        for i, daily in enumerate((14, 11, 9), 1):
            res = f"{N.OPT_ORPHANS['prefix']}{i}"
            out.append(charge(d, daily, RD_ACCOUNT, RD_SUB, "AWS", RD_SOURCE, "Orphans",
                              ServiceName="Amazon EBS", ServiceCategory="Storage", ServiceSubcategory="Block Storage",
                              ChargeDescription=f"Amazon EBS gp3 volume {res} (unattached)", RegionId="us-east-2", RegionName="us-east-2",
                              ResourceId=f"arn:aws:ec2:us-east-2:{RD_SUB[0]}:volume/{res}", ResourceName=res, ResourceType="Amazon EBS",
                              SkuId="EBS-gp3", SkuMeter="GB-Month", ConsumedQuantity=500, ConsumedUnit="GB-Month", PricingQuantity=500,
                              PricingUnit="GB-Month",
                              Tags=json.dumps({"cost_center": N.OPT_ORPHANS["cost_center"], "application": N.OPT_ORPHANS["app"],
                                               "environment": "nonprod"})))
    return out


def eks_idle(con, cols):
    """Shared-EKS idle capacity: the part of the cluster no workload requested (FOCUS split-cost allocation)."""
    tmpl = con.execute("""SELECT * FROM CostAndUsage WHERE ResourceName = 'shared-eks' AND AllocatedMethodId IS NOT NULL
                          ORDER BY ChargePeriodStart LIMIT 1""").fetchone()
    if not tmpl:
        return []
    base = dict(zip(cols, tmpl))
    out = []
    for d in days():
        cost = round(31 * h(d, "eks-idle"), 4)
        r = dict(base)
        r.update(ChargePeriodStart=ts(d), ChargePeriodEnd=ts(d + dt.timedelta(days=1)),
                 BillingPeriodStart=ts(dt.date(d.year, d.month, 1)), BillingPeriodEnd=ts(next_month(d)),
                 BilledCost=cost, EffectiveCost=cost, ListCost=cost, ContractedCost=cost, PricingCurrencyEffectiveCost=cost,
                 AllocatedResourceId="idle", AllocatedResourceName="(idle capacity)",
                 AllocatedTags=json.dumps({"cost_center": N.IT_CC}),
                 AllocatedMethodDetails=json.dumps({"Elements": [{"AllocatedRatio": 0.18, "UsageUnit": "vCPU-Hours", "Basis": "unrequested"}]}),
                 ChargeDescription="Amazon EKS shared-eks idle capacity (unrequested vCPU and memory)",
                 InvoiceDetailId=f"IND-{base['BillingAccountId']}-{d:%Y%m}-EKSIdle", InvoiceId=f"INV-{base['BillingAccountId']}-{d:%Y%m}-EKS",
                 x_SourceSystem="aws_focus_export")
        out.append(r)
    return out


# ------------------------------------------------------------------------------------------- utilization
# resource name, profile (busy | idle | nonprod | rightsize | gpu | detached)
UTIL = N.OPT_UTIL
GPU_IDLE_FROM = dt.date(2026, 9, 20)


def utilization():
    out = []
    for d in days():
        wkend = d.weekday() >= 5
        for res, prof in UTIL:
            cpu, mem, gpu, req, hrs = 0.0, 0.0, None, 0, 24.0
            if prof == "busy":
                cpu, mem, req = 62 * h(d, res), 71 * h(d, res + "m"), int(90000 * h(d, res + "r"))
            elif prof == "nonprod":  # busy in business hours on weekdays, near idle otherwise, but running 24x7
                cpu = 4 + (0 if wkend else 14 * h(d, res))
                mem, req = 22 * h(d, res + "m"), 0 if wkend else int(1500 * h(d, res + "r"))
            elif prof == "idle":
                cpu, mem, req = 2.1 * h(d, res), 9 * h(d, res + "m"), 0
            elif prof == "rightsize":
                rs = d >= dt.date(2026, 9, 1)
                cpu, mem, req = (58 if rs else 21) * h(d, res), (61 if rs else 24) * h(d, res + "m"), int(4000 * h(d, res + "r"))
            elif prof == "gpu":
                busy = d < GPU_IDLE_FROM and not wkend
                cpu, mem, gpu = (35 if busy else 1.5) * h(d, res), 40 * h(d, res + "m"), (72 * h(d, res + "g") if busy else 0.0)
                req = int(2400 * h(d, "rq")) if busy else 0
            elif prof == "detached":
                cpu, mem, req, hrs = 0.0, 0.0, 0, 24.0
            out.append({"UsageDate": d, "ResourceId": res, "ResourceName": res, "CpuP95": round(cpu, 2), "MemP95": round(mem, 2),
                        "GpuUtilAvg": None if gpu is None else round(gpu, 2), "Requests": req, "RunningHours": hrs})
    return out


def ptu_capacity(tel):
    out = []
    for t in (t for t in tel if t["Application"] == PTU["app"]):
        # utilization grows with context size; still far below what 50 PTU can serve
        util = min(95.0, 100 * t["Requests"] * t["AvgInputTokens"] / 150_000_000)
        out.append({"UsageDate": t["UsageDate"], "Deployment": PTU["deployment"], "Application": PTU["app"],
                    "ModelName": PTU["model"], "Units": PTU["units"], "DailyCost": PTU["daily"], "UtilizationPct": round(util, 2)})
    return out


T = dt.datetime


def _fill(v):
    """names.py writes the add-on's own accounts as {rd_sub}, {ptu_sub} and {ptu}."""
    if isinstance(v, str):
        return v.replace("{rd_sub}", RD_SUB[0]).replace("{ptu_sub}", PTU_SUB[0]).replace("{ptu}", PTU["deployment"])
    return v


RECS = [tuple(_fill(v) for v in r) for r in N.OPT_RECS]  # id, source, provider, sub, resource, action, current, recommended, ...
ACTIONS = [tuple(_fill(v) for v in a[:-1]) + (tuple(T(*d) if d else None for d in a[-1]),)
           for a in N.OPT_ACTIONS]  # id, origin, lever, domain, title, scope type, scope key, cost center, owner, ..., dates
RULES = [
    ("AR-01", "Cost center forecast over budget", "budget_threshold", "*", 100, "in_app"),
    ("AR-02", "AI budget will run out before month end", "token_budget", "*", 50, "in_app"),
    ("AR-03", "Daily spend anomaly", "anomaly", "*", 300, "in_app"),
    ("AR-04", "Commitment expiring within 90 days", "commitment_expiry", "*", 90, "in_app"),
    ("AR-05", "Provisioned AI capacity under 50% utilized", "forecast_overrun", "azure_openai", 50, "in_app"),
]


def insert_dicts(con, table, dicts):
    cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    if dicts:
        con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})", [[d.get(c) for c in cols] for d in dicts])


def apply(path):
    con = duckdb.connect(path)
    text = re.sub(r"--[^\n]*", "", open(os.path.join(ROOT, "schema", "costiq_optimization_schema.sql")).read())
    for stmt in [s.strip() for s in text.split(";") if s.strip()]:
        con.execute(stmt)
    cols = [r[0] for r in con.execute("DESCRIBE CostAndUsage").fetchall()]

    # remove what a previous run added
    con.execute(f"DELETE FROM CostAndUsage WHERE x_SourceSystem IN ('{SOURCE_AI}', '{SOURCE_PTU}')")
    con.execute("DELETE FROM CostAndUsage WHERE ResourceName LIKE ?", [N.OPT_ORPHANS["prefix"] + "%"])
    con.execute("DELETE FROM CostAndUsage WHERE ResourceName = 'shared-eks' AND AllocatedResourceId = 'idle'")
    con.execute("DELETE FROM InvoiceDetail WHERE InvoiceDetailId LIKE '%-PTU' OR InvoiceDetailId LIKE '%-Orphans' "
                "OR InvoiceDetailId LIKE '%-EKSIdle' OR BillingAccountId = ?", [AI_ACCOUNT[0]])

    tel = telemetry()
    new = ai_charges(tel) + eks_idle(con, cols)
    insert_dicts(con, "CostAndUsage", new)

    icol = [r[0] for r in con.execute("DESCRIBE InvoiceDetail").fetchall()]
    det_ids = sorted({r["InvoiceDetailId"] for r in new})
    for inv, det, ps, pe, cat, issuer, acct, amt in con.execute(f"""
            SELECT InvoiceId, InvoiceDetailId, BillingPeriodStart, BillingPeriodEnd, ChargeCategory, InvoiceIssuerName,
                   BillingAccountId, SUM(BilledCost)
            FROM CostAndUsage WHERE InvoiceDetailId IN ({', '.join('?' * len(det_ids))}) GROUP BY ALL""", det_ids).fetchall():
        issued = pe.date() <= AS_OF
        rec = {"BilledCost": amt, "BillingAccountId": acct, "BillingCurrency": "USD", "BillingPeriodEnd": pe,
               "BillingPeriodStart": ps, "ChargeCategory": cat, "InvoiceDetailCreated": pe,
               "InvoiceDetailDescription": f"{issuer} {cat} charges", "InvoiceDetailGrain": json.dumps({"ChargeCategory": cat}),
               "InvoiceDetailId": det, "InvoiceDetailLastUpdated": pe + dt.timedelta(days=3), "InvoiceId": inv,
               "InvoiceIssueDate": pe + dt.timedelta(days=2) if issued else None, "InvoiceIssueStatus": "Issued" if issued else "Open",
               "InvoiceIssuerName": issuer, "PaymentCurrency": "USD", "PaymentCurrencyBilledCost": amt,
               "PaymentCurrencyInvoiceDetailId": "PAY-" + det, "PaymentDueDate": pe + dt.timedelta(days=32) if issued else None,
               "PaymentTerms": "Net 30", "PurchaseOrderNumber": "PO-IT-2026-AI", "ReferenceInvoiceId": None}
        con.execute(f"INSERT INTO InvoiceDetail VALUES ({', '.join('?' * len(icol))})", [rec[c] for c in icol])

    for t in ("resource_utilization_daily", "ai_app_telemetry_daily", "ai_provisioned_capacity", "optimization_recommendation",
              "optimization_action", "cost_alert_rule", "cost_alert_event"):
        con.execute(f"DELETE FROM {t}")
    insert_dicts(con, "ai_app_telemetry_daily", tel)
    insert_dicts(con, "resource_utilization_daily", utilization())
    insert_dicts(con, "ai_provisioned_capacity", ptu_capacity(tel))
    insert_dicts(con, "optimization_recommendation", [
        {"RecommendationId": i, "Source": s, "ServiceProviderName": p, "SubAccountId": sub, "ResourceId": res, "ResourceName": res,
         "ActionType": a, "CurrentResourceSummary": cur, "RecommendedResourceSummary": rec, "CurrentMonthlyCost": c,
         "EstimatedMonthlySavings": sv, "Effort": e, "RestartNeeded": rs, "RefreshedAt": T(2026, 10, 21, 4, 0)}
        for i, s, p, sub, res, a, cur, rec, c, sv, e, rs in RECS])
    insert_dicts(con, "optimization_action", [
        {"OptimizationId": i, "Origin": o, "Lever": lv, "Domain": dm, "Title": tt, "ScopeType": st, "ScopeKey": sk,
         "CostCenter": cc, "Owner": ow, "Confidence": cf, "Status": status, "EstimatedMonthlySavings": est,
         "IdentifiedOn": dates[0], "ApprovedOn": dates[1], "ImplementedOn": dates[2], "VerifiedOn": dates[3],
         "ImplementedBy": "demo.cloud_ops@company.example" if dates[2] else None}
        for i, o, lv, dm, tt, st, sk, cc, ow, cf, status, est, dates in ACTIONS])
    # AI budgets sized for the group's AI stack (the base sample's budgets predate it)
    for cc, amt in N.OPT_AI_BUDGETS:
        con.execute("UPDATE ai_budget SET BudgetAmount = ? WHERE CostCenter = ?", [amt, cc])
    insert_dicts(con, "cost_alert_rule", [
        {"RuleId": r, "Name": n, "Kind": k, "Target": t, "ThresholdPct": th, "Channel": ch, "Enabled": True}
        for r, n, k, t, th, ch in RULES])
    refresh_budgets(con)
    con.execute("CHECKPOINT")
    con.close()
    print(f"{path}: {len(new)} optimization charge rows, {len(tel)} telemetry rows, {len(RECS)} recommendations, "
          f"{len(ACTIONS)} optimization actions")


if __name__ == "__main__":
    for p in sys.argv[1:] or [os.path.join(ROOT, "sample", "pharma", "costiq_sample.duckdb")]:
        apply(p)
