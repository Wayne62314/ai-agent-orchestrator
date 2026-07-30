$script:RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$script:RuntimeRoot = Join-Path $script:RepoRoot ".local-runtime"
$script:VirtualEnvironment = Join-Path $script:RuntimeRoot "venv"
$script:PythonExecutable = Join-Path $script:VirtualEnvironment "Scripts\python.exe"
$script:DatabasePath = Join-Path $script:RuntimeRoot "data\state.db"
$script:BackupDirectory = Join-Path $script:RuntimeRoot "backups"
$script:LogDirectory = Join-Path $script:RuntimeRoot "logs"
$script:SecretPath = Join-Path $script:RuntimeRoot "secrets\github-webhook-secret.txt"
$script:PidPath = Join-Path $script:RuntimeRoot "orchestrator.pid"
$script:TaskName = "AI Agent Orchestrator Local"
$script:BackupTaskName = "AI Agent Orchestrator Local Backup"
$script:LocalUrl = "http://127.0.0.1:8080"

function Test-LocalProcess {
    if (-not (Test-Path -LiteralPath $script:PidPath)) {
        return $false
    }
    $rawPid = (Get-Content -LiteralPath $script:PidPath -Raw).Trim()
    if ($rawPid -notmatch '^\d+$') {
        return $false
    }
    return $null -ne (Get-Process -Id ([int]$rawPid) -ErrorAction SilentlyContinue)
}
