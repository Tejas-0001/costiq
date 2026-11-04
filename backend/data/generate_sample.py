"""Generate a synthetic FOCUS 1.4 + AI-extension sample dataset (all four FOCUS datasets plus the
supporting AI tables) covering 2026-07-01 .. 2026-10-22.

All prices, seat costs and credit rates are ILLUSTRATIVE, not vendor list prices.
Deliberate scenarios are seeded so every use case returns rows:
  - Oct 14: runaway Claude API key (support-assistant-loadtest) spike
  - Oct 18+: HR-Onboarding-Agent credit surge in Copilot Studio -> Copilot Credit overage
  - genomics-data workspace partly served from 'global' geo (residency policy requires 'us')
  - analytics-sandbox writes prompt cache but rarely reads it
  - unused M365 Copilot / GitHub Copilot / Claude Enterprise seats
  - GitHub users on a non-approved model (gpt-5.4-pro)
  - Anthropic September invoice line differs from charge detail (reconciliation gap)
  - Anthropic August service credit (correction)

Usage: python3 -I gen_sample_data.py <schema.sql> <out.duckdb> <csv_dir>
"""
import sys, os, json, random, math, datetime as dt
import duckdb

SCHEMA, DB, CSV = sys.argv[1], sys.argv[2], sys.argv[3]
random.seed(42)
START, END = dt.date(2026, 7, 1), dt.date(2026, 10, 22)
DAYS = [START + dt.timedelta(d) for d in range((END - START).days + 1)]
MONTHS = [dt.date(2026, m, 1) for m in (7, 8, 9, 10)]
def month_start(d): return dt.date(d.year, d.month, 1)
def next_month(d): return dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
def ts(d): return dt.datetime(d.year, d.month, d.day)
def r2(x): return round(x, 6)

# ----------------------------------------------------------------------------- reference data
MODELS = {  # illustrative list prices per 1M tokens: input, output, cache_read, cache_write
    "claude-opus-5-5":   ("Anthropic", "frontier", True, 5.00, 25.00, 0.50, 6.25),
    "claude-sonnet-5-5": ("Anthropic", "balanced", True, 3.00, 15.00, 0.30, 3.75),
    "claude-haiku-5-5":  ("Anthropic", "fast",     True, 1.00,  5.00, 0.10, 1.25),
    "gpt-5.4":           ("OpenAI",    "frontier", True, 2.50, 15.00, 0.25, 0.00),
    "gpt-5.4-mini":      ("OpenAI",    "fast",     True, 0.40,  1.60, 0.04, 0.00),
    "gpt-5.4-pro":       ("OpenAI",    "frontier", False, 15.00, 60.00, 1.50, 0.00),
}
TT_IDX = {"input": 3, "output": 4, "cache_read": 5, "cache_write": 6}
TT_COL = {"input": "x_InputTokens", "output": "x_OutputTokens",
          "cache_read": "x_CacheReadTokens", "cache_write": "x_CacheWriteTokens"}
def price(model, tt): return MODELS[model][TT_IDX[tt]]

DEPTS = ["Clinical Operations", "Patient Data", "Consumer Health", "HR", "Finance", "Data & Analytics", "Research & Discovery", "Corporate IT"]
USERS = []
for i in range(1, 121):
    dept = DEPTS[0] if i <= 40 else DEPTS[5] if i <= 50 else DEPTS[1 + (i % 7)]
    USERS.append((f"user{i:03d}@company.example", dept))
DEPT_OF = dict(USERS)
ENG = [u for u, d in USERS if d in ("Clinical Operations", "Data & Analytics")]

ISSUERS = {  # issuer -> (billing account id, name, provider)
    "Microsoft": ("ms-mca-1001", "MCA", "Microsoft"),
    "AWS": ("aws-payer-2002", "AWS Payer", "AWS"),
    "Google Cloud": ("gcp-billing-3003", "GCP Billing", "Google Cloud"),
    "Anthropic": ("ant-org-4004", "Claude Platform", "Anthropic"),
    "GitHub": ("gh-ent-5005", "GitHub Enterprise", "GitHub"),
}
ANT_ENT_ACCT = ("ant-ent-4005", "Claude Enterprise")

CC = {}  # contract commitments
def add_cc(**k): CC[k["ContractCommitmentId"]] = k
add_cc(ContractCommitmentId="cc-ms-copilot-credits", ContractId="ms-ea-2026", ServiceProviderName="Microsoft",
       InvoiceIssuerName="Microsoft", ContractCommitmentType="Copilot Credit Pack", ContractCommitmentCategory="Usage",
       ContractCommitmentQuantity=300000, ContractCommitmentUnit="Copilot Credits", ContractCommitmentCost=2400,
       ContractCommitmentFulfillmentInterval="Monthly", ContractCommitmentModel="Discontinuous",
       ContractCommitmentBenefitCategory="Entitlement", ContractCommitmentOfferCategory="Public",
       ContractCommitmentPaymentModel="No Upfront", ContractCommitmentPaymentInterval="Monthly",
       ContractCommitmentDiscountPercentage=0.2, start=dt.date(2026, 7, 1), end=dt.date(2027, 7, 1),
       ContractCommitmentDescription="25,000 Copilot Credits per month prepaid capacity pack (use-it-or-lose-it monthly)",
       ContractCommitmentApplicability={"ServiceName": ["Microsoft Copilot Studio"]})
add_cc(ContractCommitmentId="cc-m365-copilot-seats", ContractId="ms-ea-2026", ServiceProviderName="Microsoft",
       InvoiceIssuerName="Microsoft", ContractCommitmentType="Seat Subscription", ContractCommitmentCategory="Usage",
       ContractCommitmentQuantity=100, ContractCommitmentUnit="Seats", ContractCommitmentCost=36000,
       ContractCommitmentFulfillmentInterval="Monthly", ContractCommitmentModel="Continuous",
       ContractCommitmentBenefitCategory="Entitlement", ContractCommitmentOfferCategory="Negotiated",
       ContractCommitmentPaymentModel="No Upfront", ContractCommitmentPaymentInterval="Monthly",
       ContractCommitmentDiscountPercentage=0.0, start=dt.date(2026, 7, 1), end=dt.date(2027, 7, 1),
       ContractCommitmentDescription="Microsoft 365 Copilot, 100 seats, annual term billed monthly",
       ContractCommitmentApplicability={"ServiceName": ["Microsoft 365 Copilot"]})
