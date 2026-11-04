"""Risk API: amount at risk by filing, the unlock figure, the filing audit file and the data request responder
(pharma: deduction at risk, R&D credit unlock, Examiner Request Responder). Wording comes from the tenant's copy."""
from __future__ import annotations
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from . import tenant
from .auth import Principal, current_principal
from .context import asker, audit, db, eng, ledger, reg, run_use_case
from .data_requests import DataRequests
from .evidence_pack import EvidencePack
from .optimization import Optimizer
from .prudence import PrudenceEngine

router = APIRouter(prefix="/api")
prudence = PrudenceEngine(db, eng)
packs = EvidencePack(db, eng, reg, ledger)
requests = DataRequests(db, eng, reg, asker, ledger)


def _need_any(p: Principal, *modules):
    if not any(p.can(m) for m in modules):
        raise HTTPException(403, f"Your role cannot open {' or '.join(modules)}")


@router.get("/risk")
def risk_summary(filing: str | None = None, p: Principal = Depends(current_principal)):
    p.require("risk")
    audit(p, "view", module="risk", filing=filing)
    out = prudence.summary(p.scope, filing)
    out["headline"] = prudence.headline(p.scope, Optimizer(db, eng))
    return out


@router.get("/risk/lines")
def risk_lines(filing: str | None = None, reason: str | None = None, entity: str | None = None,
                   vendor: str | None = None, p: Principal = Depends(current_principal)):
    p.require("risk")
    return prudence.lines(p.scope, filing, reason, entity, vendor)


@router.get("/tax/credit-unlock")
def credit_unlock(filing: str | None = None, p: Principal = Depends(current_principal)):
    _need_any(p, "tax", "risk")
    return prudence.credit_unlock(p.scope, filing)


@router.get("/tax/filing/{filing}/audit-file.zip")
def audit_file(filing: str, p: Principal = Depends(current_principal)):
    p.require("tax")
    p.require_action("exhibit.export")
    if p.scope is not None:
        raise HTTPException(403, "Audit files need enterprise scope")
    try:
        data, sha, cert, entry = packs.export(filing, p.user, p.persona)
    except KeyError:
        raise HTTPException(404, f"Unknown filing {filing}")
    audit(p, "export", module="tax", audit_file=filing, sha256=sha)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filing}-audit-file.zip"',
                             "X-CostIQ-SHA256": sha, "X-CostIQ-Ledger-Seq": str(entry["Seq"]),
                             "Access-Control-Expose-Headers": "X-CostIQ-SHA256, X-CostIQ-Ledger-Seq"})


@router.get("/tax/audit-files")
def audit_files(p: Principal = Depends(current_principal)):
    _need_any(p, "tax", "governance")
    return packs.packs()


@router.post("/tax/audit-file/verify")
async def verify_audit_file(file: UploadFile = File(...), p: Principal = Depends(current_principal)):
    _need_any(p, "tax", "governance")
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "File too large")
    out = packs.verify(data)
    audit(p, "verify_pack", sha256=out["sha256"], recorded=out["recorded"])
    return out


class NewRequest(BaseModel):
    filing: str
    requester: str | None = None   # default: the tenant's requester label (copy.json backend.requests.requester)
    text: str


# The same routes answer at /api/tax/data-requests (the page uses that path for every tenant); /api/tax/examiner-requests
# stays for existing clients.
@router.get("/tax/examiner-requests")
@router.get("/tax/data-requests")
def list_requests(filing: str | None = None, p: Principal = Depends(current_principal)):
    p.require("tax")
    return requests.list(filing)


@router.post("/tax/examiner-requests")
@router.post("/tax/data-requests")
def new_request(body: NewRequest, p: Principal = Depends(current_principal)):
    p.require("tax")
    p.require_action("datarequest.prepare")
    if not body.text.strip() or len(body.text) > 2000:
        raise HTTPException(400, "Request text must be 1-2000 characters")
    r = requests.draft(p, run_use_case, body.filing, body.requester or tenant.text("requests.requester"), body.text)
    audit(p, "data_request_draft", request=r["RequestId"], matched=r["MatchedUseCases"])
    return r


class RequestDecision(BaseModel):
    action: str   # approve | return
    note: str = ""


@router.post("/tax/examiner-requests/{rid}/decision")
@router.post("/tax/data-requests/{rid}/decision")
def decide_request(rid: str, body: RequestDecision, p: Principal = Depends(current_principal)):
    p.require("tax")
    r, entry = requests.decide(rid, body.action, p, body.note)
    audit(p, "data_request_decision", request=rid, decision=body.action)
    return {"request": r, "ledger": entry}
