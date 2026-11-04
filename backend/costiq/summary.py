"""AI summary: what matters now, in a few sentences, with every figure linked to the page and row that shows it.

The facts come from the same engines as the pages they link to, filtered by the caller's role and scope: spend and
forecast (Command Center, Budgets & Forecasts, AI & LLM Cost Monitor), tax risk and R&D credit (Tax Risk Radar, Tax &
Transfer Pricing), GxP and patient data, the savings pipeline (Optimization Center), alerts, anomalies and invoice
exceptions. A figure is only linked to a page the caller can open.

Sentences cite facts as ⟦f12⟧. The web app replaces each citation with the fact's figure, linked to its source, so a
figure in the summary is always the figure on the page.

Writers (nothing is sent outside CostIQ; there is no model call)
    costiq  ranks the facts and writes the sentences from fixed templates: the same data gives the same text.
    saved   prose written in advance for a set of roles and periods (data/summary_responses.json, managed with
            tools/summary_responses.py). Saved text cites facts like the costiq text, so its figures stay live. The
            headline and each section use their saved text only while the facts behind them are the ones it was
            written for. A section whose facts changed (an approval, an acknowledged alert) shows the costiq text.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
from urllib.parse import urlencode
from .window import window as make_window

log = logging.getLogger("costiq.summary")
CITE = re.compile(r"⟦(f\d+)⟧")

PAGES = {  # module -> (page title, path); "*" pages are open to every role
    "command": ("Command Center", "/command"),
    "board": ("Board Pack", "/board"),
    "budgets": ("Budgets & Forecasts", "/budgets"),
    "ai": ("AI & LLM Cost Monitor", "/ai"),
    "risk": ("Tax Risk Radar", "/risk"),
    "tax": ("Tax & Transfer Pricing", "/tax"),
    "compliance": ("GxP & Patient Data", "/compliance"),
    "optimization": ("Optimization Center", "/optimization"),
    "anomalies": ("Anomalies", "/anomalies"),
    "reconciliation": ("Reconciliation & Close", "/reconciliation"),
    "governance": ("Governance & Evidence", "/governance"),
    "alerts": ("Alerts", "/alerts"),
    "catalog": ("Use Case Catalog", "/catalog"),
}
OPEN_TO_ALL = {"alerts"}
REASON_SHORT = {
    "unactioned_savings": "known savings left unactioned for 60+ days", "no_benefit": "spend with no benefit shown",
    "unreconciled": "spend not reconciled to an invoice", "unmapped_gl": "spend with no GL account",
    "no_approved_policy": "allocations with no approved policy",
}
PART_PHRASE = {
    "month_elapsed": "only {v} of the month has elapsed",
    "implemented_adapter_coverage": "only {v} of spend arrives through live adapters",
    "allocation_coverage": "allocation rules cover only {v} of spend",
    "prior_period_tie_out": "only {v} of closed invoice lines tie out",
}
VERB = {"identified": "approving", "approved": "implementing", "implemented": "verifying"}
NEXT = {"identified": "Approve", "approved": "Implement", "implemented": "Verify"}


def money(v) -> str:
    a = abs(float(v or 0))
    if a >= 1e6:
        return f"${a / 1e6:.2f}M"
    if a >= 1e5:
        return f"${a / 1e3:.0f}K"
    if a >= 1e3:
        return f"${a / 1e3:.1f}K"
    return f"${a:,.0f}"


def pct(v, d=1) -> str:
    return f"{float(v):.{d}f}%"


def plural(n, one, many=None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


class Brief:
    """Facts, their sources and the sentences that cite them, for one caller."""

    def __init__(self, p):
        self.p = p
        self.facts: dict[str, dict] = {}
        self.sources: dict[tuple, dict] = {}
        self.sections: list[dict] = []
        self.actions: list[dict] = []
        self.lead: dict[str, str] = {}  # headline ingredients by topic

    def href(self, module, sec=None, hl=None, **extra):
        if module not in OPEN_TO_ALL and not self.p.can(module):
            return None
        q = {k: v for k, v in {**extra, "sec": sec, "hl": hl}.items() if v}
        return PAGES[module][1] + ("?" + urlencode(q) if q else "")

    def f(self, text, label, module, section, hl=None, **extra) -> str:
        """Register a fact shown on `module`'s page in `section`; returns its citation token."""
        page = PAGES[module][0]
        key = (page, section)
        if key not in self.sources:
            self.sources[key] = {"n": len(self.sources) + 1, "page": page, "section": section,
                                 "href": self.href(module, sec=section, **extra)}
        fid = f"f{len(self.facts) + 1}"
        self.facts[fid] = {"text": str(text), "label": label, "href": self.href(module, sec=section, hl=hl, **extra),
                           "source": self.sources[key]["n"]}
        return f"⟦{fid}⟧"

    def section(self, key, title, tone, state, sentences):
        """`tone` (good, warn, bad, info) colours the state pill; `state` is its label."""
        sentences = [s for s in sentences if s]
        if sentences:
            self.sections.append({"key": key, "title": title, "tone": tone, "state": state, "sentences": sentences})

    def act(self, kind, title, detail, value, href, weight):
        if href:
            self.actions.append({"kind": kind, "title": title, "detail": detail, "value": value, "href": href, "weight": weight})


