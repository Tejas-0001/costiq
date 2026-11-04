"""The time window behind the period selector at the top of every page: Today, Yesterday, WTD, MTD, YTD.

Windows end at the data's as-of day (the last billed day), not the calendar day, and each has a comparable prior window
(the day before, the same days of last week or last month; none for year to date). Pages use the window for spend
breakdowns; figures that belong to a fixed period (a filing's review period, a monthly budget) keep that period.
"""
from __future__ import annotations
import datetime as dt
import re
from dataclasses import dataclass

RANGES = ("today", "yesterday", "wtd", "mtd", "ytd")
NAME = {"today": "Today", "yesterday": "Yesterday", "wtd": "Week to date", "mtd": "Month to date", "ytd": "Year to date"}
LONG = {"today": "today", "yesterday": "yesterday", "wtd": "week to date", "mtd": "month to date", "ytd": "year to date"}


def bounds(rng: str, today: dt.date):
    """[start, end] (inclusive) of the window ending at `today`, and the comparable prior window (None for YTD)."""
    d = dt.timedelta(days=1)
    if rng == "today":
        return (today, today), (today - d, today - d)
    if rng == "yesterday":
        y = today - d
        return (y, y), (y - d, y - d)
    if rng == "wtd":
        s = today - dt.timedelta(days=today.weekday())
        return (s, today), (s - dt.timedelta(days=7), today - dt.timedelta(days=7))
    if rng == "mtd":
        s = today.replace(day=1)
        ps = (s - d).replace(day=1)
        return (s, today), (ps, min(ps.replace(day=today.day) if today.day <= 28 else (s - d), s - d))
    s = today.replace(month=1, day=1)
    return (s, today), None


@dataclass(frozen=True)
class Window:
    range: str
    start: dt.date
    end: dt.date  # inclusive
    prev: tuple[dt.date, dt.date] | None

    @property
    def name(self) -> str:
        return NAME[self.range]

    @property
    def long(self) -> str:
        return LONG[self.range]

    @property
    def dates(self) -> str:
        return self.start.isoformat() if self.start == self.end else f"{self.start.isoformat()} to {self.end.isoformat()}"

    @property
    def label(self) -> str:
        return f"{self.name} ({self.dates})"

    def params(self, p: str = "w") -> dict:
        return {f"{p}0": dt.datetime.combine(self.start, dt.time()), f"{p}1": dt.datetime.combine(self.end + dt.timedelta(days=1), dt.time())}

    def pred(self, col: str = "ChargePeriodStart", p: str = "w") -> str:
        return f"{col} >= :{p}0 AND {col} < :{p}1"

    def as_dict(self) -> dict:
        return {"range": self.range, "name": self.name, "long": self.long, "start": self.start.isoformat(),
                "end": self.end.isoformat(), "label": self.label}


def use_case_params(uc: dict, w: Window) -> tuple[dict, str | None]:
    """period_start / period_end for a use case run in window `w`. A use case that filters on the billing period runs
    over the whole billing months the window touches (a day's charges sit in their month's billing period); one that
    filters on charge or usage dates runs over the exact days. Returns ({} , None) when the use case has no period."""
    names = {p["name"] for p in uc.get("params", [])}
    if not {"period_start", "period_end"} <= names:
        return {}, None
    combine = lambda d: dt.datetime.combine(d, dt.time())  # noqa: E731
    if re.search(r"BillingPeriod(Start|End)\s*[<>]=?\s*:period_(start|end)", uc["sql"]):
        s = w.start.replace(day=1)
        e = dt.date(w.end.year + (w.end.month == 12), w.end.month % 12 + 1, 1)
        if re.search(r"BillingPeriodEnd\s*<\s*:period_end", uc["sql"]):
            e += dt.timedelta(days=1)  # the billing period ends on the 1st of the next month; keep that month in
        months = f"{s:%Y-%m}" if (s.year, s.month) == (w.end.year, w.end.month) else f"{s:%Y-%m} to {w.end:%Y-%m}"
        return {"period_start": combine(s), "period_end": combine(e)}, f"billing month {months} (this use case is monthly)"
    # a charge's period ends at midnight after it, so "ChargePeriodEnd < :period_end" needs one more day to keep the last day
    extra = 2 if re.search(r"ChargePeriodEnd\s*<\s*:period_end", uc["sql"]) else 1
    return {"period_start": combine(w.start), "period_end": combine(w.end + dt.timedelta(days=extra))}, None


def window(rng: str | None, today: dt.date) -> Window:
    rng = (rng or "mtd").lower()
    if rng not in RANGES:
        raise ValueError(f"range must be one of {', '.join(RANGES)}")
    (s, e), prev = bounds(rng, today)
    return Window(rng, s, e, prev)
