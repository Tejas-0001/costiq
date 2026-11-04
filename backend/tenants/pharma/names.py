"""Pharma name tables for the shared sample generators (data/generate_sample.py, data/cloud_rows.py,
data/optimization.py). The generators own the seeded scenarios (runaway key, agent surge, unused seats, idle GPU, ...);
this file only names the cost centers, workspaces, accounts and applications they run on. Another tenant supplies the
same variables with the same shapes (see tenants/__init__.py). Read with data/sample_common.py: names().
"""

# ------------------------------------------------------------------------------------------- cost centers
# Eight cost centers, in this order: users are spread over them by position (generate_sample.py USERS):
# [0] primary engineering, [1]..[4] business units, [5] data platform, [6] research/planning, [7] corporate IT.
DEPTS = ["Clinical Operations", "Patient Data", "Consumer Health", "HR", "Finance", "Data & Analytics", "Research & Discovery",
         "Corporate IT"]
ENG_DEPTS = ("Clinical Operations", "Data & Analytics")       # engineers: Claude Code and GitHub Copilot users
NO_CLAUDE_ENTERPRISE_DEPTS = ("Clinical Operations",)          # cost centers without Claude Enterprise seats
IT_CC = "Corporate IT"            # seat purchases, shared commitments, shared clusters; fallback cost center
SHARED_CC = "Shared Services"     # tax lines
CLAUDE_CODE_CC = "Clinical Operations"

# ------------------------------------------------------------------------------------------- Claude API workspaces
# id, name, cost center, application, data_class, api keys, model mix, tier, cache profile (good | write_only | none)
WORKSPACES = [
    ("wrkspc_support", "support-assistant", "Patient Data", "patient-support-assistant", "internal",
     ["support-assistant-prod", "support-assistant-loadtest"], {"claude-sonnet-5-5": 0.85, "claude-haiku-5-5": 0.15}, "standard", "good"),
    ("wrkspc_sales", "consumer-research", "Consumer Health", "consumer-research", "internal",
     ["consumer-research-batch"], {"claude-opus-5-5": 0.7, "claude-sonnet-5-5": 0.3}, "batch", "none"),
    ("wrkspc_fin", "doc-summarizer", "Finance", "invoice-summarizer", "confidential",
     ["doc-summarizer-prod"], {"claude-haiku-5-5": 1.0}, "batch", "none"),
    ("wrkspc_genomic", "genomics-data", "Patient Data", "protocol-assistant", "genomic",
     ["genomics-data-prod"], {"claude-sonnet-5-5": 1.0}, "standard", "good"),
    ("wrkspc_rnd", "analytics-sandbox", "Data & Analytics", "experiments", "internal",
     ["analytics-notebook", "analytics-agent-eval"], {"claude-opus-5-5": 0.9, "claude-sonnet-5-5": 0.1}, "standard", "write_only"),
    (None, "Default Workspace", None, None, None,
     [None], {"claude-sonnet-5-5": 1.0}, "standard", "none"),  # untagged + playground (null api key)
]
WS_SCALE = {"support-assistant": 40e6, "consumer-research": 18e6, "doc-summarizer": 60e6, "genomics-data": 10e6,
            "analytics-sandbox": 9e6, "Default Workspace": 1.5e6}
RUNAWAY_KEY = "support-assistant-loadtest"     # Oct 14 runaway retry loop (AN-01)
RESIDENCY_DATA_CLASS = "genomic"               # served partly from 'global' from Sep 1 (GV-02); pack usecase_vars match
DEV_WORKSPACES = ("analytics-sandbox",)        # environment = dev
FAST_MODE = {"app": "experiments", "cost_center": "Data & Analytics", "workspace_id": "wrkspc_rnd",
             "workspace": "analytics-sandbox", "api_key": "analytics-agent-eval"}   # September fast-mode experiment
CREDIT_CORRECTION = {"workspace_id": "wrkspc_support", "workspace": "support-assistant",
                     "app": "patient-support-assistant", "cost_center": "Patient Data"}   # August service credit

