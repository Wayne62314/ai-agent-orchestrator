param(
    [Parameter(Mandatory = $true)]
    [string]$Backup,
    [switch]$ConfirmReplace
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

if (-not $ConfirmReplace) {
    throw "Restore requires -ConfirmReplace."
}
if (Test-LocalProcess) {
    throw "Run deploy\local\stop.ps1 before restoring."
}

& $script:PythonExecutable `
    (Join-Path $script:RepoRoot "deploy\local_runtime.py") `
    restore `
    --backup (Resolve-Path -LiteralPath $Backup) `
    --database $script:DatabasePath `
    --pid-file $script:PidPath `
    --confirm-replace
exit $LASTEXITCODE
