$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = [Console]::OutputEncoding

function Fail([string]$Message) {
    [Console]::Error.WriteLine($Message)
    exit 1
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    Add-Type -AssemblyName System.Drawing

    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]

    $asTaskOperation = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.IsGenericMethod -and
        $_.GetGenericArguments().Count -eq 1 -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1

    $asTaskAction = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and
        -not $_.IsGenericMethod -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
    } | Select-Object -First 1

    function Await-Operation($Operation, $ResultType) {
        $method = $asTaskOperation.MakeGenericMethod($ResultType)
        $task = $method.Invoke($null, @($Operation))
        $task.Wait(-1) | Out-Null
        if ($task.IsFaulted) { throw $task.Exception.GetBaseException() }
        return $task.Result
    }

    function Await-Action($Action) {
        $task = $asTaskAction.Invoke($null, @($Action))
        $task.Wait(-1) | Out-Null
        if ($task.IsFaulted) { throw $task.Exception.GetBaseException() }
    }

    $pdfPath = $env:WMS_PDF
    $printerName = $env:WMS_PRINTER
    $jobName = $env:WMS_JOB
    if (-not $jobName) { $jobName = 'WMS' }
    if (-not $pdfPath -or -not (Test-Path -LiteralPath $pdfPath)) { Fail 'PDF_MISSING' }
    if (-not $printerName) { Fail 'PRINTER_MISSING' }

    $printDoc = New-Object System.Drawing.Printing.PrintDocument
    $printDoc.PrinterSettings.PrinterName = $printerName
    if (-not $printDoc.PrinterSettings.IsValid) { Fail 'PRINTER_NOT_FOUND' }
    $printDoc.DocumentName = $jobName
    $printDoc.PrintController = New-Object System.Drawing.Printing.StandardPrintController

    $file = Await-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($pdfPath)) ([Windows.Storage.StorageFile])
    $pdf = Await-Operation ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
    if ($pdf.PageCount -lt 1) { Fail 'PDF_EMPTY' }

    $pages = New-Object System.Collections.Generic.List[object]
    for ($i = 0; $i -lt $pdf.PageCount; $i++) {
        $page = $pdf.GetPage([uint32]$i)
        $dipW = [double]$page.Size.Width
        $dipH = [double]$page.Size.Height
        $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
        $options = New-Object Windows.Data.Pdf.PdfPageRenderOptions
        $options.DestinationWidth = [uint32]([Math]::Max(1, [Math]::Round($dipW * 300 / 96)))
        $options.DestinationHeight = [uint32]([Math]::Max(1, [Math]::Round($dipH * 300 / 96)))
        Await-Action ($page.RenderToStreamAsync($stream, $options))
        $stream.Seek(0) | Out-Null
        $size = [uint32]$stream.Size
        $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
        [void](Await-Operation ($reader.LoadAsync($size)) ([uint32]))
        $bytes = New-Object byte[] $size
        $reader.ReadBytes($bytes)
        $memory = New-Object System.IO.MemoryStream (,$bytes)
        $image = [System.Drawing.Image]::FromStream($memory)
        $pages.Add(@{
            Image = $image
            Stream = $memory
            WidthHi = ($dipW / 96.0 * 100.0)
            HeightHi = ($dipH / 96.0 * 100.0)
        })
    }

    $state = @{ Index = 0 }
    $handler = {
        param($sender, $e)
        $item = $pages[$state.Index]
        $clip = $e.Graphics.VisibleClipBounds
        $wantW = [single]$item.WidthHi
        $wantH = [single]$item.HeightHi
        $scale = 1.0
        if ($wantW -gt $clip.Width -or $wantH -gt $clip.Height) {
            $scale = [Math]::Min($clip.Width / $wantW, $clip.Height / $wantH)
        }
        $e.Graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $e.Graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $e.Graphics.DrawImage($item.Image, $clip.X, $clip.Y, [single]($wantW * $scale), [single]($wantH * $scale))
        $state.Index++
        $e.HasMorePages = $state.Index -lt $pages.Count
    }.GetNewClosure()

    $printDoc.add_PrintPage($handler)
    try {
        $printDoc.Print()
    } finally {
        $printDoc.remove_PrintPage($handler)
        foreach ($item in $pages) {
            $item.Image.Dispose()
            $item.Stream.Dispose()
        }
        $printDoc.Dispose()
    }
} catch {
    Fail $_.Exception.GetBaseException().Message
}
