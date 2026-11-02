"""Cost alerts: evaluate cost_alert_rule against current data and record cost_alert_event rows.

Events are idempotent (the id is a hash of rule, subject and period), so re-evaluating keeps acknowledgements.
A webhook (e.g. a Microsoft Teams incoming webhook) is called for new events only when COSTIQ_ALERT_WEBHOOK_URL is
set; by default nothing leaves the client's boundary.
"""
from __future__ import annotations
import datetime as dt, hashlib, json, logging, os

log = logging.getLogger("costiq.alerts")


def rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


class Alerts:
    def __init__(self, db, engines):
        self.db, self.eng = db, engines

    def rules(self):
        return rows(self.db.query("SELECT * FROM cost_alert_rule WHERE Enabled ORDER BY RuleId", {}, None, limit=None))

    # ------------------------------------------------------------------ rule kinds -> candidate events
    def _budget_threshold(self, rule):
        k = self.eng.kpis(None)
        for u in k["units"]:
            if u["Budget"] and u["Forecast"] > u["Budget"] * float(rule["ThresholdPct"]) / 100:
                yield (u["CostCenter"], k["open_month"], "high" if (u["VariancePct"] or 0) > 5 else "medium",
                       f"{u['CostCenter']} forecast ${u['Forecast']:,.0f} vs budget ${u['Budget']:,.0f} for {k['open_month']}",
                       u["Variance"])

    def _token_budget(self, rule):
        for r in self.eng._uc("BF-04", None):
            if (r["BurnPct"] or 0) >= float(rule["ThresholdPct"]) and r["Status"] in ("Exhausted", "Will run out this month"):
                yield (r["CostCenter"], self.eng.open_month()[0].isoformat()[:7], "high" if r["Status"] == "Exhausted" else "medium",
                       f"{r['CostCenter']} AI budget {r['BurnPct']}% used ({r['Status'].lower()}"
                       + (f", runs out {r['ProjectedExhaustion']}" if r["Status"] == "Will run out this month" else "") + ")",
                       r["MonthToDate"])

    def _anomaly(self, rule):
        for f in self.eng.findings(None):
            if f["id"] in ("AN-01", "F-16", "AN-02"):
                yield (f["title"], f["detail"][:10], f["severity"], f"{f['title']}: {f['detail']}", f["exposure"])

    def _commitment_expiry(self, rule):
        as_of = self.eng.as_of()
        horizon = as_of + dt.timedelta(days=int(rule["ThresholdPct"]))
        for c in rows(self.db.query("""SELECT ContractCommitmentId, ContractCommitmentDescription, ContractCommitmentPeriodEnd,
                                              ContractCommitmentCost FROM ContractCommitment""", {}, None, limit=None)):
            end = dt.date.fromisoformat(str(c["ContractCommitmentPeriodEnd"])[:10])
            if as_of < end <= horizon:
                yield (c["ContractCommitmentId"], end.isoformat(), "medium",
                       f"{c['ContractCommitmentDescription']} ends {end:%d %b %Y}: decide renewal terms before the date",
                       c["ContractCommitmentCost"])

    def _provisioned_utilization(self, rule):
        for r in self.eng._uc("UO-10", None):
            if (r["AvgUtilizationPct"] or 0) < float(rule["ThresholdPct"]):
                yield (r["Deployment"], self.eng.open_month()[0].isoformat()[:7], "medium",
                       f"{r['Deployment']} averages {r['AvgUtilizationPct']}% utilization; ~${r['MonthlySavingsIfPayg']:,.0f}/month "
                       f"above pay-as-you-go", r["MonthlySavingsIfPayg"])

    KINDS = {"budget_threshold": "_budget_threshold", "token_budget": "_token_budget", "anomaly": "_anomaly",
             "commitment_expiry": "_commitment_expiry", "forecast_overrun": "_provisioned_utilization"}

    # ------------------------------------------------------------------ evaluate / list / acknowledge
    def evaluate(self):
        fired_at = dt.datetime.combine(self.eng.as_of(), dt.time(6, 0))
        new = []
        for rule in self.rules():
            fn = getattr(self, self.KINDS.get(rule["Kind"], ""), None)
            if not fn:
                continue
            try:
                candidates = list(fn(rule))
            except Exception as e:  # noqa: BLE001 - one rule never stops the others
                log.warning("alert rule %s failed: %s", rule["RuleId"], e)
                continue
            for subject, period, severity, message, value in candidates:
                eid = "AE-" + hashlib.sha256(f"{rule['RuleId']}|{subject}|{period}".encode()).hexdigest()[:10].upper()
                if self.db.query("SELECT COUNT(*) FROM cost_alert_event WHERE EventId = :e", {"e": eid})["rows"][0][0]:
                    continue
                self.db.query("""INSERT INTO cost_alert_event (EventId, RuleId, FiredAt, Severity, Subject, Message, Value, Acknowledged)
                                 VALUES (:e, :r, :f, :s, :sub, :m, :v, FALSE)""",
                              dict(e=eid, r=rule["RuleId"], f=fired_at, s=severity, sub=str(subject)[:200], m=message,
                                   v=round(float(value or 0), 2)), limit=None)
                new.append({"EventId": eid, "RuleId": rule["RuleId"], "Severity": severity, "Message": message})
        self._notify(new)
        return new

    def _notify(self, events):
        url = os.environ.get("COSTIQ_ALERT_WEBHOOK_URL")
        if not url or not events:
            return
        import httpx
        try:
            httpx.post(url, json={"text": "\n".join(f"[{e['Severity']}] {e['Message']}" for e in events)}, timeout=10)
        except Exception as e:  # noqa: BLE001
            log.warning("alert webhook failed: %s", e)

    def events(self):
        out = rows(self.db.query("""SELECT e.*, r.Name AS RuleName, r.Kind FROM cost_alert_event e
                                    JOIN cost_alert_rule r ON r.RuleId = e.RuleId
                                    ORDER BY e.Acknowledged, CASE e.Severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                                             e.FiredAt DESC""", {}, None, limit=None))
        return {"events": out, "unacknowledged": sum(1 for e in out if not e["Acknowledged"]), "rules": self.rules()}

    def acknowledge(self, eid, user):
        n = self.db.query("SELECT COUNT(*) FROM cost_alert_event WHERE EventId = :e", {"e": eid})["rows"][0][0]
        if not n:
            from fastapi import HTTPException
            raise HTTPException(404, "Unknown alert")
        self.db.query("""UPDATE cost_alert_event SET Acknowledged = TRUE, AcknowledgedBy = :u, AcknowledgedAt = :t
                         WHERE EventId = :e""", {"u": user, "t": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0),
                                                  "e": eid}, limit=None)
