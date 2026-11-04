"""Run the suite against a throwaway copy of the active tenant's sample DuckDB file (sample/<tenant>/costiq.duckdb;
COSTIQ_TENANT, default pharma), because workflow tests (policy approvals, optimization decisions, evidence packs) write
to the database. Set COSTIQ_TEST_IN_PLACE=1 to use COSTIQ_DB_URL as is (e.g. a disposable PostgreSQL in CI).

Tests that depend on one tenant's sample carry @pytest.mark.tenant("<name>") (or a module-level pytestmark) and are
skipped when another tenant is active:
    COSTIQ_TENANT=utility python -m pytest -q tests
"""
import os, shutil, sys, tempfile
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
TENANT = (os.environ.get("COSTIQ_TENANT") or "pharma").strip().lower()

# costiq.config reads COSTIQ_DB_URL at import, so the copy is made before anything imports costiq.
if not os.environ.get("COSTIQ_TEST_IN_PLACE"):
    sample = os.path.join(ROOT, "sample", TENANT, "costiq.duckdb")
    url = os.environ.get("COSTIQ_DB_URL", "duckdb:///" + sample.replace("\\", "/"))
    if url.startswith("duckdb:///"):
        src = url[len("duckdb:///"):]
        if not os.path.exists(src):
            raise SystemExit(f"Sample database not found at {src}: run python data/build_sample.py --tenant {TENANT} first")
        tmp = os.path.join(tempfile.mkdtemp(prefix=f"costiq-test-{TENANT}-"), "costiq.duckdb")
        shutil.copyfile(src, tmp)
        os.environ["COSTIQ_DB_URL"] = "duckdb:///" + tmp.replace("\\", "/")


def pytest_configure(config):
    config.addinivalue_line("markers", "tenant(name): runs only when COSTIQ_TENANT is that tenant (default pharma)")


def pytest_collection_modifyitems(config, items):
    for item in items:
        m = item.get_closest_marker("tenant")
        if m and TENANT not in m.args:
            item.add_marker(pytest.mark.skip(reason=f"{'/'.join(m.args)} sample only (COSTIQ_TENANT={TENANT})"))