class Summarizer:
    def __init__(self, db, eng, prudence, optimizer, alerts, ledger):
        self.db, self.eng, self.prudence, self.optimizer, self.alerts, self.ledger = db, eng, prudence, optimizer, alerts, ledger
        self._cache: dict[str, tuple[float, dict]] = {}

    # ------------------------------------------------------------------ public
    def build(self, p, persona_label: str, fresh=False, win=None) -> dict:
        win = win or make_window("mtd", self.eng.as_of())
        key = self._key(p, win.range)
        hit = self._cache.get(key)
        if hit and not fresh and time.time() - hit[0] < 600:
            return hit[1]
        b = Brief(p)
        b.win = win
        for part in (self.spend, self.risk, self.money_on_table, self.operations):
            try:
                part(b)
            except Exception as e:  # one engine failing must not hide the rest of the summary
                log.warning("summary part %s failed: %s", part.__name__, e)
        out = self._assemble(b)
        out["period"] = win.as_dict()
        saved = saved_responses().get(response_key(p, win.range))
        if saved and out["sections"]:
            apply_saved(out, saved)
        self._cache[key] = (time.time(), out)
        return out

    def _key(self, p, rng) -> str:
        last = self.db.query("SELECT MAX(Seq) FROM decision_ledger")["rows"][0][0]
        acks = self.db.query("SELECT COUNT(*) FROM cost_alert_event WHERE Acknowledged")["rows"][0][0]
        return json.dumps([p.user, p.personas, p.scope, sorted(p.modules), last, acks, rng], default=str)

    # ------------------------------------------------------------------ spend and forecast
    def spend(self, b: Brief):
        p = b.p
        if not p.can("command"):
            return
        k = self.eng.kpis(p.scope)
        start, _, day, days = self.eng.open_month()
        w = b.win
        month = start.strftime("%B")
        ts = lambda d: dt.datetime.combine(d, dt.time())  # noqa: E731
        short = lambda d: f"{d.day} {d:%b}"  # noqa: E731
        # how the window reads in a sentence: subject, the spend it covers, and the comparable prior window
        subject, whose, versus = {
            "today": (f"Spend today ({short(w.end)})", "today's spend", "yesterday"),
            "yesterday": (f"Spend yesterday ({short(w.end)})", "yesterday's spend", "the day before"),
            "wtd": (f"Spend this week ({short(w.start)} to {short(w.end)})", "this week's spend", "the same days last week"),
            "mtd": (f"{month} spend to date", f"{month} spend", f"the same days of {(start - dt.timedelta(days=1)):%B}"),
            "ytd": (f"Spend this year to date", "this year's spend", None),
        }[w.range]
        sec = f"Spend, {w.long}"

        def split(a, z):
            r = self.db.query("""SELECT SUM(CASE WHEN x_AIProduct IS NOT NULL THEN EffectiveCost ELSE 0 END), SUM(EffectiveCost)
                                 FROM CostAndUsage WHERE ChargePeriodStart >= :a AND ChargePeriodStart < :z""",
                              {"a": ts(a), "z": ts(z + dt.timedelta(days=1))}, p.scope)["rows"][0]
            return float(r[0] or 0), float(r[1] or 0)

        ai, total = split(w.start, w.end)
        ai_prev, total_prev = split(*w.prev) if w.prev else (0.0, 0.0)
        s = []
        chg = 100 * (total - total_prev) / total_prev if total_prev else None
        amt = b.f(money(total), f"Spend, {w.label}", "command", sec)
        line = f"{subject} is {amt}"
        if chg is not None:
            line += f", {b.f(('down ' if chg < 0 else 'up ') + pct(abs(chg)), 'Change on ' + versus, 'command', sec)} on {versus}"
        s.append(line + ".")
        if w.range != "mtd":
            b.lead["window"] = (whose[0].upper() + whose[1:] + f" was {amt}"
                                + (f", {'down' if chg < 0 else 'up'} {pct(abs(chg))} on {versus}" if chg is not None else ""))

        top = self.db.query("""SELECT COALESCE(ServiceProviderName, 'Other'), SUM(EffectiveCost) FROM CostAndUsage
                               WHERE ChargePeriodStart >= :a AND ChargePeriodStart < :z GROUP BY 1 ORDER BY 2 DESC LIMIT 1""",
                            {"a": ts(w.start), "z": ts(w.end + dt.timedelta(days=1))}, p.scope)["rows"]
        cc, cp = self.eng.cost_center_expr()
        big = self.db.query(f"""SELECT {cc}, SUM(EffectiveCost) FROM CostAndUsage WHERE ChargePeriodStart >= :a AND ChargePeriodStart < :z
                                GROUP BY 1 ORDER BY 2 DESC LIMIT 1""",
                            {**cp, "a": ts(w.start), "z": ts(w.end + dt.timedelta(days=1))}, p.scope)["rows"]
        if top and total:
            line = (f"The largest provider in {whose} is {b.f(top[0][0], 'Largest provider, ' + w.long, 'command', 'Cloud & SaaS / AI')}"
                    f" at {b.f(money(top[0][1]), 'Its spend, ' + w.long, 'command', 'Cloud & SaaS / AI')}")
            if big and (p.scope is None or len(p.scope) > 1):
                line += (f", and the largest cost center is {b.f(big[0][0], 'Largest cost center, ' + w.long, 'command', 'Cost centers', hl=big[0][0])}"
                         f" at {b.f(money(big[0][1]), 'Its spend, ' + w.long, 'command', 'Cost centers', hl=big[0][0])}")
            s.append(line + ".")

        var = k["variance"]
        fc = b.f(money(k["forecast"]), f"{month} forecast", "command", "Forecast vs budget")
        bud = b.f(money(k["budget"]), f"{month} budget", "command", "Forecast vs budget")
        gap = b.f(f"{money(var)} {'over' if var > 0 else 'under'}", "Forecast minus budget", "command", "Forecast vs budget")
        s.append(f"At this pace {month} closes at {fc}, {gap} its {bud} budget.")
        b.lead["spend"] = f"{month} is on course to close {gap} budget"
        b.lead["spend_over"] = "1" if var > 0 else ""

        units = [u for u in k["units"] if u.get("Budget")]
        over = sorted([u for u in units if (u.get("Variance") or 0) > 0], key=lambda u: -(u["Variance"] or 0))
        risk = [u for u in units if u.get("Status") == "at risk"]
        mod, sec = ("budgets", "Forecast and budget by cost center") if p.can("budgets") else ("command", "Cost centers")
        if over:
            u = over[0]
            n = b.f(plural(len(over), "cost center"), "Cost centers forecast over budget", mod, sec)
            name = b.f(u["CostCenter"], f"{u['CostCenter']}: forecast against budget", mod, sec, hl=u["CostCenter"])
            amt = b.f(f"+{pct(u['VariancePct'])}", f"{u['CostCenter']} variance", mod, sec, hl=u["CostCenter"])
            fu = b.f(money(u["Forecast"]), f"{u['CostCenter']} forecast", mod, sec, hl=u["CostCenter"])
            bu = b.f(money(u["Budget"]), f"{u['CostCenter']} budget", mod, sec, hl=u["CostCenter"])
            tail = (f"; {b.f(plural(len(risk), 'more is', 'more are'), 'Cost centers at risk: forecast within 3% of budget', mod, sec)}"
                    f" within 3% of budget") if risk else ""
            s.append(f"{n} {'is' if len(over) == 1 else 'are'} forecast over budget, led by {name} at {amt} ({fu} against {bu}){tail}.")
        elif units:
            s.append("Every cost center is forecast within budget.")

        if total and p.can("ai"):
            asec = "AI spend by product"
            share = b.f(pct(100 * ai / total), "AI share of spend, " + w.long, "ai", asec)
            ai_amt = b.f(money(ai), "AI spend, " + w.long, "ai", asec)
            line = f"AI is {share} of {whose} ({ai_amt})"
            if ai_prev:
                g = 100 * (ai - ai_prev) / ai_prev
                line += f", {b.f(('down ' if g < 0 else 'up ') + pct(abs(g)), 'AI spend change on ' + versus, 'ai', asec)} on {versus}"
            top = self.db.query("""SELECT x_AIProduct, SUM(EffectiveCost) FROM CostAndUsage WHERE x_AIProduct IS NOT NULL
                                   AND ChargePeriodStart >= :a AND ChargePeriodStart < :z GROUP BY 1 ORDER BY 2 DESC LIMIT 1""",
                                {"a": ts(w.start), "z": ts(w.end + dt.timedelta(days=1))}, p.scope)["rows"]
            if top:
                line += (f"; the largest AI line is {b.f(top[0][0], 'Largest AI product, ' + w.long, 'ai', asec, hl=top[0][0])}"
                         f" at {b.f(money(top[0][1]), 'Its spend, ' + w.long, 'ai', asec, hl=top[0][0])}")
            s.append(line + ".")

        if p.scope is None:
            from .board import confidence
            c = confidence(self.db, self.eng, self.ledger, k)
            weakest = min(c["parts"], key=c["parts"].get)
            score = b.f(pct(c["score"]), "Forecast confidence", "command", "Forecast confidence")
            why = PART_PHRASE.get(weakest, "{v}").format(v=b.f(pct(c["parts"][weakest]), "Weakest confidence input", "command", "Forecast confidence"))
            s.append(f"Forecast confidence is {score}, held back because {why}.")
        tone = "bad" if var > 0 else "warn" if over else "good"
        state = "Over budget" if var > 0 else plural(len(over), "cost center") + " over" if over else "On track"
        b.section("spend", "Spend and forecast", tone, state, s)

    # ------------------------------------------------------------------ tax risk, GxP and patient data
    def risk(self, b: Brief):
        p = b.p
        s = []
        if p.scope is None and p.can("risk"):
            from .prudence import OPEN_FILINGS
            r = self.prudence.summary(None)
            open_f = [d for d in r["filings"] if d["Status"] in OPEN_FILINGS]
            amt = b.f(money(r["deduction_at_risk"]), "Deduction at risk", "risk", "Deduction at risk")
            nf = b.f(plural(len(open_f), "open filing"), "Open filings", "risk", "By filing")
            ev = b.f(pct(r["evidence_score"]), "Share of review-period spend evidenced", "risk", "Deduction at risk")
            s.append(f"An examiner could challenge {amt} of deductions across {nf}; {ev} of review-period spend is evidenced.")
            b.lead["risk"] = f"{amt} of deductions could be challenged"
            if r["by_reason"]:
                top_reason = r["by_reason"][0]
                why = b.f(REASON_SHORT.get(top_reason["RiskReason"], top_reason["Label"].lower()), "Largest cause of risk", "risk", "By reason",
                          hl=top_reason["Label"])
                s.append(f"The main cause is {why}, at {b.f(money(top_reason['Risk']), 'Risk from this cause', 'risk', 'By reason', hl=top_reason['Label'])}.")
            if r["top"]:
                t = r["top"][0]
                item = b.f(t["Item"], "Largest single risk", "risk", "Largest risks", hl=t["Item"])
                ramt = b.f(money(t["Risk"]), "Its deduction at risk", "risk", "Largest risks", hl=t["Item"])
                filing = b.f(t["FilingId"], "Filing it sits in", "risk", "By filing", hl=t["FilingId"])
                line = f"The largest single exposure is {item} ({t['Vendor']}, {t['LegalEntity']}) at {ramt} in {filing}"
                fix = t.get("Fix") or {}
                if fix.get("kind") == "optimization" and fix.get("status") in VERB:
                    a = next((x for x in self.optimizer.actions(None) if x["OptimizationId"] == fix["target"]), None)
                    if a:
                        oid = b.f(fix["target"], a["Title"], "optimization", "Backlog", focus=fix["target"])
                        sav = b.f(money(a["EstimatedMonthlySavings"]), "Estimated monthly saving", "optimization", "Backlog", focus=fix["target"])
                        line += f"; {VERB[fix['status']]} {oid} removes it and saves {sav} a month"
                        b.act("optimization", f"{NEXT[fix['status']]} {fix['target']}", f"{a['Title']}. Also clears {money(t['Risk'])} of deduction risk in {t['FilingId']}.",
                              f"{money(a['EstimatedMonthlySavings'])}/mo", b.href("optimization", focus=fix["target"]),
                              12 * float(a["EstimatedMonthlySavings"] or 0) + float(t["Risk"]))
                s.append(line + ".")
            due = sorted((d for d in open_f if d.get("FilingDate")), key=lambda d: str(d["FilingDate"]))
            if due:
                d = due[0]
                when = dt.date.fromisoformat(str(d["FilingDate"])[:10])
                days = (when - self.eng.as_of()).days
                fr = next((x for x in r["by_filing"] if x["FilingId"] == d["FilingId"]), None)
                fid = b.f(d["FilingId"], f"Next filing ({d['LegalEntity']}, {d['Jurisdiction']})", "risk", "By filing", hl=d["FilingId"])
                lab = f"Filing date of {d['FilingId']}"
                due_in = b.f(plural(abs(days), "day"), "Days until the filing date" if days >= 0 else "Days past the filing date", "risk", "By filing", hl=d["FilingId"])
                line = f"{fid} is due {'in ' + due_in if days >= 0 else due_in + ' late'} ({b.f(when.isoformat(), lab, 'risk', 'By filing', hl=d['FilingId'])})"
                if fr:
                    line += f" and carries {b.f(money(fr['Risk']), 'Deduction at risk in this filing', 'risk', 'By filing', hl=d['FilingId'])} of the risk"
                s.append(line + ".")

        b.section("risk", "Tax risk", "bad", "At risk", s)
        s = []
        if p.can("compliance") or p.can("governance"):
            fnd = [x for x in self.eng.findings(p.scope) if x["kind"] == "compliance" and x["severity"] == "high"]
            for x in sorted(fnd, key=lambda x: -x["exposure"])[:2]:
                ref = self._finding(b, x, x["id"], f"{x['title']} ({x['detail']})")
                amt = self._finding(b, x, money(x["exposure"]), "Spend affected")
                if x["id"] == "PH-07":
                    s.append(f"{amt} of spend runs where patient data is encrypted with provider-managed keys ({x['detail']}), flagged by {ref}.")
                    b.lead.setdefault("compliance", f"{amt} of spend holds patient data on provider-managed keys")
                    b.act("compliance", "Move patient-data workloads to customer-managed keys", f"{x['detail']} ({x['id']})",
                          f"{money(x['exposure'])} of spend", b.href("compliance", hl=x["id"]) or f"/catalog/{x['id']}", float(x["exposure"]))
                elif x["id"] == "GV-02":
                    s.append(f"A regulated workload has been served outside the US ({x['detail']}), {amt} of spend, flagged by {ref}.")
                else:
                    s.append(f"{ref}: {x['title'][0].lower() + x['title'][1:]} ({x['detail']}), {amt}.")
            overdue = self.db.query("""SELECT SystemName, NextReviewDate FROM ref_gxp_system WHERE GxpImpact <> 'None'
                                       AND NextReviewDate < :d ORDER BY NextReviewDate""", {"d": self.eng.as_of()})["rows"]
            if overdue and p.can("compliance"):
                name, d = overdue[0]
                late = (self.eng.as_of() - dt.date.fromisoformat(str(d)[:10])).days
                mod = "board" if p.can("board") else "compliance"
                sec = "Inspection readiness" if mod == "board" else "Checks"
                s.append(f"The validation review for {b.f(name, 'GxP system with an overdue review', mod, sec, hl=name)} is "
                         f"{b.f(plural(late, 'day'), 'Days past the review date', mod, sec, hl=name)} overdue.")
        b.section("gxp", "GxP and patient data", "bad", "Needs attention", s)

    def _finding(self, b: Brief, x, text, label) -> str:
        """A compliance finding: its row on GxP & Patient Data when the role can open it, else its use case page
        in the catalog (open to every role), which runs the check and shows the rows behind it."""
        if x.get("module") == "compliance" and b.p.can("compliance"):
            return b.f(text, label, "compliance", "Checks", hl=x["id"])
        page, path = PAGES["catalog"]
        key = (page, x["id"])
        if key not in b.sources:
            b.sources[key] = {"n": len(b.sources) + 1, "page": page, "section": f"{x['id']} {x['title']}", "href": f"{path}/{x['id']}"}
        fid = f"f{len(b.facts) + 1}"
        b.facts[fid] = {"text": str(text), "label": label, "href": f"{path}/{x['id']}", "source": b.sources[key]["n"]}
        return f"⟦{fid}⟧"

    # ------------------------------------------------------------------ R&D credit and the savings pipeline
    def money_on_table(self, b: Brief):
        p = b.p
        s = []
        if p.scope is None and (p.can("tax") or (p.can("risk") and p.can("command"))):
            cu = self.prudence.credit_unlock(None)
            mod, sec, extra = ("tax", "Credit unlock", {"tab": "credit"}) if p.can("tax") else ("command", "R&D credit unlock", {})
            elig = sorted([j for j in cu["jurisdictions"] if j["CreditStatus"] == "eligible"], key=lambda j: -j["Credit"])
            if cu["credit_unlock"]:
                amt = b.f(f"{money(cu['credit_unlock'])} a year", "R&D credit eligible but not claimed", mod, sec, **extra)
                line = f"Claiming eligible cloud and SaaS fees would add {amt} of R&D credit"
                if elig:
                    j = elig[0]
                    line += (f", {b.f(money(j['Credit']), j['Jurisdiction'] + ' credit', mod, sec, hl=j['Jurisdiction'], **extra)} of it in "
                             f"{b.f(j['Jurisdiction'], 'Jurisdiction', mod, sec, hl=j['Jurisdiction'], **extra)}")
                    b.act("credit", f"Claim the {j['Jurisdiction']} R&D credit",
                          f"Eligible recurring cloud and SaaS fees not yet claimed{' in ' + j['FilingId'] if j.get('FilingId') else ''}.",
                          f"{money(j['Credit'])}/yr", b.href(mod, hl=j["Jurisdiction"] if mod == "tax" else None, sec=None if mod == "tax" else sec, **extra),
                          float(j["Credit"]))
                line += f", on top of {b.f(money(cu['credit_claimed']) + ' a year', 'R&D credit claimed today', mod, 'Claimed today' if mod == 'tax' else sec, **extra)} claimed today."
                s.append(line)
                b.lead["credit"] = f"{amt} of R&D credit is unclaimed"
        if p.can("optimization"):
            sm = self.optimizer.summary(p.scope)
            st = {x["Stage"]: x for x in sm["stages"]}
            if st["Identified"]["Actions"]:
                ident = b.f(f"{money(st['Identified']['MonthlySavings'])} a month", "Savings identified", "optimization", "Savings lifecycle (monthly)")
                n = b.f(plural(st["Identified"]["Actions"], "action"), "Actions in the tracker", "optimization", "Backlog")
                ap = b.f(money(st["Approved"]["MonthlySavings"]), "Approved", "optimization", "Savings lifecycle (monthly)")
                ver = b.f(money(st["Verified"]["MonthlySavings"]), "Verified (measured)", "optimization", "Savings lifecycle (monthly)")
                rate = 100 * st["Verified"]["MonthlySavings"] / st["Identified"]["MonthlySavings"] if st["Identified"]["MonthlySavings"] else 0
                s.append(f"The savings pipeline holds {ident} across {n}; {ap} is approved and {ver} verified, "
                         f"so {b.f(pct(rate), 'Share of identified savings verified', 'optimization', 'Savings lifecycle (monthly)')} has reached the bill.")
                waiting = st["Identified"]["MonthlySavings"] - st["Approved"]["MonthlySavings"]
                b.lead.setdefault("savings", f"{b.f(money(waiting) + ' a month', 'Savings identified but not approved', 'optimization', 'Savings lifecycle (monthly)')} of savings awaits approval")
                open_acts = sorted([a for a in self.optimizer.actions(p.scope) if a["Status"] in ("identified", "approved")],
                                   key=lambda a: -float(a["EstimatedMonthlySavings"] or 0))
                taken = {a["title"].split(" ")[-1] for a in b.actions if a["kind"] == "optimization"}
                for a in open_acts[:2]:
                    oid = a["OptimizationId"]
                    if a is open_acts[0]:
                        s.append(f"The largest open action is {b.f(oid, a['Title'], 'optimization', 'Backlog', focus=oid)}, "
                                 f"{a['Title'][0].lower() + a['Title'][1:]} ({a['CostCenter']}), worth "
                                 f"{b.f(money(a['EstimatedMonthlySavings']) + ' a month', 'Estimated monthly saving', 'optimization', 'Backlog', focus=oid)}.")
                    if oid not in taken:
                        b.act("optimization", f"{NEXT[a['Status']]} {oid}", f"{a['Title']} ({a['CostCenter']}, {a['LeverLabel']}).",
                              f"{money(a['EstimatedMonthlySavings'])}/mo", b.href("optimization", focus=oid), 12 * float(a["EstimatedMonthlySavings"] or 0))
        b.section("money", "Money on the table", "good", "Opportunity", s)

    # ------------------------------------------------------------------ alerts, anomalies, invoice exceptions
    def operations(self, b: Brief):
        p = b.p
        s, urgent = [], False
        ev = self.alerts.events()["events"]
        if p.scope is not None:
            ev = [e for e in ev if e["Subject"] in set(p.scope)]
        open_ev = [e for e in ev if not e["Acknowledged"]]
        if open_ev:
            high = [e for e in open_ev if e["Severity"] == "high"]
            n = b.f(plural(len(open_ev), "alert"), "Unacknowledged alerts", "alerts", "Fired alerts")
            line = f"{n} {'is' if len(open_ev) == 1 else 'are'} unacknowledged"
            if high:
                urgent = True
                line += (f", {b.f(str(len(high)) + ' high severity', 'High-severity alerts', 'alerts', 'Fired alerts')}; the first is "
                         f"{b.f(high[0]['Message'], high[0]['RuleName'], 'alerts', 'Fired alerts', hl=high[0]['Message'])}")
                b.act("alerts", f"Review {plural(len(high), 'high-severity alert')}", high[0]["Message"], plural(len(open_ev), "open alert"),
                      b.href("alerts", hl=high[0]["Message"]), 0)
            s.append(line + ".")
        if p.can("anomalies"):
            inc = sorted([x for x in self.eng.findings(p.scope) if x["kind"] == "incurred"], key=lambda x: -x["exposure"])
            if inc:
                x = inc[0]
                tot = b.f(money(sum(i["exposure"] for i in inc)), "Already spent on waste", "anomalies", "Findings")
                nf = b.f(plural(len(inc), "finding"), "Findings of money already spent", "anomalies", "Findings")
                s.append(f"{tot} has already been spent on waste across {nf}; the largest is "
                         f"{b.f(x['id'], x['title'] + ' (' + x['detail'] + ')', 'anomalies', 'Findings', hl=x['id'])}, {x['title'][0].lower() + x['title'][1:]} "
                         f"({b.f(money(x['exposure']), 'Amount already spent', 'anomalies', 'Findings', hl=x['id'])}; {x['detail']}).")
        if p.scope is None and p.can("reconciliation"):
            rec = self.eng.reconciliation()
            exc = sorted([l for l in rec["lines"] if l["Status"] == "exception" and l["IssueStatus"] == "Issued"], key=lambda l: -abs(l["Difference"] or 0))
            if exc:
                urgent = True
                l = exc[0]
                n = b.f(plural(len(exc), "invoice line"), "Invoice lines that do not tie out", "reconciliation", "Invoice lines")
                inv = b.f(l["InvoiceDetailId"], f"{l['Issuer']} invoice, {l['Period']}", "reconciliation", "Invoice lines", hl=l["InvoiceDetailId"])
                d = b.f(money(l["Difference"]), "Difference between invoice and charges", "reconciliation", "Invoice lines", hl=l["InvoiceDetailId"])
                s.append(f"{n} {'does' if len(exc) == 1 else 'do'} not tie out to charges, the largest {inv} ({l['Issuer']}) by {d}: {l['Reason'].lower()}.")
                b.act("reconciliation", f"Resolve {l['InvoiceDetailId']}", f"{l['Issuer']}, {l['Period']}: {l['Reason'].lower()}.",
                      money(l["Difference"]), b.href("reconciliation", hl=l["InvoiceDetailId"]), abs(float(l["Difference"] or 0)))
        b.section("ops", "Operations", "warn" if urgent else "info", "Action needed" if urgent else "Monitor", s)

    # ------------------------------------------------------------------ assembly
    def _assemble(self, b: Brief) -> dict:
        lead = b.lead
        order = ("risk", "compliance", "credit", "savings") if b.p.can("compliance") and not b.p.can("tax") else ("risk", "credit", "compliance", "savings")
        parts = [lead[k] for k in order if k in lead][:2]
        if "spend" in lead:
            joiner = " and " if lead.get("spend_over") else ", but "
            headline = lead["spend"] + (joiner + " and ".join(parts) if parts else "") + "."
            if "window" in lead:
                headline = lead["window"] + ". " + headline
        elif parts:
            headline = parts[0][0].upper() + parts[0][1:] + (", and " + parts[1] if len(parts) > 1 else "") + "."
        else:
            headline = "Nothing in your scope needs attention today."
        actions = sorted(b.actions, key=lambda a: -a["weight"])[:4]
        for a in actions:
            a.pop("weight", None)
        return {
            "as_of": self.eng.as_of().isoformat(),
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "writer": "costiq", "model": None, "note": None,
            "headline": headline, "sections": b.sections, "actions": actions,
            "facts": b.facts, "sources": sorted(b.sources.values(), key=lambda s: s["n"]),
        }

    # ------------------------------------------------------------------ guard for written prose
    @staticmethod
    def _check(draft: dict, out: dict) -> str | None:
        """Every citation must be a known fact, and no digit may appear outside a citation unless it is part of a
        name or identifier in the facts (filing ids, action ids). Used on saved prose before it replaces the costiq text."""
        known = out["facts"]
        allowed = json.dumps([f["label"] for f in known.values()] + [f["text"] for f in known.values()], ensure_ascii=False)
        texts = [draft["headline"]] + [t for s in draft["sections"] for t in s["sentences"]]
        if {s["key"] for s in draft["sections"]} - {s["key"] for s in out["sections"]}:
            return "unknown section"
        for t in texts:
            for fid in CITE.findall(t):
                if fid not in known:
                    return f"unknown citation {fid}"
            bare = CITE.sub(" ", t)
            for tok in re.findall(r"[\w$€£.,%+\-]*\d[\w$€£.,%+\-]*", bare):
                tok = tok.strip(".,")
                if tok and tok not in allowed:
                    return f"uncited figure {tok}"
        return None


