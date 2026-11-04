"""Pooling-rule proposals with maker-checker: one person proposes a rule that puts unallocated charges on a cost center
(e.g. an untagged sandbox account), someone else approves it, and the allocation is restated from the rule's effective
date. Fixing a tag at the provider does not fix history, so history is restated here, through an approved rule.

Storage is the decision ledger (no new table): a proposal is a `submit` entry on `pooling_rule:<ProposalId>` whose
detail holds the rule as JSON; `approve` or `return` entries on the same target decide it. Approval inserts the rule
into allocation_rule (approved, effective-dated), re-runs the allocator for every period from EffectiveFrom and
refreshes the prudence evidence.
"""
from __future__ import annotations
import datetime as dt, json
from fastapi import HTTPException

PREFIX = "pooling_rule:"
MATCH_COLUMNS = ("SubAccountId", "ServiceName", "ChargeCategory", "x_WorkspaceName", "ServiceProviderName")
PRIORITY = 50  # after the seeded rules (10-30)


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def _iso(v):
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else (str(v).replace(" ", "T") if v else None)


class PoolingRules:
    def __init__(self, db, eng, ledger):
        self.db, self.eng, self.ledger = db, eng, ledger

    # ------------------------------------------------------------------ read side
    def proposals(self) -> list[dict]:
        out: dict[str, dict] = {}
        for e in self.ledger.entries():
            t = str(e["Target"] or "")
            if not t.startswith(PREFIX):
                continue
            pid = t[len(PREFIX):]
            try:
                d = json.loads(e["Detail"] or "{}")
            except ValueError:
                d = {}
            if e["Action"] == "submit":
                out[pid] = {"ProposalId": pid, "Name": d.get("Name"), "MatchColumn": d.get("MatchColumn"),
                            "MatchValue": d.get("MatchValue"), "CostCenter": d.get("CostCenter"),
                            "EffectiveFrom": d.get("EffectiveFrom"), "Status": "submitted", "SubmittedBy": e["Actor"],
                            "SubmittedRole": d.get("Role") or e["Persona"], "SubmittedAt": _iso(e["EntryAt"]),
                            "DecidedBy": None, "DecidedRole": None, "DecidedAt": None, "RuleId": None, "Note": d.get("Note")}
            elif pid in out and e["Action"] in ("approve", "return"):
                out[pid].update(Status="approved" if e["Action"] == "approve" else "returned", DecidedBy=e["Actor"],
                                DecidedRole=d.get("Role") or e["Persona"], DecidedAt=_iso(e["EntryAt"]), RuleId=d.get("RuleId"))
        return sorted(out.values(), key=lambda x: x["ProposalId"], reverse=True)

    def get(self, pid: str) -> dict:
        p = next((x for x in self.proposals() if x["ProposalId"] == pid), None)
        if p is None:
            raise HTTPException(404, "Unknown proposal")
        return p

    # ------------------------------------------------------------------ maker
    def submit(self, user, persona, role, body: dict) -> dict:
        col = body.get("MatchColumn")
        if col not in MATCH_COLUMNS:
            raise HTTPException(400, f"MatchColumn must be one of {', '.join(MATCH_COLUMNS)}")
        value = str(body.get("MatchValue") or "").strip()
        center = str(body.get("CostCenter") or "").strip()
        if not value or not center:
            raise HTTPException(400, "MatchValue and CostCenter are required")
        known = {r[0] for r in self.db.query("SELECT CostCenter FROM ref_cost_center", limit=None)["rows"]}
        if center not in known or center == "Unallocated":
            raise HTTPException(400, f"Unknown cost center {center}")
        hits = self.db.query(f"SELECT COUNT(*) FROM CostAndUsage WHERE {col} = :v", {"v": value})["rows"][0][0]
        if not hits:
            raise HTTPException(400, f"No charges have {col} = {value}")
        if any(p["Status"] == "submitted" and p["MatchColumn"] == col and p["MatchValue"] == value for p in self.proposals()):
            raise HTTPException(409, f"A proposal for {col} = {value} is already awaiting approval")
        first = self.eng.first_period()
        eff = body.get("EffectiveFrom") or (first.date().isoformat() if first else _now().date().replace(day=1).isoformat())
        try:
            eff = dt.date.fromisoformat(str(eff)[:10]).isoformat()
        except ValueError:
            raise HTTPException(400, "EffectiveFrom must be YYYY-MM-DD") from None
        seq = 1 + sum(1 for e in self.ledger.entries() if str(e["Target"] or "").startswith(PREFIX) and e["Action"] == "submit")
        pid = f"PR-{seq:03d}"
        name = str(body.get("Name") or "").strip() or f"{value} to {center}"
        detail = {"Name": name, "MatchColumn": col, "MatchValue": value, "CostCenter": center, "EffectiveFrom": eff,
                  "Role": role, "Note": str(body.get("Note") or "")[:500] or None}
        entry = self.ledger.append(user, persona, "submit", PREFIX + pid, json.dumps(detail, sort_keys=True))
        return {"proposal": self.get(pid), "ledger": entry}

    # ------------------------------------------------------------------ checker
    def decide(self, user, persona, role, pid: str, action: str, can_approve: bool) -> dict:
        if action not in ("approve", "return"):
            raise HTTPException(400, "action must be approve or return")
        prop = self.get(pid)
        if prop["Status"] != "submitted":
            raise HTTPException(409, f"{pid} is {prop['Status']}, not awaiting approval")
        if prop["SubmittedBy"] == user:
            raise HTTPException(409, "Separation of duties: you submitted this rule, so someone else must approve it")
        if not can_approve:
            raise HTTPException(403, "Your role is not permitted to rule.approve")
        if action == "return":
            entry = self.ledger.append(user, persona, "return", PREFIX + pid, json.dumps({"Role": role}, sort_keys=True))
            return {"proposal": self.get(pid), "rule": None, "reallocated": {}, "ledger": entry}
        from .allocation import Allocator
        from .prudence import PrudenceEngine
        rule_id = f"R-{pid}"
        eff = dt.datetime.fromisoformat(prop["EffectiveFrom"])
        self.db.query("""INSERT INTO allocation_rule (RuleId, Version, Name, Priority, MatchColumn, MatchValue, CostCenter,
                                                      EffectiveFrom, EffectiveTo, ApprovedBy, ApprovedAt)
                         VALUES (:r, 1, :n, :p, :c, :v, :cc, :f, NULL, :a, :at)""",
                      {"r": rule_id, "n": prop["Name"], "p": PRIORITY, "c": prop["MatchColumn"], "v": prop["MatchValue"],
                       "cc": prop["CostCenter"], "f": eff, "a": role or user, "at": _now()}, limit=None)
        alloc = Allocator(self.db, self.eng)
        try:  # dry run on a period with no charges: a failing allocator must not delete a period's ledger rows
            alloc.run(dt.datetime(1900, 1, 1))
        except Exception as e:  # noqa: BLE001
            self.db.query("DELETE FROM allocation_rule WHERE RuleId = :r", {"r": rule_id}, limit=None)
            raise HTTPException(500, f"Reallocation failed, rule not applied: {str(e)[:200]}") from None
        rerun = {}
        for ps in alloc.periods():
            p = ps if isinstance(ps, dt.datetime) else dt.datetime.fromisoformat(str(ps))
            if p >= eff.replace(day=1):
                rerun.update(alloc.run(p))
        PrudenceEngine(self.db, self.eng).run()
        entry = self.ledger.append(user, persona, "approve", PREFIX + pid,
                                   json.dumps({"Role": role, "RuleId": rule_id, "reallocated": rerun}, sort_keys=True))
        rule = next((r for r in self.eng.allocation_rules() if r["RuleId"] == rule_id), None)
        return {"proposal": self.get(pid), "rule": rule, "reallocated": rerun, "ledger": entry}
