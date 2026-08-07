[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BaselineInstaller,
    [Parameter(Mandatory)]
    [string]$CandidateInstaller,
    [string]$EvidenceDirectory = "work\upgrade-evidence"
)

$ErrorActionPreference = "Stop"
$BaselineInstaller = (Resolve-Path -LiteralPath $BaselineInstaller).Path
$CandidateInstaller = (Resolve-Path -LiteralPath $CandidateInstaller).Path
$EvidenceDirectory = [System.IO.Path]::GetFullPath($EvidenceDirectory)

if ([System.IO.Path]::GetFileName($BaselineInstaller) -notmatch "0\.10\.0") {
    throw "The baseline installer is not the approved 0.10.0 build."
}
if ([System.IO.Path]::GetFileName($CandidateInstaller) -notmatch "0\.13\.1") {
    throw "The candidate installer is not the expected 0.13.1 build."
}

$upgradeId = [guid]::NewGuid().ToString("N")
$testRoot = Join-Path $env:TEMP "aiao-upgrade-$upgradeId"
$installRoot = Join-Path $testRoot "application"
$dataRoot = Join-Path $testRoot "data"
$database = Join-Path $dataRoot "state.db"
$repository = Join-Path $testRoot "repository"
$preservedBackup = Join-Path $dataRoot "backups\user-preserved.sentinel"
New-Item -ItemType Directory -Path $EvidenceDirectory -Force | Out-Null

function Invoke-CheckedProcess {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments
    )
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "$FilePath exited with code $($process.ExitCode)."
    }
}

function Invoke-CheckedGit {
    param([Parameter(Mandatory)][string[]]$Arguments)
    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git failed with exit code $LASTEXITCODE."
    }
}

function Invoke-SidecarRequest {
    param(
        [Parameter(Mandatory)][string]$Sidecar,
        [Parameter(Mandatory)][hashtable]$Request,
        [switch]$AllowLegacyOutputEncodingFailure
    )
    $requestId = [string]$Request.id
    $exchangeId = [guid]::NewGuid().ToString("N")
    $requestPath = Join-Path $EvidenceDirectory "$exchangeId.request.json"
    $responsePath = Join-Path $EvidenceDirectory "$exchangeId.response.json"
    $errorPath = Join-Path $EvidenceDirectory "$exchangeId.stderr.log"
    $wrapperName = "$exchangeId.sidecar.cmd"
    $wrapperPath = Join-Path $testRoot $wrapperName
    $payload = ($Request | ConvertTo-Json -Depth 12 -Compress) + "`n"
    [System.IO.File]::WriteAllText(
        $requestPath,
        $payload,
        [System.Text.UTF8Encoding]::new($false)
    )
    # File-handle redirection avoids the environment-dependent StreamWriter
    # behavior in Windows PowerShell 5.1. The one-use wrapper also guarantees
    # frozen Python emits UTF-8 without changing the parent environment.
    $wrapper = @(
        "@echo off",
        "set `"PYTHONUTF8=1`"",
        "set `"PYTHONIOENCODING=utf-8`"",
        "`"$Sidecar`" --db `"$database`" --data-root `"$dataRoot`""
    ) -join "`r`n"
    [System.IO.File]::WriteAllText(
        $wrapperPath,
        $wrapper + "`r`n",
        [System.Text.Encoding]::Default
    )
    $process = Start-Process `
        -FilePath $env:ComSpec `
        -ArgumentList @("/d", "/c", $wrapperName) `
        -WorkingDirectory $testRoot `
        -RedirectStandardInput $requestPath `
        -RedirectStandardOutput $responsePath `
        -RedirectStandardError $errorPath `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    $stdout = [System.IO.File]::ReadAllText(
        $responsePath,
        [System.Text.UTF8Encoding]::new($false)
    )
    $stderr = [System.IO.File]::ReadAllText(
        $errorPath,
        [System.Text.UTF8Encoding]::new($false)
    )
    if ($process.ExitCode -ne 0) {
        $knownLegacyFailure = (
            $AllowLegacyOutputEncodingFailure -and
            -not $stdout.Trim() -and
            $stderr -match "UnicodeEncodeError: 'charmap' codec can't encode"
        )
        if ($knownLegacyFailure) {
            return $null
        }
        throw "The packaged sidecar request $requestId failed: $stderr"
    }
    $lines = @($stdout -split "\r?\n" | Where-Object { $_.Trim() })
    if ($lines.Count -ne 1) {
        throw "Expected one sidecar response; received $($lines.Count)."
    }
    $response = $lines[0] | ConvertFrom-Json
    if ($null -ne $response.error) {
        throw (
            "The packaged sidecar request $requestId returned an error: " +
            $response.error.message
        )
    }
    return $response.result
}

New-Item -ItemType Directory -Path $repository -Force | Out-Null
Invoke-CheckedGit @("-C", $repository, "init")
Invoke-CheckedGit @("-C", $repository, "config", "user.name", "Installer Upgrade Test")
Invoke-CheckedGit @(
    "-C", $repository, "config", "user.email", "installer-upgrade@example.invalid"
)
[System.IO.File]::WriteAllText(
    (Join-Path $repository "README.md"),
    "upgrade baseline`n",
    [System.Text.UTF8Encoding]::new($false)
)
Invoke-CheckedGit @("-C", $repository, "add", "README.md")
Invoke-CheckedGit @("-C", $repository, "commit", "-m", "upgrade baseline")

