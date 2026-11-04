"""Runtime configuration from environment variables (set by the Helm chart's ConfigMap / Secret)."""
from __future__ import annotations
import os
from dataclasses import dataclass, field

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  # mvp/backend


def _env(name, default=None):
    return os.environ.get(name, default)


@dataclass
class Settings:
    db_url: str = field(default_factory=lambda: _env("COSTIQ_DB_URL", f"duckdb:///{ROOT}/sample/costiq.duckdb"))
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
