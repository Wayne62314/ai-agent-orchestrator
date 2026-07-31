[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$InstallerPath,
    [Parameter(Mandatory)]
    [ValidatePattern("^[0-9a-f]{40}$")]
    [string]$Commit,
    [Parameter(Mandatory)]
    [string]$Version,
    [Parameter(Mandatory)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
$outputParent = Split-Path -Parent $OutputPath
if ($outputParent) {
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
}

function Get-Sha256 {
    param([Parameter(Mandatory)][string]$LiteralPath)
    $stream = [System.IO.File]::OpenRead($LiteralPath)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return -join @(
            $algorithm.ComputeHash($stream) |
                ForEach-Object { $_.ToString("x2") }
        )
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

$windows = Get-ItemProperty `
    -LiteralPath "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
$operatingSystem = Get-CimInstance -ClassName Win32_OperatingSystem
$productName = [string]$operatingSystem.Caption
$displayVersion = [string]$windows.DisplayVersion
$build = "$($windows.CurrentBuild).$($windows.UBR)"
$buildNumber = [int]$operatingSystem.BuildNumber
if ($buildNumber -eq 19045 -and $displayVersion -eq "22H2") {
    $target = "windows-10-22h2"
}
elseif ($buildNumber -ge 22000) {
    $target = "windows-11"
}
else {
    throw (
        "This client is outside the v1.0 acceptance matrix: " +
        "$productName $displayVersion build $build"
    )
}

$checkIds = @(
    "install.interactive"
    "launch.first"
    "launch.no-console"
    "auth.codex"
    "account.plan-truthful"
    "repository.select"
    "repository.changeable"
    "task.fields-empty"
    "task.create-feedback"
    "task.real"
    "acceptance.no-commands"
    "acceptance.evidence-separated"
    "accessibility.zoom-200"
    "accessibility.keyboard"
    "notification.local"
    "uninstall.copy"
    "feedback.recorded"
)
$checks = @(
    foreach ($checkId in $checkIds) {
        [ordered]@{
            id = $checkId
            status = "not-tested"
            notes = ""
        }
    }
)
$report = [ordered]@{
    schemaVersion = 2
    target = $target
    candidate = [ordered]@{
        version = $Version
        commit = $Commit
        installerFile = Split-Path -Leaf $InstallerPath
        installerSha256 = Get-Sha256 -LiteralPath $InstallerPath
    }
    environment = [ordered]@{
        productName = $productName
        displayVersion = $displayVersion
        build = $build
        architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    }
    recordedAtUtc = [DateTime]::UtcNow.ToString("o")
    checks = $checks
}

$json = $report | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText(
    $OutputPath,
    $json,
    [System.Text.UTF8Encoding]::new($false)
)
Write-Host "Created $target acceptance worksheet: $OutputPath"
Write-Host "Every check starts as not-tested and must be supported by an observation."
