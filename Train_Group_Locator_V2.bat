@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%USERPROFILE%\anaconda3\envs\patent_ai\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] GPU training environment not found: %PYTHON%
  echo Please use the patent_ai environment; the bundled company runtime is CPU-only.
  pause
  exit /b 1
)
cd /d "%ROOT%"
set "YOLO_CONFIG_DIR=%ROOT%.ultralytics_ai"
set "PYTHONPATH=%ROOT%"
"%PYTHON%" -m training.second_stage.train_group_locator_v2 --device 0
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
