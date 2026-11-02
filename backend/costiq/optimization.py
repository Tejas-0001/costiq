"""Optimization lifecycle: identified -> approved -> implemented -> verified (or rejected).

Actions come from provider recommendations (optimization_recommendation) and from CostIQ's own AI and cloud checks.
Each carries a scope (a resource, an application, a seat pool or an account) so its savings can be verified from data:

  baseline  = average daily cost of the scope over the 30 days before implementation x 30
  verified  = baseline - average daily cost after implementation x 30   (needs 14+ days of post-change data;
              with less, the estimate is recorded as "provisional")

Whoever implements a change cannot verify it (separation of duties). Every decision is written to the ledger.
"""
from __future__ import annotations
import datetime as dt
from collections import defaultdict
from fastapi import HTTPException

LEVERS = {
    "rightsizing": ("Rightsizing", "cloud"), "idle": ("Idle and unattached", "cloud"), "scheduling": ("Off-hours scheduling", "cloud"),
    "storage": ("Storage tiering", "cloud"), "egress": ("Data transfer", "cloud"), "commitment": ("Commitments", "cloud"),
    "kubernetes": ("Kubernetes bin-packing", "cloud"), "ai_cache": ("Prompt caching", "ai"), "ai_batch": ("Batch API", "ai"),
    "model_routing": ("Model routing", "ai"), "seats": ("Seat reclaim", "ai"), "ptu": ("Provisioned capacity", "ai"),
    "gpu_idle": ("Idle GPU endpoints", "ai"), "context": ("Context trimming", "ai"), "retries": ("Retry waste", "ai"),
}
ACTION_LEVER = {"rightsize": "rightsizing", "stop_idle": "idle", "delete_unattached": "idle", "schedule_offhours": "scheduling",
                "upgrade_generation": "rightsizing", "storage_tier": "storage", "purchase_commitment": "commitment"}
MIN_POST_DAYS = 14
TRANSITIONS = {  # action -> (permission, allowed from, to)
    "approve": ("recommendation.approve", {"identified"}, "approved"),
    "reject": ("recommendation.approve", {"identified", "approved"}, "rejected"),
    "implement": ("optimization.implement", {"approved"}, "implemented"),
    "verify": ("optimization.verify", {"implemented"}, "verified"),
}


def rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def as_date(v):
    if v is None:
        return None
    return v if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else dt.date.fromisoformat(str(v)[:10])


