"""Data Readiness & Accounting Discovery (/api/readiness): how ready the loaded data is for an automated ledger, section by
section, for the period selected at the top of the page.

  sources         one row per feed: format, FOCUS version and export mode (the feed's settings on file, tenant copy
                  backend.readiness.sources), InvoiceId presence, months loaded, rows against the ingest manifest, spend on
                  accounts with a sensitivity flag (ref_cloud_account.SensitivityFlag)
  tags            Engines.tag_compliance (the Allocation page's panel); the tenant's tag-compliance use case otherwise
  accounting      ledger dollars by the basis of the rule that placed them (allocated_cost.RuleId joined to ref_ferc_rule:
                  regulation, policy signed off, policy awaiting sign-off) and unmapped; capitalized dollars with a valid
                  work order (ref_work_order); mapped against unmapped only when the ledger has no RuleId
  reconciliation  Engines.reconciliation() (the Reconciliation page) grouped by invoice
  controls        Engines.controls() (the Governance page) and board.confidence()

Every section scores 0-100 and carries a status key (ready, partial, gaps); the page takes its labels from the tenant copy
(frontend.readiness.*). The API returns keys and numbers only.
"""
from __future__ import annotations
import datetime as dt
import re
from . import tenant

SECTIONS = ("sources", "tags", "accounting", "reconciliation", "controls")
READY, PARTIAL = 95.0, 80.0  # score thresholds for the status keys


def _rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def _f(v) -> float:
    return float(v or 0)


def _status(score):
    if score is None:
        return "na"
    return "ready" if score >= READY else "partial" if score >= PARTIAL else "gaps"


def _rule_order(rule_id):
    m = re.match(r"R(\d+)(\w*)", str(rule_id or ""))
    return (int(m.group(1)), m.group(2)) if m else (999, str(rule_id))


def _months(win):
    """[first billing month the window touches, first day of the month after its last day) as datetimes."""
    s = dt.datetime(win.start.year, win.start.month, 1)
    e = dt.datetime(win.end.year + (win.end.month == 12), win.end.month % 12 + 1, 1)
    return s, e


