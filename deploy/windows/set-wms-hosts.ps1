#Requires -RunAsAdministrator
param(
    [string]$ServerIP = "192.168.1.109",
    [string]$Hostname = "wms.bolrus.ru"
)

$ErrorActionPreference = "Stop"
$hostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$marker = "# WMS BolRus"
$entry = "$ServerIP`t$Hostname`t$marker"

if (-not (Test-Path $hostsPath)) {
    throw "hosts file not found: $hostsPath"
}

$content = Get-Content -Path $hostsPath -ErrorAction Stop
$filtered = @(
    $content | Where-Object {
        $_ -notmatch '(?i)^\s*\d+\.\d+\.\d+\.\d+\s+wms\.bolrus\.ru(\s|$)' -and
        $_ -notmatch '(?i)WMS BolRus'
    }
)
$filtered += $entry

# ASCII avoids PowerShell encoding/parser issues on Windows
Set-Content -Path $hostsPath -Value $filtered -Encoding Ascii

ipconfig /flushdns | Out-Null

Write-Host "OK: $Hostname -> $ServerIP"
Write-Host "Open: https://$Hostname/"
