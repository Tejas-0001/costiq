"""Identity and access.

Production (COSTIQ_AUTH_MODE=entra): the SPA signs users in with Microsoft Entra ID (OIDC auth code +
PKCE) and calls the API with an access token for the CostIQ API app registration. The API validates
the token signature against the tenant JWKS, plus issuer, audience and expiry, then maps:
  roles  claim -> CostIQ personas (access.yaml roleMap)
  groups claim -> cost-center scope (access.yaml scopeGroups)
Any OIDC provider works (AD FS, Okta, Ping, Keycloak) by changing issuer and claim names.

Demo (COSTIQ_AUTH_MODE=demo): no identity provider; the caller picks a persona with the
X-CostIQ-Persona header. Never enable demo mode on a network reachable by real users.
"""
from __future__ import annotations
import functools, hashlib, hmac, json, time, urllib.request
from dataclasses import dataclass, field
import yaml
from fastapi import Header, HTTPException, Request
from .config import settings
from .db import EntityScope


@dataclass
class Principal:
    user: str
    name: str
    personas: list[str]
    modules: list[str]
    actions: list[str]
    scope: list[str] | None          # None = enterprise (all cost centers); EntityScope = legal entities only
    pii: bool = False
    claims: dict = field(default_factory=dict)

    @property
    def persona(self):
        return self.personas[0]

    def can(self, module: str) -> bool:
        return module in self.modules

    def require(self, module: str):
        if not self.can(module):
            raise HTTPException(403, f"Your role cannot open {module}")

    def require_action(self, action: str):
        if action not in self.actions:
            raise HTTPException(403, f"Your role is not permitted to {action}")


@functools.lru_cache(maxsize=1)
def access() -> dict:
    with open(settings.access_config, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def build_principal(user, name, personas, groups=(), roles=()) -> Principal:
    acc = access()
    personas = [p for p in personas if p in acc["personas"]]
    if not personas:
        raise HTTPException(403, "No CostIQ role is assigned to this account")
    modules, actions, enterprise, entity_scoped = [], [], False, False
    for p in personas:
        cfg = acc["personas"][p]
        modules += [m for m in cfg["modules"] if m not in modules]
        actions += [a for a in cfg.get("actions", []) if a not in actions]
        enterprise = enterprise or cfg.get("enterprise", False)
        entity_scoped = entity_scoped or cfg.get("entity_scoped", False)
    scope = None
    if entity_scoped and not enterprise:
        scope = EntityScope(dict.fromkeys(e for g in groups for e in acc.get("entityScopeGroups", {}).get(g, [])))
    elif not enterprise:
        scope = []
        for g in groups:
            for cc in acc.get("scopeGroups", {}).get(g, []):
                if cc not in scope:
                    scope.append(cc)
    pii = any(r in acc.get("piiRoles", []) for r in roles)
    return Principal(user=user, name=name, personas=personas, modules=modules, actions=actions, scope=scope, pii=pii)


# ---------------------------------------------------------------------------------------- Entra / OIDC
_jwks_cache: dict = {"at": 0, "client": None}


def _jwks_client():
    import jwt
    if _jwks_cache["client"] is None or time.time() - _jwks_cache["at"] > 3600:
        url = settings.oidc_jwks_url
        if not url:
            conf = json.load(urllib.request.urlopen(settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=10))
            url = conf["jwks_uri"]
        _jwks_cache.update(client=jwt.PyJWKClient(url), at=time.time())
    return _jwks_cache["client"]


def _validate_bearer(token: str) -> dict:
    import jwt
    try:
        key = _jwks_client().get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256"], audience=settings.oidc_audience,
                          issuer=settings.oidc_issuer, options={"require": ["exp", "iss", "aud"]})
    except Exception as e:  # noqa: BLE001 - any validation failure is a 401
        raise HTTPException(401, f"Invalid token: {e}") from e


def current_principal(request: Request, authorization: str | None = Header(default=None),
                      x_costiq_persona: str | None = Header(default=None)) -> Principal:
    if settings.auth_mode == "entra":
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "Sign in required")
        claims = _validate_bearer(authorization.split(" ", 1)[1])
        roles = claims.get("roles", [])
        personas = [access()["roleMap"][r] for r in roles if r in access()["roleMap"]]
        p = build_principal(claims.get("preferred_username") or claims.get("upn") or claims.get("oid"),
                            claims.get("name", ""), personas, claims.get("groups", []), roles)
        p.claims = {k: claims.get(k) for k in ("oid", "tid", "preferred_username")}
        return p
    # demo mode
    persona = x_costiq_persona or "cfo"
    acc = access()
    if persona not in acc["personas"]:
        raise HTTPException(400, f"Unknown persona {persona}")
    p = build_principal(f"demo.{persona}@company.example", acc['personas'][persona]['label'], [persona])
    if isinstance(p.scope, EntityScope):
        p.scope = EntityScope(acc.get("demoEntityScope", {}).get(persona, []))
    elif p.scope is not None:
        p.scope = list(acc.get("demoScope", {}).get(persona, []))
    return p


def pseudonym(value: str) -> str:
    return "P-" + hmac.new(settings.pseudonym_key.encode(), value.lower().encode(), hashlib.sha256).hexdigest()[:10].upper()
