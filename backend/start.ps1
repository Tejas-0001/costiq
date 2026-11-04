# MM CostIQ backend launcher (Windows).
#   .\start.ps1                    start the API on http://localhost:8080 (builds the sample database on first run)
#   .\start.ps1 -Rebuild           regenerate the sample database, then start
#   .\start.ps1 -Tenant utility    serve another tenant pack (backend\tenants\<tenant>; default: COSTIQ_TENANT, else pharma)
param([switch]$Rebuild, [int]$Port = 8080, [string]$Tenant = $(if ($env:COSTIQ_TENANT) { $env:COSTIQ_TENANT } else { "pharma" }))
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path (Join-Path $root "tenants\$Tenant\pack.yaml"))) { throw "Unknown tenant '$Tenant' (no backend\tenants\$Tenant\pack.yaml)" }
if (-not (Test-Path $py)) {
    Write-Host "First run: creating the Python 3.12 environment..."
    uv venv --python 3.12 (Join-Path $root ".venv")
    uv pip install --python $py -r (Join-Path $root "requirements.txt") -r (Join-Path $root "requirements-dev.txt")
}

$env:COSTIQ_TENANT = $Tenant
$db = Join-Path $root "sample\$Tenant\costiq.duckdb"
$env:COSTIQ_DB_URL = "duckdb:///" + ($db -replace "\\", "/")

if ($Rebuild -or -not (Test-Path $db)) {
    Write-Host "Building the $Tenant sample database..."
    & $py (Join-Path $root "data\build_sample.py") --tenant $Tenant
    if ($LASTEXITCODE -ne 0) { throw "Sample build failed" }
}

Push-Location $root
try {
    & $py -m uvicorn costiq.main:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
