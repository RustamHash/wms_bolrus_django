$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = [Console]::OutputEncoding

function Fail([string]$Message) {
    [Console]::Error.WriteLine($Message)
    exit 1
}

$docx = $env:WMS_DOCX
$pdf = $env:WMS_PDF_OUT
if (-not $docx -or -not (Test-Path -LiteralPath $docx)) { Fail 'DOCX_MISSING' }
if (-not $pdf) { Fail 'PDF_MISSING' }

$word = $null
$doc = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($docx, $false, $true, $false)
    # 17 = wdExportFormatPDF
    $doc.ExportAsFixedFormat($pdf, 17)
} catch {
    Fail $_.Exception.GetBaseException().Message
} finally {
    if ($doc -ne $null) { $doc.Close($false) | Out-Null }
    if ($word -ne $null) { $word.Quit() | Out-Null }
}
