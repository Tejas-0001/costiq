# MM CostIQ for Electric Utilities (MVP)

> Every cloud, SaaS and AI dollar: provable, recoverable or eliminated.

This is a working MVP of MM CostIQ for a multi-OpCo US electric utility. It runs on synthetic data for **Meridian Energy**, a fictional holding company with these entities:

- a service company
- three state OpCos (Ohio, Indiana, Texas)
- a FERC-regulated transmission company
- a non-regulated renewables affiliate

It keeps CostIQ's FOCUS core (145 tested use cases, a hash-chained ledger and row-level scope) and adds the utility layer that rate cases need.

## The Prudence Engine

The CFO lands on three numbers that no FinOps tool shows a utility today. The sample data gives these values:

| Number | Value | Meaning |
|---|---|---|
| **Disallowance risk** | **$268K** | Base-period cloud, SaaS and AI spend a commission could challenge. |
| **Earnings unlock** | **$453K/yr** | Return the utility would earn if cloud subscription fees were deferred to a regulatory asset (Account 182.3) in states that don't yet allow it. |
| **Verified savings** | **$80K/yr** | Measured from run-rate after each fix and carried into the rate-case bridge as prudence evidence. |

A line counts toward disallowance risk when it is:
- not used and useful (an idle GPU, unused seats, an unused commitment)
- not reconciled to an invoice
- allocated without an approved policy
- missing a FERC account
- carrying a known saving that has been left unactioned for 60+ days

Two features let a demo make these numbers real:
- **One-click Evidence Pack:** a deterministic ZIP per docket. Its SHA-256 is sealed in the ledger, so anyone can later prove a filed exhibit was not altered.
- **Data Request Responder:** a pasted staff or intervenor data request becomes a drafted response with tables and evidence. It runs on deterministic search with no LLM, and no data leaves the boundary.

## Quick start with Docker

Prerequisite: Docker Desktop, or Docker Engine with Compose v2.

```powershell
cd mvp
docker compose up -d --build     # first build takes a few minutes, then open http://localhost:3000
docker compose logs -f backend   # follow the API log
docker compose down              # stop; demo changes are kept in the costiq-data volume
docker compose down -v           # stop and reset the demo to the built-in sample
```

The stack runs two containers:

| Service | Image | Port | Notes |
|---|---|---|---|
| `backend` | `mm-costiq-backend:mvp` (Python 3.12, FastAPI, DuckDB) | 8080 | The Meridian sample is built into the image. On first start it is copied to the `costiq-data` volume at `/data/costiq.duckdb`. |
| `frontend` | `mm-costiq-frontend:mvp` (Node 24, Next.js standalone) | 3000 | Forwards `/api/*` to `http://backend:8080` on the Compose network. The browser never calls the API directly. |

Both ports bind to `127.0.0.1`, because sign-in is demo personas only. To change that, set these variables in the shell or in an `.env` file next to `docker-compose.yml`:
- `COSTIQ_BIND=0.0.0.0` shows the app on your network.
- `COSTIQ_WEB_PORT` and `COSTIQ_API_PORT` change the ports.
- `COSTIQ_ALERT_WEBHOOK_URL` turns on outbound alert webhooks.

To run the tests inside the image (they use a throwaway copy of the built-in sample, not your demo volume):

```powershell
docker compose run --rm --no-deps backend test
```

## Quick start without Docker (Windows)

Prerequisites: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12) and Node.js 20+.

```powershell
cd mvp
.\start.ps1            # API on :8080 in a new window, web app on http://localhost:3000
.\start.ps1 -Rebuild   # reset the demo: regenerate the sample database and rebuild the web app
.\start.ps1 -Dev       # web app with hot reload
```

The first run creates `backend\.venv`, builds the sample DuckDB database (about a minute), runs `npm install`, and builds the web app.

To run the pieces separately:

```powershell
cd backend;  .\start.ps1                                   # API only
cd frontend; $env:COSTIQ_API_URL="http://127.0.0.1:8080"; npx next dev
```

Open http://localhost:3000 for the home page, then **Sign in** and choose a role. Sign-in is demo-only: a cookie selects the persona, and the web app passes it to the API. The signed-in app starts at `/command` (the Command Center).

## Layout

