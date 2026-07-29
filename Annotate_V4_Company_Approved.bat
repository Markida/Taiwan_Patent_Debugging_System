@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%ROOT%..\SantoOCR\runtime\python.exe"
if not exist "%PYTHON%" set "PYTHON=%ROOT%.venv_train_v3\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] Python runtime was not found.
  pause
  exit /b 1
)
cd /d "%ROOT%"
"%PYTHON%" -m training.manual_annotation.annotate_pages --dataset training\v4_company_approved\manual_review %*
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
