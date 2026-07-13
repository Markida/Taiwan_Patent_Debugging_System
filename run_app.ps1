$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$environmentRoot = Join-Path $env:USERPROFILE "anaconda3\envs\patent_pack_cpu"

if (-not (Test-Path (Join-Path $environmentRoot "python.exe"))) {
    $environmentRoot = Join-Path $env:LOCALAPPDATA "anaconda3\envs\patent_pack_cpu"
}

$python = Join-Path $environmentRoot "python.exe"

if (-not (Test-Path $python)) {
    throw "找不到 patent_pack_cpu 的 Python：$python"
}

$pathEntries = @(
    $environmentRoot
    (Join-Path $environmentRoot "Library\mingw-w64\bin")
    (Join-Path $environmentRoot "Library\usr\bin")
    (Join-Path $environmentRoot "Library\bin")
    (Join-Path $environmentRoot "Scripts")
    (Join-Path $environmentRoot "bin")
)
$env:PATH = ($pathEntries + $env:PATH) -join ";"
$env:YOLO_CONFIG_DIR = $env:TEMP
$env:KMP_DUPLICATE_LIB_OK = "TRUE"
$env:OMP_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"

& $python main.py

if ($LASTEXITCODE -ne 0) {
    throw "程式異常結束，exit code：$LASTEXITCODE"
}