add_cc(ContractCommitmentId="cc-ms-macc", ContractId="ms-macc-2025", ServiceProviderName="Microsoft",
       InvoiceIssuerName="Microsoft", ContractCommitmentType="Azure Consumption Commitment", ContractCommitmentCategory="Spend",
       ContractCommitmentQuantity=500000, ContractCommitmentUnit="USD", ContractCommitmentCost=500000,
       ContractCommitmentFulfillmentInterval="Full Period", ContractCommitmentModel="Discontinuous",
       ContractCommitmentBenefitCategory="Discount", ContractCommitmentOfferCategory="Negotiated",
       ContractCommitmentPaymentModel="No Upfront", ContractCommitmentPaymentInterval="Monthly",
       ContractCommitmentDiscountPercentage=0.05, start=dt.date(2025, 7, 1), end=dt.date(2028, 7, 1),
       ContractCommitmentDescription="3-year MACC; Azure OpenAI and Claude in Foundry decrement it",
       ContractCommitmentApplicability={"ServiceName": ["Azure OpenAI", "Claude in Microsoft Foundry", "Microsoft Copilot Studio"]})
add_cc(ContractCommitmentId="cc-ant-2026", ContractId="ant-msa-2026", ServiceProviderName="Anthropic",
       InvoiceIssuerName="Anthropic", ContractCommitmentType="Committed Spend", ContractCommitmentCategory="Spend",
       ContractCommitmentQuantity=150000, ContractCommitmentUnit="USD", ContractCommitmentCost=150000,
       ContractCommitmentFulfillmentInterval="Annual", ContractCommitmentModel="Discontinuous",
       ContractCommitmentBenefitCategory="Discount", ContractCommitmentOfferCategory="Negotiated",
       ContractCommitmentPaymentModel="No Upfront", ContractCommitmentPaymentInterval="Monthly",
       ContractCommitmentDiscountPercentage=0.10, start=dt.date(2026, 1, 1), end=dt.date(2027, 1, 1),
       ContractCommitmentDescription="Annual Claude API committed spend, 10% off list",
       ContractCommitmentApplicability={"ServiceName": ["Claude API"]})
add_cc(ContractCommitmentId="cc-aws-edp", ContractId="aws-edp-2025", ServiceProviderName="AWS",
       InvoiceIssuerName="AWS", ContractCommitmentType="Enterprise Discount Program", ContractCommitmentCategory="Spend",
       ContractCommitmentQuantity=1000000, ContractCommitmentUnit="USD", ContractCommitmentCost=1000000,
       ContractCommitmentFulfillmentInterval="Annual", ContractCommitmentModel="Discontinuous",
       ContractCommitmentBenefitCategory="Discount", ContractCommitmentOfferCategory="Negotiated",
       ContractCommitmentPaymentModel="No Upfront", ContractCommitmentPaymentInterval="Monthly",
       ContractCommitmentDiscountPercentage=0.08, start=dt.date(2025, 1, 1), end=dt.date(2028, 1, 1),
       ContractCommitmentDescription="3-year EDP; Bedrock Anthropic models eligible",
       ContractCommitmentApplicability={"ServiceProviderName": ["AWS"]})
add_cc(ContractCommitmentId="cc-gh-copilot-seats", ContractId="gh-ent-2026", ServiceProviderName="GitHub",
       InvoiceIssuerName="GitHub", ContractCommitmentType="Seat Subscription", ContractCommitmentCategory="Usage",
       ContractCommitmentQuantity=45, ContractCommitmentUnit="Seats", ContractCommitmentCost=10260,
       ContractCommitmentFulfillmentInterval="Monthly", ContractCommitmentModel="Continuous",
       ContractCommitmentBenefitCategory="Entitlement", ContractCommitmentOfferCategory="Public",
       ContractCommitmentPaymentModel="No Upfront", ContractCommitmentPaymentInterval="Monthly",
       ContractCommitmentDiscountPercentage=0.0, start=dt.date(2026, 7, 1), end=dt.date(2027, 7, 1),
       ContractCommitmentDescription="GitHub Copilot Business, 45 seats, includes 300 AI credits per seat per month",
       ContractCommitmentApplicability={"ServiceName": ["GitHub Copilot"]})
add_cc(ContractCommitmentId="cc-claude-ent-seats", ContractId="ant-ent-2026", ServiceProviderName="Anthropic",
       InvoiceIssuerName="Anthropic", ContractCommitmentType="Seat Subscription", ContractCommitmentCategory="Usage",
       ContractCommitmentQuantity=40, ContractCommitmentUnit="Seats", ContractCommitmentCost=28800,
       ContractCommitmentFulfillmentInterval="Monthly", ContractCommitmentModel="Continuous",
       ContractCommitmentBenefitCategory="Entitlement", ContractCommitmentOfferCategory="Negotiated",
       ContractCommitmentPaymentModel="No Upfront", ContractCommitmentPaymentInterval="Monthly",
       ContractCommitmentDiscountPercentage=0.0, start=dt.date(2026, 7, 1), end=dt.date(2027, 7, 1),
       ContractCommitmentDescription="Claude Enterprise, 40 seats, annual term billed monthly",
       ContractCommitmentApplicability={"ServiceName": ["Claude Enterprise"]})

ROWS, ACT = [], []

