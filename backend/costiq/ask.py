"""Ask CostIQ: deterministic question answering, no language model.

A question is matched to one of the registry use cases (BM25 over title, business question,
category, keywords and description), parameters are pulled out of the text (periods, providers,
cost centers), and the use case is run in the caller's scope. The answer is the use case's own
result with its formula. Nothing is generated: if no use case scores above the threshold, CostIQ
says so and offers the closest ones. Question text is never placed into SQL.
"""
from __future__ import annotations
import calendar, datetime as dt, math, re
from collections import Counter

STOP = set("""a an the of for to in on by and or is are was were be been what which who whom how why when where do does did
our we my me i us show tell give list find much many there their them this that these those with from at as it its per
can could should would vs versus across each every all any about into than then over under within please""".split())
SYN = {
    "overspend": "over budget", "overspending": "over budget", "overrun": "over budget", "spending": "spend",
    "costs": "cost", "expensive": "cost", "bill": "billed", "invoices": "invoice", "licences": "licenses",
    "licence": "license", "seat": "seats", "idle": "idle unused", "unused": "unused idle", "llm": "model",
    "models": "model", "tokens": "token", "gpt": "openai model", "claude": "anthropic claude", "copilot": "copilot",
    "savings": "savings saving", "commitments": "commitment", "reservations": "reservation reserved",
    "anomalies": "anomaly spike", "spikes": "spike anomaly", "forecasted": "forecast", "projection": "forecast",
    "trend": "month over month trend", "increasing": "increase month over month", "growing": "increase month over month",
    # utilities
    "ferc": "ferc account", "usoa": "ferc account", "rates": "rate case", "ratecase": "rate case", "docket": "rate case docket",
    "exhibit": "rate case exhibit bridge", "testyear": "test year", "opco": "operating company entity",
    "opcos": "operating company entity", "affiliate": "affiliate service company", "affiliates": "affiliate service company",
    "subsidy": "cross subsidy", "subsidization": "cross subsidy", "form60": "form 60 service company",
    "bcsi": "bcsi cip", "cip": "cip nerc", "nerc": "nerc cip", "ceii": "ceii residency", "storm": "storm event",
    "storms": "storm event", "hurricane": "storm event", "deferral": "deferral regulatory asset",
    "deferred": "deferral regulatory asset", "regulatory": "regulatory", "escalator": "escalator renewal",
    "escalators": "escalator renewal", "renewals": "renewal", "meter": "meter ami", "meters": "meter ami",
}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
PROVIDERS = {"aws": "AWS", "amazon": "AWS", "azure": "Microsoft", "microsoft": "Microsoft", "gcp": "Google Cloud",
             "google": "Google Cloud", "anthropic": "Anthropic", "github": "GitHub", "snowflake": "Snowflake"}


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in words:
        if w in STOP:
            continue
        out += SYN.get(w, w).split()
    return [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in out]


