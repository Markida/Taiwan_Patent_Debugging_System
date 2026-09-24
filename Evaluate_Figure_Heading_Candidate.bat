@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%USERPROFILE%\anaconda3\envs\patent_ai\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] 找不到 GPU 驗證環境：%PYTHON%
  pause
  exit /b 1
)
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%"
"%PYTHON%" -m training.figure_heading.evaluate_candidate
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
