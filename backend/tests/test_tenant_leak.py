"""Tenant separation: the active tenant's pages never carry the other tenant's terms (pharma never shows utility wording;
the utility never shows pharma wording). Runs for whichever tenant is active (COSTIQ_TENANT) and covers every persona x
every GET endpoint, every use-case detail, the dbt export zip and both tenants' merged copy. The term lists are the
reviewer's (scratchpad review/leak_scan.py)."""
import io, json, os, re, sys, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from fastapi.testclient import TestClient
from costiq.config import settings
from costiq.tenant import load_copy

PHARMA_TERMS = re.compile(r"pharma|gxp|patient|clinical|\btrials?\b|r&d credit|\bqre\b|examiner|transfer[ -]pricing|molgen|"
                          r"veeva|genomic|part 11|\bstud(?:y|ies)\b", re.I)
UTILITY_TERMS = re.compile(r"\bferc\b|\bnerc\b|\bcip\b|transmission|distribution|rate case|operating compan|work order", re.I)
# Utility wording that only looks like a pharma term: the FERC titles of 561.6 and 561.7 (and the rule text that names them)
# and the interconnection-studies cloud account.
ALLOWED = {"utility": re.compile(r"transmission service studies|generation interconnection studies|scheduling or studies|"
                                 r"aws-ix-studies", re.I),
           "pharma": None}
TERMS = {"pharma": UTILITY_TERMS, "utility": PHARMA_TERMS}       # tenant -> the other tenant's terms
TENANT = settings.tenant if settings.tenant in TERMS else "pharma"

SKIP = {"/api/openapi.json", "/api/docs", "/docs/oauth2-redirect", "/redoc", "/api/health"}
# endpoints that read a period: the default plus the long window, so year-to-date wording is scanned too
RANGES = {"/api/command", "/api/summary", "/api/allocation", "/api/explorer", "/api/ai", "/api/ai-monitor", "/api/readiness",
          "/api/tag-compliance", "/api/cloud-monitor"}
# pages added in phase B; listed explicitly so they are scanned even when a router has not been included yet (404 skipped)
EXTRA = ["/api/readiness", "/api/tag-compliance", "/api/tax/ledger", "/api/tax/rule-register", "/api/tax/capital-path",
         "/api/allocation/rule-proposals", "/api/tax/ledger/listing", "/api/compliance/compliance", "/api/compliance/tax",
         "/api/compliance/risk", "/api/compliance/governance"]


def leaks(text: str, tenant: str = TENANT) -> list[str]:
    """The terms of the tenant other than `tenant` in text, with context; that tenant's allowed phrases are blanked first."""
    allow = ALLOWED.get(tenant)
    if allow is not None:
        text = allow.sub(lambda m: " " * len(m.group(0)), text)
    return [text[max(0, m.start() - 50):m.end() + 30].replace("\n", " ") for m in TERMS[tenant].finditer(text)]


def get_routes(app) -> list[str]:
    """Every GET path of the app without path parameters (included routers too), plus the phase-B pages."""
    out = []

    def walk(routes, prefix=""):
        for r in routes:
            sub = getattr(r, "router", None) or getattr(r, "original_router", None)
            if sub is not None and not hasattr(r, "methods"):
                walk(sub.routes, prefix + (getattr(r, "prefix", "") or ""))
            elif "GET" in (getattr(r, "methods", None) or []):
                p = prefix + r.path
                if "{" not in p and p not in SKIP and not p.endswith((".csv", ".zip")):
                    out.append(p)
    walk(app.routes)
    return sorted(set(out) | set(EXTRA))


@pytest.fixture(scope="module")
def app():
    from costiq.main import app
    return app


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def personas():
    from costiq.auth import access
    return list(access()["personas"].keys())


@pytest.fixture(scope="module")
def sweep(app, client, personas):
    """{path: {persona: (status, text)}} over every persona and GET endpoint (each distinct body scanned once)."""
    paths = []
    for p in get_routes(app):
        paths.append(p)
        if p in RANGES:
            paths.append(p + "?range=ytd")
    out = {}
    for persona in personas:
        h = {"X-CostIQ-Persona": persona}
        for p in paths:
            r = client.get(p, headers=h)
            out.setdefault(p, {})[persona] = (r.status_code, r.text if r.status_code == 200 else "")
    return out


def test_payloads_never_carry_the_other_tenants_terms(sweep):
    hits, seen = {}, set()
    for path, by_persona in sweep.items():
        for persona, (status, text) in by_persona.items():
            if status != 200 or text in seen:
                continue
            seen.add(text)
            for h in leaks(text):
                hits.setdefault(f"{path} ({persona})", set()).add(h)
    assert not hits, {k: sorted(v)[:3] for k, v in sorted(hits.items())[:12]}


def test_no_endpoint_fails_for_any_persona(sweep):
    """A page a persona cannot open answers 403 or 404, never 5xx (a crash would also hide a leak)."""
    errors = {p: {who: s for who, (s, _) in by.items() if s >= 500} for p, by in sweep.items()}
    errors = {p: e for p, e in errors.items() if e}
    assert not errors, errors


def test_phase_b_pages_answer_the_controller(client):
    """The pages the demo walks through answer the controller (404 only until the owning package lands its router)."""
    for p in ("/api/readiness", "/api/tag-compliance", "/api/tax/ledger", "/api/tax/rule-register", "/api/tax/capital-path",
              "/api/allocation/rule-proposals"):
        r = client.get(p, headers={"X-CostIQ-Persona": "finance_controller" if TENANT == "utility" else "cfo"})
        assert r.status_code in (200, 403, 404), (p, r.status_code, r.text[:200])


def test_use_case_details_never_carry_the_other_tenants_terms(client, personas):
    h = {"X-CostIQ-Persona": personas[0]}
    ucs = client.get("/api/usecases", headers=h).json()
    assert ucs, "no use cases"
    hits = {}
    for u in ucs:
        r = client.get(f"/api/usecases/{u['id']}", headers=h)
        if r.status_code == 200:
            for x in leaks(r.text):
                hits.setdefault(u["id"], set()).add(x)
    assert not hits, {k: sorted(v)[:3] for k, v in hits.items()}


def test_dbt_export_never_carries_the_other_tenants_terms(client):
    r = client.get("/api/codebase/dbt.zip", headers={"X-CostIQ-Persona": "cfo"})
    if r.status_code in (403, 404):
        pytest.skip(f"dbt export not served to cfo ({r.status_code})")
    assert r.status_code == 200, r.text[:300]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    hits = {}
    for n in z.namelist():
        for x in leaks(n) + leaks(z.read(n).decode("utf-8", "replace")):
            hits.setdefault(n, set()).add(x)
    assert not hits, {k: sorted(v)[:3] for k, v in sorted(hits.items())[:12]}


@pytest.mark.parametrize("tenant", ["pharma", "utility"])
def test_merged_copy_never_carries_the_other_tenants_terms(tenant):
    """Both tenants' merged copy (copy.json + copy/*.json), whichever tenant is active: pharma values for utility-only
    features are neutral words, and utility values never reuse pharma wording."""
    found = leaks(json.dumps(load_copy(tenant), ensure_ascii=False), tenant)
    assert not found, found[:10]