# ------------------------------------------------------------------------------------------- other AI workloads
# Claude on Bedrock (Sonnet, Haiku), Vertex and Foundry, in that order: (application, cost center)
HOSTED_CLAUDE = [("contract-review", "Legal"), ("ticket-classifier", "Patient Data"), ("marketing-content", "Research & Discovery"),
                 ("knowledge-agent", "Corporate IT")]
AOAI_APPS = [("employee-chat", "Corporate IT"), ("doc-extraction", "Finance")]   # Azure OpenAI chat, extraction
# Copilot Studio agents: id, name, cost center, daily base volume
AGENTS = [("agt-it-01", "IT-Helpdesk-Agent", "Corporate IT", 150), ("agt-hr-02", "HR-Onboarding-Agent", "HR", 45),
          ("agt-sales-03", "Sales-Proposal-Agent", "Consumer Health", 65), ("agt-fin-04", "Finance-Expense-Agent", "Finance", 50)]
SURGE_AGENT = "HR-Onboarding-Agent"            # Oct 18+ credit surge (AN-02, RO-01)
COPILOT_ENVIRONMENT_ID = "env-pharma-prod"
GITHUB_ORG = "pharma-eng"

# ------------------------------------------------------------------------------------------- budgets
AI_BUDGET = {"Clinical Operations": 9000, "Patient Data": 9000, "Consumer Health": 3000, "Finance": 3500, "Data & Analytics": 3500,
             "Research & Discovery": 1500, "Corporate IT": 9000, "HR": 600, "Legal": 3000}
# Technology budget = September actuals x factor (cost centers not listed use the default factor).
BASE_BUDGET_FACTORS, BASE_BUDGET_DEFAULT = {"Data & Analytics": 0.97, "Patient Data": 1.0}, 1.12           # generate_sample.py
BUDGET_FACTORS = {"Data & Analytics": 0.97, "Patient Data": 1.0, "Research & Discovery": 0.98}            # after add-ons
BUDGET_DEFAULT = 1.08

# ------------------------------------------------------------------------------------------- cloud (cloud_rows.py)
# key -> sub account id, name, cost center, application, application id, issuer key. Keep the keys: the scenarios use them.
SUBS = {
    "aws-prod": ("111111111111", "pharma-prod", "Clinical Operations", "web-store", "APP-1001", "AWS"),
    "aws-data": ("222222222222", "pharma-data", "Data & Analytics", "analytics-lake", "APP-2002", "AWS"),
    "aws-dev":  ("333333333333", "pharma-dev", "Clinical Operations", "dev-sandbox", "APP-1003", "AWS"),
    "az-crm":   ("sub-0a1b-crm", "CRM-Prod", "Patient Data", "crm-portal", "APP-3001", "Microsoft"),
    "az-erp":   ("sub-0c2d-erp", "ERP", "Finance", "erp-finance", "APP-3002", "Microsoft"),
    "az-eu":    ("sub-0e3f-eu", "EU-Web", "Research & Discovery", "eu-web", "APP-3003", "MicrosoftEU"),
    "gcp-mktg": ("pharma-rd-analytics", "Marketing Analytics", "Research & Discovery", "marketing-analytics", "APP-4001", "Google Cloud"),
    "gcp-hr":   ("pharma-hr-apps", "HR Apps", "HR", "hr-portal", "APP-4002", "Google Cloud"),
    "snow":     ("PHARMA_PROD", "Snowflake Prod", "Data & Analytics", "analytics-lake", "APP-2002", "Snowflake"),
}
S3_BUCKETS = [("aws-prod", "pharma-web-assets", 18000), ("aws-data", "pharma-datalake-raw", 420000),
              ("aws-data", "pharma-datalake-curated", 160000), ("aws-dev", "pharma-dev-scratch", 9000)]   # sub, bucket, GB
# Shared EKS split-cost allocation: namespace, share, cost center, application
EKS_NAMESPACES = [("checkout", 0.45, "Clinical Operations", "web-store"), ("ml-serving", 0.35, "Data & Analytics", "ml-serving"),
                  ("personalization", 0.20, "Research & Discovery", "personalization")]

