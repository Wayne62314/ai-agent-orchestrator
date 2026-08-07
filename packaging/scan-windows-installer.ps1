[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$InstallerPath,
    [string]$EvidenceDirectory = "work\security-scan",
    [switch]$AllowInconclusive
)

$ErrorActionPreference = "Stop"
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
$EvidenceDirectory = [System.IO.Path]::GetFullPath($EvidenceDirectory)

$platformRoot = Join-Path $env:ProgramData "Microsoft\Windows Defender\Platform"
$platformCandidates = @(
    Get-ChildItem -LiteralPath $platformRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName "MpCmdRun.exe" }
)
$defenderCandidates = @(
    $platformCandidates
    (Join-Path $env:ProgramFiles "Windows Defender\MpCmdRun.exe")
)
$defender = $defenderCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if (-not $defender) {
    throw "Microsoft Defender command-line scanner is unavailable."
}

New-Item -ItemType Directory -Path $EvidenceDirectory -Force | Out-Null
$stdout = Join-Path $EvidenceDirectory "defender-stdout.log"
$stderr = Join-Path $EvidenceDirectory "defender-stderr.log"
$scanStartedUtc = [DateTime]::UtcNow
$scan = Start-Process `
    -FilePath $defender `
    -ArgumentList @("-Scan", "-ScanType", "3", "-File", $InstallerPath) `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -Wait `
    -PassThru

$stdoutText = if (Test-Path -LiteralPath $stdout) {
    Get-Content -LiteralPath $stdout -Raw
} else {
    ""
}
$stderrText = if (Test-Path -LiteralPath $stderr) {
    Get-Content -LiteralPath $stderr -Raw
} else {
    ""
}
$combinedOutput = "$stdoutText`n$stderrText"
$threatLines = @(
    $combinedOutput -split "`r?`n" |
        Where-Object {
            $_ -match "(?i)(threat\s+(detected|found)|malware\s+(detected|found)|virus\s+(detected|found)|found\s+and\s+not\s+remediated|user\s+action\s+required|threat\s+name)" -and
            $_ -notmatch "(?i)no\s+(threats?|malware|viruses?)\s+found"
        } |
        Select-Object -First 10
)
$threatDetections = @()
$threatQueryError = $null
try {
    $threatDetections = @(
        Get-MpThreatDetection -ErrorAction Stop |
            Where-Object {
                $resources = @($_.Resources) -join "`n"
                if ($resources -notmatch [regex]::Escape($InstallerPath)) {
                    return $false
                }
                $initialDetection = $_.InitialDetectionTime
                if ($null -eq $initialDetection) {
                    return $true
                }
                return (
                    ([DateTime]$initialDetection).ToUniversalTime() -ge
                    $scanStartedUtc.AddMinutes(-1)
                )
            }
    )
} catch {
    $threatQueryError = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
}

$threatDetected = $threatLines.Count -gt 0 -or $threatDetections.Count -gt 0
$result = if ($threatDetected) {
    "threat-detected"
} elseif ($scan.ExitCode -eq 0) {
    "no-threats"
} else {
    "scan-inconclusive"
}

$status = Get-MpComputerStatus
$sha256 = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash
$signatureUpdated = $null
if ($null -ne $status.AntivirusSignatureLastUpdated) {
    $signatureUpdated = (
        $status.AntivirusSignatureLastUpdated.ToUniversalTime().ToString("O")
    )
}
$evidence = [ordered]@{
    schemaVersion = 1
    scanner = "Microsoft Defender"
    scannerExecutable = [System.IO.Path]::GetFileName($defender)
    antivirusEnabled = [bool]$status.AntivirusEnabled
    antispywareEnabled = [bool]$status.AntispywareEnabled
    realTimeProtectionEnabled = [bool]$status.RealTimeProtectionEnabled
    antivirusSignatureVersion = [string]$status.AntivirusSignatureVersion
    antivirusSignatureLastUpdated = $signatureUpdated
    file = [System.IO.Path]::GetFileName($InstallerPath)
    sizeBytes = (Get-Item -LiteralPath $InstallerPath).Length
    sha256 = $sha256.ToLowerInvariant()
    result = $result
    scannerExitCode = [int]$scan.ExitCode
    allowInconclusive = [bool]$AllowInconclusive
    threatLines = @($threatLines)
    threatDetectionCount = $threatDetections.Count
    threatQueryError = $threatQueryError
    runnerImage = $env:ImageOS
    createdAtUtc = [DateTime]::UtcNow.ToString("O")
}
$evidencePath = Join-Path $EvidenceDirectory "windows-defender-scan.json"
[System.IO.File]::WriteAllText(
    $evidencePath,
    ($evidence | ConvertTo-Json -Depth 5) + "`n",
    [System.Text.UTF8Encoding]::new($false)
)

if ($result -eq "threat-detected") {
    throw "Microsoft Defender detected a threat in $InstallerPath (exit $($scan.ExitCode))."
}
if ($result -eq "scan-inconclusive" -and -not $AllowInconclusive) {
    throw "Microsoft Defender scan was inconclusive for $InstallerPath (exit $($scan.ExitCode))."
}
if ($result -eq "scan-inconclusive") {
    Write-Warning "Microsoft Defender scan was inconclusive for $InstallerPath (exit $($scan.ExitCode)); evidence was recorded."
} else {
    Write-Host "Microsoft Defender reported no threats for $InstallerPath."
}
