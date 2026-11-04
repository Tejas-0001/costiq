"""Append-only, hash-chained decision ledger. Each entry's hash covers the previous hash, so editing
or deleting any entry breaks verification from that point on."""
from __future__ import annotations
import datetime as dt, hashlib, json


def _hash(prev: str, at: str, actor: str, persona: str, action: str, target: str, detail: str) -> str:
    payload = json.dumps([prev, at, actor, persona, action, target, detail], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class Ledger:
    def __init__(self, db):
        self.db = db

    def entries(self):
        r = self.db.query("SELECT Seq, EntryAt, Actor, Persona, Action, Target, Detail, PrevHash, Hash "
                          "FROM decision_ledger ORDER BY Seq", limit=None)
        return [dict(zip(r["columns"], row)) for row in r["rows"]]

    def append(self, actor, persona, action, target, detail=""):
        last = self.db.query("SELECT Seq, Hash FROM decision_ledger ORDER BY Seq DESC", limit=1)["rows"]
        seq, prev = (last[0][0] + 1, last[0][1]) if last else (1, "0" * 64)
        at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
        h = _hash(prev, at.isoformat(), actor, persona, action, target, detail)
        self.db.query("INSERT INTO decision_ledger VALUES (:s, :at, :ac, :p, :a, :t, :d, :ph, :h)",
                      dict(s=seq, at=at, ac=actor, p=persona, a=action, t=target, d=detail, ph=prev, h=h), limit=None)
        return {"Seq": seq, "Hash": h}

    def verify(self):
        prev = "0" * 64
        for e in self.entries():
            at = e["EntryAt"] if isinstance(e["EntryAt"], str) else e["EntryAt"].isoformat()
            at = at.replace(" ", "T")
            if e["PrevHash"] != prev or _hash(prev, at, e["Actor"], e["Persona"], e["Action"], e["Target"],
                                              e["Detail"] or "") != e["Hash"]:
                return {"verified": False, "broken_at": e["Seq"]}
            prev = e["Hash"]
        return {"verified": True, "entries": len(self.entries())}

    def reset(self, actor="system"):
        self.db.query("DELETE FROM decision_ledger", limit=None)
        self.append(actor, "system", "seed", "tenant:default", "Ledger initialised")
