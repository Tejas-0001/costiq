"""Write registry/usecases/CO-*.yaml, UO-09..14 and BF-04: cloud and AI/LLM optimization use cases that read the
monitoring tables (provider recommendations, utilization, LLM telemetry, provisioned capacity) alongside FOCUS cost.
Run from mvp/backend: python tools/optimization_usecases.py
"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(__file__))
from pharma_usecases import SHARED_OUT as OUT, render  # noqa: E402

SOURCE = "'{{extension_source}}'"   # tenant pack usecase_vars

PERIOD = [("period_start", "date", "2026-07-01", "Start of the period (inclusive)"),
          ("period_end", "date", "2026-10-23", "End of the period (exclusive)")]
AS_OF = [("as_of", "date", "2026-10-22", "Evaluate as of")]


def uc(id, module, category, title, question, personas, catalog_persona, description, keywords, sql, params, notes,
       datasets=("Cost and Usage", "Monitoring tables")):
    return dict(id=id, module=module, category=category, title=title, question=question, personas=personas,
                catalog_persona=catalog_persona, description=description, keywords=keywords, sql=sql, params=params,
                compliance=None, notes=notes, datasets=list(datasets),
                columns="FOCUS columns plus monitoring tables (recommendations, utilization, LLM telemetry, provisioned capacity)")


SPEND_30D = """spend AS (
  SELECT ResourceName, MAX(json_extract_string(Tags, '$.cost_center')) AS CostCenter, MAX(ServiceName) AS ServiceName,
         SUM(EffectiveCost) AS Last30DaysCost
  FROM CostAndUsage
  WHERE ChargePeriodStart > CAST(:as_of AS TIMESTAMP) - INTERVAL '30' DAY
    AND ChargePeriodStart <= CAST(:as_of AS TIMESTAMP)
  GROUP BY ResourceName
)"""

USE_CASES = [
    uc("CO-01", "optimization", "Optimization · Cloud", "Rightsizing opportunities by cost center",
       "Which cloud resources are over-provisioned, and what would rightsizing save each month?",
       ["finops_analyst", "cloud_ops", "tech_exec", "cfo"], "FinOps Practitioner, Cloud Operations",
       "Provider rightsizing recommendations (AWS Cost Optimization Hub, Azure Advisor, Google Recommender) joined to "
       "FOCUS cost, so each recommendation carries its cost center and its last 30 days of spend, and shows whether it is "
       "already in the optimization tracker.",
       ["rightsizing", "rightsize", "over-provisioned", "downsize", "instance size", "advisor", "cost optimization hub"],
       f"""
WITH {SPEND_30D}
SELECT
  r.RecommendationId, r.Source, r.ResourceName, s.CostCenter, s.ServiceName,
  r.CurrentResourceSummary, r.RecommendedResourceSummary,
  ROUND(COALESCE(s.Last30DaysCost, r.CurrentMonthlyCost), 2) AS Last30DaysCost,
  ROUND(r.EstimatedMonthlySavings, 2) AS EstimatedMonthlySavings,
  r.Effort,
  COALESCE(a.Status, 'not tracked') AS TrackerStatus
FROM optimization_recommendation r
LEFT JOIN spend s ON s.ResourceName = r.ResourceName
LEFT JOIN optimization_action a ON a.Origin = r.RecommendationId
WHERE r.ActionType = 'rightsize'
ORDER BY EstimatedMonthlySavings DESC""", AS_OF,
       "Savings are the provider's estimates; CostIQ verifies them from run-rate after implementation."),
    uc("CO-02", "optimization", "Optimization · Cloud", "Idle and unattached resources",
       "Which resources are running and billing but doing no work?",
       ["finops_analyst", "cloud_ops", "tech_exec"], "Cloud Operations",
       "Resources whose CPU p95 stayed under 5% with no requests for 14 days (utilization exports), and unattached volumes. "
       "{{idle_risk_note}}",
       ["idle", "unused", "unattached", "orphaned volume", "zombie", "no benefit", "stop instance"],
       f"""