class Optimizer:
    def __init__(self, db, engines):
        self.db, self.eng = db, engines

    # ------------------------------------------------------------------ measurement
    def _scope_filter(self, scope_type, key):
        """(SQL predicate on CostAndUsage, its parameters) selecting the charges an action is about."""
        if scope_type == "resource":
            return "ResourceName = :k", {"k": key}
        if scope_type == "application":
            return "json_extract_string(Tags, '$.application') = :k", {"k": key}
        if scope_type == "account":
            return "SubAccountId = :k", {"k": key.split(":", 1)[-1]}
        if scope_type == "seat_pool":
            return "ChargeCategory = 'Purchase' AND x_AIProduct IN ('m365_copilot', 'github_copilot', 'claude_enterprise')", {}
        raise HTTPException(400, f"Unknown scope type {scope_type}")

    def run_rate(self, scope_type, key, start: dt.date, end: dt.date):
        """(monthly run rate, days of data) for the scope over [start, end)."""
        where, params = self._scope_filter(scope_type, key)
        r = self.db.query(f"""SELECT SUM(EffectiveCost), COUNT(DISTINCT CAST(ChargePeriodStart AS DATE)) FROM CostAndUsage
                              WHERE {where} AND ChargePeriodStart >= :s AND ChargePeriodStart < :e""",
                          {**params, "s": dt.datetime.combine(start, dt.time()), "e": dt.datetime.combine(end, dt.time())})["rows"][0]
        cost, days = float(r[0] or 0), int(r[1] or 0)
        span = max(1, (end - start).days)
        if scope_type == "seat_pool":  # monthly seat purchases: spread over the window, not per billed day
            return cost / span * 30, span
        return (cost / days * 30 if days else 0.0), days

    def measure(self, a):
        """(verified monthly savings, basis) for an implemented action."""
        as_of = self.eng.as_of()
        impl = as_date(a["ImplementedOn"])
        base, _ = self.run_rate(a["ScopeType"], a["ScopeKey"], impl - dt.timedelta(days=30), impl)
        post_days = (as_of - impl).days + 1
        if post_days >= MIN_POST_DAYS:
            post, _ = self.run_rate(a["ScopeType"], a["ScopeKey"], impl, as_of + dt.timedelta(days=1))
            return round(base - post, 2), round(base, 2), "measured"
        return round(float(a["EstimatedMonthlySavings"] or 0), 2), round(base, 2), "provisional"

    # ------------------------------------------------------------------ backlog
    def actions(self, scope=None):
        out = rows(self.db.query("SELECT * FROM optimization_action ORDER BY OptimizationId", {}, None, limit=None))
        if scope is None:
            pass
        elif type(scope) is list:  # cost-center scope
            out = [a for a in out if a["CostCenter"] in scope]
        else:  # entity-scoped users do not manage optimizations
            out = []
        for a in out:
            label, domain = LEVERS.get(a["Lever"], (a["Lever"], a["Domain"]))
            a["LeverLabel"] = label
        return out

    def action(self, oid):
        r = rows(self.db.query("SELECT * FROM optimization_action WHERE OptimizationId = :i", {"i": oid}, None, limit=1))
        if not r:
            raise HTTPException(404, f"Unknown optimization {oid}")
        return r[0]

    def _next_id(self):
        ids = [r[0] for r in self.db.query("SELECT OptimizationId FROM optimization_action", {}, None, limit=None)["rows"]]
        n = max([int(i.split("-")[1]) for i in ids if i.startswith("OPT-")] or [0]) + 1
        return f"OPT-{n:03d}"

    def create(self, origin, lever, title, scope_type, scope_key, cost_center, owner, estimate, confidence="costiq_estimated",
               identified_on=None):
        """Add an action unless one already exists for the same origin and scope. Returns the action."""
        existing = rows(self.db.query("SELECT * FROM optimization_action WHERE Origin = :o AND ScopeKey = :k",
                                      {"o": origin, "k": scope_key}, None, limit=1))
        if existing:
            return existing[0]
        if lever not in LEVERS:
            raise HTTPException(400, f"Unknown lever {lever}")
        self._scope_filter(scope_type, scope_key)
        oid = self._next_id()
        self.db.query("""INSERT INTO optimization_action (OptimizationId, Origin, Lever, Domain, Title, ScopeType, ScopeKey,
                         CostCenter, Owner, Confidence, Status, EstimatedMonthlySavings, IdentifiedOn)
                         VALUES (:i, :o, :l, :d, :t, :st, :sk, :cc, :ow, :cf, 'identified', :e, :ident_on)""",
                      dict(i=oid, o=origin, l=lever, d=LEVERS[lever][1], t=title, st=scope_type, sk=scope_key, cc=cost_center,
                           ow=owner, cf=confidence, e=round(float(estimate or 0), 2),
                           ident_on=identified_on or self.eng.as_of()), limit=None)
        return self.action(oid)

    def seed_from_sources(self):
        """Track every provider recommendation and the strongest AI findings (idempotent)."""
        recs = rows(self.db.query("""
            SELECT r.*, (SELECT MAX(json_extract_string(c.Tags, '$.cost_center')) FROM CostAndUsage c
                         WHERE c.ResourceName = r.ResourceName) AS CostCenter
            FROM optimization_recommendation r""", {}, None, limit=None))
        for r in recs:
            lever = "ptu" if r["ActionType"] == "rightsize" and r["ServiceProviderName"] == "Microsoft" else ACTION_LEVER[r["ActionType"]]
            scope_type = "account" if str(r["ResourceName"]).startswith("account:") else "resource"
            self.create(r["RecommendationId"], lever, f"{r['RecommendedResourceSummary']}: {r['ResourceName']}", scope_type,
                        r["ResourceName"], r["CostCenter"] or "Corporate IT", "Cloud Operations", r["EstimatedMonthlySavings"],
                        "provider_recommended", as_date(r["RefreshedAt"]))
        uc = self.eng._uc
        for x in sorted(uc("UO-11", None), key=lambda r: -(r["RetriedPct"] or 0))[:1]:
            self.create("UO-11", "retries", f"Fix retry storm in {x['Application']} ({x['RetriedPct']}% retried)", "application",
                        x["Application"], None, "AI Program Office", (x["WastedCost"] or 0) / self._months("UO-11"))
        for x in [x for x in uc("UO-14", None) if (x["Savings"] or 0) > 0 and x["AvgOutputTokens"] < 500][:1]:
            self.create("UO-14", "model_routing", f"Route {x['Application']} from {x['CurrentModel']} to {x['CandidateModel']}",
                        "application", x["Application"], None, "AI Program Office", (x["Savings"] or 0) / self._months("UO-14"))
        # cost centers for application-scoped actions
        self.db.query("""UPDATE optimization_action SET CostCenter = (
                           SELECT MAX(json_extract_string(Tags, '$.cost_center')) FROM CostAndUsage
                           WHERE json_extract_string(Tags, '$.application') = optimization_action.ScopeKey)
                         WHERE CostCenter IS NULL AND ScopeType = 'application'""", {}, None, limit=None)

    def _months(self, uid):
        """Length in months of a use case's default period (to turn its period totals into monthly savings)."""
        p = self.eng.reg.resolve_params(self.eng.reg.get(uid), None)
        return max(1.0, (p["period_end"] - p["period_start"]).days / 30.0)

    def refresh(self):
        """Seed actions, refresh baselines, and measure verified actions that have no figure yet."""
        self.seed_from_sources()
        as_of = self.eng.as_of()
        for a in self.actions():
            ref = as_date(a["ImplementedOn"]) or as_of + dt.timedelta(days=1)
            base, _ = self.run_rate(a["ScopeType"], a["ScopeKey"], ref - dt.timedelta(days=30), ref)
            self.db.query("UPDATE optimization_action SET BaselineMonthlyCost = :b WHERE OptimizationId = :i",
                          {"b": round(base, 2), "i": a["OptimizationId"]}, limit=None)
            if a["Status"] == "verified" and a["VerifiedMonthlySavings"] is None:
                saving, base, basis = self.measure(a)
                self.db.query("""UPDATE optimization_action SET VerifiedMonthlySavings = :v, VerificationBasis = :b,
                                 VerifiedOn = COALESCE(VerifiedOn, :ver_on) WHERE OptimizationId = :i""",
                              {"v": saving, "b": basis, "ver_on": as_of, "i": a["OptimizationId"]}, limit=None)

    # ------------------------------------------------------------------ workflow
    def decide(self, oid, action, p, ledger, note=""):
        if action not in TRANSITIONS:
            raise HTTPException(400, "action must be approve, reject, implement or verify")
        perm, allowed_from, to = TRANSITIONS[action]
        p.require_action(perm)
        a = self.action(oid)
        if type(p.scope) is list and a["CostCenter"] not in p.scope:
            raise HTTPException(403, "This optimization is outside your cost-center scope")
        if a["Status"] not in allowed_from:
            raise HTTPException(409, f"{oid} is {a['Status']}; {action} needs {' or '.join(sorted(allowed_from))}")
        today = dt.date.today()
        sets, params = {"Status": to}, {}
        if action == "approve":
            sets["ApprovedOn"] = today
        elif action == "implement":
            sets.update(ImplementedOn=today, ImplementedBy=p.user)
        elif action == "verify":
            if a["ImplementedBy"] == p.user:
                raise HTTPException(409, "Separation of duties: you implemented this change, so someone else must verify it")
            saving, base, basis = self.measure(a)
            sets.update(VerifiedOn=today, VerifiedMonthlySavings=saving, VerificationBasis=basis, BaselineMonthlyCost=base)
        assign = ", ".join(f"{k} = :{k}" for k in sets)
        self.db.query(f"UPDATE optimization_action SET {assign} WHERE OptimizationId = :oid", {**sets, "oid": oid}, limit=None)
        detail = note or (f"verified ${sets['VerifiedMonthlySavings']:,.2f}/month ({sets['VerificationBasis']})" if action == "verify" else "")
        entry = ledger.append(p.user, p.persona, action, f"optimization:{oid}", detail)
        return self.action(oid), entry

    # ------------------------------------------------------------------ summary
    def summary(self, scope=None):
        acts = [a for a in self.actions(scope) if a["Status"] != "rejected"]
        est = lambda a: float(a["EstimatedMonthlySavings"] or 0)
        stages = [
            {"Stage": "Identified", "MonthlySavings": round(sum(est(a) for a in acts), 2), "Actions": len(acts)},
            {"Stage": "Approved", "MonthlySavings": round(sum(est(a) for a in acts if a["Status"] in ("approved", "implemented", "verified")), 2),
             "Actions": sum(1 for a in acts if a["Status"] in ("approved", "implemented", "verified"))},
            {"Stage": "Implemented", "MonthlySavings": round(sum(est(a) for a in acts if a["Status"] in ("implemented", "verified")), 2),
             "Actions": sum(1 for a in acts if a["Status"] in ("implemented", "verified"))},
            {"Stage": "Verified", "MonthlySavings": round(sum(float(a["VerifiedMonthlySavings"] or 0) for a in acts if a["Status"] == "verified"), 2),
             "Actions": sum(1 for a in acts if a["Status"] == "verified")},
        ]
        by_lever = defaultdict(lambda: {"Identified": 0.0, "Verified": 0.0, "Actions": 0, "ProviderRecommended": 0.0})
        for a in acts:
            k = (a["Lever"], LEVERS.get(a["Lever"], (a["Lever"], a["Domain"]))[0], a["Domain"])
            by_lever[k]["Identified"] += est(a)
            by_lever[k]["Actions"] += 1
            if a["Confidence"] == "provider_recommended":
                by_lever[k]["ProviderRecommended"] += est(a)
            if a["Status"] == "verified":
                by_lever[k]["Verified"] += float(a["VerifiedMonthlySavings"] or 0)
        realized = defaultdict(float)
        for a in acts:
            if a["Status"] == "verified" and a["VerifiedOn"]:
                realized[str(a["VerifiedOn"])[:7]] += float(a["VerifiedMonthlySavings"] or 0)
        return {
            "stages": stages,
            "by_lever": sorted([{"Lever": k[0], "LeverLabel": k[1], "Domain": k[2], **{x: round(y, 2) if isinstance(y, float) else y
                                                                                       for x, y in v.items()}}
                                for k, v in by_lever.items()], key=lambda r: -r["Identified"]),
            "realized_by_month": [{"Month": m, "VerifiedMonthlySavings": round(v, 2)} for m, v in sorted(realized.items())],
            "verified_monthly": stages[-1]["MonthlySavings"],
            "verified_annual": round(stages[-1]["MonthlySavings"] * 12, 2),
            "formula": "Identified to implemented use estimated monthly savings; verified uses measured savings: the scope's "
                       "30-day run-rate before implementation minus its run-rate after (14+ days of data), else the "
                       "estimate marked provisional. Rejected actions are excluded.",
        }
