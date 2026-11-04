"""costiq command line.

  python -m costiq.cli migrate
  python -m costiq.cli load-sample ../sample/costiq_sample.duckdb
  python -m costiq.cli seed                                    demo only: resets rules, manifests and the ledger
  python -m costiq.cli refresh                                 after loading data: allocation, optimization, alerts, filing evidence
  python -m costiq.cli allocate [--period 2026-10]                 rebuild the entity allocation chain
  python -m costiq.cli conformance [--target postgres]     run all catalog use cases; exit 1 on any failure
  python -m costiq.cli translate F-41 --target tsql        show a use case in another dialect
"""
from __future__ import annotations
import argparse, json, sys
from .config import settings
from .db import connect
from .registry import Registry
from . import loader, dialect


def conformance(db, reg, scope=None, verbose=False):
    ok, empty, failed = [], [], []
    for uid in reg.order:
        uc = reg.get(uid)
        try:
            r = db.query(uc["sql"], reg.resolve_params(uc, None), scope=scope, limit=50)
            (ok if r["rows"] else empty).append(uid)
            if verbose:
                print(f"{uid:6} rows={len(r['rows']):>3} {r['elapsed_ms']:>7}ms  {uc['title'][:60]}")
        except Exception as e:
            failed.append((uid, str(e).splitlines()[0][:200]))
            if verbose:
                print(f"{uid:6} FAILED {str(e).splitlines()[0][:150]}")
    return ok, empty, failed


def main(argv=None):
    ap = argparse.ArgumentParser(prog="costiq")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    p = sub.add_parser("load-sample"); p.add_argument("path")
    sub.add_parser("seed")
    sub.add_parser("refresh")
    p = sub.add_parser("allocate"); p.add_argument("--period", help="YYYY-MM; default every billing period")
    p = sub.add_parser("conformance"); p.add_argument("--scope", nargs="*"); p.add_argument("-v", action="store_true")
    p = sub.add_parser("translate"); p.add_argument("uid"); p.add_argument("--target", default="postgres")
    a = ap.parse_args(argv)

    if a.cmd == "translate":
        uc = Registry(settings.registry_dir).get(a.uid)
        print(dialect.translate(uc["sql"], a.target)[0])
        return 0
    db = connect(settings.db_url)
    if a.cmd == "migrate":
        loader.migrate(db); print("schema ready on", db.target)
    elif a.cmd == "load-sample":
        loader.load_from_duckdb(db, a.path)
    elif a.cmd == "seed":
        loader.seed(db); print("seeded allocation rules, manifests and ledger")
    elif a.cmd == "refresh":
        loader.refresh(db); print("rebuilt allocation, optimization, alerts and filing evidence")
    elif a.cmd == "allocate":
        import datetime as dt
        from .allocation import Allocator
        from .engines import Engines
        ps = dt.datetime.fromisoformat(a.period + "-01") if a.period else None
        print(json.dumps(Allocator(db, Engines(db, None)).run(ps), indent=2))
    elif a.cmd == "conformance":
        ok, empty, failed = conformance(db, Registry(settings.registry_dir), a.scope, a.v)
        print(json.dumps({"dialect": db.target, "returned_rows": len(ok), "empty": empty,
                          "failed": failed}, indent=2))
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
