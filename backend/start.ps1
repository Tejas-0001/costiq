# MM CostIQ backend launcher (Windows).
#   .\start.ps1            start the API on http://localhost:8080 (builds the sample database on first run)
#   .\start.ps1 -Rebuild   regenerate the synthetic sample database, then start
param([switch]$Rebuild, [int]$Port = 8080)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "First run: creating the Python 3.12 environment..."
    uv venv --python 3.12 (Join-Path $root ".venv")
    uv pip install --python $py -r (Join-Path $root "requirements.txt") -r (Join-Path $root "requirements-dev.txt")
}

$db = Join-Path $root "sample\costiq.duckdb"
$env:COSTIQ_DB_URL = "duckdb:///" + ($db -replace "\\", "/")

if ($Rebuild -or -not (Test-Path $db)) {
    Write-Host "Building the synthetic sample database..."
    & $py (Join-Path $root "data\build_sample.py")
    if ($LASTEXITCODE -ne 0) { throw "Sample build failed" }
}

Push-Location $root
try {
    & $py -m uvicorn costiq.main:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
