@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-windows11-golden-journey.ps1" -LaunchInstaller
set "AIAO_EXIT=%ERRORLEVEL%"
echo.
if "%AIAO_EXIT%"=="2" (
  echo Acceptance is incomplete. You can run this file again to continue.
) else if not "%AIAO_EXIT%"=="0" (
  echo Acceptance could not start or encountered an error. Exit code: %AIAO_EXIT%
)
pause
exit /b %AIAO_EXIT%
