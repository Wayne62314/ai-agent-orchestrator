$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

if (Test-LocalProcess) {
    Write-Output "Local orchestrator is already running."
    exit 0
}

$scheduledTask = Get-ScheduledTask `
    -TaskName $script:TaskName `
    -ErrorAction SilentlyContinue
if ($null -ne $scheduledTask) {
    Start-ScheduledTask -TaskName $script:TaskName
} else {
    $runScript = Join-Path $PSScriptRoot "run.ps1"
    $launcherOutput = Join-Path $script:LogDirectory "launcher.stdout.log"
    $launcherError = Join-Path $script:LogDirectory "launcher.stderr.log"
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`"" `
        -RedirectStandardOutput $launcherOutput `
        -RedirectStandardError $launcherError `
        -WindowStyle Hidden
}

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "$script:LocalUrl/readyz" `
            -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Output "Local orchestrator is ready at $script:LocalUrl"
            exit 0
        }
    } catch {
        # The process may still be initializing.
    }
}

throw "The service did not become ready. Check .local-runtime\logs."
