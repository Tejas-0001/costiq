"""Build a tenant's sample database end to end.

  python data/build_sample.py                     the active tenant (COSTIQ_TENANT, default pharma)
  python data/build_sample.py --tenant utility    -> sample/utility/costiq_sample.duckdb (source) and costiq.duckdb (served)

Steps: generate the FOCUS + AI base dataset with the tenant's name tables (tenants/<t>/names.py), apply the tenant
pack's add-on generators (pack.yaml `addons`) to the source file, then migrate, load and seed the database in
COSTIQ_DB_URL (default: the tenant's served DuckDB file). The sample is generated, not customer data.
"""
from __future__ import annotations
import argparse, glob, os, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def run(*args):
    subprocess.run([sys.executable, *args], check=True, cwd=ROOT, env=os.environ.copy())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", help="tenant pack in backend/tenants (default: COSTIQ_TENANT, else pharma)")
    a = ap.parse_args(argv)
    tenant = (a.tenant or os.environ.get("COSTIQ_TENANT") or "pharma").strip().lower()
    os.environ["COSTIQ_TENANT"] = tenant  # the generators and the costiq CLI below read the same tenant
    served = os.path.join(ROOT, "sample", tenant, "costiq.duckdb")
    # costiq.config reads COSTIQ_DB_URL at import: point it at the file being built before importing it
    os.environ.setdefault("COSTIQ_DB_URL", "duckdb:///" + served.replace("\\", "/"))
    sys.path.insert(0, ROOT)
    from costiq.config import sample_paths, tenant_name
    tenant_name(tenant)  # unknown tenant: stop here with the list of packs
    source, served = sample_paths(tenant)
    sample = os.path.dirname(served)
    os.makedirs(sample, exist_ok=True)
    for f in glob.glob(source + "*") + glob.glob(served + "*"):
        os.remove(f)
    print(f"Building the {tenant} sample in {sample}")
    run(os.path.join(ROOT, "data", "generate_sample.py"), os.path.join(ROOT, "schema", "focus_1_4_ai_schema.sql"),
        source, os.path.join(sample, "csv"))
    from costiq.tenant import pack, path
    for addon in pack().get("addons", []):
        script = path(addon)
        if not os.path.exists(script):
            raise SystemExit(f"add-on {addon} (tenants/{tenant}/pack.yaml) not found")
        run(script, source)

    from costiq import cli
    for cmd in (["migrate"], ["load-sample", source], ["seed"]):
        if cli.main(cmd):
            raise SystemExit(f"costiq {' '.join(cmd)} failed")


if __name__ == "__main__":
    main()
