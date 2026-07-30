[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

$configPath = Join-Path $RepositoryRoot "desktop\src-tauri\tauri.conf.json"
$bundleDirectory = Join-Path $RepositoryRoot "desktop\src-tauri\target\release\bundle\nsis"
$outputDirectory = Join-Path $RepositoryRoot "dist\windows-installer"

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Tauri configuration was not found: $configPath"
}

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$installers = @(
    Get-ChildItem -LiteralPath $bundleDirectory -Filter "*-setup.exe" -File
)
if ($installers.Count -ne 1) {
    throw "Expected exactly one NSIS setup executable in $bundleDirectory; found $($installers.Count)."
}

$sourceInstaller = $installers[0]
$stream = [System.IO.File]::OpenRead($sourceInstaller.FullName)
try {
    $firstByte = $stream.ReadByte()
    $secondByte = $stream.ReadByte()
}
finally {
    $stream.Dispose()
}
if ($firstByte -ne 0x4D -or $secondByte -ne 0x5A) {
    throw "The NSIS artifact is not a Windows PE executable: $($sourceInstaller.FullName)"
}

New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$artifactName = "AI-Agent-Orchestrator-$($config.version)-x64-setup.exe"
$artifactPath = Join-Path $outputDirectory $artifactName
Copy-Item -LiteralPath $sourceInstaller.FullName -Destination $artifactPath -Force

$artifact = Get-Item -LiteralPath $artifactPath
$sha256 = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumPath = "$artifactPath.sha256"
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText(
    $checksumPath,
    "$sha256  $artifactName`n",
    $utf8WithoutBom
)

$manifest = [ordered]@{
    schemaVersion = 1
    productName = $config.productName
    appVersion = $config.version
    targetTriple = "x86_64-pc-windows-msvc"
    installerType = "nsis"
    installMode = $config.bundle.windows.nsis.installMode
    file = $artifactName
    sizeBytes = $artifact.Length
    sha256 = $sha256
    sourceFile = $sourceInstaller.Name
    createdAtUtc = [DateTime]::UtcNow.ToString("O")
}
$manifestPath = Join-Path $outputDirectory "AI-Agent-Orchestrator-$($config.version)-x64-setup.build.json"
[System.IO.File]::WriteAllText(
    $manifestPath,
    ($manifest | ConvertTo-Json -Depth 5) + "`n",
    $utf8WithoutBom
)

Write-Host "Collected installer: $artifactPath"
Write-Host "SHA-256: $sha256"
Write-Host "Build manifest: $manifestPath"
