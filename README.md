# MM CostIQ

Cloud and AI cost governance for regulated industries. MM CostIQ is Miracle Minds' cost management platform for organizations that answer to regulators and auditors. It brings bills from every cloud provider, AI model vendor and Copilot-style tool into one view built on FOCUS 1.0, and runs inside your own environment.

The built-in sample is a multi-entity pharmaceutical group (life sciences pack). Its entities, filings and figures are generated for evaluation.

| Entity | Role | Tax jurisdiction |
|---|---|---|
| Global Services | Shared services; recharges technology cost | Unassigned |
| US Operations | Operating company | US (93%), PR (7%) |
| Germany GmbH | Operating company | DE |
| UK Operations | Operating company | UK |
| Research Ireland | Research entity | IE |
| Consumer Health Affiliate | Non-GxP affiliate | Non-GxP |

Cost centers: Clinical Operations, Patient Data, Research & Discovery, Data & Analytics, Consumer Health, Corporate IT, Finance, HR, Legal, Shared Services.

## Board Pack and Glossary

- **Board Pack** (`/board`; CFO, Technology Executive, Internal Controller, Internal Audit, Investor Relations): spend and outlook, AI investment and value, compliance and controls risk, decisions, then nine life-sciences additions (programs and phases, R&D vs SG&A, AI value in R&D, inspection readiness, patient data exposure, AI regulation readiness, cyber, vendor concentration, GMP AI). A section that needs data CostIQ does not hold says which input is missing. Printable.
- **AI Summary** (`/summary`, every role; also the agent's "Brief me"): a headline, what matters in spend, tax risk, GxP, savings and operations, and up to four next actions, for the signed-in role and scope. Every figure links to the panel or table row it comes from (the page scrolls to it and highlights it), and numbered chips list the source pages. The wording comes from saved responses for every role (Month to date) and for the CFO in every period (`backend/data/summary_responses.json`); other combinations, and any section whose figures have changed since, use CostIQ's built-in writer. No model or API key is involved.
- **Glossary** (`/glossary`, every role): terms, and how forecasts, run-rates, confidence, anomalies and savings estimates are calculated.

## Headline numbers (sample data)

| Number | Value | Meaning |
|---|---|---|
| **Deduction at risk** | **$266K** | Recharged technology spend in open tax filings that a tax examiner could disallow |
| **Credit unlock** | **$481K/yr** | R&D credit on recurring cloud and SaaS fees that qualify (UK, PR) but are not yet claimed. US already claims $173K/yr. |
| **Verified savings** | **$88K/yr** | Run-rate reduction measured after each fix |

A line counts toward deduction at risk when any of these is true:
- No benefit shown (idle resource, unused seats or commitment)
- Not reconciled to an invoice
- Allocated without an approved policy
- No GL account
- A known saving has been left unactioned for 60+ days

**Audit file:** a deterministic ZIP per filing. Its SHA-256 is sealed in the ledger.

**Examiner Request Responder:** a pasted information document request (IDR) becomes a drafted response, using deterministic search with no LLM.

## Quick start (Docker)

```powershell
cd mvp
docker compose up -d --build     # then open http://localhost:3000
docker compose logs -f backend
docker compose down              # stop; demo changes kept in the costiq-data volume
docker compose down -v           # stop and reset to the built-in sample
```

| Service | Image | Port | Notes |
|---|---|---|---|
| `backend` | `mm-costiq-backend:mvp` (Python 3.12, FastAPI, DuckDB) | 8080 | Sample copied to the `costiq-data` volume on first start |
| `frontend` | `mm-costiq-frontend:mvp` (Node 24, Next.js standalone) | 3000 | Forwards `/api/*` to the backend |

Both ports bind to `127.0.0.1`. To change that, set these in the shell or in `.env`:
- `COSTIQ_BIND=0.0.0.0` to expose on the network
- `COSTIQ_WEB_PORT` / `COSTIQ_API_PORT` to change ports
- `COSTIQ_ALERT_WEBHOOK_URL` to turn on alert webhooks

Tests in the image: `docker compose run --rm --no-deps backend test`

## Quick start (Windows, no Docker)

Needs [uv](https://docs.astral.sh/uv/) and Node.js 20+.

```powershell
cd mvp
.\start.ps1            # API on :8080, web app on http://localhost:3000
.\start.ps1 -Rebuild   # regenerate the sample database and rebuild the web app
.\start.ps1 -Dev       # web app with hot reload
```

Sign-in: choose a role (Entra ID mode exists in the backend). The signed-in app starts at `/command`.

## Deploy on Vercel

`vercel.json` deploys both apps as one Vercel project with two [services](https://vercel.com/docs/services):

| Service | Root | Public path | Notes |
|---|---|---|---|
| `frontend` | `frontend/` (Next.js) | `/(.*)`, everything | Binding to `backend` injects `COSTIQ_API_URL`; its `/api/*` route handler forwards calls with the persona header |
| `backend` | `backend/` (FastAPI, `costiq.main:app`) | none, internal only | Build step `data/vercel_build.py` builds the sample database into the bundle |

- The backend has no public route because demo sign-in trusts the `X-CostIQ-Persona` header. Only the frontend, through the binding, can reach it.
- No environment variables or secrets are needed: the binding provides `COSTIQ_API_URL` (don't set it yourself), and the pseudonym key keeps its built-in default, which is fine for the generated sample.
- Data: each backend instance works on its own copy of the sample in `/tmp`. Changes (approvals, acknowledgements, ledger entries) last until that instance is recycled, and instances don't share them. To keep changes, point `COSTIQ_DB_URL` at a PostgreSQL database loaded with the sample. The backend supports PostgreSQL, but these pages have only been tested on DuckDB.
- Anyone with the URL can sign in as any role, so turn on Deployment Protection (Project Settings → Deployment Protection → Vercel Authentication) to limit who can open it.
- Local run of both services: `vercel dev -L` from `mvp/` (needs `backend/sample/costiq.duckdb`; build it once with `backend\start.ps1 -Rebuild`).

## Layout

```
mvp/
  docker-compose.yml, start.ps1, vercel.json
  backend/                  FastAPI + DuckDB (PostgreSQL via sqlglot)
    requirements.txt        what the API needs at runtime; requirements-dev.txt adds the sample generator and pytest
    costiq/
      allocation.py           pool → entity → jurisdiction → GL account and cost class
      regulatory.py           tax filings, R&D credit schedule, allocation-policy workflow
      prudence.py             tax risk: evidence flags, deduction at risk, credit unlock
      evidence_pack.py        deterministic, ledger-sealed audit file
      data_requests.py        Examiner Request Responder
      optimization.py         savings lifecycle: identified → approved → implemented → verified
      board.py                Board Pack: 13 sections, each marked In CostIQ / Partial / Needs data
      summary.py              AI summary: ranked facts, linked sources, saved responses
      monitoring.py, alerts.py, adapters/
    registry/usecases/      145 executable use cases (YAML); PH-* are the pharma ones
    schema/                 FOCUS 1.4 + app, pharma and optimization schemas
    config/access.yaml      modules, 14 personas, actions, scopes
    data/                   synthetic sample generator (pharma.py), Vercel build step and file-drop templates
    tools/summary_responses.py  dump facts, save and check the AI summary's saved responses
    tests/
  frontend/                 Next.js 16 + Tailwind v4 + Chart.js
```

## Personas

Whoever submits a finding, policy, optimization or examiner response cannot also approve or verify it.

| Group | Persona | Can |
|---|---|---|
| Finance | Chief Financial Officer | Approve optimizations, budgets, examiner responses; export |
| Finance | P&L Executive, FP&A Planning Partner, FP&A Analyst | Read; endorse / submit forecasts |
| Finance | Internal Controller | Approve allocation policies and examiner responses |
| Regulatory | Tax & R&D Credits | Export audit files; prepare and approve examiner responses |
| Regulatory | Transfer Pricing | Submit allocation policies; prepare examiner responses |
| Regulatory | GxP Quality & Data Privacy | Read-only: patient data, quality agreements, GxP AI |
| Technology | Technology Executive | Approve optimizations |
| Technology | FinOps Analyst | Add to tracker; verify savings |
| Technology | Cloud Operations, AI Program Owner | Add to tracker; mark implemented |
| Finance | Internal Audit | Read-only: controls, evidence, Board Pack |
| Board / Investor | Investor Relations | Read-only: Board Pack, spend, savings |

## Walkthrough

Every change is written to the ledger. Reset with `docker compose down -v` or `.\start.ps1 -Rebuild`.

1. **CFO** → Command Center: the three numbers.
2. **Tax Risk Radar**: the idle `molgen-gpu-endpoint` (SageMaker, Research & Discovery, filing IE-CT1-FY2026) has **$61,601** at risk (no benefit shown).
3. **Fix it: OPT-005** → approve. **AI Program Owner** → mark implemented. Back as **CFO**, deduction at risk drops by exactly that amount.
4. **Tax & R&D Credits** → Tax & Transfer Pricing:
   - Credit unlock by jurisdiction (US claimed; UK and PR eligible; DE not eligible).
   - Audit file: export, then verify the ZIP against the ledger.
   - Examiner requests: paste *"Provide cloud subscription costs for the forecast year by vendor"* (maps to PH-05). Approve as **CFO**.
5. **Transfer Pricing**: AP-ANALYTICS v2 (by trial sites) is pending. **Internal Controller** approves it and the entity split changes.
6. **GxP Quality & Data Privacy** → GxP & Patient Data:
   - PH-07: patient data with provider-managed keys (EDC, ServiceNow).
   - PH-08: GxP vendors missing quality-agreement clauses.
   - PH-11: GxP AI without a validation review.
7. **FinOps Analyst** → monitors:
   - `aoai-ptu-discovery` runs at about 23% utilization.
   - `patient-support-assistant` has 14% retry waste.
   - OPT-004 (ePRO Kinesis shards) is ready to verify.

## Bringing in real data

Templates are in `backend/data/templates/`. Run from `backend/` with the venv active:

| Data | Template | Command |
|---|---|---|
| SaaS invoices (SAP, Veeva, ServiceNow, Oracle) | `saas_invoice_template.csv` | `python -m costiq.adapters.saas_invoice --period 2026-11 --path "landing/saas/*.csv"` |
| AWS Cost Optimization Hub | `aws_cost_optimization_hub_template.csv` | `python -m costiq.adapters.recommendations --source aws_cost_optimization_hub --path "landing/coh/*.csv"` |
| Google Recommender | `gcp_recommender_template.csv` | `python -m costiq.adapters.recommendations --source gcp_recommender --path "landing/gcp/*.csv"` |
| Azure Advisor | `azure_advisor_template.json` | `python -m costiq.adapters.recommendations --source azure_advisor --path "landing/advisor/*.json"` |
| LLM gateway telemetry | `llm_gateway_template.csv` | `python -m costiq.adapters.llm_gateway --path "landing/llm/*.csv"` |

With Docker, stop the API first (DuckDB has one writer):

```powershell
docker compose stop backend
docker compose run --rm --no-deps -v ${PWD}/landing:/landing backend saas_invoice --period 2026-11 --path "/landing/saas/*.csv"
docker compose run --rm --no-deps backend cli refresh
docker compose start backend
```

Cloud billing loads as FOCUS 1.x exports. Afterwards, `python -m costiq.cli refresh` re-runs allocation, optimization, alerts and filing evidence.

## Tests

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q                    # 184 tests on a temp copy of the sample DB
.venv\Scripts\python.exe -m costiq.cli conformance -v    # every use case runs
cd ..\frontend
npx eslint . ; npx next build
```

## Limits

- Demo sign-in only. Entra ID mode exists in the backend but is not wired to the web app.
- Synthetic data. Credit rates, filings and jurisdiction rules are illustrative.
- CostIQ recommends and records; it never changes cloud resources.
- Decision support only. It does not determine GxP, privacy or tax compliance, or predict any tax authority's ruling.
