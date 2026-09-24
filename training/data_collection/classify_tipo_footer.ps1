param(
    [Parameter(Mandatory = $false)]
    [string[]]$ImagePath,
    [Parameter(Mandatory = $false)]
    [string]$InputList,
    [switch]$AlreadyCropped
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapTransform, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapBounds, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapPixelFormat, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapAlphaMode, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapInterpolationMode, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.ExifOrientationMode, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.ColorManagementMode, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null

$script:AsTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.IsGenericMethodDefinition -and
        $_.GetParameters().Count -eq 1
    } |
    Select-Object -First 1

function Await-Result {
    param(
        [Parameter(Mandatory = $true)]$Operation,
        [Parameter(Mandatory = $true)][Type]$ResultType
    )
    $task = $script:AsTaskGeneric.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    try {
        $task.Wait()
    }
    catch {
        throw $task.Exception.Flatten().InnerException
    }
    return $task.Result
}

$language = [Windows.Globalization.Language]::new('zh-Hant-TW')
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if ($null -eq $engine) {
    throw 'Traditional Chinese Windows OCR is not available.'
}
$drawingToken = -join @([char]0x767C, [char]0x660E, [char]0x5716, [char]0x5F0F)
$utilityToken = -join @([char]0x65B0, [char]0x578B, [char]0x5716, [char]0x5F0F)
$designToken = -join @([char]0x8A2D, [char]0x8A08, [char]0x5716, [char]0x8AAA)

if ($InputList) {
    $ImagePath = Get-Content -LiteralPath $InputList -Encoding UTF8 | Where-Object { $_.Trim() }
}
if (-not $ImagePath) {
    throw 'Provide -ImagePath or -InputList.'
}

$processed = 0
foreach ($item in $ImagePath) {
    $absolute = [System.IO.Path]::GetFullPath($item)
    $file = Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($absolute)) ([Windows.Storage.StorageFile])
    $stream = Await-Result ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        if ($AlreadyCropped) {
            $bitmapOperation = $decoder.GetSoftwareBitmapAsync(
                [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
                [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
            )
        }
        else {
            $height = [uint32]$decoder.PixelHeight
            $width = [uint32]$decoder.PixelWidth
            $bounds = [Windows.Graphics.Imaging.BitmapBounds]::new()
            $bounds.X = [uint32][Math]::Floor($width * 0.25)
            $bounds.Y = [uint32][Math]::Floor($height * 0.90)
            $bounds.Width = [uint32][Math]::Floor($width * 0.50)
            $bounds.Height = [uint32][Math]::Floor($height * 0.08)
            $transform = [Windows.Graphics.Imaging.BitmapTransform]::new()
            $transform.Bounds = $bounds
            $bitmapOperation = $decoder.GetSoftwareBitmapAsync(
                [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
                [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,
                $transform,
                [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
                [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
            )
        }
        $bitmap = Await-Result $bitmapOperation ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $result = Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $normalized = ($result.Text -replace '\s+', '')
            [pscustomobject]@{
                path = $absolute
                text = $result.Text
                normalized = $normalized
                is_drawing = $normalized.Contains($drawingToken) -or $normalized.Contains($utilityToken) -or $normalized.Contains($designToken)
            } | ConvertTo-Json -Compress
        }
        finally {
            $bitmap.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
    $processed += 1
    if (($processed % 100) -eq 0 -or $processed -eq @($ImagePath).Count) {
        [Console]::Error.WriteLine(("footer OCR {0}/{1}" -f $processed, @($ImagePath).Count))
    }
}
