$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Test-LocalProcess)) {
    Write-Output "STOPPED"
    exit 1
}

try {
    $health = Invoke-RestMethod -Uri "$script:LocalUrl/healthz" -TimeoutSec 3
    $ready = Invoke-RestMethod -Uri "$script:LocalUrl/readyz" -TimeoutSec 3
    Write-Output "RUNNING"
    Write-Output "healthz: $($health.status)"
    Write-Output "readyz: $($ready.status)"
} catch {
    Write-Output "RUNNING_BUT_UNHEALTHY"
    exit 1
}