```
mvp/
  docker-compose.yml        backend + frontend containers and the costiq-data volume
  start.ps1                 launches both apps without Docker
  backend/                  FastAPI + DuckDB (PostgreSQL also supported via sqlglot)
    Dockerfile, docker-entrypoint.sh
    costiq/                 API and engines
      allocation.py           driver-based allocation: pool → OpCo → jurisdiction → FERC account and cost class
      regulatory.py           rate cases, deferrals, regulatory assets, allocation-policy workflow
      prudence.py             Prudence Engine: evidence flags, disallowance risk, earnings unlock
      evidence_pack.py        deterministic, ledger-sealed rate-case evidence pack
      data_requests.py        Data Request Responder
      optimization.py         savings lifecycle: identified → approved → implemented → verified
      monitoring.py           cloud and AI/LLM monitor payloads
      alerts.py               budget, token-budget, anomaly, commitment-expiry and PTU alerts
      adapters/               SaaS invoices, provider recommendations, LLM gateway telemetry
    registry/usecases/      145 executable use cases (YAML)
    schema/                 FOCUS 1.4 + app, utility and optimization schemas
    config/access.yaml      modules, 14 personas, actions, scopes
    data/                   synthetic sample generator and file-drop templates
    tests/                  pytest suite
  frontend/                 Next.js 16 App Router + Tailwind v4 + Chart.js
    Dockerfile              standalone server build (COSTIQ_STANDALONE=1)
    app/api/[...path]/      backend-for-frontend: forwards /api/* with the persona header
    components/views/       one view per screen
```

## Personas

Separation of duties applies to the person, not the role. Whoever submits a finding, policy, optimization or data-request response cannot also approve or verify it, so switch personas to move an item through its workflow.

| Persona | Uses it for | Can |
|---|---|---|
| Chief Financial Officer | The three numbers, approvals | Approve optimizations, budgets and data requests; export exhibits |
| Regulatory Accounting & Rates | Prudence Radar, rate cases, evidence packs | Export exhibits; prepare and approve data requests |
| Service-Company Accounting | Allocation drivers, Form 60 | Submit allocation policies; prepare data requests |
| Internal Controller | Close, reconciliation, policies | Approve allocation policies and data requests |
| NERC CIP Compliance | BCSI and CIP-013 checks | Read-only |
| OpCo Finance Manager (Ohio) | Its own entity only (affiliate wall) | Read-only; sees only Meridian Electric Ohio |
| Technology Executive | Cloud and AI spend | Approve optimizations |
| FinOps Analyst | Monitors, anomalies, savings | Add to the tracker; verify savings; prepare data requests |
| Cloud Operations | Implementing cloud fixes | Add to the tracker; mark implemented |
| AI Program Owner | LLM unit cost, PTU and GPU waste | Add to the tracker; mark implemented |
| Internal Audit | Controls, ledger, evidence | Read-only |

Also available: P&L Executive, FP&A Planning Partner and FP&A Analyst.

## Demo stories

Every change below is written to the ledger. To return to the starting state, run `docker compose down -v` (Docker) or `.\start.ps1 -Rebuild`.

### 0. The hook (about 5 minutes)

1. **CFO** → Command Center. Show the three numbers and the risk-reason strip.
2. Click **Disallowance risk** to open the Prudence Radar.
   - Show the OpCo × vendor heatmap.
   - Then open the risks table: the idle `gridplan-gpu-endpoint` (AWS SageMaker, Transmission Planning, docket ER27-118-000) has **$63,845** at risk as *not used and useful*.
3. Click **Fix it: OPT-005**. In the Optimization Center, **approve** it.
4. Switch to **AI Program Owner** → Optimization Center → **Mark implemented** on OPT-005.
5. Switch back to **CFO**. Disallowance risk has fallen by exactly that line's amount, because the line now carries a documented corrective action.
6. **Regulatory Accounting** → Regulatory → **Earnings Unlock**.
   - Ohio earns a return today under its deferral order.
   - Toggle Texas and Indiana in and out to show the what-if.
7. **Evidence Pack** tab → **Export** a docket.
   - The ZIP downloads, and its SHA-256 appears under *Packs sealed in the ledger*.
   - Upload the same file under **Verify a pack** to confirm it. Changing any byte makes verification fail.
8. **Data Requests** tab.
   - Paste *"Provide cloud subscription costs for the test year by vendor"*. The draft maps to UT-05, the test-year bridge.
   - Approving it as the same person returns an error (separation of duties). Approve it as the **CFO** or **Internal Controller** instead.

### 1. Monitor

- **FinOps Analyst** → **Cloud Cost Monitor**.
  - The September anomaly shows on the daily line.
  - *What to optimize* lists idle resources, non-production servers running 24×7, rightsizing and storage tiering. Use **Add to tracker** on one.
