#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Прописывает wms.bolrus.ru на сервер в локальной сети (hosts).

.DESCRIPTION
  Запуск от имени администратора (ПКМ → «Запуск от имени администратора»).
  По умолчанию: 192.168.1.109 wms.bolrus.ru

.EXAMPLE
  .\set-wms-hosts.ps1
.EXAMPLE
  .\set-wms-hosts.ps1 -ServerIP 192.168.1.109
#>
param(
    [string]$ServerIP = "192.168.1.109",
    [string]$Hostname = "wms.bolrus.ru"
)

$ErrorActionPreference = "Stop"
$hostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$marker = "# WMS BolRus"
$entry = "$ServerIP`t$Hostname`t$marker"

if (-not (Test-Path $hostsPath)) {
    throw "Не найден файл hosts: $hostsPath"
}

$content = Get-Content -Path $hostsPath -ErrorAction Stop
$filtered = $content | Where-Object {
    $_ -notmatch '(?i)^\s*\d+\.\d+\.\d+\.\d+\s+wms\.bolrus\.ru(\s|$)' -and
    $_ -notmatch '(?i)^\s*#\s*WMS BolRus\s*$'
}

$filtered = @($filtered) + $entry
Set-Content -Path $hostsPath -Value $filtered -Encoding ASCII

ipconfig /flushdns | Out-Null

Write-Host "OK: $Hostname -> $ServerIP"
Write-Host "Проверка: https://$Hostname"
Write-Host "Открыть в браузере: https://$Hostname/"
