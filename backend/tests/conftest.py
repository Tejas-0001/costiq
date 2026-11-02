"""Run the suite against a throwaway copy of the sample DuckDB file, because workflow tests (policy approvals,
optimization decisions, evidence packs) write to the database. Set COSTIQ_TEST_IN_PLACE=1 to use COSTIQ_DB_URL as is
(e.g. a disposable PostgreSQL in CI)."""
import os, shutil, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

if not os.environ.get("COSTIQ_TEST_IN_PLACE"):
    url = os.environ.get("COSTIQ_DB_URL", "duckdb:///" + os.path.join(ROOT, "sample", "costiq.duckdb").replace("\\", "/"))
    if url.startswith("duckdb:///"):
        src = url[len("duckdb:///"):]
        if not os.path.exists(src):
            raise SystemExit(f"Sample database not found at {src}: run python data/build_sample.py first")
        tmp = os.path.join(tempfile.mkdtemp(prefix="costiq-test-"), "costiq.duckdb")
        shutil.copyfile(src, tmp)
        os.environ["COSTIQ_DB_URL"] = "duckdb:///" + tmp.replace("\\", "/")