- **AI & LLM Cost Monitor**:
  - The `aoai-ptu-gridplan` PTU deployment runs at about 23% utilization.
  - The customer-outage assistant has 14% retry waste.
  - Cost per *accepted* outcome is shown by app, with the suggested lever (routing, trimming context, fixing retries).

### 2. Optimize

The tracker follows a fixed sequence:
1. A FinOps analyst or engineer adds an item.
2. The **CFO** or **Technology Executive** approves it.
3. **Cloud Ops** or the **AI Program Owner** marks it implemented.
4. The **FinOps Analyst** verifies it. They cannot verify their own implementation.

Verified savings are measured as the scope's 30-day run-rate before implementation minus its run-rate after. With fewer than 14 days of post-change data the figure is marked *provisional*. OPT-004 (AMI Kinesis shards) is already implemented and ready to verify.

### 3. Regulatory

1. **Service-Company Accounting** submits an allocation policy (Regulatory → Allocation chain). AP-ANALYTICS v2 is already pending.
2. The **Internal Controller** approves it. CostIQ re-allocates from the policy's effective date, and the OpCo split changes.
3. The **Regulatory Accounting** analyst exports the **Rate-case bridge** CSV. It includes the verified-savings column.

### 4. Compliance

**NERC CIP Compliance** → Utility Compliance:
- UT-07: BCSI held in the cloud without customer-held keys.
- UT-08: CIP-scoped vendors missing CIP-013 supply-chain clauses.

### 5. Scope

**OpCo Finance Manager** sees only Meridian Electric Ohio's allocated cost and prudence lines. Shared pre-allocation charges and other entities are hidden, and reconciliation returns 403.

### 6. Alerts

The bell shows fired alerts, including token-budget and PTU-utilization alerts. Acknowledging one clears it from the count.

## Bringing in real data

Each file drop runs through the standard ingestion path: a manifest, schema checks, and idempotent replay. Templates are in `backend/data/templates/`.

| Data | Template | Command (from `backend/`, venv active) |
|---|---|---|
| SaaS invoices (SAP, Salesforce, ServiceNow, Oracle) | `saas_invoice_template.csv` | `python -m costiq.adapters.saas_invoice --period 2026-11 --path "landing/saas/*.csv"` |
| AWS Cost Optimization Hub export | `aws_cost_optimization_hub_template.csv` | `python -m costiq.adapters.recommendations --source aws_cost_optimization_hub --path "landing/coh/*.csv"` |
| Google Recommender export | `gcp_recommender_template.csv` | `python -m costiq.adapters.recommendations --source gcp_recommender --path "landing/gcp/*.csv"` |
| Azure Advisor | `azure_advisor_template.json` | `python -m costiq.adapters.recommendations --source azure_advisor --path "landing/advisor/*.json"` |
| LLM gateway or app telemetry | `llm_gateway_template.csv` | `python -m costiq.adapters.llm_gateway --path "landing/llm/*.csv"` |

With Docker, mount the landing folder and run the adapter in a one-off backend container. DuckDB allows only one writer, so stop the API first:

```powershell
docker compose stop backend
docker compose run --rm --no-deps -v ${PWD}/landing:/landing backend saas_invoice --period 2026-11 --path "/landing/saas/*.csv"
docker compose run --rm --no-deps backend cli refresh
docker compose start backend
```

The same pattern works for `recommendations` and `llm_gateway`, and for `cli allocate` and `cli conformance -v`.

Cloud billing comes in as FOCUS 1.x exports through CostIQ's existing ingestion. After loading, refresh the derived tables:
- `python -m costiq.cli allocate` re-runs allocation.
- `python -m costiq.cli refresh` re-runs allocation, optimization, alerts and prudence. It keeps the ledger. (`seed` is for the demo sample only: it resets the ledger and manifests.)

Outbound alert webhooks are off unless `COSTIQ_ALERT_WEBHOOK_URL` is set.

## Tests

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q                      # 183 tests; runs against a temp copy of the sample DB
.venv\Scripts\python.exe -m costiq.cli conformance -v      # every use case runs on the configured database
cd ..\frontend
npx eslint . ; npx next build
```

## Limits of the MVP

- Sign-in is demo personas only. The backend's Entra ID mode exists but is not wired to the web app.
- Data is synthetic. Rates of return, deferral orders and dockets are illustrative.
- CostIQ recommends and records changes. It never changes cloud resources itself.
- Providers are loaded from file drops and templates, not live API pulls.
- CostIQ gives decision support. It does not determine regulatory or NERC CIP compliance or predict any commission's ruling.
