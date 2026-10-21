@echo off
REM Run as Administrator (right-click -> Run as administrator)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set-wms-hosts.ps1" %*
if errorlevel 1 pause
