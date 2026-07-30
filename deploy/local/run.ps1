$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

if (Test-LocalProcess) {
    throw "The local orchestrator is already running."
}
if (-not (Test-Path -LiteralPath $script:PythonExecutable)) {
    throw "Run deploy\local\install.ps1 first."
}
if (-not (Test-Path -LiteralPath $script:SecretPath)) {
    throw "Webhook secret file is missing. Run install.ps1 again."
}

$env:ORCHESTRATOR_GITHUB_WEBHOOK_SECRET = (
    Get-Content -LiteralPath $script:SecretPath -Raw
).Trim()
$date = Get-Date -Format "yyyyMMdd"
$standardOutput = Join-Path $script:LogDirectory (
    "orchestrator-$date.stdout.log"
)
$standardError = Join-Path $script:LogDirectory (
    "orchestrator-$date.stderr.log"
)
$arguments = (
    "-m agent_orchestrator --db `"$script:DatabasePath`" " +
    "serve --host 127.0.0.1 --port 8080"
)
$process = Start-Process `
    -FilePath $script:PythonExecutable `
    -ArgumentList $arguments `
    -RedirectStandardOutput $standardOutput `
    -RedirectStandardError $standardError `
    -WindowStyle Hidden `
    -PassThru
$encoding = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($script:PidPath, "$($process.Id)", $encoding)

try {
    $process.WaitForExit()
    exit $process.ExitCode
} finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $script:PidPath -Force -ErrorAction SilentlyContinue
    Remove-Item Env:\ORCHESTRATOR_GITHUB_WEBHOOK_SECRET -ErrorAction SilentlyContinue
}