def row(d, issuer, service, product, charge_cat="Usage", acct=None, sub=None, sub_name=None, publisher=None,
        host=None, region=None, consumed=None, cunit=None, pqty=None, punit=None, list_up=None, contr_up=None,
        list_cost=0.0, contr_cost=0.0, eff_cost=0.0, billed=0.0, pricing_cat="Standard", freq="Usage-Based",
        tags=None, cc_id=None, desc=None, sku=None, period_end=None, **x):
    ba_id, ba_name, provider = ISSUERS[issuer]
    if acct: ba_id, ba_name = acct
    pe = period_end or (d + dt.timedelta(1))
    r = dict(BillingAccountId=ba_id, BillingAccountName=ba_name, BillingAccountType="Billing Account",
             BillingCurrency="USD", PricingCurrency="USD",
             BillingPeriodStart=ts(month_start(d)), BillingPeriodEnd=ts(next_month(d)),
             ChargePeriodStart=ts(d), ChargePeriodEnd=ts(pe), ChargeCategory=charge_cat,
             ChargeFrequency=freq, ChargeDescription=desc or f"{service} {product} usage",
             ServiceProviderName=provider, HostProviderName=host or provider, InvoiceIssuerName=issuer,
             ServiceName=service, ServiceCategory="AI and Machine Learning", ServiceSubcategory="Generative AI",
             SubAccountId=sub, SubAccountName=sub_name, SubAccountType=None,
             RegionId=region, RegionName=region, SkuId=sku, SkuPriceId=(f"{sku}-std" if sku else None), SkuMeter=cunit,
             ConsumedQuantity=consumed, ConsumedUnit=cunit,
             PricingQuantity=pqty if pqty is not None else (consumed / 1e6 if cunit == "Tokens" else consumed),
             PricingUnit=punit or ("1M Tokens" if cunit == "Tokens" else cunit),
             ListUnitPrice=list_up, ContractedUnitPrice=contr_up,
             PricingCurrencyListUnitPrice=list_up, PricingCurrencyContractedUnitPrice=contr_up,
             ListCost=r2(list_cost), ContractedCost=r2(contr_cost), EffectiveCost=r2(eff_cost),
             BilledCost=r2(billed), PricingCurrencyEffectiveCost=r2(eff_cost), PricingCategory=pricing_cat,
             Tags=json.dumps(tags) if tags else None,
             ContractApplied=json.dumps({"Elements": [{
                 "ContractID": CC[cc_id]["ContractId"], "ContractCommitmentID": cc_id,
                 "ContractCommitmentAppliedCost": r2(eff_cost),
                 "ContractCommitmentAppliedQuantity": r2(x.get("x_CreditQuantity") or eff_cost),
                 "ContractCommitmentAppliedUnit": CC[cc_id]["ContractCommitmentUnit"]}]}) if cc_id else None,
             x_AIProduct=product, x_CostSource="invoiced")
    if publisher: r["PublisherName"] = publisher
    r.update(x)
    ROWS.append(r)

def token_rows(d, issuer, service, product, model, toks, tier="standard", disc=0.0, cc_id=None, tags=None,
               sub=None, sub_name=None, region=None, publisher=None, host=None, acct=None, **x):
    tier_f = 0.5 if tier == "batch" else (1.75 if tier == "priority" else 1.0)
    speed_f = 6.0 if x.get("x_Speed") == "fast" else 1.0
    req = x.pop("x_RequestCount", None)
    for tt, n in toks.items():
        if n <= 0: continue
        x["x_RequestCount"] = req if tt == "input" else None  # count requests once, on the input row
        lp = price(model, tt) * tier_f * speed_f
        cp = lp * (1 - disc)
        lc, cc_ = n / 1e6 * lp, n / 1e6 * cp
        extra = {c: 0 for c in TT_COL.values()}; extra[TT_COL[tt]] = int(n)
        row(d, issuer, service, product, consumed=int(n), cunit="Tokens", list_up=lp, contr_up=cp,
            list_cost=lc, contr_cost=cc_, eff_cost=cc_, billed=cc_, cc_id=cc_id, tags=tags, sub=sub,
            sub_name=sub_name, region=region, publisher=publisher, host=host, acct=acct,
            pricing_cat="Committed" if disc else "Standard",
            sku=f"{model}-{tt}", desc=f"{model} {tt.replace('_',' ')} tokens ({tier})",
            x_ModelName=model, x_ModelVendor=MODELS[model][0], x_TokenType=tt, x_ServiceTier=tier,
            **extra, **x)

def wk(d): return 0.35 if d.weekday() >= 5 else 1.0
def jitter(x, s=0.25): return max(0, x * random.lognormvariate(0, s))
def growth(d): return 1 + 0.004 * (d - START).days  # ~45% growth over the window