class AskEngine:
    def __init__(self, registry, engines):
        self.reg, self.eng = registry, engines
        self.docs = {}
        for uid in registry.order:
            uc = registry.get(uid)
            text = " ".join([uc["title"]] * 2 + [uc["business_question"]] * 3 + [uc["category"]] +
                            [" ".join(uc.get("keywords", []))] * 3 + [uc.get("description", "")[:400]])
            self.docs[uid] = Counter(tokens(text))
        self.N = len(self.docs)
        self.avgdl = sum(sum(d.values()) for d in self.docs.values()) / self.N
        self.df = Counter(t for d in self.docs.values() for t in d)

    def _score(self, q: list[str], uid: str) -> float:
        d, dl, s = self.docs[uid], sum(self.docs[uid].values()), 0.0
        for t in set(q):
            if t not in d:
                continue
            idf = math.log(1 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
            tf = d[t]
            s += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * dl / self.avgdl))
        return s

    def extract(self, question: str):
        q = question.lower()
        asof = self.eng.as_of()
        params, notes = {}, []
        m = re.search(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b(?:\s+(20\d\d))?", q)
        if "last month" in q or "previous month" in q:
            first = dt.date(asof.year, asof.month, 1)
            start = dt.date(first.year - (first.month == 1), (first.month - 2) % 12 + 1, 1)
            params.update(period_start=start, period_end=first); notes.append(f"period {start:%B %Y}")
        elif "this month" in q or "month to date" in q or "mtd" in q.split():
            start = dt.date(asof.year, asof.month, 1)
            params.update(period_start=start, period_end=dt.date(start.year + (start.month == 12), start.month % 12 + 1, 1))
            notes.append(f"period {start:%B %Y} (month to date)")
        elif re.search(r"\bq([1-4])\b", q):
            qn = int(re.search(r"\bq([1-4])\b", q).group(1))
            start = dt.date(asof.year, 3 * qn - 2, 1)
            params.update(period_start=start, period_end=dt.date(asof.year + (qn == 4), (3 * qn) % 12 + 1, 1))
            notes.append(f"period Q{qn} {asof.year}")
        elif m and m.group(1) not in ("may",) or (m and re.search(r"\bmay\s+20\d\d", q)):
            mon = MONTHS[m.group(1)]
            yr = int(m.group(2)) if m.group(2) else (asof.year if mon <= asof.month else asof.year - 1)
            start = dt.date(yr, mon, 1)
            params.update(period_start=start, period_end=dt.date(yr + (mon == 12), mon % 12 + 1, 1))
            notes.append(f"period {start:%B %Y}")
        elif "ytd" in q.split() or "year to date" in q:
            params.update(period_start=dt.date(asof.year, 1, 1), period_end=asof + dt.timedelta(1))
            notes.append(f"period year to date {asof.year}")
        for w, prov in PROVIDERS.items():
            if re.search(rf"\b{w}\b", q):
                params["provider"] = prov
                notes.append(f"provider {prov}")
                break
        return params, notes

    def ask(self, question: str, principal, run):
        q = tokens(question)
        if not q:
            return {"status": "no_match", "message": "Ask a question about cost, usage, budgets, commitments, AI or invoices."}
        allowed = [u for u in self.reg.order if principal.can(self.reg.get(u)["module"])]
        scored = sorted(((self._score(q, u), u) for u in allowed), reverse=True)
        if not scored:
            return {"status": "no_match", "message": "Your role cannot open any use cases to answer this."}
        best, uid = scored[0]
        alts = [{"id": u, "title": self.reg.get(u)["title"], "business_question": self.reg.get(u)["business_question"]}
                for s, u in scored[1:4] if s > 0]
        if best < 2.5:
            return {"status": "no_match", "message": "I couldn't match that question to a CostIQ use case with confidence.",
                    "suggestions": [{"id": u, "title": self.reg.get(u)["title"],
                                     "business_question": self.reg.get(u)["business_question"]} for s, u in scored[:4] if s > 0]}
        uc = self.reg.get(uid)
        params, notes = self.extract(question)
        names = {p["name"] for p in uc.get("params", [])}
        given = {k: (dt.datetime.combine(v, dt.time()) if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else v)
                 for k, v in params.items() if k in names}
        used = [n for n in notes if any(n.startswith(k.split("_")[0]) for k in given)] or (
            ["parameters: use-case defaults"] if names else ["whole data window"])
        result = run(uc, given)
        rest = {k: v for k, v in params.items() if k not in given}
        if rest:
            used = [u for u in used if not u.startswith("parameters") and not u.startswith("whole")]
            used += self.post_filter(result, rest, notes)
        headline = self.headline(uc, result)
        if uc.get("compliance"):
            try:
                n, bad, amt = self.eng.compliance_score(uc, principal.scope, given)
                meta = uc["compliance"]
                headline = (f"{bad} of {n} {meta.get('unit', 'items')} flagged ({meta['reference']})"
                            + (f"; ${amt:,.2f} involved" if meta.get("amount") and bad else "") + "."
                            if bad else f"No issues: all {n} {meta.get('unit', 'items')} pass ({meta['reference']}).")
            except Exception:
                pass
        return {"status": "answered", "use_case": {"id": uid, "title": uc["title"], "module": uc["module"],
                                                   "business_question": uc["business_question"]},
                "confidence": "high" if best > 2 * ((scored[1][0] if len(scored) > 1 else 0) or 1) else "medium",
                "interpreted": used, "headline": headline, "result": result,
                "formula": uc.get("description", ""), "sources": uc.get("datasets", []), "alternatives": alts}

    @staticmethod
    def post_filter(result, params, notes):
        """Use cases without period/provider parameters still answer for the asked period/provider:
        rows are filtered on the result's month and provider columns."""
        cols, data = result["columns"], result["rows"]
        applied = []
        if "period_start" in params:
            mi = next((i for i, c in enumerate(cols) if re.search(r"month$|^month|period", c, re.I)
                       and data and isinstance(data[0][i], str) and re.match(r"\d{4}-\d{2}", data[0][i])), None)
            if mi is not None:
                lo, hi = params["period_start"].isoformat()[:7], params["period_end"].isoformat()[:7]
                data = [r for r in data if lo <= r[mi][:7] < hi]
                applied.append(f"filtered to {lo}" + (f"..{hi}" if hi != lo else ""))
        if "provider" in params:
            pi = next((i for i, c in enumerate(cols) if re.search(r"provider", c, re.I)), None)
            if pi is not None:
                data = [r for r in data if r[pi] == params["provider"]]
                applied.append(f"filtered to provider {params['provider']}")
        result["rows"] = data
        return applied

    @staticmethod
    def headline(uc, result):
        cols, data = result["columns"], result["rows"]
        if not data:
            return "No rows matched in your scope for this period."
        num = [i for i, c in enumerate(cols) if all(isinstance(r[i], (int, float)) or r[i] is None for r in data)
               and any(isinstance(r[i], (int, float)) for r in data) and not re.search(r"^(seq|year)$|id$", c, re.I)]
        txt = [i for i, c in enumerate(cols) if i not in num and not re.search(r"hash|key|id$", c, re.I)]
        si = next((i for i, c in enumerate(cols) if c.lower() in ("status", "finding")), None)
        if si is not None and len(data) > 1:
            flagged = [r for r in data if str(r[si]).lower() not in ("ok", "on track", "under", "within", "matched", "none")]
            names = ", ".join(str(r[txt[0]]) for r in flagged[:5]) if txt else ""
            return (f"{len(flagged)} of {len(data)} flagged ({', '.join(sorted({str(r[si]) for r in flagged}))})"
                    + (f": {names}" if names else "") + ".") if flagged else f"All {len(data)} rows are within policy."
        prio = [r"variance|overage|exposure|savings|shortfall", r"forecast|projected(?!pct)", r"effective", r"total.*cost|billed|cost|spend|amount"]
        mi = next((i for pat in prio for i in num if re.search(pat, cols[i], re.I) and not re.search(r"pct|rate|ratio", cols[i], re.I)),
                  num[-1] if num else None)
        if mi is None:
            return f"{len(data)} rows. First: " + ", ".join(str(v) for v in data[0][:3])
        money = bool(re.search(r"cost|spend|billed|amount|savings|variance|exposure|overage|shortfall|forecast|projected|budget", cols[mi], re.I)) \
            and not re.search(r"pct|rate|ratio|percent", cols[mi], re.I)
        f = (lambda v: f"${v:,.2f}") if money else (lambda v: f"{v:,.4g}")
        top = max(data, key=lambda r: abs(r[mi] or 0))
        label = " · ".join(str(top[i]) for i in txt[:2] if top[i] is not None)
        if len(data) == 1:
            return f"{cols[mi]}: {f(top[mi] or 0)}" + (f" ({label})" if label else "")
        total = sum(r[mi] or 0 for r in data)
        return f"{len(data)} rows. Largest {cols[mi]}: {label or 'row'} at {f(top[mi] or 0)}. Total: {f(total)}."
