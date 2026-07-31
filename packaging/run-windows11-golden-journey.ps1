[CmdletBinding()]
param(
    [string]$CandidateDirectory = "",
    [string]$EvidencePath = "",
    [switch]$LaunchInstaller
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($CandidateDirectory)) {
    $CandidateDirectory = $PSScriptRoot
}
$CandidateDirectory = (Resolve-Path -LiteralPath $CandidateDirectory).Path
if ([string]::IsNullOrWhiteSpace($EvidencePath)) {
    $EvidencePath = Join-Path $CandidateDirectory "windows11-acceptance.json"
}
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)

function Save-Evidence {
    param([Parameter(Mandatory)]$Report)
    [System.IO.File]::WriteAllText(
        $EvidencePath,
        ($Report | ConvertTo-Json -Depth 8) + "`n",
        $utf8WithoutBom
    )
}

function Read-Status {
    while ($true) {
        $value = (
            Read-Host "Result: [P]assed [F]ailed [B]locked [N]ot tested"
        ).Trim().ToLowerInvariant()
        switch ($value) {
            "p" { return "passed" }
            "f" { return "failed" }
            "b" { return "blocked" }
            "n" { return "not-tested" }
            default {
                Write-Host "Enter P, F, B, or N." -ForegroundColor Yellow
            }
        }
    }
}

$manifests = @(
    Get-ChildItem -LiteralPath $CandidateDirectory -Filter "*.build.json" -File
)
$installers = @(
    Get-ChildItem -LiteralPath $CandidateDirectory -Filter "*.exe" -File
)
if ($manifests.Count -ne 1 -or $installers.Count -ne 1) {
    throw (
        "The candidate directory must contain exactly one installer and " +
        "one build manifest."
    )
}
$manifest = Get-Content -LiteralPath $manifests[0].FullName -Raw |
    ConvertFrom-Json
$installer = $installers[0]
if ($manifest.file -ne $installer.Name) {
    throw "The build manifest does not match the installer file name."
}
if ($manifest.sourceCommit -notmatch "^[0-9a-f]{40}$") {
    throw "The build manifest does not contain a valid full Git commit."
}
$actualHash = (
    Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($actualHash -ne $manifest.sha256) {
    throw "The installer SHA-256 does not match. Do not install this file."
}

$worksheetScript = Join-Path (
    $CandidateDirectory
) "new-windows-client-acceptance.ps1"
if (-not (Test-Path -LiteralPath $EvidencePath -PathType Leaf)) {
    & $worksheetScript `
        -InstallerPath $installer.FullName `
        -Commit $manifest.sourceCommit `
        -Version $manifest.appVersion `
        -OutputPath $EvidencePath
}
$report = Get-Content -LiteralPath $EvidencePath -Raw | ConvertFrom-Json
if ($report.target -ne "windows-11") {
    throw "This recovery gate requires a real Windows 11 client."
}
if (
    $report.candidate.commit -ne $manifest.sourceCommit -or
    $report.candidate.installerSha256 -ne $actualHash
) {
    throw "The existing evidence belongs to a different candidate."
}

$journey = @(
    [ordered]@{
        id = "install.interactive"
        title = "Interactive installation"
        instruction = "The installer is clear and completes a current-user install."
    }
    [ordered]@{
        id = "launch.first"
        title = "First launch"
        instruction = "Open the app from Start; the main window remains available."
    }
    [ordered]@{
        id = "launch.no-console"
        title = "No extra console"
        instruction = "No command prompt or black console window appears."
    }
    [ordered]@{
        id = "auth.codex"
        title = "Codex sign-in"
        instruction = "Sign in with a real account. Never record email or credentials."
    }
    [ordered]@{
        id = "account.plan-truthful"
        title = "Truthful account information"
        instruction = "The UI shows only confirmed account facts and does not guess Free."
    }
    [ordered]@{
        id = "task.fields-empty"
        title = "No demo task content"
        instruction = "Task title, objective, and project commands start empty."
    }
    [ordered]@{
        id = "repository.select"
        title = "Select a real repository"
        instruction = "Browse to a real Git repository you choose."
    }
    [ordered]@{
        id = "repository.changeable"
        title = "Change repository"
        instruction = "Select another valid repo; invalid paths show a useful error."
    }
    [ordered]@{
        id = "task.create-feedback"
        title = "Visible task creation result"
        instruction = "Create shows success or an actionable error, never silence."
    }
    [ordered]@{
        id = "acceptance.no-commands"
        title = "No project commands required"
        instruction = "Add no command. The task can still be created with AI review."
    }
    [ordered]@{
        id = "task.real"
        title = "Complete a real task"
        instruction = "Have Codex make a small real change in the isolated workspace."
    }
    [ordered]@{
        id = "acceptance.evidence-separated"
        title = "Evidence types stay separate"
        instruction = "AI, command, and human evidence are separate; no false test claim."
    }
    [ordered]@{
        id = "accessibility.zoom-200"
        title = "200 percent display scaling"
        instruction = "At 200 percent scaling, primary pages remain readable and usable."
    }
    [ordered]@{
        id = "accessibility.keyboard"
        title = "Keyboard journey"
        instruction = "Complete primary creation and review actions using only keyboard."
    }
    [ordered]@{
        id = "notification.local"
        title = "Local notification"
        instruction = "A local notification appears when attention or completion occurs."
    }
    [ordered]@{
        id = "uninstall.copy"
        title = "Uninstall copy and behavior"
        instruction = "Copy is clear; uninstall succeeds and preserves data as stated."
    }
    [ordered]@{
        id = "feedback.recorded"
        title = "Overall feedback"
        instruction = "Record remaining product concerns or state that none were found."
    }
)

Write-Host ""
Write-Host "AI Agent Orchestrator - Windows 11 Golden Journey" -ForegroundColor Cyan
Write-Host "Version: $($manifest.appVersion)"
Write-Host "Commit: $($manifest.sourceCommit)"
Write-Host "SHA-256: $actualHash"
Write-Host "Evidence: $EvidencePath"
Write-Host ""
Write-Host (
    "Never enter email, tokens, API keys, or authentication output in notes."
) -ForegroundColor Yellow

if ($LaunchInstaller) {
    Write-Host ""
    Read-Host "Press Enter to launch the verified installer"
    $install = Start-Process -FilePath $installer.FullName -Wait -PassThru
    if ($install.ExitCode -ne 0) {
        throw "The installer exited with code $($install.ExitCode)."
    }
}

foreach ($item in $journey) {
    $check = $report.checks | Where-Object { $_.id -eq $item.id }
    if ($null -eq $check) {
        throw "The worksheet is missing check: $($item.id)"
    }
    if ($check.status -eq "passed") {
        continue
    }
    Write-Host ""
    Write-Host $item.title -ForegroundColor Cyan
    Write-Host $item.instruction
    $check.status = Read-Status
    if ($check.status -in @("failed", "blocked")) {
        do {
            $check.notes = Read-Host "Briefly describe the reproducible issue"
        } while (-not $check.notes.Trim())
    }
    elseif ($check.status -eq "passed") {
        $check.notes = Read-Host "Optional note (press Enter to skip)"
    }
    Save-Evidence -Report $report
}

$remaining = @($report.checks | Where-Object { $_.status -ne "passed" })
Write-Host ""
if ($remaining.Count -eq 0) {
    Write-Host "The Windows 11 golden journey passed." -ForegroundColor Green
    exit 0
}
Write-Host (
    "Acceptance is incomplete: {0} item(s) failed, blocked, or not tested. " +
    "Run this script again to continue." -f $remaining.Count
) -ForegroundColor Yellow
exit 2
