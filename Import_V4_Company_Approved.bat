@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv_train_v3\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] Training Python was not found: %PYTHON%
  pause
  exit /b 1
)
cd /d "%ROOT%"
"%PYTHON%" -m training.v3.import_manual_reviews --config training\v4_company_approved\config.yaml --review-root training\v4_company_approved\manual_review --dataset training\char_yolo\dataset_v4_company_approved
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
