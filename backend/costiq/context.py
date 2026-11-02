"""Process-wide objects shared by every API module: database, registry, engines, ledger, Ask, and the helpers that
run a use case for a principal (scope, audit line, pseudonymization)."""
from __future__ import annotations
import datetime as dt, json, logging, re
from .ask import AskEngine
from .auth import Principal, pseudonym
from .config import settings
from .db import connect
from .engines import Engines
from .ledger import Ledger
from .registry import Registry

log = logging.getLogger("costiq.audit")
logging.basicConfig(level=logging.INFO, format="%(message)s")

db = connect(settings.db_url)
reg = Registry(settings.registry_dir)
eng = Engines(db, reg)
ledger = Ledger(db)
asker = AskEngine(reg, eng)
PII_COL = re.compile(r"(userprincipal|^usedby$|^user$)", re.I)


def audit(p: Principal, event: str, **kw):
    log.info(json.dumps({"audit": event, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "user": p.user,
                         "personas": p.personas, "scope": p.scope, **kw}, default=str))


def protect(p: Principal, result: dict) -> dict:
    """Replace person identifiers with keyed pseudonyms unless the caller holds pii.view."""
    if p.pii:
        return result
    idx = [i for i, c in enumerate(result["columns"]) if PII_COL.search(c)]
    if idx:
        for r in result["rows"]:
            for i in idx:
                if isinstance(r[i], str) and "@" in r[i]:
                    r[i] = pseudonym(r[i])
    return result


def run_use_case(p: Principal, uc: dict, params: dict | None, limit=2000):
    resolved = reg.resolve_params(uc, params)
    res = db.query(uc["sql"], resolved, p.scope, limit=limit)
    audit(p, "run_use_case", use_case=uc["id"], params=resolved, rows=len(res["rows"]))
    res.pop("dialect", None)  # the database engine is an implementation detail, not shown to users
    res["params"] = {k: (v.isoformat()[:10] if isinstance(v, (dt.date, dt.datetime)) else v) for k, v in resolved.items()}
    return protect(p, res)


def records(res: dict) -> list[dict]:
    """Use-case result (columns + rows) as a list of dicts."""
    return [dict(zip(res["columns"], r)) for r in res["rows"]]