WITH u AS (
  SELECT ResourceName, MAX(CpuP95) AS MaxCpuP95, SUM(Requests) AS Requests
  FROM resource_utilization_daily
  WHERE UsageDate > CAST(:as_of AS DATE) - INTERVAL '14' DAY AND UsageDate <= CAST(:as_of AS DATE)
  GROUP BY ResourceName
),
{SPEND_30D}
SELECT
  u.ResourceName, s.CostCenter, s.ServiceName,
  ROUND(u.MaxCpuP95, 1) AS MaxCpuP95Last14Days,
  u.Requests AS RequestsLast14Days,
  CASE WHEN s.ServiceName = 'Amazon EBS' THEN 'Unattached volume' ELSE 'Idle: no requests, CPU p95 under 5%' END AS Finding,
  ROUND(s.Last30DaysCost, 2) AS Last30DaysCost,
  ROUND(s.Last30DaysCost, 2) AS EstimatedMonthlySavings
FROM u
JOIN spend s ON s.ResourceName = u.ResourceName
WHERE u.MaxCpuP95 < 5 AND u.Requests = 0
ORDER BY EstimatedMonthlySavings DESC""", AS_OF,
       "Snapshot before deleting volumes; stopping is reversible."),
    uc("CO-03", "optimization", "Optimization · Cloud", "Non-production running 24x7: off-hours scheduling candidates",
       "Which non-production resources run around the clock, and what would a business-hours schedule save?",
       ["finops_analyst", "cloud_ops"], "Cloud Operations",
       "Resources tagged environment = nonprod that bill 20+ hours a day. Running them 12 hours on weekdays (60 of 168 "
       "hours) saves about 64% of their cost.",
       ["schedule", "off hours", "nonprod", "dev", "test", "24x7", "business hours"],
       """
SELECT
  ResourceName,
  json_extract_string(Tags, '$.cost_center') AS CostCenter,
  json_extract_string(Tags, '$.application') AS Application,
  ServiceName,
  COUNT(DISTINCT CAST(ChargePeriodStart AS DATE)) AS DaysRunning,
  ROUND(SUM(ConsumedQuantity) / COUNT(DISTINCT CAST(ChargePeriodStart AS DATE)), 1) AS AvgHoursPerDay,
  ROUND(SUM(EffectiveCost), 2) AS PeriodCost,
  ROUND(SUM(EffectiveCost) / COUNT(DISTINCT CAST(ChargePeriodStart AS DATE)) * 30 * (1 - 60.0 / 168), 2) AS MonthlySchedulingSavings
FROM CostAndUsage
WHERE json_extract_string(Tags, '$.environment') = 'nonprod' AND ConsumedUnit = 'Hours' AND ChargeCategory = 'Usage'
  AND ChargePeriodStart >= :period_start AND ChargePeriodStart < :period_end
GROUP BY ResourceName, json_extract_string(Tags, '$.cost_center'), json_extract_string(Tags, '$.application'), ServiceName
HAVING SUM(ConsumedQuantity) / COUNT(DISTINCT CAST(ChargePeriodStart AS DATE)) >= 20
ORDER BY MonthlySchedulingSavings DESC""", PERIOD,
       "Assumes a 12 x 5 schedule. Exclude resources that serve overnight batch work."),
    uc("CO-04", "optimization", "Optimization · Cloud", "Previous-generation and ARM migration candidates",
       "Which resources run on older or x86 instance families where a current or ARM (Graviton) family is cheaper?",
       ["finops_analyst", "cloud_ops"], "Cloud Operations",
       "Provider upgrade-generation recommendations with the resource's cost center and recent spend.",
       ["graviton", "arm", "previous generation", "instance family", "modernize"],
       f"""
WITH {SPEND_30D}
SELECT
  r.RecommendationId, r.ResourceName, s.CostCenter, s.ServiceName, r.CurrentResourceSummary, r.RecommendedResourceSummary,
  ROUND(COALESCE(s.Last30DaysCost, r.CurrentMonthlyCost), 2) AS Last30DaysCost,
  ROUND(r.EstimatedMonthlySavings, 2) AS EstimatedMonthlySavings, r.Effort, r.RestartNeeded,
  COALESCE(a.Status, 'not tracked') AS TrackerStatus
FROM optimization_recommendation r
LEFT JOIN spend s ON s.ResourceName = r.ResourceName
LEFT JOIN optimization_action a ON a.Origin = r.RecommendationId
WHERE r.ActionType = 'upgrade_generation'
ORDER BY EstimatedMonthlySavings DESC""", AS_OF,
       "Database engines need a compatibility test before moving to ARM."),
    uc("CO-05", "optimization", "Optimization · Cloud", "Storage growth and tiering opportunities",
       "Which storage is growing fastest, and where would tiering or lifecycle rules save money?",
       ["finops_analyst", "cloud_ops"], "Cloud Operations",
       "Storage resources over $500 a month with their growth since the first month, joined to storage-tiering "
       "recommendations.",
       ["storage", "tiering", "lifecycle", "s3", "intelligent tiering", "archive", "snapshots"],
       """