# ------------------------------------------------------------------------------------------- optimization add-on
OPT_AI_ACCOUNT = ("aws-ai-8009", "AI Platform AWS")
OPT_AI_SUB = ("888800008009", "pharma-ai-platform")
OPT_RD_ACCOUNT = ("aws-rd-8008", "R&D AWS")              # the tenant add-on's workload account (tenants/pharma/sample.py)
OPT_RD_SUB = ("888800008008", "pharma-rd-platform")
OPT_RD_SOURCE = "aws_rd_focus_export"                  # x_SourceSystem of the workload account rows (data/optimization.py)
OPT_PTU_SUB = ("sub-ms-ai-ptu", "pharma-ai-ptu")
OPT_PTU = {"deployment": "aoai-ptu-discovery", "app": "csr-drafting-agent", "model": "gpt-5.4", "units": 50, "daily": 1200.0,
           "cost_center": "Research & Discovery"}
# LLM app telemetry: app, model, cost center, requests/day (weekday), avg input, avg output, max_tokens, failed %,
# retried %, accepted %, input growth over the window (context bloat), truncation %. The PTU app is OPT_PTU["app"].
OPT_APPS = [
    ("patient-support-assistant", "claude-sonnet-5-5", "Patient Data", 2000, 9000, 400, 1024, 0.03, 0.14, 0.70, 0.0, 0.01),
    ("protocol-assistant", "claude-sonnet-5-5", "Research & Discovery", 300, 25000, 900, 2048, 0.01, 0.02, 0.60, 0.0, 0.02),
    ("csr-drafting-agent", "gpt-5.4", "Research & Discovery", 420, 40000, 2500, 4096, 0.02, 0.04, 0.35, 3.0, 0.12),
    ("doc-search-rag", "claude-sonnet-5-5", "Corporate IT", 3000, 12000, 600, 1024, 0.01, 0.02, 0.80, 0.0, 0.01),
    ("ae-intake-bot", "claude-opus-5-5", "Clinical Operations", 2400, 6000, 350, 1024, 0.005, 0.01, 0.95, 0.0, 0.0),
]
OPT_BEDROCK_APPS = [("doc-search-rag", "Corporate IT"), ("ae-intake-bot", "Clinical Operations")]   # priced from telemetry
OPT_RAG = {"app": "doc-search-rag", "cost_center": "Corporate IT"}      # embeddings + vector store components
OPT_ORPHANS = {"prefix": "vol-edc-test-orphan-0", "app": "edc-test", "cost_center": "Clinical Operations",
               "description": "left behind by the EDC test rebuild"}   # three unattached EBS volumes
# resource name, profile (busy | idle | nonprod | rightsize | gpu | detached)
OPT_UTIL = [("edc-dev-ec2", "nonprod"), ("edc-test-ec2", "idle"), ("insights-ec2", "rightsize"),
            ("molgen-gpu-endpoint", "gpu"), ("trial-analytics-emr", "busy"), ("registry-aurora", "busy"), ("edc-app-asg", "busy"),
            ("portal-eks", "busy"), ("vol-edc-test-orphan-01", "detached"), ("vol-edc-test-orphan-02", "detached"),
            ("vol-edc-test-orphan-03", "detached")]