# ---------------------------------------------------------------------------------------- saved responses
RESPONSES = os.path.join(os.path.dirname(__file__), "..", "data", "summary_responses.json")
_responses: dict | None = None


def saved_responses() -> dict:
    """Saved prose by response key (persona:range). Loaded once; a missing or unreadable file means none."""
    global _responses
    if _responses is None:
        try:
            with open(RESPONSES, encoding="utf-8") as fh:
                _responses = json.load(fh)["responses"]
        except (OSError, ValueError, KeyError) as e:
            log.warning("saved summaries not loaded: %s", e)
            _responses = {}
    return _responses


def response_key(p, rng: str) -> str:
    return f"{'+'.join(p.personas)}:{rng}"


def fact_key(f: dict) -> str:
    """A fact's identity across builds: what it means, its figure and where it links (ids are only positions)."""
    return f"{f['label']}|{f['text']}|{f['href']}"


def basis(out: dict) -> dict:
    """For the headline and each section: a hash of the facts its costiq text cites. Saved prose for a part is used
    only while this hash is the one it was written against."""
    def h(texts):
        keys = sorted({fact_key(out["facts"][fid]) for t in texts for fid in CITE.findall(t) if fid in out["facts"]})
        return hashlib.sha256("\n".join(keys).encode()).hexdigest()[:16]
    return {"headline": h([out["headline"]]), **{s["key"]: h(s["sentences"]) for s in out["sections"]}}


