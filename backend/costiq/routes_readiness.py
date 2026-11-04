"""Data Readiness & Accounting Discovery API (module `readiness`, enterprise scope). Served by main.py's optional-router
loop as soon as this file exists."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from .auth import Principal, current_principal
from .context import audit, db, eng, ledger
from .readiness import Readiness
from .window import window

router = APIRouter(prefix="/api")
readiness_engine = Readiness(db, eng, ledger)


@router.get("/readiness")
def readiness(range: str = "mtd", p: Principal = Depends(current_principal)):
    """Readiness scorecard for the selected period: sources, tags, accounting, reconciliation and controls, each scored."""
    p.require("readiness")
    if p.scope is not None:
        raise HTTPException(403, "Data readiness needs enterprise scope")
    w = window(range, eng.as_of())
    out = readiness_engine.build(w)
    audit(p, "view", module="readiness", period=w.range)
    return out
