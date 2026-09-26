@echo off
setlocal
cd /d "%~dp0"
echo Stopping all Adjutant services...
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1"
echo Done.