WITH s AS (
  SELECT ResourceName, MAX(ServiceName) AS ServiceName, MAX(json_extract_string(Tags, '$.cost_center')) AS CostCenter,
         SUM(CASE WHEN BillingPeriodStart = :first_month THEN EffectiveCost ELSE 0 END) AS FirstMonthCost,
         SUM(CASE WHEN BillingPeriodStart = :last_month THEN EffectiveCost ELSE 0 END) AS LastMonthCost
  FROM CostAndUsage
  WHERE ServiceCategory = 'Storage' AND ChargeCategory = 'Usage' AND ResourceName IS NOT NULL
  GROUP BY ResourceName
)
SELECT
  s.ResourceName, s.ServiceName, s.CostCenter,
  ROUND(s.FirstMonthCost, 2) AS FirstMonthCost, ROUND(s.LastMonthCost, 2) AS LastMonthCost,
  ROUND(100 * (s.LastMonthCost - s.FirstMonthCost) / NULLIF(s.FirstMonthCost, 0), 1) AS GrowthPct,
  r.RecommendedResourceSummary,
  ROUND(COALESCE(r.EstimatedMonthlySavings, 0), 2) AS EstimatedMonthlySavings,
  CASE WHEN r.RecommendationId IS NOT NULL THEN 'Tiering recommended'
       WHEN s.LastMonthCost > 1.2 * s.FirstMonthCost THEN 'Growing: review lifecycle rules'
       ELSE 'Review retention' END AS Finding
FROM s
LEFT JOIN optimization_recommendation r ON r.ResourceName = s.ResourceName AND r.ActionType = 'storage_tier'
WHERE s.LastMonthCost > 500
ORDER BY EstimatedMonthlySavings DESC, LastMonthCost DESC""",
       [("first_month", "date", "2026-07-01", "First month"), ("last_month", "date", "2026-09-01", "Last full month")],
       "{{storage_tier_note}}"),
    uc("CO-06", "optimization", "Optimization · Cloud", "Data transfer and egress hotspots",
       "Where is data-transfer and egress cost concentrated, by account, region and cost center?",
       ["finops_analyst", "cloud_ops"], "Cloud Operations",
       "Networking charges for data transfer and content delivery, by account, region and cost center. Egress is often "
       "fixed by architecture (caching, compression, keeping traffic in region) rather than by rate.",
       ["egress", "data transfer", "bandwidth", "networking", "cdn", "nat gateway"],
       """
SELECT
  ServiceProviderName, SubAccountId, RegionId, ServiceName, ChargeDescription,
  json_extract_string(Tags, '$.cost_center') AS CostCenter,
  ROUND(SUM(ConsumedQuantity), 0) AS Quantity, MAX(ConsumedUnit) AS Unit,
  ROUND(SUM(EffectiveCost), 2) AS EgressCost
FROM CostAndUsage
WHERE ServiceCategory = 'Networking'
  AND (LOWER(ChargeDescription) LIKE '%transfer%' OR ServiceSubcategory IN ('Network Connectivity', 'Content Delivery'))
  AND ChargePeriodStart >= :period_start AND ChargePeriodStart < :period_end
GROUP BY ServiceProviderName, SubAccountId, RegionId, ServiceName, ChargeDescription, json_extract_string(Tags, '$.cost_center')
ORDER BY EgressCost DESC""", PERIOD, ""),
    uc("CO-07", "optimization", "Optimization · Cloud", "Kubernetes cluster idle capacity",
       "How much of each shared Kubernetes cluster's cost is capacity no workload requested?",
       ["finops_analyst", "cloud_ops", "tech_exec"], "Cloud Operations, Platform Engineering",
       "Uses FOCUS split-cost allocation rows: the share allocated to '(idle capacity)' is cluster cost no pod requested. "
       "Bin-packing, autoscaler limits or smaller node groups reduce it.",
       ["kubernetes", "eks", "aks", "gke", "idle capacity", "cluster", "split cost allocation", "bin packing"],
       """
