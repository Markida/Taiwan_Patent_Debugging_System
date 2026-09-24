$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $ProjectRoot ".venv_train_v3"
$Python = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    $BootstrapPython = $null
    $KnownPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path -LiteralPath $KnownPython) {
        $BootstrapPython = $KnownPython
    } else {
        try {
            $Resolved = Get-Command "python.exe" -ErrorAction Stop
            $BootstrapPython = $Resolved.Source
        } catch {}
    }
    if (-not $BootstrapPython) {
        throw "Python 3.12 was not found. Install 64-bit Python 3.12 and retry."
    }
    & $BootstrapPython -m venv $Venv
}

$UpgradeArgs = @("-m", "pip", "install", "--upgrade", "pip")
& $Python $UpgradeArgs
$TorchArgs = @("-m", "pip", "install", "--index-url", "https://download.pytorch.org/whl/cu130", "torch==2.12.1+cu130", "torchvision==0.27.1+cu130")
& $Python $TorchArgs
$PackageArgs = @("-m", "pip", "install", "ultralytics==8.4.90", "opencv-python==5.0.0.93", "numpy==2.4.4", "pillow==12.2.0", "PyYAML==6.0.3", "onnx==1.22.0", "onnxruntime==1.27.0", "tensorboard==2.21.0", "pytest==9.1.1")
& $Python $PackageArgs

$SmokeCode = "import torch; print('torch', torch.__version__); print('CUDA', torch.cuda.is_available()); print('GPU', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE'); assert torch.cuda.is_available(), 'CUDA GPU unavailable'"
& $Python @("-c", $SmokeCode)
Write-Host "The v3 training environment is ready." -ForegroundColor Green