class Readiness:
    def __init__(self, db, eng, ledger):
        self.db, self.eng, self.ledger = db, eng, ledger

    def q(self, sql, params=None):
        return _rows(self.db.query(sql, params or {}, None, limit=None))

    def has(self, table, column=None) -> bool:
        """Whether a table (and optionally a column on it) exists in the served database."""
        try:
            cols = self.db.query(f"SELECT * FROM {table} WHERE 1 = 0", {}, None, limit=1)["columns"]
        except Exception:  # noqa: BLE001 - missing table on this tenant
            return False
        return column is None or column in cols

    @staticmethod
    def _copy(key, default):
        try:
            return tenant.lookup(f"readiness.{key}")
        except (KeyError, TypeError):
            return default

    # ------------------------------------------------------------------ sources
    def sources(self, win):
        p0, p1 = _months(win)
        mp = {"p0": p0, "p1": p1}
        wp = win.params()
        rows = self.q(f"""SELECT x_SourceSystem AS Source, COUNT(*) AS Rows,
                                 SUM(CASE WHEN InvoiceId IS NOT NULL AND InvoiceId <> '' THEN 1 ELSE 0 END) AS WithInvoice
                          FROM CostAndUsage WHERE BillingPeriodStart >= :p0 AND BillingPeriodStart < :p1 GROUP BY 1""", mp)
        issuers = self.q("""SELECT x_SourceSystem AS Source, InvoiceIssuerName AS Issuer, COUNT(*) AS n FROM CostAndUsage
                            WHERE BillingPeriodStart >= :p0 AND BillingPeriodStart < :p1 GROUP BY 1, 2""", mp)
        months = {r["Source"]: int(r["n"]) for r in self.q(
            "SELECT x_SourceSystem AS Source, COUNT(DISTINCT BillingPeriodStart) AS n FROM CostAndUsage GROUP BY 1")}
        spend = {r["Source"]: _f(r["Spend"]) for r in self.q(
            f"SELECT x_SourceSystem AS Source, SUM(EffectiveCost) AS Spend FROM CostAndUsage WHERE {win.pred()} GROUP BY 1", wp)}
        man = {r["Source"]: r for r in self.q(
            """SELECT SourceSystem AS Source, SUM(RecordCount) AS n, COUNT(*) AS Manifests,
                      SUM(CASE WHEN Status = 'promoted' THEN 0 ELSE 1 END) AS NotPromoted
               FROM ingest_manifest WHERE BillingPeriodStart >= :p0 AND BillingPeriodStart < :p1 GROUP BY 1""", mp)}
        flags = list(self._copy("flaggedSensitivity", []) or [])
        flagged = {}
        if flags and self.has("ref_cloud_account", "SensitivityFlag"):
            ph = ", ".join(f":f{i}" for i in range(len(flags)))
            flagged = {r["Source"]: _f(r["Spend"]) for r in self.q(
                f"""SELECT c.x_SourceSystem AS Source, SUM(c.EffectiveCost) AS Spend FROM CostAndUsage c
                    WHERE {win.pred('c.ChargePeriodStart')}
                      AND c.SubAccountId IN (SELECT SubAccountId FROM ref_cloud_account WHERE SensitivityFlag IN ({ph}))
                    GROUP BY 1""", {**wp, **{f"f{i}": f for i, f in enumerate(flags)}})}
        top = {}
        for r in issuers:
            if r["Source"] not in top or r["n"] > top[r["Source"]][1]:
                top[r["Source"]] = (r["Issuer"], r["n"])
        profiles = list(self._copy("sources", []) or [])

        def profile(src):
            exact = next((x for x in profiles if x.get("source") == src), None)
            if exact:
                return exact
            return next((x for x in profiles if x.get("prefix") and str(src).startswith(x["prefix"])), {})

        out = []
        for r in sorted(rows, key=lambda r: -spend.get(r["Source"], 0)):
            src, n = r["Source"], int(r["Rows"])
            pr, m = profile(src), man.get(src) or {}
            mrows = int(m.get("n") or 0) if m else None
            out.append({
                "Source": src, "Provider": pr.get("provider") or (top.get(src) or (None,))[0],
                "Format": pr.get("format"), "FocusVersion": pr.get("focus"),
                "InvoiceIdPresentPct": round(100 * _f(r["WithInvoice"]) / n, 1) if n else None,
                "ExportMode": pr.get("export"), "MonthsAvailable": months.get(src, 0),
                "Rows": n, "ManifestRows": mrows, "RowsMatch": mrows == n and not _f(m.get("NotPromoted")),
                "CipFlaggedSpend": round(flagged.get(src, 0.0), 2) if flags else None,
                "Spend": round(spend.get(src, 0.0), 2),
            })
        total = sum(max(s["Spend"], 0) for s in out)
        ok = sum(max(s["Spend"], 0) for s in out if s["RowsMatch"] and (s["InvoiceIdPresentPct"] or 0) >= 99)
        score = round(100 * ok / total, 1) if total else None
        return out, score

    # ------------------------------------------------------------------ tags
    def tags(self, win):
        fn = getattr(self.eng, "tag_compliance", None)
        if fn is not None:
            out = fn(None, win=win)
        else:
            out = self._tags_fallback(win)
        keys = [k for k in (out.get("keys") or []) if k.get("CoveragePct") is not None]
        req = [k for k in keys if (k.get("Requirement") or "required") == "required"] or keys
        score = round(sum(_f(k["CoveragePct"]) for k in req) / len(req), 1) if out.get("available") and req else None
        return out, score

    def _tags_fallback(self, win):
        uid = ((tenant.pack().get("usecases") or {}).get("tag_compliance"))
        if not uid:
            return {"available": False}
        from .window import use_case_params
        uc = self.eng.reg.get(uid)
        params, _ = use_case_params(uc, win)
        keys = self.eng._uc(uid, None, {k: v.isoformat()[:10] for k, v in params.items()})
        cc = next((k for k in keys if k["TagKey"] == "cost_center"), {})
        tags = tenant.pack().get("tags") or {}
        return {"available": True, "period": {"label": win.label, "start": win.start.isoformat(), "end": win.end.isoformat()},
                "keys": keys, "by_entity": [], "by_business_unit": [],
                "cannot_be_tagged": {"amount": round(_f(keys[0]["CannotBeTagged"]), 2) if keys else 0.0, "by_category": []},
                "violations": [],
                "suspense": {"amount": round(_f(cc.get("UntaggedSpend")), 2), "share_pct": None, "lines": None,
                             "owner": tags.get("suspense_owner"), "oldest_days": None, "accounts": []},
                "formula": f"{uid}: cost-weighted coverage of each required tag in the period."}

    # ------------------------------------------------------------------ accounting
    def accounting(self, win):
        from . import allocation
        cols = set(self.db.query("SELECT * FROM allocated_cost WHERE 1 = 0", {}, None, limit=1)["columns"])
        declared = getattr(allocation, "extra_columns", None)
        if declared is not None:  # the tenant's declared ledger columns (empty for pharma), present in the served table
            cols = {c for c in cols if c not in {"RuleId", "PolicyFlag", "WorkOrderId"}} | (cols & set(declared() or []))
        has_rule = "RuleId" in cols and self.has("ref_ferc_rule")
        has_wo = "WorkOrderId" in cols and self.has("ref_work_order")
        wp = win.params()
        pred = win.pred("a.ChargePeriodStart")
        if has_rule:
            basis = "COALESCE(a.PolicyFlag, r.Basis)" if "PolicyFlag" in cols else "r.Basis"
            groups = self.q(f"""SELECT a.RuleId, {basis} AS Basis, r.Status, CASE WHEN a.GlAccount IS NULL THEN 1 ELSE 0 END AS Unmapped,
                                       COUNT(*) AS Lines, SUM(a.AllocatedCost) AS Cost
                                FROM allocated_cost a LEFT JOIN ref_ferc_rule r ON r.RuleId = a.RuleId
                                WHERE {pred} GROUP BY 1, 2, 3, 4""", wp)
        else:
            groups = self.q(f"""SELECT NULL AS RuleId, NULL AS Basis, NULL AS Status, CASE WHEN a.GlAccount IS NULL THEN 1 ELSE 0 END AS Unmapped,
                                       COUNT(*) AS Lines, SUM(a.AllocatedCost) AS Cost
                                FROM allocated_cost a WHERE {pred} GROUP BY 4""", wp)
        total = sum(_f(g["Cost"]) for g in groups)
        unmapped = sum(_f(g["Cost"]) for g in groups if g["Unmapped"])
        mapped = [g for g in groups if not g["Unmapped"]]
        policy = lambda g: str(g["Basis"] or "").upper() == "POLICY"  # noqa: E731
        signed = lambda g: str(g["Status"] or "").lower() == "approved"  # noqa: E731
        regulation = sum(_f(g["Cost"]) for g in mapped if g["RuleId"] and not policy(g)) if has_rule else None
        pol_ok = sum(_f(g["Cost"]) for g in mapped if g["RuleId"] and policy(g) and signed(g)) if has_rule else None
        pol_wait = sum(_f(g["Cost"]) for g in mapped if g["RuleId"] and policy(g) and not signed(g)) if has_rule else None
        no_rule = sum(_f(g["Cost"]) for g in mapped if not g["RuleId"]) if has_rule else None
        by_rule = {}
        for g in groups if has_rule else []:
            r = by_rule.setdefault(g["RuleId"], {"RuleId": g["RuleId"], "Basis": g["Basis"], "Status": g["Status"],
                                                 "Lines": 0, "AllocatedCost": 0.0})
            r["Lines"] += int(g["Lines"])
            r["AllocatedCost"] += _f(g["Cost"])
        rules = sorted(by_rule.values(), key=lambda r: _rule_order(r["RuleId"]))
        for r in rules:
            r["AllocatedCost"] = round(r["AllocatedCost"], 2)
        cap = self._capital(win, has_wo)
        out = {"total": round(total, 2), "mapped": round(total - unmapped, 2), "unmapped": round(unmapped, 2),
               "regulation": round(regulation, 2) if has_rule else None,
               "policy_signed_off": round(pol_ok, 2) if has_rule else None,
               "policy_awaiting": round(pol_wait, 2) if has_rule else None,
               "no_rule": round(no_rule, 2) if has_rule else None,
               "by_rule": rules, "rules_available": has_rule, **cap}
        if not total:
            score = None
        elif has_rule:
            score = round(100 * (regulation + pol_ok) / total, 1)
        else:
            score = round(100 * (total - unmapped) / total, 1)
        return out, score

    def _capital(self, win, has_wo):
        """Capitalized dollars in the period and the part with a valid work order (on file, threshold date set)."""
        if not self.has("ref_work_order"):
            return {"capitalized": None, "capitalized_valid_wo": None, "work_orders": None, "work_orders_with_threshold": None,
                    "capital_source": None}
        wo = self.q("""SELECT COUNT(DISTINCT WorkOrderId) AS n,
                              COUNT(DISTINCT CASE WHEN CapitalizationStartDate IS NOT NULL THEN WorkOrderId END) AS t
                       FROM ref_work_order""")[0]
        valid = "(SELECT DISTINCT WorkOrderId FROM ref_work_order WHERE CapitalizationStartDate IS NOT NULL)"
        held = ("no_work_order", "capitalizable_without_threshold_date")
        if has_wo:
            r = self.q(f"""SELECT SUM(CASE WHEN a.CostClass = 'cwip' OR a.Exception IN (:h0, :h1) THEN a.AllocatedCost ELSE 0 END) AS Cap,
                                  SUM(CASE WHEN a.CostClass = 'cwip' AND a.WorkOrderId IN {valid} THEN a.AllocatedCost ELSE 0 END) AS Valid
                           FROM allocated_cost a WHERE {win.pred('a.ChargePeriodStart')}""",
                       {**win.params(), "h0": held[0], "h1": held[1]})[0]
            source = "ledger"
        else:  # ledger without work-order ids: the capex and work_order tags on the source rows
            r = self.q(f"""SELECT SUM(EffectiveCost) AS Cap,
                                  SUM(CASE WHEN json_extract_string(Tags, '$.work_order') IN {valid} THEN EffectiveCost ELSE 0 END) AS Valid
                           FROM CostAndUsage
                           WHERE {win.pred()} AND (json_extract_string(Tags, '$.capex') = 'true'
                                                   OR json_extract_string(Tags, '$.implementation') = 'true')""", win.params())[0]
            source = "tags"
        return {"capitalized": round(_f(r["Cap"]), 2), "capitalized_valid_wo": round(_f(r["Valid"]), 2),
                "work_orders": int(wo["n"] or 0), "work_orders_with_threshold": int(wo["t"] or 0), "capital_source": source}

    # ------------------------------------------------------------------ reconciliation
    def reconciliation(self, win):
        rec = self.eng.reconciliation()
        first, last = f"{win.start:%Y-%m}", f"{win.end:%Y-%m}"
        lines = [l for l in rec["lines"] if first <= str(l["Period"]) <= last]
        inv = {}
        for l in lines:
            inv.setdefault(l["InvoiceId"], []).append(l)
        tied = sum(1 for ls in inv.values() if all(l["Status"] == "matched" for l in ls))
        labeled = sum(1 for ls in inv.values() if not all(l["Status"] == "matched" for l in ls)
                      and all(l["Status"] in ("matched", "labeled") for l in ls))
        reasons = {}
        for l in lines:
            if l["Status"] == "matched":
                continue
            key = l.get("Label") or l.get("Reason") or l["Status"]
            r = reasons.setdefault(key, {"Reason": key, "Status": l["Status"], "Lines": 0, "Amount": 0.0})
            r["Lines"] += 1
            r["Amount"] += _f(l["Difference"])
        by_reason = sorted(reasons.values(), key=lambda r: -abs(r["Amount"]))
        for r in by_reason:
            r["Amount"] = round(r["Amount"], 2)
        n = len(inv)
        out = {"invoices": n, "tied": tied, "labeled": labeled, "tied_pct": round(100 * tied / n, 1) if n else None,
               "lines": len(lines), "lines_matched": sum(1 for l in lines if l["Status"] == "matched"),
               "periods": sorted({str(l["Period"]) for l in lines}), "variance_by_reason": by_reason}
        return out, (round(100 * (tied + labeled) / n, 1) if n else None)

    # ------------------------------------------------------------------ controls
    def controls(self):
        from .board import confidence
        c = self.eng.controls(self.ledger.verify())["controls"]
        conf = confidence(self.db, self.eng, self.ledger, self.eng.kpis(None))
        passed = sum(1 for x in c if x["result"] == "pass")
        return c, conf, (round(100 * passed / len(c), 1) if c else None)

    # ------------------------------------------------------------------ page
    def build(self, win) -> dict:
        sources, s_src = self.sources(win)
        tags, s_tag = self.tags(win)
        acct, s_acc = self.accounting(win)
        rec, s_rec = self.reconciliation(win)
        ctrl, conf, s_ctl = self.controls()
        scores = dict(zip(SECTIONS, (s_src, s_tag, s_acc, s_rec, s_ctl)))
        have = [v for v in scores.values() if v is not None]
        overall = round(sum(have) / len(have), 1) if have else None
        return {
            "period": win.as_dict(),
            "score": {"overall": overall, "status": _status(overall),
                      "sections": [{"key": k, "score": v, "status": _status(v)} for k, v in scores.items()]},
            "sources": sources, "tags": tags, "accounting": acct, "reconciliation": rec,
            "controls": ctrl, "confidence": conf,
            "formula": self._copy("formula", ""),
        }
