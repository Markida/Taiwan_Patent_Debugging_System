@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%USERPROFILE%\anaconda3\envs\patent_pack_cpu\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] 找不到 patent_pack_cpu Python：%PYTHON%
  pause
  exit /b 1
)
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%"
"%PYTHON%" -m training.figure_heading.build_training_dataset
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
