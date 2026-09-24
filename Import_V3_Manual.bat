@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv_train_v3\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] .venv_train_v3 was not found.
  pause
  exit /b 1
)
cd /d "%ROOT%"
"%PYTHON%" -m training.v3.import_manual_reviews
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
