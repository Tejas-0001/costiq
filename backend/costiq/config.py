"""Runtime configuration from environment variables (set by the Helm chart's ConfigMap / Secret)."""
from __future__ import annotations
import os, shutil, tempfile
from dataclasses import dataclass, field

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  # mvp/backend


def _env(name, default=None):
    return os.environ.get(name, default)


def _sample_db_url() -> str:
    """The built-in sample database (when COSTIQ_DB_URL is not set). DuckDB writes next to its file, so where the app
    directory is read-only, as in a Vercel Function, the API works on a copy in the temp directory: changes last for
    that instance, and a new instance starts again from the sample."""
    path = os.path.join(ROOT, "sample", "costiq.duckdb")
    if os.path.exists(path) and not os.access(os.path.dirname(path), os.W_OK):
        copy = os.path.join(tempfile.gettempdir(), "costiq.duckdb")
        if not os.path.exists(copy):
            part = f"{copy}.{os.getpid()}"
            shutil.copyfile(path, part)
            os.replace(part, copy)
        path = copy
    return "duckdb:///" + path.replace("\\", "/")


@dataclass
class Settings:
    db_url: str = field(default_factory=lambda: _env("COSTIQ_DB_URL") or _sample_db_url())
    registry_dir: str = field(default_factory=lambda: _env("COSTIQ_REGISTRY_DIR", os.path.join(ROOT, "registry", "usecases")))
    access_config: str = field(default_factory=lambda: _env("COSTIQ_ACCESS_CONFIG", os.path.join(ROOT, "config", "access.yaml")))
    # auth_mode: "entra" (production: validate Entra ID / OIDC bearer tokens) or "demo" (persona picker, no IdP)
    auth_mode: str = field(default_factory=lambda: _env("COSTIQ_AUTH_MODE", "demo"))
    oidc_issuer: str = field(default_factory=lambda: _env("COSTIQ_OIDC_ISSUER", ""))  # https://login.microsoftonline.com/<tenant>/v2.0
    oidc_audience: str = field(default_factory=lambda: _env("COSTIQ_OIDC_AUDIENCE", ""))  # api://costiq or the API app's client id
    oidc_jwks_url: str = field(default_factory=lambda: _env("COSTIQ_OIDC_JWKS_URL", ""))  # defaults from issuer discovery
    spa_client_id: str = field(default_factory=lambda: _env("COSTIQ_SPA_CLIENT_ID", ""))
    pseudonym_key: str = field(default_factory=lambda: _env("COSTIQ_PSEUDONYM_KEY", "dev-only-change-me"))
    tenant_name: str = field(default_factory=lambda: _env("COSTIQ_TENANT_NAME", ""))
    data_as_of: str = field(default_factory=lambda: _env("COSTIQ_DATA_AS_OF", ""))
    https_proxy: str = field(default_factory=lambda: _env("HTTPS_PROXY", ""))


settings = Settings()