SELECT
  ResourceName AS Cluster,
  strftime(BillingPeriodStart, '%Y-%m') AS Month,
  ROUND(SUM(EffectiveCost), 2) AS ClusterCost,
  ROUND(SUM(CASE WHEN AllocatedResourceId = 'idle' THEN EffectiveCost ELSE 0 END), 2) AS IdleCost,
  ROUND(100 * SUM(CASE WHEN AllocatedResourceId = 'idle' THEN EffectiveCost ELSE 0 END) / NULLIF(SUM(EffectiveCost), 0), 1) AS IdlePct
FROM CostAndUsage
WHERE AllocatedMethodId IS NOT NULL
GROUP BY ResourceName, strftime(BillingPeriodStart, '%Y-%m')
ORDER BY Month, Cluster""", [],
       "The idle share is reported by the split-cost allocation export as AllocatedResourceId = 'idle'."),
    uc("UO-09", "optimization", "Optimization · AI", "Idle GPU and model endpoints",
       "Which GPU or model endpoints billed for capacity on days they served no requests?",
       ["ai_program_owner", "finops_analyst", "tech_exec", "cfo"], "AI Program Owner, FinOps Practitioner",
       "Hourly-billed AI endpoints (real-time inference, GPU hosting) by day, counting days with zero requests. Scale-to-zero, "
       "serverless inference or a schedule removes most of the idle cost.",
       ["gpu", "endpoint", "idle", "sagemaker", "inference endpoint", "scale to zero", "model hosting"],
       """
WITH d AS (
  SELECT CAST(ChargePeriodStart AS DATE) AS Day, ResourceName,
         json_extract_string(Tags, '$.application') AS Application,
         json_extract_string(Tags, '$.cost_center') AS CostCenter,
         SUM(EffectiveCost) AS Cost, SUM(COALESCE(x_RequestCount, 0)) AS Requests
  FROM CostAndUsage
  WHERE ServiceCategory = 'AI and Machine Learning' AND ConsumedUnit = 'Hours' AND x_AIProduct IS NOT NULL
    AND ChargePeriodStart >= :period_start AND ChargePeriodStart < :period_end
  GROUP BY CAST(ChargePeriodStart AS DATE), ResourceName, json_extract_string(Tags, '$.application'),
           json_extract_string(Tags, '$.cost_center')
)
SELECT
  ResourceName, Application, CostCenter,
  COUNT(*) AS DaysBilled,
  SUM(CASE WHEN Requests = 0 THEN 1 ELSE 0 END) AS IdleDays,
  MIN(CASE WHEN Requests = 0 THEN Day END) AS FirstIdleDay,
  ROUND(SUM(CASE WHEN Requests = 0 THEN Cost ELSE 0 END), 2) AS IdleCost,
  ROUND(SUM(Cost), 2) AS TotalCost
FROM d
GROUP BY ResourceName, Application, CostCenter
HAVING SUM(CASE WHEN Requests = 0 THEN 1 ELSE 0 END) > 0
ORDER BY IdleCost DESC""", PERIOD, "Request counts come from the endpoint's metrics (x_RequestCount)."),
    uc("UO-10", "optimization", "Optimization · AI", "Provisioned throughput utilization and pay-as-you-go break-even",
       "Is provisioned AI capacity (PTU, provisioned throughput) used enough to beat pay-as-you-go?",
       ["ai_program_owner", "finops_analyst", "cfo"], "AI Program Owner, FinOps Practitioner",
       "Compares each provisioned deployment's fixed cost with what its actual tokens would cost at pay-as-you-go list "
       "prices (ai_model_catalog), with average utilization.",
       ["ptu", "provisioned throughput", "reserved capacity", "utilization", "azure openai", "break even"],
       """
WITH c AS (
  SELECT Deployment, Application, ModelName, MAX(Units) AS Units, COUNT(*) AS Days,
         SUM(DailyCost) AS ProvisionedCost, AVG(UtilizationPct) AS AvgUtilizationPct
  FROM ai_provisioned_capacity
  WHERE UsageDate >= :period_start AND UsageDate < :period_end
  GROUP BY Deployment, Application, ModelName
),
t AS (
  SELECT Application, SUM(Requests * AvgInputTokens) AS InTok, SUM(Requests * AvgOutputTokens) AS OutTok
  FROM ai_app_telemetry_daily
  WHERE UsageDate >= :period_start AND UsageDate < :period_end
  GROUP BY Application
),
x AS (
  SELECT c.*, (t.InTok * m.InputPricePerM + t.OutTok * m.OutputPricePerM) / 1000000 AS Payg
  FROM c LEFT JOIN t ON t.Application = c.Application LEFT JOIN ai_model_catalog m ON m.ModelName = c.ModelName
)
SELECT
  Deployment, Application, ModelName, Units, Days,
  ROUND(AvgUtilizationPct, 1) AS AvgUtilizationPct,
  ROUND(ProvisionedCost, 2) AS ProvisionedCost,
  ROUND(Payg, 2) AS PayAsYouGoEquivalent,
  ROUND((ProvisionedCost - Payg) / Days * 30, 2) AS MonthlySavingsIfPayg,
  CASE WHEN AvgUtilizationPct < 50 AND ProvisionedCost > Payg THEN 'Under-utilized: move to pay-as-you-go or fewer units'
       ELSE 'Provisioning justified' END AS Finding
