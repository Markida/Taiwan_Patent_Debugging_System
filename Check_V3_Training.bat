@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv_train_v3\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [錯誤] 找不到 v3 訓練環境：%PYTHON%
  echo 請先執行 install_training_v3.ps1
  pause
  exit /b 1
)
cd /d "%ROOT%"
"%PYTHON%" -m training.v3.preflight --smoke --max-smoke-crops 3
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" echo [失敗] 請查看 training\v3\preflight_report.json
if "%CODE%"=="0" echo [完成] 環境、資料與三個模型均已通過檢查；沒有開始訓練。
pause
exit /b %CODE%
