param(
    [switch]$RegisterStartup
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

foreach ($directory in @(
    $script:RuntimeRoot,
    (Split-Path $script:DatabasePath -Parent),
    $script:BackupDirectory,
    $script:LogDirectory,
    (Split-Path $script:SecretPath -Parent)
)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

if (-not (Test-Path -LiteralPath $script:PythonExecutable)) {
    $systemPython = (Get-Command python -ErrorAction Stop).Source
    & $systemPython -m venv $script:VirtualEnvironment
}

& $script:PythonExecutable -m pip install --disable-pip-version-check -e $script:RepoRoot

if (-not (Test-Path -LiteralPath $script:SecretPath)) {
    $bytes = New-Object byte[] 48
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    $secret = [Convert]::ToBase64String($bytes)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($script:SecretPath, $secret, $encoding)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $accessRule = [Security.AccessControl.FileSystemAccessRule]::new(
        $identity,
        "FullControl",
        "Allow"
    )
    $accessControl = [Security.AccessControl.FileSecurity]::new()
    $accessControl.SetAccessRuleProtection($true, $false)
    $accessControl.AddAccessRule($accessRule)
    Set-Acl -LiteralPath $script:SecretPath -AclObject $accessControl
}

if (-not (Test-Path -LiteralPath $script:DatabasePath)) {
    & $script:PythonExecutable -m agent_orchestrator `
        --db $script:DatabasePath `
        init
}

if ($RegisterStartup) {
    $runScript = Join-Path $PSScriptRoot "run.ps1"
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive `
        -RunLevel Limited
    Register-ScheduledTask `
        -TaskName $script:TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null
    Write-Output "Registered login task: $script:TaskName"

    $backupScript = Join-Path $PSScriptRoot "backup.ps1"
    $backupAction = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$backupScript`""
    $backupTrigger = New-ScheduledTaskTrigger -Daily -At "03:00"
    Register-ScheduledTask `
        -TaskName $script:BackupTaskName `
        -Action $backupAction `
        -Trigger $backupTrigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null
    Write-Output "Registered daily backup task: $script:BackupTaskName"
}

Write-Output "Local runtime installed at: $script:RuntimeRoot"
Write-Output "Webhook secret file: $script:SecretPath"
Write-Output "Run deploy\local\start.ps1 to start the service."