Invoke-CheckedProcess $BaselineInstaller @("/S", "/D=$installRoot")
$baselineMain = Join-Path $installRoot "aiao-desktop.exe"
$baselineSidecar = Join-Path $installRoot "agent-orchestrator-sidecar.exe"
foreach ($path in @($baselineMain, $baselineSidecar)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "The baseline install is missing: $path"
    }
}
$baselineMainHash = (Get-FileHash -LiteralPath $baselineMain -Algorithm SHA256).Hash

$created = Invoke-SidecarRequest -Sidecar $baselineSidecar -Request @{
    protocol = "aiao.desktop.v1"
    id = "baseline-create"
    method = "task/create"
    params = @{
        input = @{
            title = "Preserved upgrade task"
            objective = "Remain queryable after the installer and Schema upgrade."
            repository = $repository
            permission = "read-only"
            checks = @("cmd /c exit 0")
            maxRepairs = 0
        }
        expectedVersion = 0
        idempotencyKey = "stage-10-real-upgrade-task"
    }
} -AllowLegacyOutputEncodingFailure
$legacyBaselineEncodingFailure = $null -eq $created
if ($legacyBaselineEncodingFailure) {
    if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
        throw "The legacy sidecar did not persist its database before exiting."
    }
    $worktreeRoot = Join-Path $dataRoot "worktrees"
    $worktrees = @(
        Get-ChildItem -LiteralPath $worktreeRoot -Directory -ErrorAction Stop
    )
    if ($worktrees.Count -ne 1 -or $worktrees[0].Name -notmatch "^task_") {
        throw "The legacy sidecar did not persist exactly one real task worktree."
    }
    $taskId = $worktrees[0].Name
    $worktree = $worktrees[0].FullName
}
else {
    if ($created.state -ne "READY") {
        throw "The baseline task was not prepared successfully."
    }
    $taskId = $created.id
    $worktree = $created.workspacePath
}
New-Item -ItemType Directory -Path (Split-Path -Parent $preservedBackup) -Force |
    Out-Null
[System.IO.File]::WriteAllText(
    $preservedBackup,
    "preserve through upgrade",
    [System.Text.UTF8Encoding]::new($false)
)

Invoke-CheckedProcess $CandidateInstaller @("/S", "/D=$installRoot")
$candidateMainHash = (
    Get-FileHash -LiteralPath $baselineMain -Algorithm SHA256
).Hash
$candidateSidecar = Join-Path $installRoot "agent-orchestrator-sidecar.exe"
$snapshot = Invoke-SidecarRequest $candidateSidecar @{
    protocol = "aiao.desktop.v1"
    id = "candidate-initialize"
    method = "system/initialize"
    params = @{}
}
$snapshotJson = $snapshot | ConvertTo-Json -Depth 20 -Compress
if ($snapshotJson -notlike "*$taskId*" -or $snapshotJson -notlike "*Preserved upgrade task*") {
    throw "The upgraded application cannot query the baseline task."
}
foreach ($path in @($worktree, $preservedBackup)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Upgrade removed preserved user data: $path"
    }
}
$manifest = Get-ChildItem `
    -LiteralPath (Join-Path $dataRoot "backups\pre-upgrade") `
    -Filter "*.manifest.json" `
    -File
if (@($manifest).Count -ne 1) {
    throw "Expected one pre-upgrade manifest; found $(@($manifest).Count)."
}
$migrationEvidence = Get-Content -LiteralPath $manifest.FullName -Raw |
    ConvertFrom-Json
if (
    $migrationEvidence.sourceDatabaseSchema -ne 6 -or
    $migrationEvidence.targetDatabaseSchema -ne 7
) {
    throw "The real upgrade did not record the expected Schema 6 to 7 migration."
}

$uninstaller = Join-Path $installRoot "uninstall.exe"
Invoke-CheckedProcess $uninstaller @("/S")
foreach ($path in @($database, $worktree, $preservedBackup, $manifest.FullName)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Default uninstall removed protected user data: $path"
    }
}

$evidence = [ordered]@{
    schemaVersion = 1
    baselineVersion = "0.10.0"
    candidateVersion = "0.13.1"
    sourceDatabaseSchema = 6
    targetDatabaseSchema = 7
    taskId = $taskId
    taskPreserved = $true
    worktreePreserved = $true
    backupPreserved = $true
    legacyBaselineEncodingFailureVerified = $legacyBaselineEncodingFailure
    preUpgradeBackupVerified = $true
    defaultUninstallPreservedData = $true
    baselineMainSha256 = $baselineMainHash.ToLowerInvariant()
    candidateMainSha256 = $candidateMainHash.ToLowerInvariant()
    runnerImage = $env:ImageOS
    createdAtUtc = [DateTime]::UtcNow.ToString("O")
}
$evidencePath = Join-Path $EvidenceDirectory "windows-upgrade-evidence.json"
[System.IO.File]::WriteAllText(
    $evidencePath,
    ($evidence | ConvertTo-Json -Depth 5) + "`n",
    [System.Text.UTF8Encoding]::new($false)
)
Write-Host "Windows 0.10.0 to 0.13.1 upgrade validation passed."
