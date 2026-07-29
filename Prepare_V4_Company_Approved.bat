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
"%PYTHON%" -m training.v3.build_pseudo_dataset --config training\v4_company_approved\config.yaml --resume
if errorlevel 1 goto :failed
"%PYTHON%" -m training.v4_company_approved.select_manual_review --config training\v4_company_approved\config.yaml --output training\v4_company_approved\manual_review
if errorlevel 1 goto :failed
echo [OK] Company-approved preprocessing is ready.
pause
exit /b 0
:failed
echo [ERROR] Preparation failed. See training\v4_company_approved\work for details.
pause
exit /b 1
