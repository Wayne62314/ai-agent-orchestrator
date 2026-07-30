[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$TargetTriple = "x86_64-pc-windows-msvc"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outputDirectory = Join-Path $repositoryRoot "desktop\src-tauri\binaries"
$workDirectory = Join-Path $repositoryRoot "build\windows-sidecar"
$distDirectory = Join-Path $workDirectory "dist"
$specFile = Join-Path $PSScriptRoot "windows-sidecar.spec"
$bundleDirectory = Join-Path $distDirectory "agent-orchestrator-sidecar"
$unsuffixedExecutable = Join-Path $bundleDirectory "agent-orchestrator-sidecar.exe"
$sourceRuntimeDirectory = Join-Path $bundleDirectory "agent-orchestrator-sidecar-runtime"
$targetExecutable = Join-Path $outputDirectory "agent-orchestrator-sidecar-$TargetTriple.exe"
$targetRuntimeDirectory = Join-Path $outputDirectory "agent-orchestrator-sidecar-runtime"
$manifestPath = Join-Path $outputDirectory "agent-orchestrator-sidecar-$TargetTriple.build.json"

if ($env:OS -ne "Windows_NT") {
    throw "The Windows sidecar must be built on Windows."
}
if ($TargetTriple -ne "x86_64-pc-windows-msvc") {
    throw "Stage 10 currently supports only x86_64-pc-windows-msvc."
}

$pythonVersion = & $Python -c "import platform; print(platform.python_version())"
if ($LASTEXITCODE -ne 0) {
    throw "Python could not be started."
}
$pythonArchitecture = & $Python -c "import platform; print(platform.machine().lower())"
if ($pythonArchitecture -notin @("amd64", "x86_64")) {
    throw "A 64-bit x86 Python interpreter is required."
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $workDirectory | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $distDirectory `
    --workpath (Join-Path $workDirectory "work") `
    $specFile
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $unsuffixedExecutable -PathType Leaf)) {
    throw "PyInstaller did not produce the expected Windows sidecar."
}

Copy-Item -LiteralPath $unsuffixedExecutable -Destination $targetExecutable -Force
if (-not (Test-Path -LiteralPath $sourceRuntimeDirectory -PathType Container)) {
    throw "PyInstaller did not produce the expected private runtime directory."
}
$resolvedOutput = [System.IO.Path]::GetFullPath($outputDirectory)
$resolvedRuntime = [System.IO.Path]::GetFullPath($targetRuntimeDirectory)
if (-not $resolvedRuntime.StartsWith($resolvedOutput + [System.IO.Path]::DirectorySeparatorChar)) {
    throw "Refusing to replace a runtime directory outside the sidecar output directory."
}
if (Test-Path -LiteralPath $targetRuntimeDirectory) {
    Remove-Item -LiteralPath $targetRuntimeDirectory -Recurse -Force
}
Copy-Item -LiteralPath $sourceRuntimeDirectory -Destination $targetRuntimeDirectory -Recurse

$selfCheck = & $targetExecutable --self-check
if ($LASTEXITCODE -ne 0) {
    throw "The frozen sidecar self-check failed."
}
$selfCheckObject = $selfCheck | ConvertFrom-Json
if (-not $selfCheckObject.healthy) {
    throw "The frozen sidecar reported an unhealthy runtime."
}

$file = Get-Item -LiteralPath $targetExecutable
$hash = Get-FileHash -LiteralPath $targetExecutable -Algorithm SHA256
$manifest = [ordered]@{
    schemaVersion = 1
    targetTriple = $TargetTriple
    fileName = $file.Name
    sizeBytes = $file.Length
    runtimeSizeBytes = (
        Get-ChildItem -LiteralPath $targetRuntimeDirectory -Recurse -File |
            Measure-Object -Property Length -Sum
    ).Sum
    sha256 = $hash.Hash.ToLowerInvariant()
    pythonVersion = $pythonVersion
    pyinstallerVersion = (& $Python -m PyInstaller --version)
    applicationVersion = $selfCheckObject.applicationVersion
    codexSdkVersion = $selfCheckObject.codexSdkVersion
    codexRuntime = $selfCheckObject.codexRuntime
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding utf8

Write-Host "Built $targetExecutable"
Write-Host "SHA256 $($manifest.sha256)"
