"""Prudence Engine API: disallowance risk, Earnings Unlock, the Rate-Case Evidence Pack and the Data Request Responder."""
from __future__ import annotations
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
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


@router.get("/prudence")
def prudence_summary(docket: str | None = None, p: Principal = Depends(current_principal)):
    p.require("prudence")
    audit(p, "view", module="prudence", docket=docket)
    out = prudence.summary(p.scope, docket)
    out["headline"] = prudence.headline(p.scope, Optimizer(db, eng))
    return out


@router.get("/prudence/lines")
def prudence_lines(docket: str | None = None, reason: str | None = None, entity: str | None = None,
                   vendor: str | None = None, p: Principal = Depends(current_principal)):
    p.require("prudence")
    return prudence.lines(p.scope, docket, reason, entity, vendor)


@router.get("/regulatory/earnings-unlock")
def earnings_unlock(docket: str | None = None, p: Principal = Depends(current_principal)):
    _need_any(p, "regulatory", "prudence")
    return prudence.earnings_unlock(p.scope, docket)


@router.get("/regulatory/rate-case/{docket}/evidence-pack.zip")
def evidence_pack(docket: str, p: Principal = Depends(current_principal)):
    p.require("regulatory")
    p.require_action("exhibit.export")
    if p.scope is not None:
        raise HTTPException(403, "Evidence packs need enterprise scope")
    try:
        data, sha, cert, entry = packs.export(docket, p.user, p.persona)
    except KeyError:
        raise HTTPException(404, f"Unknown docket {docket}")
    audit(p, "export", module="regulatory", evidence_pack=docket, sha256=sha)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{docket}-evidence-pack.zip"',
                             "X-CostIQ-SHA256": sha, "X-CostIQ-Ledger-Seq": str(entry["Seq"]),
                             "Access-Control-Expose-Headers": "X-CostIQ-SHA256, X-CostIQ-Ledger-Seq"})


@router.get("/regulatory/evidence-packs")
def evidence_packs(p: Principal = Depends(current_principal)):
    _need_any(p, "regulatory", "governance")
    return packs.packs()


@router.post("/regulatory/evidence-pack/verify")
async def verify_pack(file: UploadFile = File(...), p: Principal = Depends(current_principal)):
    _need_any(p, "regulatory", "governance")
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "File too large")
    out = packs.verify(data)
    audit(p, "verify_pack", sha256=out["sha256"], recorded=out["recorded"])
    return out


class NewRequest(BaseModel):
    docket: str
    requester: str = "Commission staff"
    text: str


@router.get("/regulatory/data-requests")
def list_requests(docket: str | None = None, p: Principal = Depends(current_principal)):
    p.require("regulatory")
    return requests.list(docket)


@router.post("/regulatory/data-requests")
def new_request(body: NewRequest, p: Principal = Depends(current_principal)):
    p.require("regulatory")
    p.require_action("datarequest.prepare")
    if not body.text.strip() or len(body.text) > 2000:
        raise HTTPException(400, "Request text must be 1-2000 characters")
    r = requests.draft(p, run_use_case, body.docket, body.requester, body.text)
    audit(p, "data_request_draft", request=r["RequestId"], matched=r["MatchedUseCases"])
    return r


class RequestDecision(BaseModel):
    action: str   # approve | return
    note: str = ""


@router.post("/regulatory/data-requests/{rid}/decision")
def decide_request(rid: str, body: RequestDecision, p: Principal = Depends(current_principal)):
    p.require("regulatory")
    r, entry = requests.decide(rid, body.action, p, body.note)
    audit(p, "data_request_decision", request=rid, decision=body.action)
    return {"request": r, "ledger": entry}
