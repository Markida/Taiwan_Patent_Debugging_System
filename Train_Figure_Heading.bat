@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%USERPROFILE%\anaconda3\envs\patent_ai\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] 找不到 GPU 訓練環境：%PYTHON%
  pause
  exit /b 1
)
cd /d "%ROOT%"
set "YOLO_CONFIG_DIR=%ROOT%.ultralytics_ai"
set "PYTHONPATH=%ROOT%"
"%PYTHON%" -m training.figure_heading.train_figure_heading --device 0
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