# Provider recommendations: id, source, provider, sub, resource, action, current, recommended, monthly cost, savings,
# effort, restart ("{rd_sub}" and "{ptu_sub}" are the accounts above; "{ptu}" is the PTU deployment)
OPT_RECS = [
    ("coh-0001", "aws_cost_optimization_hub", "AWS", "{rd_sub}", "edc-test-ec2", "stop_idle", "m6i.4xlarge, CPU p95 2%",
     "Stop or terminate", 4800, 4800, "low", True),
    ("coh-0002", "aws_cost_optimization_hub", "AWS", "{rd_sub}", "edc-dev-ec2", "schedule_offhours", "m6i.8xlarge running 24x7",
     "Run 07:00-19:00 weekdays", 9300, 5980, "low", True),
    ("coh-0003", "aws_cost_optimization_hub", "AWS", "{rd_sub}", "trial-analytics-emr", "rightsize", "r6g.8xlarge x 6 core nodes",
     "r6g.4xlarge x 6 core nodes", 24600, 4900, "medium", True),
    ("coh-0004", "aws_cost_optimization_hub", "AWS", "{rd_sub}", "registry-aurora", "upgrade_generation", "db.r6i.8xlarge",
     "db.r7g.8xlarge (Graviton)", 28800, 3460, "medium", True),
    ("coh-0005", "aws_cost_optimization_hub", "AWS", "{rd_sub}", "trial-data-lake", "storage_tier", "S3 Standard, 62% objects not read in 90 days",
     "S3 Intelligent-Tiering", 7200, 2160, "low", False),
    ("coh-0006", "aws_cost_optimization_hub", "AWS", "{rd_sub}", "vol-edc-test-orphan-01", "delete_unattached",
     "gp3 500 GiB, unattached 110 days", "Snapshot and delete", 420, 420, "low", False),
    ("coh-0007", "aws_cost_optimization_hub", "AWS", "{rd_sub}", "vol-edc-test-orphan-02", "delete_unattached",
     "gp3 500 GiB, unattached 110 days", "Snapshot and delete", 330, 330, "low", False),
    ("coh-0008", "aws_cost_optimization_hub", "AWS", "{rd_sub}", "vol-edc-test-orphan-03", "delete_unattached",
     "gp3 500 GiB, unattached 110 days", "Snapshot and delete", 270, 270, "low", False),
    ("coh-0009", "aws_cost_optimization_hub", "AWS", "{rd_sub}", "account:{rd_sub}", "purchase_commitment",
     "On-demand compute $64K/month", "1-year Compute Savings Plan, $28/hour", 64000, 11800, "low", False),
    ("adv-0001", "azure_advisor", "Microsoft", "{ptu_sub}", "{ptu}", "rightsize", "50 PTU, average utilization 31%",
     "Pay-as-you-go or 15 PTU", 36000, 29500, "medium", False),
]
# Optimization backlog: id, origin, lever, domain, title, scope type, scope key, cost center, owner, confidence, status,
# estimated monthly savings, (identified, approved, implemented, verified) as (y, m, d) or None
OPT_ACTIONS = [
    ("OPT-001", "coh-0001", "idle", "cloud", "Stop idle EDC test instance", "resource", "edc-test-ec2", "Clinical Operations",
     "Clinical Operations IT", "provider_recommended", "identified", 4800, ((2026, 7, 28), None, None, None)),
    ("OPT-002", "coh-0002", "scheduling", "cloud", "Schedule EDC dev to business hours", "resource", "edc-dev-ec2", "Clinical Operations",
     "Clinical Operations IT", "provider_recommended", "approved", 5980, ((2026, 8, 20), (2026, 9, 2), None, None)),
    ("OPT-003", "manual", "rightsizing", "cloud", "Rightsize consumer insights server", "resource", "insights-ec2",
     "Consumer Health", "Consumer Health IT", "costiq_estimated", "verified", 7300, ((2026, 8, 10), (2026, 8, 20), (2026, 9, 1), None)),
    ("OPT-004", "manual", "rightsizing", "cloud", "Reduce ePRO ingest Kinesis shards", "resource", "epro-ingest-kinesis",
     "Patient Data", "Patient Data IT", "costiq_estimated", "implemented", 3700, ((2026, 8, 25), (2026, 9, 5), (2026, 9, 15), None)),
    ("OPT-005", "UO-09", "gpu_idle", "ai", "Scale discovery GPU endpoint to zero when idle", "resource", "molgen-gpu-endpoint",
     "Research & Discovery", "Research & Discovery", "costiq_estimated", "identified", 49000, ((2026, 9, 24), None, None, None)),
    ("OPT-006", "adv-0001", "ptu", "ai", "Move CSR drafting agent off 50 PTU", "resource", "{ptu}", "Research & Discovery",
     "AI Program Office", "provider_recommended", "identified", 29500, ((2026, 8, 1), None, None, None)),
    ("OPT-007", "UO-05", "seats", "ai", "Reclaim unused Copilot and Claude seats", "seat_pool", "unused_seats", "Corporate IT",
     "Corporate IT", "costiq_estimated", "approved", 1190, ((2026, 10, 1), (2026, 10, 6), None, None)),
]
OPT_AI_BUDGETS = [("Research & Discovery", 95000), ("Corporate IT", 16000), ("Clinical Operations", 16000), ("Patient Data", 9000)]
