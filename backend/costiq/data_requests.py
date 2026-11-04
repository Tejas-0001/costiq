"""Data request responder: draft answers to a reviewer's requests on a filing (pharma: tax examiners' information
document requests, IDRs; a utility: commission staff and intervenor data requests). Wording comes from the tenant's copy.

The analyst pastes a request; the deterministic Ask engine (BM25 over the use-case catalog, no LLM, nothing leaves the
boundary) picks the use case that answers it, runs it for the filing, and drafts a response: the table, a narrative
built from the numbers, the evidence to attach and how the figures trace to invoices and approvals. A second person
approves it (separation of duties) and the approval is written to the ledger.
"""
from __future__ import annotations
import datetime as dt, json
from fastapi import HTTPException
from . import tenant


def ledger_target() -> str:
    """Ledger target type for data requests (pack.yaml data_requests.ledger_target; pharma: examiner_request)."""
    return (tenant.pack().get("data_requests") or {}).get("ledger_target", "data_request")


def rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


class DataRequests:
    def __init__(self, db, engines, registry, asker, ledger):
        self.db, self.eng, self.reg, self.asker, self.ledger = db, engines, registry, asker, ledger

    def list(self, filing=None):
        where, p = ("WHERE FilingId = :d", {"d": filing}) if filing else ("", {})
        out = rows(self.db.query(f"SELECT * FROM data_request {where} ORDER BY PreparedAt DESC, RequestId DESC", p, None, limit=None))
        for r in out:
            r["Draft"] = json.loads(r.pop("DraftJson") or "{}")
        return out

    def get(self, rid):
        r = [x for x in self.list() if x["RequestId"] == rid]
        if not r:
            try:
                msg = tenant.text("tax.unknown_request", rid=rid)   # copy/rates.json backend.tax.unknown_request
            except KeyError:
                msg = f"Unknown request {rid}"
            raise HTTPException(404, msg)
        return r[0]

    def _next_id(self, filing):
        n = self.db.query("SELECT COUNT(*) FROM data_request WHERE FilingId = :d", {"d": filing})["rows"][0][0]
        prefix = (tenant.pack().get("data_requests") or {}).get("id_prefix", "REQ")
        return f"{prefix}-{filing}-{n + 1:03d}"

    def draft(self, p, run_use_case, filing, requester, text):
        rc = rows(self.db.query("SELECT * FROM ref_tax_filing WHERE FilingId = :d", {"d": filing}, None, limit=1))
        if not rc:
            raise HTTPException(404, f"Unknown filing {filing}")
        rc = rc[0]

        def run(uc, params):
            if any(x["name"] == "filing" for x in uc.get("params", [])):
                params = {**params, "filing": filing}
            return run_use_case(p, uc, params, limit=200)

        ans = self.asker.ask(text, p, run)
        if ans.get("status") != "answered":
            draft = {"status": "no_match", "message": ans.get("message"), "suggestions": ans.get("suggestions", [])}
            matched = ""
        else:
            uc = ans["use_case"]
            res = ans["result"]
            n = len(res["rows"])
            verify = self.ledger.verify()
            narrative = tenant.text(
                "requests.narrative", requester=requester, filing=filing, entity=rc["LegalEntity"], title=uc["title"], rows=n,
                headline=ans["headline"], review_start=str(rc["ReviewPeriodStart"])[:10], review_end=str(rc["ReviewPeriodEnd"])[:10],
                as_of=self.eng.as_of(), jurisdiction=rc["Jurisdiction"], entries=verify.get("entries", 0))
            draft = {"status": "drafted", "use_case": uc, "confidence": ans["confidence"], "interpreted": ans["interpreted"],
                     "headline": ans["headline"], "narrative": narrative,
                     "result": {"columns": res["columns"], "rows": res["rows"]},
                     "attachments": [{"label": f"{uc['id']} table (CSV)", "href": f"/api/usecases/{uc['id']}/export.csv"
                                      + (f"?filing={filing}" if uc["id"] in tenant.usecase("filing_exhibits") else "")},
                                     {"label": f"Audit file for {filing}",
                                      "href": f"/api/tax/filing/{filing}/audit-file.zip"}],
                     "alternatives": ans.get("alternatives", [])}
            matched = uc["id"]
        rid = self._next_id(filing)
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
        self.db.query("""INSERT INTO data_request (RequestId, FilingId, Requester, RequestText, MatchedUseCases, DraftJson, Status,
                                                   PreparedBy, PreparedAt)
                         VALUES (:i, :d, :r, :t, :m, :j, 'draft', :u, :at)""",
                      dict(i=rid, d=filing, r=requester, t=text, m=matched, j=json.dumps(draft, default=str), u=p.user, at=now),
                      limit=None)
        self.ledger.append(p.user, p.persona, "prepare", f"{ledger_target()}:{rid}", matched or "no match")
        return self.get(rid)

    def decide(self, rid, action, p, note=""):
        if action not in ("approve", "return"):
            raise HTTPException(400, "action must be approve or return")
        p.require_action("datarequest.approve")
        r = self.get(rid)
        if r["Status"] != "draft":
            raise HTTPException(409, f"{rid} is {r['Status']}")
        if action == "approve" and r["PreparedBy"] == p.user:
            raise HTTPException(409, "Separation of duties: you prepared this response, so someone else must approve it")
        if action == "approve" and r["Draft"].get("status") != "drafted":
            raise HTTPException(409, "Nothing to approve: no use case matched this request")
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
        self.db.query("""UPDATE data_request SET Status = :s, ApprovedBy = :u, ApprovedAt = :at WHERE RequestId = :i""",
                      {"s": "approved" if action == "approve" else "returned", "u": p.user, "at": now, "i": rid}, limit=None)
        entry = self.ledger.append(p.user, p.persona, action, f"{ledger_target()}:{rid}", note)
        return self.get(rid), entry
