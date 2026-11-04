"""Entry point of an API that serves the utility tenant pack (Vercel service; see vercel.json). Fixes the tenant before the
app is imported, so the deployment needs no COSTIQ_TENANT setting."""
import os

os.environ["COSTIQ_TENANT"] = "utility"
from costiq.main import app  # noqa: E402,F401
