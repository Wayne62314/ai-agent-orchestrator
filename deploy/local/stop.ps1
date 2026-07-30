$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$scheduledTask = Get-ScheduledTask `
    -TaskName $script:TaskName `
    -ErrorAction SilentlyContinue
if ($null -ne $scheduledTask -and $scheduledTask.State -eq "Running") {
    Stop-ScheduledTask -TaskName $script:TaskName
}

if (Test-LocalProcess) {
    $processId = [int](Get-Content -LiteralPath $script:PidPath -Raw).Trim()
    Stop-Process -Id $processId
    Wait-Process -Id $processId -Timeout 10 -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $script:PidPath -Force -ErrorAction SilentlyContinue
Write-Output "Local orchestrator is stopped."
