"""Vercel build step for the backend service (vercel.json: services.backend.buildCommand).

Vercel installs requirements.txt into the function bundle. The sample generator also needs pandas and pyarrow
(requirements-dev.txt), which would push the bundle past the 500 MB Python limit, so this step installs them into a
temporary directory outside the service, builds sample/costiq.duckdb with them, and removes the generator's
intermediate files. At runtime the API works on a copy of that file in the temp directory (see config.py).
"""
from __future__ import annotations
import glob, os, shutil, subprocess, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SAMPLE = os.path.join(ROOT, "sample")
DEV = os.path.join(ROOT, "requirements-dev.txt")


def install(target: str):
    """pip if the build environment has it, else uv, else bootstrap pip with ensurepip."""
    pip = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--target", target, "-r", DEV]
    uv = ["uv", "pip", "install", "--quiet", "--python", sys.executable, "--target", target, "-r", DEV]
    for cmd in (pip, uv):
        try:
            if subprocess.run(cmd, cwd=ROOT).returncode == 0:
                return
        except FileNotFoundError:
            pass
    subprocess.run([sys.executable, "-m", "ensurepip"], check=True)
    subprocess.run(pip, check=True, cwd=ROOT)


def main():
    deps = tempfile.mkdtemp(prefix="costiq-build-")
    try:
        install(deps)
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in (deps, os.environ.get("PYTHONPATH")) if p)}
        env.pop("COSTIQ_DB_URL", None)  # always build the bundled sample file, never a database configured for runtime
        subprocess.run([sys.executable, os.path.join(ROOT, "data", "build_sample.py")], check=True, cwd=ROOT, env=env)
    finally:
        shutil.rmtree(deps, ignore_errors=True)

    import duckdb
    served = os.path.join(SAMPLE, "costiq.duckdb")
    con = duckdb.connect(served)
    con.execute("CHECKPOINT")  # fold any write-ahead log into the file, since only the file is copied at runtime
    con.close()
    shutil.rmtree(os.path.join(SAMPLE, "csv"), ignore_errors=True)
    for f in glob.glob(os.path.join(SAMPLE, "costiq_sample.duckdb*")) + glob.glob(os.path.join(SAMPLE, "*.wal")):
        os.remove(f)
    print(f"sample database: {served} ({os.path.getsize(served) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
