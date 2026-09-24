@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%ROOT%..\SantoOCR\runtime\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] Company runtime Python was not found: %PYTHON%
  pause
  exit /b 1
)
cd /d "%ROOT%"
"%PYTHON%" -m training.manual_annotation.annotate_pages --dataset training\v3\manual_review
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