FROM x
ORDER BY MonthlySavingsIfPayg DESC""", PERIOD, "Pay-as-you-go equivalents use list prices; negotiated rates move the break-even."),
    uc("UO-11", "optimization", "Optimization · AI", "Failed and retried LLM requests (tokens paid for nothing)",
       "How much LLM spend goes on requests that failed or were retried?",
       ["ai_program_owner", "finops_analyst"], "AI Program Owner, Engineering",
       "Joins gateway telemetry (failed and retried requests) to each application's AI cost. Retry spikes usually come "
       "from timeouts set below the model's p95 latency or from missing backoff.",
       ["retry", "retries", "failed requests", "errors", "timeout", "waste", "gateway"],
       """
WITH t AS (
  SELECT Application, MAX(ModelName) AS ModelName, SUM(Requests) AS Requests, SUM(FailedRequests) AS Failed,
         SUM(RetriedRequests) AS Retried
  FROM ai_app_telemetry_daily
  WHERE UsageDate >= :period_start AND UsageDate < :period_end
  GROUP BY Application
),
c AS (
  SELECT json_extract_string(Tags, '$.application') AS Application, SUM(EffectiveCost) AS Cost
  FROM CostAndUsage
  WHERE (x_AIProduct IS NOT NULL OR json_extract_string(Tags, '$.ai_component') IS NOT NULL)
    AND ChargePeriodStart >= :period_start AND ChargePeriodStart < :period_end
  GROUP BY json_extract_string(Tags, '$.application')
)
SELECT
  t.Application, t.ModelName, t.Requests,
  ROUND(100.0 * t.Failed / NULLIF(t.Requests, 0), 1) AS FailedPct,
  ROUND(100.0 * t.Retried / NULLIF(t.Requests, 0), 1) AS RetriedPct,
  ROUND(c.Cost, 2) AS AppCost,
  ROUND(c.Cost * (t.Failed + t.Retried) / NULLIF(t.Requests, 0), 2) AS WastedCost
FROM t
JOIN c ON c.Application = t.Application
WHERE t.Failed + t.Retried > 0
ORDER BY WastedCost DESC""", PERIOD, "Waste is pro rata by request count; long failed requests may cost more."),
    uc("UO-12", "optimization", "Optimization · AI", "Context bloat and truncated responses",
       "Which LLM applications are sending ever-larger prompts, or hitting their output cap?",
       ["ai_program_owner", "finops_analyst"], "AI Program Owner, Engineering",
       "Compares each application's average input tokens in the first and last two weeks, and the share of responses "
       "truncated at max_tokens. Growing context usually means conversation history or retrieval results are never trimmed; "
       "caching the stable prefix and retrieving fewer chunks cut cost without losing quality.",
       ["context", "prompt size", "input tokens", "truncated", "max tokens", "context window", "bloat"],
       """