# ----------------------------------------------------------------------------- Claude API (Console org)
WORKSPACES = [  # id, name, cost center, app, data_class, keys, model mix, tier, cache profile
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
for d in DAYS:
    for wid, wname, cc, app, dclass, keys, mix, tier, cache in WORKSPACES:
        tags = {"application": app, "cost_center": cc, "environment": "prod" if "sandbox" not in wname else "dev",
                "data_class": dclass} if wid else None
        for key in keys:
            base = WS_SCALE[wname] * wk(d) * growth(d) / len(keys)
            if key == "support-assistant-loadtest":
                base *= 0.05
                if d == dt.date(2026, 10, 14): base *= 400   # runaway retry loop
            for model, share in mix.items():
                inp = jitter(base * share)
                if cache == "good":   toks = {"input": inp * 0.35, "cache_read": inp * 0.6, "cache_write": inp * 0.05, "output": inp * 0.12}
                elif cache == "write_only": toks = {"input": inp * 0.5, "cache_write": inp * 0.48, "cache_read": inp * 0.02, "output": inp * 0.03}
                else: toks = {"input": inp, "output": inp * (0.08 if tier == "batch" else 0.15)}
                geos = ["us"]
                if dclass == "genomic" and d >= dt.date(2026, 9, 1): geos = ["us", "global"]
                for g in geos:
                    gtoks = {k: v / len(geos) for k, v in toks.items()}
                    token_rows(d, "Anthropic", "Claude API", "claude_api", model, gtoks, tier=tier, disc=0.10,
                               cc_id="cc-ant-2026", tags=tags, sub=wid or "default", sub_name=wname,
                               x_InferenceGeo=g, x_Speed="standard", x_ContextWindow="0-200k",
                               x_ApiKeyId=(f"apikey_{key}" if key else None), x_ApiKeyName=key,
                               x_WorkspaceId=wid, x_WorkspaceName=wname,
                               x_RequestCount=int(gtoks["input"] / 6000) + 1, x_SourceSystem="anthropic_cost_report")
    # fast-mode experiment in analytics-sandbox during September
    if dt.date(2026, 9, 8) <= d <= dt.date(2026, 9, 19) and d.weekday() < 5:
        token_rows(d, "Anthropic", "Claude API", "claude_api", "claude-opus-5-5",
                   {"input": jitter(1.2e6), "output": jitter(0.25e6)}, disc=0.10, cc_id="cc-ant-2026",
                   tags={"application": "experiments", "cost_center": "Data & Analytics", "environment": "dev", "data_class": "internal"},
                   sub="wrkspc_rnd", sub_name="analytics-sandbox", x_InferenceGeo="us", x_Speed="fast",
                   x_ContextWindow="0-200k", x_ApiKeyId="apikey_analytics-agent-eval", x_ApiKeyName="analytics-agent-eval",
                   x_WorkspaceId="wrkspc_rnd", x_WorkspaceName="analytics-sandbox", x_RequestCount=150,
                   x_SourceSystem="anthropic_cost_report")

# ----------------------------------------------------------------------------- Claude Code (API-billed workspace)
CC_USERS = ENG[:25]
for d in DAYS:
    ws_tokens = {"claude-opus-5-5": {k: 0 for k in TT_COL}, "claude-sonnet-5-5": {k: 0 for k in TT_COL}}
    for i, u in enumerate(CC_USERS):
        if random.random() > (0.85 if d.weekday() < 5 else 0.15): continue
        heavy = 3.0 if i < 3 else 1.0
        if u == "user007@company.example" and d == dt.date(2026, 10, 9): heavy = 12.0  # runaway agentic session
        sessions = random.randint(1, 6)
        for model, share in (("claude-opus-5-5", 0.6), ("claude-sonnet-5-5", 0.4)):
            inp = jitter(2.2e6 * heavy * share * growth(d) * sessions / 3)
            toks = {"input": inp * 0.15, "cache_read": inp * 0.78, "cache_write": inp * 0.07, "output": inp * 0.03}
            est = sum(n / 1e6 * price(model, tt) for tt, n in toks.items())
            for tt in toks: ws_tokens[model][tt] += toks[tt]
            loc = int(jitter(180 * sessions * share))
            acc = int(jitter(12 * sessions * share)); rej = int(acc * random.uniform(0.03, 0.25))
            ACT.append(dict(ActivityDate=d, x_AIProduct="claude_code", UserPrincipal=u, Surface=random.choice(["vscode", "cli", "jetbrains"]),
                            IsActive=True, Prompts=int(jitter(20 * sessions * share)), Conversations=None,
                            Sessions=max(1, round(sessions * share)), LinesAdded=loc, LinesRemoved=int(loc * 0.4),
                            Commits=int(jitter(1.2 * share * sessions / 2)), PullRequests=1 if random.random() < 0.15 * share else 0,
                            ToolAccepted=acc, ToolRejected=rej, ModelName=model,
                            InputTokens=int(toks["input"]), OutputTokens=int(toks["output"]),
                            CacheReadTokens=int(toks["cache_read"]), CacheWriteTokens=int(toks["cache_write"]),
                            EstimatedCost=round(est, 6), BillingCurrency="USD"))
    for model, toks in ws_tokens.items():
        token_rows(d, "Anthropic", "Claude API", "claude_code", model, toks, disc=0.10, cc_id="cc-ant-2026",
                   tags={"application": "claude-code", "cost_center": "Clinical Operations", "environment": "dev", "data_class": "internal"},
                   sub="wrkspc_claude_code", sub_name="claude-code", x_InferenceGeo="us", x_Speed="standard",
                   x_ContextWindow="0-200k", x_ApiKeyId="apikey_claude-code-org", x_ApiKeyName="claude-code-org",
                   x_WorkspaceId="wrkspc_claude_code", x_WorkspaceName="claude-code", x_SourceSystem="anthropic_cost_report")

# ----------------------------------------------------------------------------- Claude on Bedrock / Vertex / Foundry
for d in DAYS:
    for (issuer, service, product, sub, sub_name, region, disc, ccid, app, cc, scale, model, src) in [
        ("AWS", "Claude Sonnet 5.5 (Amazon Bedrock Edition)", "claude_bedrock", "111122223333", "bedrock-prod", "us-east-1",
         0.08, "cc-aws-edp", "contract-review", "Legal", 25e6, "claude-sonnet-5-5", "aws_focus_export"),
        ("AWS", "Claude Haiku 5.5 (Amazon Bedrock Edition)", "claude_bedrock", "111122223333", "bedrock-prod", "us-east-1",
         0.08, "cc-aws-edp", "ticket-classifier", "Patient Data", 30e6, "claude-haiku-5-5", "aws_focus_export"),
        ("Google Cloud", "Vertex AI", "claude_vertex", "vertex-ai-prod", "vertex-ai-prod", "us-east5",
         0.0, None, "marketing-content", "Research & Discovery", 8e6, "claude-sonnet-5-5", "gcp_focus_export"),
        ("Microsoft", "Claude in Microsoft Foundry", "claude_foundry", "sub-ai-prod", "AI-Prod", "eastus2",
         0.05, "cc-ms-macc", "knowledge-agent", "Corporate IT", 12e6, "claude-sonnet-5-5", "azure_focus_export"),
    ]:
        inp = jitter(scale * wk(d) * growth(d))
        token_rows(d, issuer, service, product, model,
                   {"input": inp * 0.5, "cache_read": inp * 0.45, "cache_write": inp * 0.05, "output": inp * 0.1},
                   disc=disc, cc_id=ccid, tags={"application": app, "cost_center": cc, "environment": "prod", "data_class": "internal"},
                   sub=sub, sub_name=sub_name, region=region, publisher="Anthropic",
                   x_InferenceGeo="us", x_WorkspaceId=sub, x_WorkspaceName=sub_name, x_SourceSystem=src)

# ----------------------------------------------------------------------------- Azure OpenAI
for d in DAYS:
    for dep, model, app, cc, scale in [("aoai-chat-prod", "gpt-5.4", "employee-chat", "Corporate IT", 30e6),
                                        ("aoai-extract-prod", "gpt-5.4-mini", "doc-extraction", "Finance", 80e6)]:
        inp = jitter(scale * wk(d) * growth(d))
        token_rows(d, "Microsoft", "Azure OpenAI", "azure_openai", model,
                   {"input": inp * 0.6, "cache_read": inp * 0.4, "output": inp * 0.1}, disc=0.05, cc_id="cc-ms-macc",
                   tags={"application": app, "cost_center": cc, "environment": "prod", "data_class": "internal"},
                   sub="sub-ai-prod", sub_name="AI-Prod", region="eastus2", publisher="OpenAI",
                   x_InferenceGeo="us", x_WorkspaceId=dep, x_WorkspaceName=dep, x_SourceSystem="azure_focus_export")

# ----------------------------------------------------------------------------- Copilot Studio (Copilot Credits)
AGENTS = [("agt-it-01", "IT-Helpdesk-Agent", "Corporate IT", 150), ("agt-hr-02", "HR-Onboarding-Agent", "HR", 45),
          ("agt-sales-03", "Sales-Proposal-Agent", "Consumer Health", 65), ("agt-fin-04", "Finance-Expense-Agent", "Finance", 50)]
ACTIONS = [("classic_answer", 1, 0.35), ("generative_answer", 2, 0.40), ("agent_action", 5, 0.15), ("graph_grounding", 10, 0.10)]
PACK_PER_MONTH, PACK_RATE, PAYG_RATE = 25000, 2400 / 300000, 0.01
used = {}
for d in DAYS:
    m = month_start(d)
    if d == m:  # monthly pack purchase (prepaid capacity) -> amortized into usage rows
        row(d, "Microsoft", "Microsoft Copilot Studio", "copilot_studio", charge_cat="Purchase", freq="Recurring",
            consumed=PACK_PER_MONTH, cunit="Copilot Credits", list_up=PAYG_RATE, contr_up=PACK_RATE,
            list_cost=PACK_PER_MONTH * PAYG_RATE, contr_cost=200, eff_cost=0, billed=200, pricing_cat="Committed",
            cc_id="cc-ms-copilot-credits", sub="sub-ai-prod", sub_name="AI-Prod", period_end=next_month(d),
            sku="copilot-credit-pack-25k", desc="Copilot Credits capacity pack (25,000 credits)",
            x_CreditQuantity=PACK_PER_MONTH, x_CreditUnit="Copilot Credits", x_TokenType="n/a",
            x_SourceSystem="azure_focus_export")
    for aid, aname, cc, base in AGENTS:
        vol = base * wk(d) * growth(d)
        if aname == "HR-Onboarding-Agent" and d >= dt.date(2026, 10, 18): vol *= 9  # new-hire wave + loop
        for act, credits, share in ACTIONS:
            n = int(jitter(vol * share)); cr = n * credits
            if cr == 0: continue
            remaining = max(0, PACK_PER_MONTH - used.get(m, 0))
            from_pack = min(cr, remaining); over = cr - from_pack
            used[m] = used.get(m, 0) + cr
            for qty, committed in ((from_pack, True), (over, False)):
                if qty <= 0: continue
                row(d, "Microsoft", "Microsoft Copilot Studio", "copilot_studio", consumed=qty, cunit="Copilot Credits",
                    list_up=PAYG_RATE, contr_up=PACK_RATE if committed else PAYG_RATE,
                    list_cost=qty * PAYG_RATE, contr_cost=qty * (PACK_RATE if committed else PAYG_RATE),
                    eff_cost=qty * (PACK_RATE if committed else PAYG_RATE), billed=0 if committed else qty * PAYG_RATE,
                    pricing_cat="Committed" if committed else "Standard",
                    cc_id="cc-ms-copilot-credits" if committed else "cc-ms-macc",
                    tags={"application": aname, "cost_center": cc, "environment": "prod", "data_class": "internal"},
                    sub="sub-ai-prod", sub_name="AI-Prod", sku=f"copilot-credit-{act}",
                    desc=f"Copilot Studio {act.replace('_',' ')} ({'capacity pack' if committed else 'pay-as-you-go'})",
                    x_AgentId=aid, x_AgentName=aname, x_ActionType=act, x_CreditQuantity=qty,
                    x_CreditUnit="Copilot Credits", x_RequestCount=n if committed or not from_pack else int(over / credits),
                    x_TokenType="n/a", x_WorkspaceId="env-pharma-prod", x_WorkspaceName="Default environment",
                    x_SourceSystem="azure_focus_export")

# ----------------------------------------------------------------------------- Seats: M365 Copilot, GitHub Copilot, Claude Enterprise
SEATS = []
m365_users = [u for u, _ in USERS[:100]]
gh_users = ENG[:45]
ce_users = [u for u, dpt in USERS if dpt not in ("Clinical Operations",)][:40]
SEAT_DEF = [("m365_copilot", "Microsoft", "Microsoft 365 Copilot", m365_users, 30.0, "cc-m365-copilot-seats", "Microsoft 365 Copilot"),
            ("github_copilot", "GitHub", "GitHub Copilot", gh_users, 19.0, "cc-gh-copilot-seats", "Copilot Business"),
            ("claude_enterprise", "Anthropic", "Claude Enterprise", ce_users, 60.0, "cc-claude-ent-seats", "Claude Enterprise Seat")]
INACTIVE = {"m365_copilot": set(m365_users[-22:]), "github_copilot": set(gh_users[-6:]), "claude_enterprise": set(ce_users[-7:])}
for prod, issuer, service, users, seat_cost, ccid, sku in SEAT_DEF:
    for m in MONTHS:
        acct = ANT_ENT_ACCT if prod == "claude_enterprise" else None
        sub, subn = {"m365_copilot": ("m365-tenant", "tenant.onmicrosoft.com"), "github_copilot": ("pharma-eng", "pharma-eng"),
                     "claude_enterprise": ("ant-ent-4005", "Claude Enterprise")}[prod]
        row(m, issuer, service, prod, charge_cat="Purchase", freq="Recurring", acct=acct, sub=sub, sub_name=subn,
            consumed=len(users), cunit="Seats", punit="Seats", list_up=seat_cost, contr_up=seat_cost,
            list_cost=len(users) * seat_cost, contr_cost=len(users) * seat_cost, eff_cost=len(users) * seat_cost,
            billed=len(users) * seat_cost, pricing_cat="Committed", cc_id=ccid, period_end=next_month(m),
            sku=sku, desc=f"{service} seats", tags={"cost_center": "Corporate IT", "application": service},
            x_TokenType="n/a", x_SourceSystem={"m365_copilot": "azure_focus_export", "github_copilot": "github_ai_usage_report",
                                               "claude_enterprise": "anthropic_enterprise_analytics"}[prod])
    for u in users:
        SEATS.append(dict(SnapshotDate=END, x_AIProduct=prod, ServiceProviderName=issuer, UserPrincipal=u,
                          Department=DEPT_OF[u], CostCenter=DEPT_OF[u], LicenseSku=sku, AssignedDate=dt.date(2026, 7, 1),
                          MonthlySeatCost=seat_cost, BillingCurrency="USD"))

# M365 Copilot activity (Graph v2 metrics)
for d in DAYS:
    for i, u in enumerate(m365_users):
        if u in INACTIVE["m365_copilot"]: continue
        p_active = 0.75 if i % 4 else 0.25  # every 4th user is a light user
        if d.weekday() >= 5 or random.random() > p_active: continue
        ACT.append(dict(ActivityDate=d, x_AIProduct="m365_copilot", UserPrincipal=u,
                        Surface=random.choice(["teams", "outlook", "word", "excel", "powerpoint", "chat"]),
                        IsActive=True, Prompts=int(jitter(9)), Conversations=None))

# GitHub Copilot: AI usage report rows (per user/day/model, token detail, gross/discount/net)
GH_CREDIT, GH_INCLUDED = 0.01, 300
gh_used = {}
for d in DAYS:
    m = month_start(d)
    for i, u in enumerate(gh_users):
        if u in INACTIVE["github_copilot"] or d.weekday() >= 5 or random.random() > 0.8: continue
        models = [("claude-sonnet-5-5", 0.6), ("gpt-5.4-mini", 0.4)]
        if i in (3, 11) and d >= dt.date(2026, 8, 15): models.append(("gpt-5.4-pro", 0.3))  # non-approved model
        for model, share in models:
            inp = jitter(0.9e6 * share * (2.5 if i < 5 else 1))
            toks = {"input": inp * 0.3, "cache_read": inp * 0.6, "cache_write": inp * 0.1 if model.startswith("claude") else 0, "output": inp * 0.04}
            gross = sum(n / 1e6 * price(model, tt) for tt, n in toks.items())
            credits = gross / GH_CREDIT
            allow = max(0, GH_INCLUDED - gh_used.get((m, u), 0))
            covered = min(allow, credits); gh_used[(m, u)] = gh_used.get((m, u), 0) + credits
            disc = covered * GH_CREDIT; net = gross - disc
            row(d, "GitHub", "GitHub Copilot", "github_copilot", sub="pharma-eng", sub_name="pharma-eng",
                consumed=round(credits, 4), cunit="GitHub AI Credits", list_up=GH_CREDIT, contr_up=GH_CREDIT,
                list_cost=gross, contr_cost=gross, eff_cost=net, billed=net,
                pricing_cat="Committed" if net == 0 else "Standard", cc_id="cc-gh-copilot-seats" if disc else None,
                tags={"cost_center": DEPT_OF[u], "application": "github-copilot"}, sku="copilot_ai_credit",
                desc=f"Copilot AI credits {model}",
                x_ModelName=model, x_ModelVendor=MODELS[model][0], x_TokenType="mixed",
                x_InputTokens=int(toks["input"]), x_OutputTokens=int(toks["output"]),
                x_CacheReadTokens=int(toks["cache_read"]), x_CacheWriteTokens=int(toks["cache_write"]),
                x_UserPrincipal=u, x_CreditQuantity=round(credits, 4), x_CreditUnit="GitHub AI Credits",
                x_SourceSystem="github_ai_usage_report")
            ACT.append(dict(ActivityDate=d, x_AIProduct="github_copilot", UserPrincipal=u, Surface="vscode", IsActive=True,
                            Prompts=int(jitter(25 * share)), LinesAdded=int(jitter(120 * share)), ToolAccepted=int(jitter(30 * share)),
                            ToolRejected=int(jitter(8 * share)), ModelName=model, InputTokens=int(toks["input"]),
                            OutputTokens=int(toks["output"]), CacheReadTokens=int(toks["cache_read"]),
                            CacheWriteTokens=int(toks["cache_write"]), EstimatedCost=round(gross, 6), BillingCurrency="USD"))

# Claude Enterprise activity + usage-credit overage for heavy users
for d in DAYS:
    for i, u in enumerate(ce_users):
        if u in INACTIVE["claude_enterprise"] or d.weekday() >= 5 or random.random() > 0.7: continue
        ACT.append(dict(ActivityDate=d, x_AIProduct="claude_enterprise", UserPrincipal=u,
                        Surface=random.choice(["chat", "chat", "cowork", "projects"]), IsActive=True,
                        Prompts=int(jitter(18)), Conversations=int(jitter(4))))
        if i < 5:
            inp = jitter(1.5e6)
            token_rows(d, "Anthropic", "Claude Enterprise", "claude_enterprise", "claude-sonnet-5-5",
                       {"input": inp * 0.4, "cache_read": inp * 0.55, "cache_write": inp * 0.05, "output": inp * 0.06},
                       acct=ANT_ENT_ACCT, sub="ant-ent-4005", sub_name="Claude Enterprise",
                       tags={"cost_center": DEPT_OF[u], "application": "claude-enterprise"},
                       x_UserPrincipal=u, x_InferenceGeo="us", x_SourceSystem="anthropic_enterprise_analytics")

# ----------------------------------------------------------------------------- cloud (AWS, Azure, GCP, Snowflake)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cloud_rows
cloud_rows.generate(dict(ROWS=ROWS, add_cc=add_cc, DAYS=DAYS, ts=ts, month_start=month_start, next_month=next_month))

# ----------------------------------------------------------------------------- corrections, tax
row(dt.date(2026, 8, 20), "Anthropic", "Claude API", "claude_api", charge_cat="Credit", freq="One-Time",
    list_cost=0, contr_cost=0, eff_cost=-150, billed=-150, sub="wrkspc_support", sub_name="support-assistant",
    desc="Service credit: July 22 elevated error rates", ChargeClass="Correction", x_SourceSystem="anthropic_cost_report",
    BillingPeriodStart=ts(dt.date(2026, 8, 1)), BillingPeriodEnd=ts(dt.date(2026, 9, 1)),
    tags={"application": "patient-support-assistant", "cost_center": "Patient Data"})

# ----------------------------------------------------------------------------- write CostAndUsage, then derive invoices
con = duckdb.connect(DB)
con.execute(open(SCHEMA).read())
import pandas as pd
cols = [r[0] for r in con.execute("select column_name from information_schema.columns where table_name='CostAndUsage' order by ordinal_position").fetchall()]
df = pd.DataFrame(ROWS).reindex(columns=cols)
con.register("df", df); con.execute("INSERT INTO CostAndUsage SELECT * FROM df"); con.unregister("df")

# Tax rows per issuer/account/month (6% on Anthropic & GitHub usage+purchase; others 0)
tax = con.execute("""
  SELECT InvoiceIssuerName, BillingAccountId, BillingAccountName, BillingPeriodStart, BillingPeriodEnd, SUM(BilledCost)
  FROM CostAndUsage WHERE InvoiceIssuerName IN ('Anthropic','GitHub') GROUP BY ALL""").fetchall()
TAXROWS = []
for iss, ba, ban, bps, bpe, amt in tax:
    TAXROWS.append(dict(BillingAccountId=ba, BillingAccountName=ban, BillingCurrency="USD", BillingPeriodStart=bps,
                        BillingPeriodEnd=bpe, ChargePeriodStart=bps, ChargePeriodEnd=bpe, ChargeCategory="Tax",
                        ChargeFrequency="Recurring", ChargeDescription="Sales tax 6%", ServiceProviderName=iss,
                        HostProviderName=iss, InvoiceIssuerName=iss, ServiceName="Tax", ServiceCategory="Other",
                        ListCost=0, ContractedCost=0, EffectiveCost=round(float(amt) * 0.06, 2),
                        BilledCost=round(float(amt) * 0.06, 2), PricingQuantity=None, PricingUnit=None,
                        x_SourceSystem="anthropic_cost_report" if iss == "Anthropic" else "github_ai_usage_report",
                        x_CostSource="invoiced"))
tdf = pd.DataFrame(TAXROWS).reindex(columns=cols)
con.register("tdf", tdf); con.execute("INSERT INTO CostAndUsage SELECT * FROM tdf"); con.unregister("tdf")

# Assign InvoiceId / InvoiceDetailId (grain: issuer, billing account, billing period, charge category)
con.execute("""
UPDATE CostAndUsage SET
  InvoiceId = 'INV-' || upper(replace(InvoiceIssuerName,' ','')) || '-' || BillingAccountId || '-' || strftime(BillingPeriodStart, '%Y%m'),
  InvoiceDetailId = 'IND-' || upper(replace(InvoiceIssuerName,' ','')) || '-' || BillingAccountId || '-' || strftime(BillingPeriodStart, '%Y%m') || '-' || ChargeCategory
""")
con.execute("""
INSERT INTO InvoiceDetail (BilledCost, BillingAccountId, BillingCurrency, BillingPeriodEnd, BillingPeriodStart, ChargeCategory,
  InvoiceDetailCreated, InvoiceDetailDescription, InvoiceDetailGrain, InvoiceDetailId, InvoiceDetailLastUpdated, InvoiceId,
  InvoiceIssueDate, InvoiceIssueStatus, InvoiceIssuerName, PaymentCurrency, PaymentCurrencyBilledCost, PaymentDueDate,
  PaymentTerms, PurchaseOrderNumber, ReferenceInvoiceId)
SELECT ROUND(SUM(BilledCost), 2), BillingAccountId, BillingCurrency, BillingPeriodEnd, BillingPeriodStart, ChargeCategory,
  BillingPeriodEnd, InvoiceIssuerName || ' ' || ChargeCategory || ' charges', '{"ChargeCategory":"' || ChargeCategory || '"}',
  InvoiceDetailId, BillingPeriodEnd + INTERVAL 3 DAY, InvoiceId,
  CASE WHEN BillingPeriodEnd <= TIMESTAMP '2026-10-01' THEN BillingPeriodEnd + INTERVAL 2 DAY ELSE NULL END,
  CASE WHEN BillingPeriodEnd <= TIMESTAMP '2026-10-01' THEN 'Issued' ELSE 'Open' END,
  InvoiceIssuerName, 'USD', ROUND(SUM(BilledCost * CASE WHEN BillingCurrency = 'EUR' THEN 1.085 ELSE 1 END), 2),
  CASE WHEN BillingPeriodEnd <= TIMESTAMP '2026-10-01' THEN BillingPeriodEnd + INTERVAL 32 DAY ELSE NULL END,
  'Net 30', 'PO-CLOUD-2026-' || upper(substr(InvoiceIssuerName, 1, 3)), NULL
FROM CostAndUsage GROUP BY ALL
""")
# Seeded discrepancy: Anthropic September usage invoice line is $412.80 higher than charge detail
con.execute("""UPDATE InvoiceDetail SET BilledCost = BilledCost + 412.80, PaymentCurrencyBilledCost = PaymentCurrencyBilledCost + 412.80
               WHERE InvoiceIssuerName='Anthropic' AND BillingAccountId='ant-org-4004'
                 AND BillingPeriodStart = TIMESTAMP '2026-09-01' AND ChargeCategory='Usage'""")
# Seeded tax variance: GitHub August tax line billed at 7% instead of 6%
con.execute("""UPDATE InvoiceDetail SET BilledCost = ROUND(BilledCost * 7 / 6, 2), PaymentCurrencyBilledCost = ROUND(PaymentCurrencyBilledCost * 7 / 6, 2)
               WHERE InvoiceIssuerName='GitHub' AND BillingPeriodStart = TIMESTAMP '2026-08-01' AND ChargeCategory='Tax'""")

# Payment-currency lineage: EUR invoice lines settle in USD; each settlement line groups the month's EUR lines
con.execute("""UPDATE InvoiceDetail SET PaymentCurrencyInvoiceDetailId = 'PAY-' || InvoiceId
               WHERE BillingCurrency = 'EUR'""")
con.execute("""UPDATE InvoiceDetail SET PaymentCurrencyInvoiceDetailId = 'PAY-' || InvoiceDetailId
               WHERE PaymentCurrencyInvoiceDetailId IS NULL""")

# BillingPeriod
con.execute("""
INSERT INTO BillingPeriod (BillingPeriodCreated, BillingPeriodEnd, BillingPeriodLastUpdated, BillingPeriodStart, BillingPeriodStatus, InvoiceIssuerName)
SELECT DISTINCT BillingPeriodStart, BillingPeriodEnd, LEAST(BillingPeriodEnd + INTERVAL 3 DAY, TIMESTAMP '2026-10-23'),
       BillingPeriodStart, CASE WHEN BillingPeriodEnd <= TIMESTAMP '2026-10-01' THEN 'Closed' ELSE 'Open' END, InvoiceIssuerName
FROM CostAndUsage WHERE ServiceName <> 'Tax' OR TRUE
""")

# ContractCommitment
for c in CC.values():
    con.execute("""INSERT INTO ContractCommitment (BillingCurrency, ContractCommitmentApplicability, ContractCommitmentBenefitCategory,
      ContractCommitmentCategory, ContractCommitmentCost, ContractCommitmentCreated, ContractCommitmentDescription,
      ContractCommitmentDiscountPercentage, ContractCommitmentDurationType, ContractCommitmentFulfillmentInterval,
      ContractCommitmentId, ContractCommitmentLastUpdated, ContractCommitmentLifecycleStatus, ContractCommitmentModel,
      ContractCommitmentOfferCategory, ContractCommitmentPaymentInterval, ContractCommitmentPaymentModel,
      ContractCommitmentPeriodEnd, ContractCommitmentPeriodStart, ContractCommitmentQuantity, ContractCommitmentType,
      ContractCommitmentUnit, ContractId, ContractPeriodEnd, ContractPeriodStart, InvoiceIssuerName, PricingCurrency,
      PricingCurrencyContractCommitmentCost, ServiceProviderName)
      VALUES ('USD', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'USD', ?, ?)""",
      [json.dumps(c["ContractCommitmentApplicability"]), c["ContractCommitmentBenefitCategory"], c["ContractCommitmentCategory"],
       c["ContractCommitmentCost"], ts(c["start"]), c["ContractCommitmentDescription"], c["ContractCommitmentDiscountPercentage"],
       f"{round((c['end'] - c['start']).days / 365)} Years", c["ContractCommitmentFulfillmentInterval"], c["ContractCommitmentId"],
       ts(END), c["ContractCommitmentModel"], c["ContractCommitmentOfferCategory"], c["ContractCommitmentPaymentInterval"],
       c["ContractCommitmentPaymentModel"], ts(c["end"]), ts(c["start"]), c["ContractCommitmentQuantity"],
       c["ContractCommitmentType"], c["ContractCommitmentUnit"], c["ContractId"], ts(c["end"]), ts(c["start"]),
       c["InvoiceIssuerName"], c["ContractCommitmentCost"], c["ServiceProviderName"]])

# Supporting tables
DIRECTORY = [dict(UserPrincipal=u, DisplayName=u.split("@")[0].replace("user", "User "), Department=dpt, CostCenter=dpt,
                  IsActiveEmployee=True) for u, dpt in USERS]
for name, data in (("ai_seat_assignment", SEATS), ("ai_user_activity", ACT), ("ai_user_directory", DIRECTORY)):
    tcols = [r[0] for r in con.execute(f"select column_name from information_schema.columns where table_name='{name}' order by ordinal_position").fetchall()]
    t = pd.DataFrame(data).reindex(columns=tcols)
    con.register("t", t); con.execute(f"INSERT INTO {name} SELECT * FROM t"); con.unregister("t")
for mname, (vendor, tier, ok, i_, o_, cr, cw) in MODELS.items():
    con.execute("INSERT INTO ai_model_catalog VALUES (?,?,?,?,?,?,?,?, 'USD')", [mname, vendor, tier, ok, i_, o_, cr, cw])
BUDGET = {"Clinical Operations": 9000, "Patient Data": 9000, "Consumer Health": 3000, "Finance": 3500, "Data & Analytics": 3500,
          "Research & Discovery": 1500, "Corporate IT": 9000, "HR": 600, "Legal": 3000}
for m in MONTHS:
    for cc, b in BUDGET.items():
        con.execute("INSERT INTO ai_budget VALUES (?,?,?, 'USD')", [ts(m), cc, b])

# Total technology budgets by cost center (cloud + AI): 108% of September actuals; Data Science and
# Customer Support are budgeted tighter so the open month shows over-budget units.
con.execute("""CREATE TABLE IF NOT EXISTS cost_budget (BudgetPeriodStart TIMESTAMP, CostCenter VARCHAR,
               BudgetAmount DECIMAL(38,10), BillingCurrency VARCHAR)""")
con.execute("""
INSERT INTO cost_budget
WITH base AS (
  SELECT COALESCE(json_extract_string(AllocatedTags, '$.cost_center'), json_extract_string(Tags, '$.cost_center'),
                  CASE WHEN ChargeCategory = 'Tax' THEN 'Shared Services' END, 'Corporate IT') AS CostCenter,
         SUM(EffectiveCost) AS Sep
  FROM CostAndUsage WHERE BillingPeriodStart = TIMESTAMP '2026-09-01' GROUP BY 1
)
SELECT m.BudgetPeriodStart, b.CostCenter,
       ROUND(b.Sep * CASE b.CostCenter WHEN 'Data & Analytics' THEN 0.97 WHEN 'Patient Data' THEN 1.0 ELSE 1.12 END, -2),
       'USD'
FROM base b CROSS JOIN (SELECT DISTINCT BillingPeriodStart AS BudgetPeriodStart FROM CostAndUsage
                        WHERE BillingPeriodStart >= TIMESTAMP '2026-07-01') m
""")

# CSV export of every table
os.makedirs(CSV, exist_ok=True)
for (t,) in con.execute("select table_name from information_schema.tables order by 1").fetchall():
    con.execute(f"COPY {t} TO '{os.path.join(CSV, t + '.csv')}' (HEADER)")
    print(t, con.execute(f"select count(*) from {t}").fetchone()[0])
con.close()
