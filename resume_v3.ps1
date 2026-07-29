$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv_train_v3\Scripts\python.exe"
$Runs = Join-Path $ProjectRoot "runs\v3"
if (-not (Test-Path -LiteralPath $Python)) { throw ".venv_train_v3 is missing." }
$Checkpoint = Get-ChildItem -LiteralPath $Runs -Filter last.pt -Recurse -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $Checkpoint) { throw "No last.pt was found under runs\v3." }

Write-Host "Checkpoint: $($Checkpoint.FullName)" -ForegroundColor Yellow
$Answer = Read-Host "Type RESUME and press Enter"
if ($Answer -cne "RESUME") { Write-Host "Cancelled."; exit 0 }
Set-Location $ProjectRoot
& $Python -m training.v3.train_v3 --resume $Checkpoint.FullName
if ($LASTEXITCODE -ne 0) { throw "Resume failed." }
& $Python -m training.v3.evaluate_v3
if ($LASTEXITCODE -ne 0) { throw "Resume finished, but the three-model comparison failed." }