def apply_saved(out: dict, saved: dict) -> list[str]:
    """Replace the costiq text with the saved text, part by part, where the part's facts are unchanged. Citations in
    saved text are re-pointed to the current fact ids. Returns the parts that use saved text."""
    now = basis(out)
    by_key = {fact_key(f): fid for fid, f in out["facts"].items()}

    def remap(text):
        ids = {fid: by_key.get(saved["facts"].get(fid, "")) for fid in CITE.findall(text)}
        if not all(ids.values()):
            return None
        return CITE.sub(lambda m: f"⟦{ids[m.group(1)]}⟧", text)

    headline, sections, used = out["headline"], {}, []
    for part, text in [("headline", [saved.get("headline")])] + list(saved.get("sections", {}).items()):
        new = [remap(t) for t in text if t]
        if new and all(new) and saved.get("basis", {}).get(part) == now.get(part):
            if part == "headline":
                headline = new[0]
            else:
                sections[part] = new
            used.append(part)
    draft = {"headline": headline, "sections": [{"key": k, "sentences": v} for k, v in sections.items()]}
    problem = Summarizer._check(draft, out)
    if problem:
        log.warning("saved summary %s not used: %s", saved.get("key"), problem)
        return []
    if used:
        out["headline"] = headline
        for s in out["sections"]:
            s["sentences"] = sections.get(s["key"], s["sentences"])
        out["writer"] = "saved"
    return used
