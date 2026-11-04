"""Bill unbundling and tag enforcement: the invoice-to-ledger drill, tag compliance with suspense, and pooling-rule
proposals with maker-checker. Included by main.py as an optional router."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from .auth import Principal, access, current_principal
from .context import audit, db, eng, ledger
from .pooling_rules import PoolingRules
from .window import window

router = APIRouter(prefix="/api")
rules = PoolingRules(db, eng, ledger)


def _role(p: Principal) -> str:
    return access()["personas"].get(p.persona, {}).get("label", p.persona)


@router.get("/reconciliation/{detail_id}/ledger")
def invoice_ledger(detail_id: str, p: Principal = Depends(current_principal)):
    """One invoice line unbundled: allocated rows by company, function and account with rule and citation, the
    reconciling items behind any difference, and the largest charges (each opens its lineage)."""
    p.require("reconciliation")
    if p.scope is not None:
        raise HTTPException(403, "Reconciliation needs enterprise scope")
    out = eng.invoice_ledger(detail_id)
    if out is None:
        raise HTTPException(404, "Unknown invoice line")
    audit(p, "view", module="reconciliation", invoice_detail=detail_id)
    return out


@router.get("/tag-compliance")
def tag_compliance(range: str | None = None, month: str | None = None, p: Principal = Depends(current_principal)):
    p.require("allocation")
    w = window(range, eng.as_of()) if range and not month else None
    try:
        out = eng.tag_compliance(p.scope, w, month)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if out.get("available"):
        audit(p, "view", module="allocation", panel="tag_compliance", period=month or range)
    return out


@router.get("/allocation/rule-proposals")
def rule_proposals(p: Principal = Depends(current_principal)):
    p.require("allocation")
    return rules.proposals()


class RuleProposal(BaseModel):
    Name: str | None = None
    MatchColumn: str
    MatchValue: str
    CostCenter: str
    EffectiveFrom: str | None = None
    Note: str | None = None


@router.post("/allocation/rule-proposals")
def submit_rule(body: RuleProposal, p: Principal = Depends(current_principal)):
    p.require("allocation")
    p.require_action("driver.submit")
    out = rules.submit(p.user, p.persona, _role(p), body.model_dump())
    audit(p, "rule_proposal", proposal=out["proposal"]["ProposalId"], decision="submit")
    return out


class RuleDecision(BaseModel):
    action: str  # approve | return


@router.post("/allocation/rule-proposals/{pid}/decision")
def decide_rule(pid: str, body: RuleDecision, p: Principal = Depends(current_principal)):
    p.require("allocation")
    out = rules.decide(p.user, p.persona, _role(p), pid, body.action, "rule.approve" in p.actions)
    audit(p, "rule_proposal", proposal=pid, decision=body.action)
    return out
