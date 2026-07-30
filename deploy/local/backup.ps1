param(
    [int]$Keep = 30
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Test-Path -LiteralPath $script:PythonExecutable)) {
    throw "Run deploy\local\install.ps1 first."
}

& $script:PythonExecutable `
    (Join-Path $script:RepoRoot "deploy\local_runtime.py") `
    backup `
    --database $script:DatabasePath `
    --destination $script:BackupDirectory `
    --keep $Keep
exit $LASTEXITCODE
