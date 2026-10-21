@echo off
:: Запуск от имени администратора: ПКМ по файлу → «Запуск от имени администратора»
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set-wms-hosts.ps1" %*
if errorlevel 1 pause
