@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-windows11-golden-journey.ps1" -LaunchInstaller
set "AIAO_EXIT=%ERRORLEVEL%"
echo.
if not "%AIAO_EXIT%"=="0" echo Acceptance is incomplete. You can run this file again to continue.
pause
exit /b %AIAO_EXIT%