WITH w AS (
  SELECT Application, MAX(ModelName) AS ModelName,
         AVG(CASE WHEN UsageDate < CAST(:window_start AS DATE) + INTERVAL '14' DAY THEN AvgInputTokens END) AS FirstTwoWeeksInput,
         AVG(CASE WHEN UsageDate > CAST(:window_end AS DATE) - INTERVAL '14' DAY THEN AvgInputTokens END) AS LastTwoWeeksInput,
         SUM(TruncatedResponses) AS Truncated, SUM(Requests) AS Requests, MAX(MaxTokensSetting) AS MaxTokens,
         AVG(AvgOutputTokens) AS AvgOutput
  FROM ai_app_telemetry_daily
  WHERE UsageDate >= :window_start AND UsageDate <= :window_end
  GROUP BY Application
)
SELECT
  Application, ModelName,
  ROUND(FirstTwoWeeksInput, 0) AS FirstTwoWeeksAvgInput,
  ROUND(LastTwoWeeksInput, 0) AS LastTwoWeeksAvgInput,
  ROUND(100 * (LastTwoWeeksInput - FirstTwoWeeksInput) / NULLIF(FirstTwoWeeksInput, 0), 1) AS InputGrowthPct,
  ROUND(100.0 * Truncated / NULLIF(Requests, 0), 1) AS TruncatedPct,
  MaxTokens,
  ROUND(AvgOutput, 0) AS AvgOutputTokens,
  CASE WHEN LastTwoWeeksInput > 1.5 * FirstTwoWeeksInput THEN 'Context growing: trim history, retrieve less, cache the stable prefix'
       WHEN 100.0 * Truncated / NULLIF(Requests, 0) > 5 THEN 'Responses truncated at max_tokens'
       ELSE 'Stable' END AS Finding
FROM w
ORDER BY InputGrowthPct DESC""",
       [("window_start", "date", "2026-07-01", "Window start"), ("window_end", "date", "2026-10-22", "Window end")],
       ""),
    uc("UO-13", "optimization", "Optimization · AI", "Cost per accepted outcome by LLM application",
       "What does each LLM application cost per request and per outcome a user actually accepted?",
       ["ai_program_owner", "finops_analyst", "cfo", "tech_exec"], "AI Program Owner, CFO",
       "All AI cost of an application (inference, embeddings, vector store) divided by its requests and by the outcomes "
       "users or workflows accepted. Cost per accepted outcome is the unit to manage and to defend to the board; cost per "
       "prompt rewards answers nobody uses.",
       ["unit cost", "cost per answer", "cost per outcome", "accepted", "value", "roi", "rag"],
       """
WITH t AS (
  SELECT Application, MAX(ModelName) AS ModelName, SUM(Requests) AS Requests, SUM(AcceptedOutcomes) AS Accepted
  FROM ai_app_telemetry_daily
  WHERE UsageDate >= :period_start AND UsageDate < :period_end
  GROUP BY Application
),
c AS (
  SELECT json_extract_string(Tags, '$.application') AS Application, SUM(EffectiveCost) AS Cost
  FROM CostAndUsage
  WHERE (x_AIProduct IS NOT NULL OR json_extract_string(Tags, '$.ai_component') IS NOT NULL)
    AND ChargePeriodStart >= :period_start AND ChargePeriodStart < :period_end
  GROUP BY json_extract_string(Tags, '$.application')
)
SELECT
  t.Application, t.ModelName, t.Requests,
  t.Accepted AS AcceptedOutcomes,
  ROUND(100.0 * t.Accepted / NULLIF(t.Requests, 0), 1) AS AcceptancePct,
  ROUND(c.Cost, 2) AS Cost,
  ROUND(c.Cost / NULLIF(t.Requests, 0), 4) AS CostPerRequest,
  ROUND(c.Cost / NULLIF(t.Accepted, 0), 4) AS CostPerAcceptedOutcome
FROM t
JOIN c ON c.Application = t.Application
ORDER BY CostPerAcceptedOutcome DESC""", PERIOD, "Accepted outcomes come from application telemetry (thumbs-up, task completed, ticket closed)."),
    uc("UO-14", "optimization", "Optimization · AI", "Model routing what-if: same workload on a cheaper approved model",
       "What would each LLM application cost on a cheaper approved model from the same vendor?",
       ["ai_program_owner", "finops_analyst", "cfo"], "AI Program Owner, FinOps Practitioner",
       "Re-prices each application's actual token mix at the list prices of cheaper approved models from the same vendor. "
       "Short outputs (extraction, summaries, classification) usually route well; validate quality on a sample first.",
       ["model routing", "cheaper model", "haiku", "downgrade model", "what if", "right size model"],
       """
