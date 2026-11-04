"""Build the sample database end to end.

  python data/build_sample.py            -> sample/costiq_sample.duckdb (source) and sample/costiq.duckdb (served)

Steps: generate the FOCUS + AI base dataset, apply the add-on generators (pharma registers, optimization
telemetry) to the source file, then migrate, load and seed the database in COSTIQ_DB_URL (default: the served
DuckDB file). The sample is generated, not customer data.
"""
from __future__ import annotations
import glob, os, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SAMPLE = os.path.join(ROOT, "sample")
SOURCE = os.path.join(SAMPLE, "costiq_sample.duckdb")
SERVED = os.path.join(SAMPLE, "costiq.duckdb")
# Add-on generators run in this order against the source file; each takes the DuckDB path as its argument.
ADDONS = ["pharma.py", "optimization.py"]


def run(*args):
    subprocess.run([sys.executable, *args], check=True, cwd=ROOT)


def main():
    os.makedirs(SAMPLE, exist_ok=True)
    for f in glob.glob(SOURCE + "*") + glob.glob(SERVED + "*"):
        os.remove(f)
    run(os.path.join(ROOT, "data", "generate_sample.py"), os.path.join(ROOT, "schema", "focus_1_4_ai_schema.sql"),
        SOURCE, os.path.join(SAMPLE, "csv"))
    for addon in ADDONS:
        path = os.path.join(ROOT, "data", addon)
        if os.path.exists(path):
            run(path, SOURCE)

    os.environ.setdefault("COSTIQ_DB_URL", "duckdb:///" + SERVED.replace("\\", "/"))
    sys.path.insert(0, ROOT)
    from costiq import cli
    for cmd in (["migrate"], ["load-sample", SOURCE], ["seed"]):
        if cli.main(cmd):
            raise SystemExit(f"costiq {' '.join(cmd)} failed")


if __name__ == "__main__":
    main()
