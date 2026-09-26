@echo off
setlocal
cd /d "%~dp0"
echo Starting Adjutant services...
powershell -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if %ERRORLEVEL% neq 0 (
    echo.
    echo An error occurred while starting services.
    pause
)
