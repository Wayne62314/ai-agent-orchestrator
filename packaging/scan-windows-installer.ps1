[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$InstallerPath,
    [string]$EvidenceDirectory = "work\security-scan"
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
$scan = Start-Process `
    -FilePath $defender `
    -ArgumentList @("-Scan", "-ScanType", "3", "-File", $InstallerPath) `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -Wait `
    -PassThru
if ($scan.ExitCode -ne 0) {
    throw "Microsoft Defender scan failed or detected a threat (exit $($scan.ExitCode))."
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
    result = "no-threats"
    runnerImage = $env:ImageOS
    createdAtUtc = [DateTime]::UtcNow.ToString("O")
}
$evidencePath = Join-Path $EvidenceDirectory "windows-defender-scan.json"
[System.IO.File]::WriteAllText(
    $evidencePath,
    ($evidence | ConvertTo-Json -Depth 5) + "`n",
    [System.Text.UTF8Encoding]::new($false)
)
Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
Write-Host "Microsoft Defender reported no threats for $InstallerPath."
