"""Runtime configuration from environment variables (set by the Helm chart's ConfigMap / Secret).

COSTIQ_TENANT (default "pharma") picks the tenant pack in backend/tenants/<tenant>/: its access model, its use cases
(added to the shared registry), its sample database (sample/<tenant>/costiq.duckdb), its saved AI summaries, copy and
hooks (see costiq/tenant.py). One tenant per process: context.py builds the database, registry and engines at import.
"""
from __future__ import annotations
import os, re, shutil, tempfile
from dataclasses import dataclass, field

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  # mvp/backend
TENANTS_DIR = os.path.join(ROOT, "tenants")
SHARED_REGISTRY = os.path.join(ROOT, "registry", "usecases")
DEFAULT_TENANT = "pharma"


def _env(name, default=None):
    return os.environ.get(name, default)


def tenant_name(value: str | None = None) -> str:
    """The active tenant: `value`, else COSTIQ_TENANT, else pharma. Must name a folder in backend/tenants."""
    name = (value or _env("COSTIQ_TENANT") or DEFAULT_TENANT).strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or not os.path.isfile(os.path.join(TENANTS_DIR, name, "pack.yaml")):
        known = sorted(d for d in os.listdir(TENANTS_DIR) if os.path.isfile(os.path.join(TENANTS_DIR, d, "pack.yaml")))
        raise ValueError(f"Unknown tenant {name!r} (COSTIQ_TENANT); available: {', '.join(known)}")
    return name


def tenant_dir(tenant: str) -> str:
    return os.path.join(TENANTS_DIR, tenant)


def sample_paths(tenant: str) -> tuple[str, str]:
    """(source, served) sample DuckDB files of a tenant: the generators write the source, the API serves the other."""
    d = os.path.join(ROOT, "sample", tenant)
    return os.path.join(d, "costiq_sample.duckdb"), os.path.join(d, "costiq.duckdb")


def _sample_db_url(tenant: str) -> str:
    """The tenant's built-in sample database (when COSTIQ_DB_URL is not set). DuckDB writes next to its file, so where
    the app directory is read-only, as in a Vercel Function, the API works on a copy in the temp directory: changes last
    for that instance, and a new instance starts again from the sample. Before the sample is built, an empty in-memory
    database stands in (the API starts; pages report missing data)."""
    path = sample_paths(tenant)[1]
    if not os.path.exists(path):
        return "duckdb:///:memory:"
    if not os.access(os.path.dirname(path), os.W_OK):
        copy = os.path.join(tempfile.gettempdir(), f"costiq-{tenant}.duckdb")
        if not os.path.exists(copy):
            part = f"{copy}.{os.getpid()}"
            shutil.copyfile(path, part)
            os.replace(part, copy)
        path = copy
    return "duckdb:///" + path.replace("\\", "/")


TENANT = tenant_name()


@dataclass
class Settings:
    tenant: str = TENANT
    db_url: str = field(default_factory=lambda: _env("COSTIQ_DB_URL") or _sample_db_url(TENANT))
    # Use-case folders, joined with os.pathsep: the shared registry, then the tenant's own use cases.
    registry_dir: str = field(default_factory=lambda: _env("COSTIQ_REGISTRY_DIR") or os.pathsep.join(
        [SHARED_REGISTRY, os.path.join(tenant_dir(TENANT), "usecases")]))
    access_config: str = field(default_factory=lambda: _env("COSTIQ_ACCESS_CONFIG", os.path.join(tenant_dir(TENANT), "access.yaml")))
    summary_responses: str = field(default_factory=lambda: _env("COSTIQ_SUMMARY_RESPONSES",
                                                                os.path.join(tenant_dir(TENANT), "summary_responses.json")))
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

    @property
    def registry_dirs(self) -> list[str]:
        return [d for d in self.registry_dir.split(os.pathsep) if d]

    @property
    def tenant_dir(self) -> str:
        return tenant_dir(self.tenant)


settings = Settings()
