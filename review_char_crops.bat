@echo off
setlocal
chcp 65001 >nul

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PATENT_PYTHON%"
if not defined PYTHON_EXE set "PYTHON_EXE=%USERPROFILE%\anaconda3\envs\patent_pack_cpu\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%LOCALAPPDATA%\anaconda3\envs\patent_pack_cpu\python.exe"
set "REVIEW_SCRIPT=%PROJECT_DIR%training\crop_classifier\review_crops.py"
set "MANIFEST=%PROJECT_DIR%training\crop_classifier\review_dataset\manifest.csv"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found: %PYTHON_EXE%
    pause
    exit /b 1
)

if not exist "%REVIEW_SCRIPT%" (
    echo [ERROR] Review script not found: %REVIEW_SCRIPT%
    pause
    exit /b 1
)

if not exist "%MANIFEST%" (
    echo [ERROR] Crop manifest not found: %MANIFEST%
    echo Run prepare_crops.py first.
    pause
    exit /b 1
)

if /I "%~1"=="--check" (
    echo [OK] Python: %PYTHON_EXE%
    echo [OK] Script: %REVIEW_SCRIPT%
    echo [OK] Manifest: %MANIFEST%
    exit /b 0
)

cd /d "%PROJECT_DIR%"
"%PYTHON_EXE%" "%REVIEW_SCRIPT%" %*

if errorlevel 1 (
    echo.
    echo [ERROR] Character review tool exited with an error.
    pause
    exit /b 1
)

endlocal
