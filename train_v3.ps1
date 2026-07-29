$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv_train_v3\Scripts\python.exe"
$Report = Join-Path $ProjectRoot "training\char_yolo\dataset_v3_consensus\build_report.json"

if (-not (Test-Path -LiteralPath $Python)) { throw ".venv_train_v3 is missing." }
if (-not (Test-Path -LiteralPath $Report)) { throw "Run Prepare_V3_Dataset.bat first." }
Set-Location $ProjectRoot
# Windows PowerShell's ConvertFrom-Json treats property names as
# case-insensitive, so a valid v3 report containing both "A" and "a" fails as
# a duplicate-key object. Parse it with Python, whose JSON keys are
# case-sensitive, and return only the three scalar values needed here.
$BuildValues = @(& $Python -c "import json, pathlib, sys; data=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8-sig')); counts=data.get('image_counts', {}); print(1 if data.get('complete') else 0); print(counts.get('train', 0)); print(counts.get('val', 0))" $Report)
if ($LASTEXITCODE -ne 0 -or $BuildValues.Count -ne 3) {
    throw "Cannot read the v3 dataset build report."
}
$BuildComplete = $BuildValues[0].Trim() -eq "1"
$TrainImageCount = $BuildValues[1].Trim()
$ValImageCount = $BuildValues[2].Trim()
if (-not $BuildComplete) { throw "This is a smoke/incomplete dataset. Formal training is blocked." }

Write-Host "The v3 formal GPU training run is ready to start." -ForegroundColor Yellow
Write-Host "Train images: $TrainImageCount; manual val images: $ValImageCount"
$Answer = Read-Host "Type TRAIN and press Enter to start"
if ($Answer -cne "TRAIN") {
    Write-Host "Cancelled. Training did not start."
    exit 0
}

& $Python -m training.v3.train_v3 --start
if ($LASTEXITCODE -ne 0) { throw "Training failed. Resume_V3.bat can continue from last.pt." }
& $Python -m training.v3.evaluate_v3
if ($LASTEXITCODE -ne 0) { throw "Training finished, but the three-model comparison failed." }
Write-Host "v3 training, validation and export completed." -ForegroundColor Green
