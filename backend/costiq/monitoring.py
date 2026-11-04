"""Cloud Cost Monitor and AI & LLM Cost Monitor: dashboard payloads.

Every figure is computed from FOCUS rows or a registry use case (named in the payload), so a number on the dashboard
can be traced to the query behind it.
"""
from __future__ import annotations
import datetime as dt
from .engines import Engines
from .window import window


def rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


class Monitor:
    def __init__(self, db, engines: Engines):
        self.db, self.eng = db, engines

    def q(self, sql, params=None, scope=None):
        return rows(self.db.query(sql, params or {}, scope, limit=None))

    def _uc(self, uid, scope, params=None):
        return self.eng._uc(uid, scope, params)

    # ------------------------------------------------------------------ cloud
    def cloud(self, scope, provider=None, cost_center=None, win=None):
        as_of = self.eng.as_of()
        win = win or window('mtd', as_of)
        start, end, day, days = self.eng.open_month()
        cc, ccp = self.eng.cost_center_expr()
        filt, p = ["x_AIProduct IS NULL"], dict(ccp)
        if provider:
            filt.append("ServiceProviderName = :provider"); p["provider"] = provider
        if cost_center:
            filt.append(f"{cc} = :cc"); p["cc"] = cost_center
        w = " AND ".join(filt)
        since = dt.datetime.combine(as_of - dt.timedelta(days=59), dt.time())
        daily = self.q(f"""SELECT CAST(ChargePeriodStart AS DATE) AS Day, ROUND(SUM(EffectiveCost), 2) AS Cost
                           FROM CostAndUsage WHERE {w} AND ChargeCategory = 'Usage' AND ChargePeriodStart >= :since
                           GROUP BY 1 ORDER BY 1""", {**p, "since": since}, scope)
        spikes = {str(s["Day"])[:10]: s for s in self.eng._daily_spikes(scope)}
        for d in daily:
            s = spikes.get(str(d["Day"])[:10])
            d["Anomaly"] = f"{s['SubAccountId']} ({s['ServiceProviderName']}) {s['DailyEffectiveCost']:,.0f} vs {s['Baseline']:,.0f}" if s else None
        month = self.q(f"""SELECT ROUND(SUM(EffectiveCost), 2) AS MTD,
                                  ROUND(SUM(CASE WHEN {cc} = 'Unallocated' THEN EffectiveCost ELSE 0 END), 2) AS Untagged
                           FROM CostAndUsage WHERE {w} AND {win.pred()}""", {**p, **win.params()}, scope)[0]
        wk = lambda a, b: dt.datetime.combine(as_of - dt.timedelta(days=a), dt.time())
        movers = self.q(f"""SELECT ServiceName AS Service, ServiceProviderName AS Provider,
                                   ROUND(SUM(CASE WHEN ChargePeriodStart > :w1 THEN EffectiveCost ELSE 0 END), 2) AS Last7Days,
                                   ROUND(SUM(CASE WHEN ChargePeriodStart <= :w1 AND ChargePeriodStart > :w2 THEN EffectiveCost ELSE 0 END), 2) AS Prior7Days
                            FROM CostAndUsage WHERE {w} AND ChargeCategory = 'Usage' AND ChargePeriodStart > :w2
                            GROUP BY 1, 2""", {**p, "w1": wk(7, 0), "w2": wk(14, 0)}, scope)
        for m in movers:
            m["Change"] = round((m["Last7Days"] or 0) - (m["Prior7Days"] or 0), 2)
        movers.sort(key=lambda m: -abs(m["Change"]))
        by = lambda col, alias: self.q(f"""SELECT {col} AS {alias}, ROUND(SUM(EffectiveCost), 2) AS Effective FROM CostAndUsage
                                          WHERE {w} AND {win.pred()} GROUP BY 1 ORDER BY 2 DESC LIMIT 10""", {**p, **win.params()}, scope)
        cov = self._uc("F-41", scope)
        covered, eligible = sum(r["CoveredCost"] or 0 for r in cov), sum(r["EligibleCost"] or 0 for r in cov)
        unused = self._uc("F-47", scope)
        recs = [r for uid in ("CO-01", "CO-02", "CO-03", "CO-04", "CO-05") for r in
                [dict(x, UseCase=uid) for x in self._uc(uid, scope)]]
        opp = sorted(({"UseCase": r["UseCase"], "Item": r.get("ResourceName"), "CostCenter": r.get("CostCenter"),
                       "Finding": r.get("Finding") or r.get("RecommendedResourceSummary") or "Schedule to business hours",
                       "MonthlySavings": round(float(r.get("EstimatedMonthlySavings") or r.get("MonthlySchedulingSavings") or 0), 2),
                       "TrackerStatus": r.get("TrackerStatus")}
                      for r in recs), key=lambda r: -r["MonthlySavings"])
        kp = self.eng.kpis(scope)
        return {
            "as_of": as_of.isoformat(), "open_month": start.isoformat()[:7], "period": win.as_dict(),
            "kpis": {"period_spend": month["MTD"], "untagged": month["Untagged"],
                     "untagged_pct": round(100 * (month["Untagged"] or 0) / month["MTD"], 2) if month["MTD"] else None,
                     "forecast": kp["forecast"], "budget": kp["budget"], "variance": kp["variance"],
                     "commitment_coverage_pct": round(100 * covered / eligible, 1) if eligible else None,
                     "unused_commitment_cost": round(sum(r["TotalEffectiveCost"] or 0 for r in unused), 2),
                     "open_anomalies": len(spikes), "opportunity_monthly": round(sum(o["MonthlySavings"] for o in opp), 2)},
            "daily": daily, "movers": movers[:8],
            "by_provider": by("ServiceProviderName", "Provider"), "by_service": by("ServiceName", "Service"),
            "by_account": by("COALESCE(SubAccountName, SubAccountId)", "Account"),
            "commitments": {"coverage": cov, "unused": unused},
            "opportunities": opp[:12],
            "providers": [r["p"] for r in self.q("SELECT DISTINCT ServiceProviderName AS p FROM CostAndUsage ORDER BY 1", {}, scope)],
            "formula": "Cloud = FOCUS charges without an AI product. Anomalies: a sub account's daily cost > 3x its prior "
                       "14-day average and > $500. Coverage: F-41. Opportunities: CO-01 to CO-05 estimated monthly savings.",
        }

    # ------------------------------------------------------------------ AI & LLM
    def ai(self, scope, win=None):
        as_of = self.eng.as_of()
        win = win or window('mtd', as_of)
        start, *_ = self.eng.open_month()
        ai_w = "(x_AIProduct IS NOT NULL OR json_extract_string(Tags, '$.ai_component') IS NOT NULL)"
        by_month = self.q(f"""SELECT strftime(BillingPeriodStart, '%Y-%m') AS Month, COALESCE(x_AIProduct, 'AI infrastructure') AS Product,
                                     ROUND(SUM(EffectiveCost), 2) AS Effective
                              FROM CostAndUsage WHERE {ai_w} GROUP BY 1, 2 ORDER BY 1, 3 DESC""", {}, scope)
        tokens = self.q("""SELECT strftime(BillingPeriodStart, '%Y-%m') AS Month,
                                  SUM(COALESCE(x_InputTokens, 0)) AS Input, SUM(COALESCE(x_OutputTokens, 0)) AS Output,
                                  SUM(COALESCE(x_CacheReadTokens, 0)) AS CacheRead, SUM(COALESCE(x_CacheWriteTokens, 0)) AS CacheWrite,
                                  ROUND(SUM(CASE WHEN x_InputTokens IS NOT NULL OR x_OutputTokens IS NOT NULL THEN EffectiveCost ELSE 0 END), 2) AS TokenCost,
                                  ROUND(SUM(CASE WHEN x_ServiceTier = 'batch' THEN EffectiveCost ELSE 0 END), 2) AS BatchCost
                           FROM CostAndUsage WHERE x_AIProduct IS NOT NULL GROUP BY 1 ORDER BY 1""", {}, scope)
        for t in tokens:
            tok = (t["Input"] or 0) + (t["Output"] or 0) + (t["CacheRead"] or 0) + (t["CacheWrite"] or 0)
            t["CostPer1M"] = round(t["TokenCost"] * 1e6 / tok, 4) if tok else None
        cache = self._uc("UO-01", scope)
        unc, hit = sum(r["UncachedInputTokens"] or 0 for r in cache), sum(r["CacheReadTokens"] or 0 for r in cache)
        seats = self._uc("UO-06", scope)
        apps = self._uc("UO-13", scope)
        retries = {r["Application"]: r for r in self._uc("UO-11", scope)}
        context = {r["Application"]: r for r in self._uc("UO-12", scope)}
        routing = {}
        for r in self._uc("UO-14", scope):
            routing.setdefault(r["Application"], r)
        ptu = self._uc("UO-10", scope)
        ptu_apps = {r["Application"]: r for r in ptu}
        for a in apps:
            app = a["Application"]
            r, c, m, pt = retries.get(app, {}), context.get(app, {}), routing.get(app), ptu_apps.get(app)
            a["FailedPct"], a["RetriedPct"] = r.get("FailedPct"), r.get("RetriedPct")
            a["InputGrowthPct"] = c.get("InputGrowthPct")
            levers = []
            if pt and pt["Finding"].startswith("Under"):
                levers.append(f"Provisioned at {pt['AvgUtilizationPct']}%: move to pay-as-you-go")
            if (r.get("RetriedPct") or 0) > 10:
                levers.append(f"Fix retries ({r['RetriedPct']}%)")
            if (c.get("InputGrowthPct") or 0) > 50:
                levers.append("Trim context")
            if m and m["Savings"] > 0 and m["AvgOutputTokens"] < 500:
                levers.append(f"Route to {m['CandidateModel']} (-{m['SavingsPct']}%)")
            a["SuggestedLevers"] = levers
        acc = sum(a["AcceptedOutcomes"] or 0 for a in apps)
        app_cost = sum(a["Cost"] or 0 for a in apps)
        mtd = self.q(f"SELECT ROUND(SUM(EffectiveCost), 2) AS v FROM CostAndUsage WHERE {ai_w} AND {win.pred()}",
                     win.params(), scope)[0]["v"]
        by_product = self.q(f"""SELECT COALESCE(x_AIProduct, 'AI infrastructure') AS Product, ServiceProviderName AS Provider,
                                       ROUND(SUM(EffectiveCost), 2) AS Effective
                                FROM CostAndUsage WHERE {ai_w} AND {win.pred()} GROUP BY 1, 2 ORDER BY 3 DESC""", win.params(), scope)
        rag = self.q(f"""SELECT json_extract_string(Tags, '$.application') AS Application, json_extract_string(Tags, '$.ai_component') AS Component,
                                ROUND(SUM(EffectiveCost), 2) AS Effective
                         FROM CostAndUsage WHERE json_extract_string(Tags, '$.ai_component') IS NOT NULL AND {win.pred()}
                         GROUP BY 1, 2 ORDER BY 1, 3 DESC""", win.params(), scope)
        channel = self.q(f"""SELECT x_ModelVendor AS ModelVendor, ServiceProviderName AS Channel, ROUND(SUM(EffectiveCost), 2) AS Effective
                             FROM CostAndUsage WHERE x_ModelVendor IS NOT NULL AND {win.pred()} GROUP BY 1, 2 ORDER BY 3 DESC""",
                         win.params(), scope)
        tot_tok = tokens[-2] if len(tokens) > 1 else (tokens[-1] if tokens else {})
        return {
            "as_of": as_of.isoformat(), "open_month": start.isoformat()[:7], "period": win.as_dict(), "by_product": by_product,
            "kpis": {"period_spend": mtd,
                     "cost_per_1m_tokens": tot_tok.get("CostPer1M"), "cost_per_1m_month": tot_tok.get("Month"),
                     "cache_hit_pct": round(100 * hit / (unc + hit), 1) if unc + hit else None,
                     "batch_share_pct": round(100 * sum(t["BatchCost"] for t in tokens) / sum(t["TokenCost"] for t in tokens), 1)
                     if tokens and sum(t["TokenCost"] for t in tokens) else None,
                     "cost_per_accepted_outcome": round(app_cost / acc, 4) if acc else None,
                     "seat_utilization_pct": round(100 * sum(s["ActiveSeats"] or 0 for s in seats) / sum(s["AssignedSeats"] or 0 for s in seats), 1)
                     if seats and sum(s["AssignedSeats"] or 0 for s in seats) else None},
            "by_month": by_month, "tokens": tokens, "channel": channel, "rag_components": rag,
            "apps": apps, "provisioned": ptu, "idle_endpoints": self._uc("UO-09", scope),
            "budgets": self._uc("BF-04", scope), "routing": list(routing.values()), "seats": seats,
            "new_models": self._uc("AN-04", scope),
            "formula": "AI = charges with an AI product or an ai_component tag. Cost per accepted outcome: UO-13 (all of an "
                       "app's AI cost / outcomes users accepted). Cache hit rate: UO-01. Provisioned capacity: UO-10. "
                       "Routing what-if: UO-14. Budgets: BF-04.",
        }
