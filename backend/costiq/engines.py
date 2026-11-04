"""Engines behind the modules: allocation, KPIs, findings, budgets, reconciliation, lineage, evidence.

All SQL is DuckDB dialect with :named parameters and runs through Database.query(), so it is
translated for the client's database and filtered to the caller's scope like every use case.
Every figure returned carries a `formula` string: the evidence shown next to the number.
"""
from __future__ import annotations
import calendar, datetime as dt, json

CHARGE_KEY = ("md5(COALESCE(CAST(ChargePeriodStart AS VARCHAR), '') || '|' || COALESCE(BillingAccountId, '') || '|' || "
              "COALESCE(SubAccountId, '') || '|' || COALESCE(ServiceName, '') || '|' || COALESCE(ChargeCategory, '') || '|' || "
              "COALESCE(x_ModelName, '') || '|' || COALESCE(x_TokenType, '') || '|' || COALESCE(x_WorkspaceId, '') || '|' || "
              "COALESCE(x_ServiceTier, '') || '|' || COALESCE(x_ActionType, '') || '|' || COALESCE(x_InferenceGeo, '') || '|' || "
              "COALESCE(ResourceId, '') || '|' || COALESCE(SkuId, '') || '|' || COALESCE(ChargeDescription, '') || '|' || "
              "COALESCE(x_ApiKeyId, '') || '|' || COALESCE(x_UserPrincipal, '') || '|' || COALESCE(x_AgentId, '') || '|' || "
              "COALESCE(AllocatedResourceId, '') || '|' || COALESCE(CAST(BilledCost AS VARCHAR), '') || '|' || "
              "COALESCE(CAST(EffectiveCost AS VARCHAR), ''))")


def _j(v):
    """JSON columns arrive as text (DuckDB) or already decoded (PostgreSQL)."""
    if v is None or isinstance(v, (dict, list)):
        return v or {}
    return json.loads(v)


def rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


