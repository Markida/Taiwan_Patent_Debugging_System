@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%USERPROFILE%\anaconda3\envs\patent_pack_cpu\python.exe"
set "DATASET=%ROOT%..\AI訓練圖集\prepared_dataset_v1\figure_heading_annotation_v2"
if not exist "%PYTHON%" (
  echo [ERROR] 找不到 patent_pack_cpu Python：%PYTHON%
  pause
  exit /b 1
)
if not exist "%DATASET%\summary.json" (
  echo [ERROR] 尚未建立圖題標註資料：%DATASET%
  pause
  exit /b 1
)
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%"
"%PYTHON%" -m training.manual_annotation.annotate_pages --mode figure-heading --dataset "%DATASET%"
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
