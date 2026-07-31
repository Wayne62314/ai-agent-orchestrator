[CmdletBinding()]
param(
    [string]$InstallerPath = "",
    [switch]$LaunchApplication
)

$ErrorActionPreference = "Stop"

if (-not $InstallerPath) {
    $candidates = @(
        Get-ChildItem -LiteralPath "dist\windows-installer" -Filter "*.exe" -File
    )
    if ($candidates.Count -ne 1) {
        throw "Expected exactly one collected Windows installer; found $($candidates.Count)."
    }
    $InstallerPath = $candidates[0].FullName
}
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path

$smokeId = [guid]::NewGuid().ToString("N")
$installRoot = Join-Path $env:TEMP "aiao-installer-smoke-$smokeId"
$mainExecutable = Join-Path $installRoot "aiao-desktop.exe"
$sidecarExecutable = Join-Path $installRoot "agent-orchestrator-sidecar.exe"
$uninstaller = Join-Path $installRoot "uninstall.exe"
$startMenuShortcut = Join-Path $env:APPDATA (
    "Microsoft\Windows\Start Menu\Programs\AI Agent Orchestrator\" +
    "AI Agent Orchestrator.lnk"
)
$desktopShortcut = Join-Path (
    [Environment]::GetFolderPath([Environment+SpecialFolder]::Desktop)
) "AI Agent Orchestrator.lnk"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$runValueName = "AI Agent Orchestrator"
$dataSentinels = @(
    Join-Path $env:APPDATA "io.aiao.desktop\installer-smoke-$smokeId.sentinel"
    Join-Path $env:LOCALAPPDATA "io.aiao.desktop\installer-smoke-$smokeId.sentinel"
)

function Invoke-InstallerProcess {
    param(
        [Parameter(Mandatory)]
        [string]$FilePath,
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "$FilePath exited with code $($process.ExitCode)."
    }
}

function Assert-PathExists {
    param([Parameter(Mandatory)][string]$LiteralPath)
    if (-not (Test-Path -LiteralPath $LiteralPath)) {
        throw "Expected path was not created: $LiteralPath"
    }
}

function Assert-LoginStartupAbsent {
    $value = Get-LoginStartupValue
    if ($null -ne $value) {
        throw "Login startup should be disabled by default."
    }
}

function Get-LoginStartupValue {
    $properties = Get-ItemProperty `
        -LiteralPath $runKey `
        -ErrorAction SilentlyContinue
    if ($null -eq $properties) {
        return $null
    }
    $property = $properties.PSObject.Properties[$runValueName]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Assert-UninstalledAndDataPreserved {
    if (Test-Path -LiteralPath $mainExecutable) {
        throw "The application executable remains after uninstall."
    }
    foreach ($sentinel in $dataSentinels) {
        Assert-PathExists $sentinel
    }
    Assert-LoginStartupAbsent
}

function Test-FirstApplicationLaunch {
    $database = Join-Path $env:APPDATA "io.aiao.desktop\state.db"
    $stdout = Join-Path $env:TEMP "aiao-first-launch-$smokeId.stdout.log"
    $stderr = Join-Path $env:TEMP "aiao-first-launch-$smokeId.stderr.log"
    if (Test-Path -LiteralPath $database) {
        throw "The candidate launch runner already contains an application database."
    }
    $application = Start-Process `
        -FilePath $mainExecutable `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(60)
        while (
            -not $application.HasExited -and
            -not (Test-Path -LiteralPath $database) -and
            [DateTime]::UtcNow -lt $deadline
        ) {
            Start-Sleep -Milliseconds 500
            $application.Refresh()
        }
        if ($application.HasExited) {
            throw "The installed desktop application exited during first launch."
        }
        if (-not (Test-Path -LiteralPath $database)) {
            $sidecars = @(
                Get-Process -Name "agent-orchestrator-sidecar" -ErrorAction SilentlyContinue
            )
            Write-Host (
                "First-launch diagnostics: appRunning=$(-not $application.HasExited); " +
                "sidecarCount=$($sidecars.Count); database=$database"
            )
            foreach ($log in @($stdout, $stderr)) {
                if (Test-Path -LiteralPath $log) {
                    Write-Host "--- $(Split-Path -Leaf $log) ---"
                    Get-Content -LiteralPath $log -Tail 100
                }
            }
            throw "The desktop backend did not initialize within 60 seconds."
        }
    }
    finally {
        if (-not $application.HasExited) {
            Stop-Process -Id $application.Id -Force
            $application.WaitForExit()
        }
        $sidecars = @(Get-Process -Name "agent-orchestrator-sidecar" -ErrorAction SilentlyContinue)
        if ($sidecars.Count -gt 0) {
            $sidecars | Wait-Process -Timeout 15 -ErrorAction Stop
        }
        Remove-Item -LiteralPath $stdout -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderr -Force -ErrorAction SilentlyContinue
    }
}

if (Test-Path -LiteralPath $installRoot) {
    throw "The isolated installer smoke directory already exists: $installRoot"
}

foreach ($sentinel in $dataSentinels) {
    $parent = Split-Path -Parent $sentinel
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    [System.IO.File]::WriteAllText($sentinel, "preserve", [System.Text.UTF8Encoding]::new($false))
}

try {
    Invoke-InstallerProcess $InstallerPath @("/S", "/D=$installRoot")
    Assert-PathExists $mainExecutable
    Assert-PathExists $sidecarExecutable
    Assert-PathExists $uninstaller
    Assert-PathExists $startMenuShortcut
    Assert-PathExists $desktopShortcut
    Assert-LoginStartupAbsent

    $selfCheck = & $sidecarExecutable --self-check | ConvertFrom-Json
    if (-not $selfCheck.healthy) {
        throw "The installed sidecar self-check did not report healthy."
    }
    if ($LaunchApplication) {
        Test-FirstApplicationLaunch
    }

    Invoke-InstallerProcess $uninstaller @("/S")
    Assert-UninstalledAndDataPreserved

    Invoke-InstallerProcess $InstallerPath @("/S", "/AUTOSTART", "/D=$installRoot")
    $startupCommand = Get-LoginStartupValue
    if ($startupCommand -notlike "*aiao-desktop.exe*") {
        throw "Explicit /AUTOSTART did not register the installed application."
    }

    Invoke-InstallerProcess $uninstaller @("/S")
    Assert-UninstalledAndDataPreserved
}
finally {
    foreach ($sentinel in $dataSentinels) {
        Remove-Item -LiteralPath $sentinel -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $startMenuShortcut -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $desktopShortcut -Force -ErrorAction SilentlyContinue
}

Write-Host "Windows installer install/uninstall smoke test passed."