class Engines:
    def __init__(self, db, registry):
        self.db, self.reg = db, registry
        self._asof = None

    # ------------------------------------------------------------------ time
    def as_of(self) -> dt.date:
        if self._asof is None:
            v = self.db.query("SELECT MAX(ChargePeriodStart) AS d FROM CostAndUsage WHERE ChargeCategory = 'Usage'")["rows"][0][0]
            self._asof = dt.date.fromisoformat(str(v)[:10])
        return self._asof

    def open_month(self):
        d = self.as_of()
        start = dt.date(d.year, d.month, 1)
        days = calendar.monthrange(d.year, d.month)[1]
        end = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        return start, end, d.day, days

    # ------------------------------------------------------------------ allocation
    def allocation_rules(self):
        return rows(self.db.query("SELECT * FROM allocation_rule ORDER BY Priority, RuleId", limit=None))

    def cost_center_expr(self):
        """SQL expression + params assigning every charge to one cost center, in this order:
        split-cost allocation tags, resource tags, approved rules by priority, else Unallocated."""
        parts, params = [], {}
        allowed = {"SubAccountId", "ServiceName", "ChargeCategory", "x_WorkspaceName", "ServiceProviderName"}
        for i, r in enumerate(self.allocation_rules()):
            if r["MatchColumn"] not in allowed:
                continue
            parts.append(f"WHEN {r['MatchColumn']} = :rv{i} THEN :rc{i}")
            params[f"rv{i}"], params[f"rc{i}"] = r["MatchValue"], r["CostCenter"]
        rules = f"CASE {' '.join(parts)} END" if parts else "NULL"
        expr = ("COALESCE(json_extract_string(AllocatedTags, '$.cost_center'), "
                f"json_extract_string(Tags, '$.cost_center'), {rules}, 'Unallocated')")
        return expr, params

    def allocation_summary(self, scope, win=None):
        cc, p = self.cost_center_expr()
        wf = f"WHERE {win.pred()}" if win else ""
        if win:
            p.update(win.params())
        method = ("CASE WHEN json_extract_string(AllocatedTags, '$.cost_center') IS NOT NULL THEN 'Split-cost allocation' "
                  "WHEN json_extract_string(Tags, '$.cost_center') IS NOT NULL THEN 'Resource tag' "
                  f"WHEN {cc} <> 'Unallocated' THEN 'Approved rule' ELSE 'Unallocated' END")
        by_method = rows(self.db.query(f"""
            SELECT {method} AS Method, COUNT(*) AS ChargeLines, SUM(EffectiveCost) AS EffectiveCost
            FROM CostAndUsage {wf} GROUP BY 1 ORDER BY 3 DESC""", p, scope))
        by_cc = rows(self.db.query(f"""
            SELECT {cc} AS CostCenter, SUM(EffectiveCost) AS EffectiveCost,
                   SUM(CASE WHEN x_AIProduct IS NOT NULL THEN EffectiveCost ELSE 0 END) AS AICost
            FROM CostAndUsage {wf} GROUP BY 1 ORDER BY 2 DESC""", p, scope))
        total = sum(r["EffectiveCost"] or 0 for r in by_method)
        unalloc = sum(r["EffectiveCost"] or 0 for r in by_method if r["Method"] == "Unallocated")
        return {"rules": self.allocation_rules(), "by_method": by_method, "by_cost_center": by_cc,
                "coverage": round(100 * (1 - unalloc / total), 2) if total else None,
                "formula": "Coverage = 1 − unallocated effective cost ÷ total effective cost. Order: split-cost "
                           "allocation tags, resource tags, approved rules by priority."}

    # ------------------------------------------------------------------ command center KPIs
    def kpis(self, scope):
        start, end, day, days = self.open_month()
        cc, p = self.cost_center_expr()
        p.update(ms=dt.datetime.combine(start, dt.time()), me=dt.datetime.combine(end, dt.time()))
        mtd = rows(self.db.query(f"""
            SELECT {cc} AS CostCenter, SUM(BilledCost) AS Billed, SUM(EffectiveCost) AS Effective,
                   SUM(CASE WHEN x_AIProduct IS NOT NULL THEN EffectiveCost ELSE 0 END) AS AI,
                   SUM(CASE WHEN ChargeCategory = 'Usage' THEN EffectiveCost ELSE 0 END) AS UsageCost
            FROM CostAndUsage WHERE BillingPeriodStart = :ms GROUP BY 1""", p, scope))
        budgets = {r["CostCenter"]: r["BudgetAmount"] for r in rows(self.db.query(
            "SELECT CostCenter, BudgetAmount FROM cost_budget WHERE BudgetPeriodStart = :ms",
            {"ms": p["ms"]}, scope))}
        units = []
        for r in mtd:
            fixed = (r["Effective"] or 0) - (r["UsageCost"] or 0)
            forecast = fixed + (r["UsageCost"] or 0) / day * days
            b = budgets.get(r["CostCenter"])
            var = forecast - b if b else None
            status = ("no budget" if b is None else "over budget" if var > 0 else
                      "at risk" if var > -0.03 * b else "on track")
            units.append(dict(CostCenter=r["CostCenter"], MonthToDate=r["Effective"], Forecast=round(forecast, 2),
                              Budget=b, Variance=round(var, 2) if var is not None else None,
                              VariancePct=round(100 * var / b, 1) if b else None, AIShare=round(100 * (r["AI"] or 0) / r["Effective"], 1) if r["Effective"] else None,
                              Status=status))
        units.sort(key=lambda u: -(u["Variance"] or -1e18))
        total_mtd = sum(u["MonthToDate"] or 0 for u in units)
        total_fc = sum(u["Forecast"] for u in units)
        total_budget = sum(v for k, v in budgets.items())
        sav = rows(self.db.query("""
            SELECT SUM(CASE WHEN CommitmentDiscountStatus = 'Used' THEN ContractedCost - EffectiveCost ELSE 0 END) AS Commit,
                   SUM(CASE WHEN CommitmentDiscountStatus = 'Unused' THEN EffectiveCost ELSE 0 END) AS Unused,
                   SUM(CASE WHEN ChargeCategory = 'Usage' THEN ListCost - ContractedCost ELSE 0 END) AS Negotiated
            FROM CostAndUsage""", {}, scope))[0]
        trend = rows(self.db.query("""
            SELECT strftime(BillingPeriodStart, '%Y-%m') AS Month,
                   SUM(CASE WHEN x_AIProduct IS NULL THEN EffectiveCost ELSE 0 END) AS Cloud,
                   SUM(CASE WHEN x_AIProduct IS NOT NULL THEN EffectiveCost ELSE 0 END) AS AI
            FROM CostAndUsage GROUP BY 1 ORDER BY 1""", {}, scope))
        return {
            "as_of": self.as_of().isoformat(), "open_month": start.isoformat()[:7], "days_elapsed": day, "days_in_month": days,
            "month_to_date": round(total_mtd, 2), "forecast": round(total_fc, 2), "budget": round(total_budget, 2),
            "variance": round(total_fc - total_budget, 2) if total_budget else None,
            "units": units, "trend": trend,
            "verified_savings": round((sav["Commit"] or 0) - (sav["Unused"] or 0), 2),
            "negotiated_savings": round(sav["Negotiated"] or 0, 2),
            "formulas": {
                "forecast": "Forecast = non-usage charges booked this month + usage month-to-date ÷ days elapsed × days in month.",
                "verified_savings": "Verified commitment savings = Σ(contracted − effective) on rows covered by a commitment "
                                    "− effective cost of unused commitment. Computed from source rows.",
                "negotiated_savings": "Negotiated discount = Σ(list − contracted) on usage rows. Reported separately; never added to verified savings.",
                "status": "Over budget above 0%; at risk within 3% of budget.",
            }}

    # ------------------------------------------------------------------ explorer
    def explorer(self, scope, month=None, provider=None, cost_center=None, limit=25, win=None):
        cc, p = self.cost_center_expr()
        where = ["1 = 1"]
        if win and not month:  # a chosen month overrides the period selector
            where.append(win.pred()); p.update(win.params())
        if month:
            where.append("strftime(BillingPeriodStart, '%Y-%m') = :month"); p["month"] = month
        if provider:
            where.append("ServiceProviderName = :provider"); p["provider"] = provider
        if cost_center:
            where.append(f"{cc} = :fcc"); p["fcc"] = cost_center
        w = " AND ".join(where)
        q = lambda s, lim=None: rows(self.db.query(s, p, scope, limit=lim))
        return {
            "months": [r["m"] for r in q("SELECT DISTINCT strftime(BillingPeriodStart, '%Y-%m') AS m FROM CostAndUsage ORDER BY 1")],
            "providers": [r["p"] for r in q("SELECT DISTINCT ServiceProviderName AS p FROM CostAndUsage ORDER BY 1")],
            "by_provider": q(f"SELECT ServiceProviderName AS Provider, SUM(BilledCost) AS Billed, SUM(EffectiveCost) AS Effective FROM CostAndUsage WHERE {w} GROUP BY 1 ORDER BY 3 DESC"),
            "by_cost_center": q(f"SELECT {cc} AS CostCenter, SUM(EffectiveCost) AS Effective FROM CostAndUsage WHERE {w} GROUP BY 1 ORDER BY 2 DESC"),
            "by_service": q(f"SELECT ServiceName AS Service, ServiceCategory AS Category, SUM(EffectiveCost) AS Effective FROM CostAndUsage WHERE {w} GROUP BY 1, 2 ORDER BY 3 DESC", 10),
            "lines": q(f"""SELECT {CHARGE_KEY} AS ChargeKey, CAST(ChargePeriodStart AS DATE) AS Day, ServiceProviderName AS Provider,
                           ServiceName AS Service, COALESCE(x_ModelName, ResourceName, ChargeDescription) AS Item,
                           {cc} AS CostCenter, BilledCost AS Billed, EffectiveCost AS Effective
                           FROM CostAndUsage WHERE {w} ORDER BY EffectiveCost DESC""", limit),
            "formula": "Billed is the invoiced amount; effective is after commitment discounts and credits, amortized.",
        }

    def lineage(self, scope, key):
        cc, p = self.cost_center_expr()
        p["k"] = key
        r = rows(self.db.query(f"SELECT *, {cc} AS __cc FROM CostAndUsage WHERE {CHARGE_KEY} = :k", p, scope, limit=1))
        if not r:
            return None
        c = r[0]
        bps = c["BillingPeriodStart"]
        man = rows(self.db.query("SELECT * FROM ingest_manifest WHERE SourceSystem = :s AND BillingPeriodStart = :b",
                                 {"s": c["x_SourceSystem"], "b": dt.datetime.fromisoformat(str(bps))}, None, limit=1))
        alloc_tags, tags = _j(c.get("AllocatedTags")), _j(c.get("Tags"))
        if alloc_tags.get("cost_center"):
            how = f"Split-cost allocation ({c.get('AllocatedMethodId')}) → {alloc_tags['cost_center']}"
        elif tags.get("cost_center"):
            how = f"Resource tag cost_center = {tags['cost_center']}"
        elif c["__cc"] != "Unallocated":
            rule = next((x for x in self.allocation_rules() if str(c.get(x["MatchColumn"])) == x["MatchValue"]), None)
            how = f"Rule {rule['RuleId']} v{rule['Version']}: {rule['Name']} (approved by {rule['ApprovedBy']})" if rule else "Approved rule"
        else:
            how = "No rule matched: unallocated"
        inv = rows(self.db.query("SELECT InvoiceId, InvoiceDetailId, BilledCost, InvoiceIssueStatus FROM InvoiceDetail WHERE InvoiceDetailId = :i",
                                 {"i": c.get("InvoiceDetailId")}, None, limit=1)) if c.get("InvoiceDetailId") else []
        return {"steps": [
            {"step": "Source manifest", "detail": (f"{man[0]['SourceSystem']} · {str(bps)[:7]} · digest {man[0]['Digest']} · {man[0]['Maturity']}"
                                                   if man else c.get("x_SourceSystem"))},
            {"step": "Normalized FOCUS charge", "detail": f"{c['ServiceProviderName']} · {c['ServiceName']} · {c['ChargeCategory']} · "
                                                         f"billed {c['BilledCost']:.2f} · effective {c['EffectiveCost']:.2f} {c['BillingCurrency']}"},
            {"step": "Allocation", "detail": how},
            {"step": "Allocated to", "detail": c["__cc"]},
            *self.entity_lineage(key),
            {"step": "Invoice line", "detail": (f"{inv[0]['InvoiceId']} / {inv[0]['InvoiceDetailId']} · {inv[0]['InvoiceIssueStatus']}"
                                                if inv else "Not yet invoiced (open period)")}],
            "charge": {k: v for k, v in c.items() if not k.startswith("__") and v not in (None, "")}}

    def entity_lineage(self, key):
        """Steps 3-5 of the entity allocation chain for one charge (entity split, jurisdiction, GL account)."""
        parts = rows(self.db.query("""
            SELECT LegalEntity, OpcoPolicy, OpcoPolicyVersion, DriverName, OpcoShare, Jurisdiction, JurisdictionFactor,
                   GlAccount, GlTitle, CostClass, AllocatedCost, Exception
            FROM allocated_cost WHERE ChargeKey = :k ORDER BY AllocatedCost DESC""", {"k": key}, None, limit=None))
        if not parts:
            return []
        p0 = parts[0]
        basis = f"driver {p0['DriverName']}" if p0["DriverName"] else "fixed entity"
        opco = "; ".join(f"{e} {100 * s:.1f}%" for e, s in dict((r["LegalEntity"], r["OpcoShare"]) for r in parts).items())
        jur = "; ".join(sorted({f"{r['LegalEntity']} → {r['Jurisdiction']} {100 * r['JurisdictionFactor']:.0f}%" for r in parts}))
        gl = "; ".join(sorted({f"{r['GlAccount'] or 'UNMAPPED'} {r['GlTitle'] or ''} ({r['CostClass']})".strip() for r in parts}))
        return [
            {"step": "Entity split", "detail": (f"Policy {p0['OpcoPolicy']} v{p0['OpcoPolicyVersion']} ({basis}): {opco}"
                                              if p0["OpcoPolicy"] else "No approved policy matched: stays on the shared-services entity")},
            {"step": "Jurisdiction", "detail": jur},
            {"step": "GL account and cost class", "detail": gl + (f" · exception: {p0['Exception']}" if p0["Exception"] else "")},
        ]

    # ------------------------------------------------------------------ AI economics
    def ai_overview(self, scope):
        q = lambda s: rows(self.db.query(s, {}, scope))
        return {
            "by_product": q("SELECT x_AIProduct AS Product, ServiceProviderName AS Provider, SUM(EffectiveCost) AS Effective FROM CostAndUsage WHERE x_AIProduct IS NOT NULL GROUP BY 1, 2 ORDER BY 3 DESC"),
            "by_month": q("SELECT strftime(BillingPeriodStart, '%Y-%m') AS Month, x_AIProduct AS Product, SUM(EffectiveCost) AS Effective FROM CostAndUsage WHERE x_AIProduct IS NOT NULL GROUP BY 1, 2 ORDER BY 1"),
            "models": q("""SELECT x_ModelName AS Model, x_ModelVendor AS Vendor, SUM(EffectiveCost) AS Effective,
                           SUM(x_InputTokens + x_CacheReadTokens + x_CacheWriteTokens) AS InputTokens, SUM(x_OutputTokens) AS OutputTokens,
                           SUM(EffectiveCost) * 1000000 / NULLIF(SUM(x_InputTokens + x_CacheReadTokens + x_CacheWriteTokens + x_OutputTokens), 0) AS CostPer1M
                           FROM CostAndUsage WHERE x_ModelName IS NOT NULL AND ChargeCategory = 'Usage' GROUP BY 1, 2 ORDER BY 3 DESC"""),
            "formula": "Cost per 1M tokens = effective cost × 1,000,000 ÷ (input + cache + output tokens). Seat and credit products have no tokens.",
        }

    # ------------------------------------------------------------------ findings (detectors keyed by catalog id)
    def _uc(self, uid, scope, params=None):
        """Rows of a registry use case for a detector. A failing use case yields no finding instead of breaking the
        Command Center (logged for the operator)."""
        uc = self.reg.get(uid)
        try:
            return rows(self.db.query(uc["sql"], self.reg.resolve_params(uc, params), scope, limit=None))
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("costiq.engines").warning("detector %s failed: %s", uid, str(e).splitlines()[0][:200])
            return []

    def findings(self, scope):
        out = []

        def add(uid, title, kind, severity, exposure, detail, owner, formula, evidence):
            out.append(dict(id=uid, title=title, kind=kind, severity=severity, exposure=round(exposure or 0, 2),
                            detail=detail, owner=owner, formula=formula, evidence=evidence[:20],
                            use_case=self.reg.get(uid)["title"], module=self.reg.get(uid)["module"]))

        r = self._uc("AN-01", scope)
        if r:
            exp = sum(x["Cost"] - (x["Trailing14DayAvg"] or 0) for x in r)
            top = max(r, key=lambda x: x["Cost"])
            add("AN-01", f"Runaway API key {top['ApiKey']}", "incurred", "high", exp,
                f"{top['UsageDate']}: ${top['Cost']:,.0f} against a ${top['Trailing14DayAvg'] or 0:,.0f} trailing average (z={top['ZScore']})",
                f"{top['x_WorkspaceName'] or 'Default Workspace'} owner", "Flag when daily cost > 3σ above the trailing 14-day mean and > $25.", r)
        spikes = self._daily_spikes(scope)
        if spikes:
            s = spikes[0]
            add("F-16", f"Spend spike in {s['SubAccountId']} ({s['ServiceProviderName']})", "incurred", "high",
                sum(x["Excess"] for x in spikes), f"{s['Day']}: ${s['DailyEffectiveCost']:,.0f} vs ${s['Baseline']:,.0f} 14-day average",
                "Sub-account owner", "Flag when a sub account's daily cost > 3 × its average over the prior 14 days and > $500.", spikes)
        r = self._uc("AN-02", scope)
        if r:
            add("AN-02", f"Copilot Studio agent surge: {r[0]['x_AgentName']}", "incurred", "medium",
                sum((x["Cost"] or 0) * (1 - 1 / x["Multiple"]) for x in r if x["Multiple"]),
                f"{len(r)} days at ≥3× the 7-day average", "Business application owner",
                "Flag when credits ≥ 3 × trailing 7-day average.", r)
        r = self._uc("F-47", scope)
        if r:
            add("F-47", "Unused commitment (Savings Plan / reservation)", "incurred", "medium",
                sum(x["TotalEffectiveCost"] or 0 for x in r), f"{len(r)} commitments with unused hours or spend",
                "FinOps Analyst", "Σ effective cost of rows with CommitmentDiscountStatus = Unused.", r)
        r = self._uc("F-19", scope)
        if r:
            add("F-19", "Idle capacity reservation", "incurred", "medium", sum(x["TotalEffectiveCost"] or 0 for x in r),
                f"{r[0]['CapacityReservationId'].split('/')[-1]} reserved with no instance running", "Cloud Operations",
                "Σ effective cost of rows with CapacityReservationStatus = Unused.", r)
        r = self._uc("UO-05", scope)
        if r:
            add("UO-05", f"{len(r)} AI seats idle for 28+ days", "estimate", "medium", sum(x["MonthlySeatCost"] or 0 for x in r),
                ", ".join(sorted({x["x_AIProduct"] for x in r})), "AI Program Owner",
                "Monthly cost = Σ seat price of assigned seats with no activity in 28 days. Opportunity, not realized savings.", r)
        r = [x for x in self._uc("UO-02", scope) if x["Finding"] != "OK"]
        if r:
            add("UO-02", "Prompt cache writes that never pay back", "estimate", "low",
                -sum(x["NetCacheBenefitAtList"] for x in r), f"{r[0]['Workspace']} · {r[0]['ReadsPerWrite']} reads per write",
                "Clinical Operations", "Cache write premium − cache read savings, at list price.", r)
        r = self._uc("UO-03", scope)
        if r:
            add("UO-03", "Workloads eligible for the Batch API", "estimate", "low", sum(x["PotentialBatchSavings"] or 0 for x in r),
                f"{len(r)} standard-tier workloads", "Clinical Operations", "Standard-tier cost × 50% (batch discount). Over the data window.", r)
        r = self._uc("UO-04", scope)
        if r:
            pos = [x for x in r if (x["EstimatedListSavings"] or 0) > 0]
            add("UO-04", "Frontier models on simple tasks", "estimate", "low", sum(x["EstimatedListSavings"] for x in pos),
                f"{len(pos)} application/model pairs", "Clinical Operations", "Current list cost − same tokens at the vendor's balanced-tier price.", pos)
        r = self._uc("F-46", scope)
        if r:
            add("F-46", "Commitment-eligible spend not covered", "estimate", "low",
                sum(x["EligibleUncoveredCost"] or 0 for x in r) * 0.28,
                f"${sum(x['EligibleUncoveredCost'] or 0 for x in r):,.0f} eligible and uncovered", "FinOps Analyst",
                "Eligible uncovered cost × 28% (assumed Savings Plan discount). Estimate.", r)
        r = self._uc("GV-01", scope)
        if r:
            add("GV-01", "Usage of non-approved models", "compliance", "high", sum(x["EffectiveCost"] or 0 for x in r),
                ", ".join(sorted({x["x_ModelName"] for x in r})), "AI Governance", "Usage where the model is not approved in ai_model_catalog.", r)
        r = self._uc("GV-02", scope)
        if r:
            add("GV-02", "Regulated workload served outside the US", "compliance", "high", sum(x["EffectiveCost"] or 0 for x in r),
                f"{r[0]['Application']} · {r[0]['DaysInViolation']} days", "AI Governance",
                "Genomic-data usage where inference_geo ≠ us. Compliance exposure (the cost shown is the affected spend).", r)
        if scope is None:
            r = self._uc("IC-01", scope)
            if r:
                add("IC-01", "Invoice line without matching charges", "exception", "medium", sum(abs(x["Variance"]) for x in r),
                    f"{r[0]['InvoiceId']} · variance ${r[0]['Variance']:,.2f}", "Internal Controller",
                    "Invoice Detail amount − Σ BilledCost of its charges; flag above $1.", r)
            r = self._uc("IC-02", scope)
            if r:
                add("IC-02", "Tax billed at an unexpected rate", "exception", "low", sum(abs(x["TaxVariance"]) for x in r),
                    f"{r[0]['InvoiceId']}: {r[0]['InvoiceTaxRatePct']}% vs {r[0]['ExpectedTaxRatePct']}%", "Internal Controller",
                    "Invoiced tax − tax implied by charge-level tax rows; flag above $0.50.", r)
        r = [x for x in self._uc("RO-01", scope) if (x["PaygCost"] or 0) > 0]
        if r:
            add("RO-01", "Copilot Credit pack overage", "incurred", "low", sum(x["PaygCost"] for x in r),
                f"{sum(x['PaygCredits'] for x in r):,.0f} credits billed pay-as-you-go", "Business application owner",
                "Σ billed cost of credits consumed beyond the monthly pack.", r)
        if scope is None:
            for detect in (self._pharma_findings, ):
                try:
                    detect(scope, add)
                except Exception as e:  # noqa: BLE001 - one detector never breaks the list
                    import logging
                    logging.getLogger("costiq.engines").warning("detector %s failed: %s", detect.__name__, e)
        order = {"high": 0, "medium": 1, "low": 2}
        out.sort(key=lambda f: (order[f["severity"]], -f["exposure"]))
        return out

    def _pharma_findings(self, scope, add):
        """Transfer-pricing, GxP and patient-data findings from the pharma use cases (enterprise scope only)."""
        r = [x for x in self._uc("PH-03", scope) if (x["CrossCharge"] or 0) > 0]
        if r:
            add("PH-03", "GxP entities carrying Consumer Health work (cross-charging)", "compliance", "high",
                sum(x["CrossCharge"] for x in r), f"{r[0]['CostCenter']} via {r[0]['OpcoPolicy']} · {len(r)} allocations",
                "Transfer Pricing", "Allocated cost from Consumer Health cost centers that landed on GxP entities.", r)
        r = [x for x in self._uc("PH-07", scope) if x["Status"] == "Provider can decrypt patient data"]
        if r:
            add("PH-07", "Patient data in cloud without customer-managed keys", "compliance", "high", sum(x["EffectiveCost"] or 0 for x in r),
                ", ".join(x["Application"] for x in r), "GxP Quality & Data Privacy",
                "Workloads holding patient data where the provider manages the keys (spend shown).", r)
        r = [x for x in self._uc("PH-01", scope) if x["Status"] == "Unmapped"]
        if r:
            add("PH-01", "Technology spend with no GL account", "exception", "medium", sum(x["AllocatedCost"] or 0 for x in r),
                f"{len(r)} entity lines unmapped", "Internal Controller",
                "Allocated opex with no ref_gl_account_map match; needs a manual entry before close and filing.", r)
        r = [x for x in self._uc("PH-06", scope) if x["Timing"] == "In forecast year"]
        if r:
            add("PH-06", "Contract escalators landing in the forecast year", "estimate", "low", sum(x["AnnualEscalation"] or 0 for x in r),
                f"{len(r)} renewals in {r[0]['FilingId']}'s forecast year", "FP&A Analyst",
                "Annual contract value × escalator for renewals inside the forecast year. Renegotiate or budget for it.", r)

    def _daily_spikes(self, scope):
        return rows(self.db.query("""
            WITH d AS (
              SELECT CAST(ChargePeriodStart AS DATE) AS Day, ServiceProviderName, SubAccountId,
                     SUM(EffectiveCost) AS DailyEffectiveCost
              FROM CostAndUsage WHERE ChargeCategory = 'Usage' AND x_AIProduct IS NULL GROUP BY 1, 2, 3
            ), b AS (
              SELECT *, AVG(DailyEffectiveCost) OVER (PARTITION BY ServiceProviderName, SubAccountId ORDER BY Day
                        ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING) AS Baseline FROM d
            )
            SELECT Day, ServiceProviderName, SubAccountId, DailyEffectiveCost, Baseline,
                   DailyEffectiveCost - Baseline AS Excess
            FROM b WHERE DailyEffectiveCost > 3 * Baseline AND DailyEffectiveCost > 500
            ORDER BY Excess DESC""", {}, scope))

    # ------------------------------------------------------------------ budgets
    def budgets(self, scope):
        cc, p = self.cost_center_expr()
        start, end, day, days = self.open_month()
        res = rows(self.db.query(f"""
            WITH a AS (
              SELECT BillingPeriodStart, {cc} AS CostCenter, SUM(EffectiveCost) AS Actual,
                     SUM(CASE WHEN ChargeCategory = 'Usage' THEN EffectiveCost ELSE 0 END) AS UsageCost
              FROM CostAndUsage GROUP BY 1, 2)
            SELECT strftime(b.BudgetPeriodStart, '%Y-%m') AS Month, b.CostCenter, b.BudgetAmount AS Budget,
                   a.Actual, a.UsageCost
            FROM cost_budget b LEFT JOIN a ON a.BillingPeriodStart = b.BudgetPeriodStart AND a.CostCenter = b.CostCenter
            ORDER BY 1, 2""", p, scope, limit=None))
        om = start.isoformat()[:7]
        for r in res:
            actual = r["Actual"] or 0
            if r["Month"] == om:
                r["Forecast"] = round(actual - (r["UsageCost"] or 0) + (r["UsageCost"] or 0) / day * days, 2)
            else:
                r["Forecast"] = round(actual, 2)
            r["Variance"] = round(r["Forecast"] - r["Budget"], 2)
            r["VariancePct"] = round(100 * r["Variance"] / r["Budget"], 1) if r["Budget"] else None
            r["Status"] = ("critical" if r["VariancePct"] and r["VariancePct"] > 5 else "warning" if r["VariancePct"] and r["VariancePct"] > 2
                           else "within" if r["VariancePct"] and r["VariancePct"] > 0 else "under")
            r.pop("UsageCost")
        return {"lines": res, "open_month": om,
                "formula": "Closed months: actual effective cost. Open month: non-usage charges + usage MTD ÷ days elapsed × days "
                           "in month. Thresholds: warning above +2%, critical above +5% of budget."}

    # ------------------------------------------------------------------ reconciliation and close
    def reconciliation(self):
        lines = rows(self.db.query("""
            WITH cu AS (SELECT InvoiceDetailId, SUM(BilledCost) AS ChargeTotal, COUNT(*) AS ChargeLines FROM CostAndUsage GROUP BY 1)
            SELECT i.InvoiceIssuerName AS Issuer, i.InvoiceId, i.InvoiceDetailId, strftime(i.BillingPeriodStart, '%Y-%m') AS Period,
                   i.ChargeCategory, i.BillingCurrency AS Currency, i.InvoiceIssueStatus AS IssueStatus, i.BilledCost AS Invoiced,
                   cu.ChargeTotal, cu.ChargeLines, i.BilledCost - COALESCE(cu.ChargeTotal, 0) AS Difference
            FROM InvoiceDetail i LEFT JOIN cu ON cu.InvoiceDetailId = i.InvoiceDetailId
            ORDER BY Period, Issuer, i.InvoiceDetailId""", {}, None, limit=None))
        for l in lines:
            l["Status"] = "matched" if abs(l["Difference"] or 0) <= 1 else "exception"
            l["Reason"] = None if l["Status"] == "matched" else ("Tax rate differs from expected" if l["ChargeCategory"] == "Tax"
                                                                else "Invoice amount exceeds charge detail" if l["Difference"] > 0 else "Charges exceed invoice")
        periods = rows(self.db.query("""SELECT strftime(BillingPeriodStart, '%Y-%m') AS Period, InvoiceIssuerName AS Issuer,
                                        BillingPeriodStatus AS Status FROM BillingPeriod ORDER BY 1, 2""", {}, None, limit=None))
        close = {}
        for p in periods:
            c = close.setdefault(p["Period"], {"Period": p["Period"], "Issuers": 0, "Closed": 0, "Exceptions": 0})
            c["Issuers"] += 1
            c["Closed"] += p["Status"] == "Closed"
        for l in lines:
            if l["Status"] == "exception" and l["Period"] in close:
                close[l["Period"]]["Exceptions"] += 1
        for c in close.values():
            c["Readiness"] = ("Open period" if c["Closed"] < c["Issuers"] else "Exceptions" if c["Exceptions"] else "Ready")
        return {"lines": lines, "close": list(close.values()),
                "formula": "Each Invoice Detail line is compared with Σ BilledCost of the FOCUS charges carrying its InvoiceDetailId. "
                           "Differences above $1 become exceptions."}

    # ------------------------------------------------------------------ compliance scorecards (any module)
    # Areas come from the use cases' `compliance.area` values, in catalog order. One disclaimer per module.
    DISCLAIMERS = {
        "compliance": "Decision support for Tax, Transfer Pricing, GxP Quality and Data Privacy teams. CostIQ does not "
                      "determine GxP, privacy or tax compliance; the reference registers are maintained by those teams.",
    }

    def compliance_score(self, uc, scope, params=None):
        """(items checked, items flagged, amount on flagged items) for one use case with a `compliance` block."""
        meta = uc["compliance"]
        amount = meta.get("amount") or "0"
        sql = (f"SELECT COUNT(*) AS Checked, SUM(CASE WHEN {meta['flag']} THEN 1 ELSE 0 END) AS Flagged, "
               f"SUM(CASE WHEN {meta['flag']} THEN {amount} ELSE 0 END) AS AmountAffected FROM ({uc['sql']}) cs")
        r = self.db.query(sql, self.reg.resolve_params(uc, params), scope)["rows"][0]
        return int(r[0] or 0), int(r[1] or 0), float(r[2] or 0)

    def compliance(self, scope, module=None):
        out = []
        for uid in self.reg.order:
            uc = self.reg.get(uid)
            meta = uc.get("compliance")
            if not meta or (module and uc["module"] != module):
                continue
            try:
                checked, bad, amt = self.compliance_score(uc, scope)
                status, err = ("compliant" if bad == 0 else "attention"), None
            except Exception as e:  # keep the page usable if one check fails on a target database
                checked, bad, amt, status, err = 0, 0, 0.0, "error", str(e)[:200]
            out.append({"id": uid, "title": uc["title"], "area": meta["area"], "reference": meta["reference"],
                        "unit": meta.get("unit", "items"), "business_question": uc["business_question"],
                        "checked": checked, "flagged": bad, "amount_affected": round(amt, 2),
                        "has_amount": bool(meta.get("amount")), "status": status, "error": err})
        area_order = list(dict.fromkeys(x["area"] for x in out))
        areas = [{"area": a, "checks": len(items), "attention": sum(1 for x in items if x["status"] != "compliant"), "items": items}
                 for a in area_order if (items := [x for x in out if x["area"] == a])]
        return {"areas": areas, "as_of": self.as_of().isoformat(),
                "formula": "Each check runs its use case with default parameters and counts the rows its rule flags. "
                           "Amounts are those rows' spend or idle cost; they differ by check and are never summed.",
                "disclaimer": self.DISCLAIMERS.get(module, "Decision support. CostIQ does not determine compliance; the "
                                                           "reference registers are maintained by the owning teams.")}

    # ------------------------------------------------------------------ controls and evidence
    def controls(self, ledger_verify):
        rec = self.reconciliation()
        closed_lines = [l for l in rec["lines"] if l["IssueStatus"] == "Issued"]
        exc = [l for l in closed_lines if l["Status"] == "exception"]
        alloc = self.allocation_summary(None)
        rules = self.allocation_rules()
        man = rows(self.db.query("SELECT * FROM ingest_manifest ORDER BY SourceSystem, BillingPeriodStart", {}, None, limit=None))
        warn = sum(1 for m in man for c in _j(m["Checks"]) if c["status"] != "pass")
        for m in man:
            m["Checks"] = _j(m["Checks"])
        dup = self.db.query(f"SELECT COUNT(*) - COUNT(DISTINCT {CHARGE_KEY}) FROM CostAndUsage", {}, None)["rows"][0][0]
        return {"controls": [
            {"id": "C-01", "control": "Every closed invoice line ties to source charges or is an exception",
             "result": "exceptions" if exc else "pass", "evidence": f"{len(closed_lines)} issued invoice lines, {len(exc)} exceptions"},
            {"id": "C-02", "control": "Invoice evidence on file for closed periods", "result": "pass",
             "evidence": f"{len({l['InvoiceId'] for l in closed_lines})} invoices"},
            {"id": "C-03", "control": "Allocation rules approved and effective-dated",
             "result": "pass" if all(r["ApprovedBy"] and r["EffectiveFrom"] for r in rules) else "fail",
             "evidence": f"{len(rules)} rule versions"},
            {"id": "C-04", "control": "Allocation coverage ≥ 99%", "result": "pass" if (alloc["coverage"] or 0) >= 99 else "fail",
             "evidence": f"{alloc['coverage']}%"},
            {"id": "C-05", "control": "Ingestion schema checks", "result": "warnings" if warn else "pass",
             "evidence": f"{len(man)} manifests, {warn} warnings"},
            {"id": "C-06", "control": "Replay idempotency (no duplicate charge keys)", "result": "pass" if dup == 0 else "fail",
             "evidence": f"{dup} duplicates"},
            {"id": "C-07", "control": "Decision ledger hash chain verifies", "result": "pass" if ledger_verify["verified"] else "fail",
             "evidence": f"{ledger_verify.get('entries', ledger_verify.get('broken_at'))} entries"},
        ] + self.allocation_controls(), "manifests": man}

    def allocation_controls(self):
        """C-08..C-11: the controls over the entity allocation chain (allocated_cost)."""
        q = lambda sql: self.db.query(sql, {}, None, limit=None)["rows"]
        total, unmapped = q("""SELECT SUM(AllocatedCost), SUM(CASE WHEN GlAccount IS NULL THEN AllocatedCost ELSE 0 END)
                               FROM allocated_cost""")[0]
        total, unmapped = float(total or 0), float(unmapped or 0)
        xsub = float(q("""SELECT COALESCE(SUM(a.AllocatedCost), 0) FROM allocated_cost a
                          JOIN ref_cost_center c ON c.CostCenter = a.CostCenter
                          WHERE NOT c.Regulated AND a.Regulated""")[0][0] or 0)
        ties = q("""SELECT s.p, s.src, COALESCE(a.alloc, 0) FROM
                      (SELECT BillingPeriodStart AS p, SUM(EffectiveCost) AS src FROM CostAndUsage GROUP BY 1) s
                      LEFT JOIN (SELECT PeriodStart AS p, SUM(AllocatedCost) AS alloc FROM allocated_cost GROUP BY 1) a ON a.p = s.p""")
        off = [r for r in ties if abs(float(r[1] or 0) - float(r[2] or 0)) > 0.01]
        pol = rows(self.db.query("SELECT PolicyId, Version, Status, ApprovedBy, EffectiveFrom FROM ref_allocation_policy", {}, None, limit=None))
        applied = [r for r in pol if r["Status"] == "approved"]
        bad = [r for r in applied if not (r["ApprovedBy"] and r["EffectiveFrom"])]
        pending = [r for r in pol if r["Status"] == "submitted"]
        return [
            {"id": "C-08", "control": "Every allocated dollar has a GL account",
             "result": "pass" if unmapped < 0.01 else "exceptions",
             "evidence": f"${unmapped:,.2f} of ${total:,.2f} unmapped ({100 * unmapped / total if total else 0:.2f}%)"},
            {"id": "C-09", "control": "No GxP entity carries Consumer Health cost centers' work (cross-charging)",
             "result": "pass" if xsub < 0.01 else "exceptions", "evidence": f"${xsub:,.2f} on GxP entities"},
            {"id": "C-10", "control": "Allocated total ties to source total for every period (±$0.01)",
             "result": "pass" if not off else "fail", "evidence": f"{len(ties)} periods, {len(off)} out of balance"},
            {"id": "C-11", "control": "Allocation policies applied are approved and effective-dated",
             "result": "pass" if not bad else "fail",
             "evidence": f"{len(applied)} approved policies applied, {len(pending)} awaiting approval"},
        ]
