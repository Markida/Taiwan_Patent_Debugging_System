$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv_train_v3\Scripts\python.exe"
$Dataset = Join-Path $ProjectRoot "training\char_yolo\dataset_v3_consensus"

if (-not (Test-Path -LiteralPath $Python)) {
    throw ".venv_train_v3 is missing. Run install_training_v3.ps1 first."
}
Set-Location $ProjectRoot

Write-Host "[1/2] Running preflight (this does not train)..." -ForegroundColor Cyan
& $Python -m training.v3.preflight
if ($LASTEXITCODE -ne 0) { throw "Preflight failed." }

Write-Host "[2/2] Building/resuming the v3 consensus dataset..." -ForegroundColor Cyan
if (Test-Path -LiteralPath $Dataset) {
    & $Python -m training.v3.build_pseudo_dataset --resume
} else {
    & $Python -m training.v3.build_pseudo_dataset
}
if ($LASTEXITCODE -ne 0) { throw "The v3 dataset build failed." }

Write-Host "Selecting high-value crops for targeted manual annotation..." -ForegroundColor Cyan
& $Python -m training.v3.select_manual_review
if ($LASTEXITCODE -ne 0) { throw "Manual-review crop selection failed." }

Write-Host "Dataset preparation finished. Formal training has not started." -ForegroundColor Green
Write-Host "Report: training\char_yolo\dataset_v3_consensus\build_report.json"
Write-Host "Red-box review images: training\v3\work\review"
Write-Host "Next: Annotate_V3_Manual.bat, then Import_V3_Manual.bat."
