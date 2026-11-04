"""Native Snowflake Pipeline and Client-Owned Codebase API: a use case's SQL compiled for Snowflake (or Databricks),
and the whole ledger as a dbt project the client keeps in its own Git (dbt_export.py). Anyone who can open the
catalog can read both."""
from __future__ import annotations
import hashlib
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from . import dbt_export, dialect
from .auth import Principal, current_principal
from .context import audit, db, eng, reg

router = APIRouter(prefix="/api/codebase")
TARGETS = ("snowflake", "databricks")


@router.get("/usecases/{uid}/sql")
def usecase_sql(uid: str, target: str = "snowflake", p: Principal = Depends(current_principal)):
    p.require("catalog")
    if target not in TARGETS:
        raise HTTPException(400, f"target must be one of {', '.join(TARGETS)}")
    try:
        uc = reg.get(uid)
    except KeyError:
        raise HTTPException(404, "Unknown use case")
    sql, params = dialect.translate(uc["sql"], target, pretty=True)
    return {"id": uid, "target": target, "sql": sql, "params": params}


@router.get("/dbt.zip")
def dbt_zip(p: Principal = Depends(current_principal)):
    p.require("catalog")
    files = dbt_export.build("snowflake", db, reg, eng)
    data = dbt_export.to_zip(files)
    digest = hashlib.sha256(data).hexdigest()
    audit(p, "export", artifact="dbt_project", files=len(files), sha256=digest)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{dbt_export.ROOT}.zip"',
                             "X-CostIQ-SHA256": digest})
