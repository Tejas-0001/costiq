# MM CostIQ launcher (Windows).
#   .\start.ps1                  API on http://localhost:8080 (new window) + web app on http://localhost:3000
#   .\start.ps1 -Rebuild         regenerate the sample database and rebuild the web app first
#   .\start.ps1 -Dev             run the web app with `next dev` (hot reload) instead of the production build
#   .\start.ps1 -Tenant utility  serve another tenant pack (backend\tenants\<tenant>; default: COSTIQ_TENANT, else pharma)
param([switch]$Rebuild, [switch]$Dev, [int]$ApiPort = 8080, [int]$WebPort = 3000,
      [string]$Tenant = $(if ($env:COSTIQ_TENANT) { $env:COSTIQ_TENANT } else { "pharma" }))
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$web = Join-Path $root "frontend"

# Backend in its own window so its log stays readable.
$apiArgs = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $root "backend\start.ps1"), "-Port", $ApiPort, "-Tenant", $Tenant)
if ($Rebuild) { $apiArgs += "-Rebuild" }
Start-Process powershell -ArgumentList $apiArgs -WindowStyle Normal

Write-Host "Waiting for the API on port $ApiPort (first run builds the sample database)..."
$deadline = (Get-Date).AddMinutes(10)
while ($true) {
    try {
        Invoke-WebRequest "http://127.0.0.1:$ApiPort/api/me" -Headers @{ "X-CostIQ-Persona" = "cfo" } -UseBasicParsing -TimeoutSec 5 | Out-Null
        break
    } catch {
        if ((Get-Date) -gt $deadline) { throw "The API did not start; check the backend window." }
        Start-Sleep -Seconds 2
    }
}

Push-Location $web
try {
    if (-not (Test-Path "node_modules")) { npm install; if ($LASTEXITCODE -ne 0) { throw "npm install failed" } }
    $env:COSTIQ_API_URL = "http://127.0.0.1:$ApiPort"
    if ($Dev) {
        npx next dev -p $WebPort
    } else {
        if ($Rebuild -or -not (Test-Path ".next\BUILD_ID")) { npx next build; if ($LASTEXITCODE -ne 0) { throw "next build failed" } }
        Write-Host "MM CostIQ: http://localhost:$WebPort"
        npx next start -p $WebPort
    }
} finally {
    Pop-Location
}