WITH t AS (
  SELECT Application, ModelName, SUM(Requests * AvgInputTokens) AS InTok, SUM(Requests * AvgOutputTokens) AS OutTok,
         AVG(AvgOutputTokens) AS AvgOut
  FROM ai_app_telemetry_daily
  WHERE UsageDate >= :period_start AND UsageDate < :period_end
  GROUP BY Application, ModelName
),
cur AS (
  SELECT t.*, m.ModelVendor, m.ModelTier, m.InputPricePerM AS CurIn,
         (t.InTok * m.InputPricePerM + t.OutTok * m.OutputPricePerM) / 1000000 AS CurrentCost
  FROM t JOIN ai_model_catalog m ON m.ModelName = t.ModelName
)
SELECT
  c.Application, c.ModelName AS CurrentModel, c.ModelTier AS CurrentTier,
  ROUND(c.AvgOut, 0) AS AvgOutputTokens,
  a.ModelName AS CandidateModel, a.ModelTier AS CandidateTier,
  ROUND(c.CurrentCost, 2) AS CurrentCost,
  ROUND((c.InTok * a.InputPricePerM + c.OutTok * a.OutputPricePerM) / 1000000, 2) AS CandidateCost,
  ROUND(c.CurrentCost - (c.InTok * a.InputPricePerM + c.OutTok * a.OutputPricePerM) / 1000000, 2) AS Savings,
  ROUND(100 * (c.CurrentCost - (c.InTok * a.InputPricePerM + c.OutTok * a.OutputPricePerM) / 1000000) / NULLIF(c.CurrentCost, 0), 1) AS SavingsPct,
  CASE WHEN c.AvgOut < 500 THEN 'Short outputs: strong routing candidate (validate quality on a sample)'
       ELSE 'Long outputs: evaluate quality before routing' END AS Guidance
FROM cur c
JOIN ai_model_catalog a ON a.ModelVendor = c.ModelVendor AND a.Approved AND a.ModelName <> c.ModelName AND a.InputPricePerM < c.CurIn
ORDER BY Savings DESC""", PERIOD, "List prices from ai_model_catalog; Provisioned deployments are excluded from savings in practice."),
    uc("BF-04", "budgets", "Budgeting & Forecasting", "AI budget burn-down and projected exhaustion date",
       "Which teams will run out of their monthly AI budget before month end, and when?",
       ["finops_analyst", "fpa_analyst", "ai_program_owner", "cfo"], "FinOps Practitioner, FP&A",
       "Month-to-date AI spend against each cost center's AI budget, the daily burn rate and the date the budget runs out "
       "at that rate. Feeds the token-budget alert.",
       ["ai budget", "token budget", "burn rate", "run out", "exhaustion", "budget alert"],
       """
WITH b AS (
  SELECT CostCenter, BudgetAmount FROM ai_budget WHERE BudgetPeriodStart = :month
),
s AS (
  SELECT COALESCE(json_extract_string(Tags, '$.cost_center'), '(untagged)') AS CostCenter,
         SUM(EffectiveCost) AS MonthToDate, COUNT(DISTINCT CAST(ChargePeriodStart AS DATE)) AS DaysElapsed
  FROM CostAndUsage
  WHERE x_AIProduct IS NOT NULL AND BillingPeriodStart = :month AND ChargePeriodStart <= :as_of
  GROUP BY COALESCE(json_extract_string(Tags, '$.cost_center'), '(untagged)')
)
SELECT
  b.CostCenter,
  ROUND(b.BudgetAmount, 2) AS BudgetAmount,
  ROUND(s.MonthToDate, 2) AS MonthToDate,
  ROUND(100 * s.MonthToDate / NULLIF(b.BudgetAmount, 0), 1) AS BurnPct,
  ROUND(s.MonthToDate / NULLIF(s.DaysElapsed, 0), 2) AS DailyBurn,
  CAST(:month AS DATE) + CAST(FLOOR(b.BudgetAmount / NULLIF(s.MonthToDate / NULLIF(s.DaysElapsed, 0), 0)) AS INTEGER) AS ProjectedExhaustion,
  CASE WHEN s.MonthToDate >= b.BudgetAmount THEN 'Exhausted'
       WHEN b.BudgetAmount / NULLIF(s.MonthToDate / NULLIF(s.DaysElapsed, 0), 0) < 31 THEN 'Will run out this month'
       ELSE 'Within budget' END AS Status
FROM b
JOIN s ON s.CostCenter = b.CostCenter
ORDER BY BurnPct DESC""",
       [("month", "date", "2026-10-01", "Budget month"), ("as_of", "date", "2026-10-22", "Evaluate as of")],
       "Budgets come from ai_budget; seats bought at month start burn the budget on day one."),
]


if __name__ == "__main__":
    for u in USE_CASES:
        with open(os.path.join(OUT, f"{u['id']}.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(u, SOURCE))
        print("wrote", u["id"])
