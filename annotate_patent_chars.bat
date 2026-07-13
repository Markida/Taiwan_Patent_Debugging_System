@echo off
setlocal
chcp 65001 >nul

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PATENT_PYTHON%"
if not defined PYTHON_EXE set "PYTHON_EXE=%USERPROFILE%\anaconda3\envs\patent_pack_cpu\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%LOCALAPPDATA%\anaconda3\envs\patent_pack_cpu\python.exe"
set "TOOL_DIR=%PROJECT_DIR%training\manual_annotation"
set "ANNOTATION_SET=%TOOL_DIR%\annotation_set"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found: %PYTHON_EXE%
    pause
    exit /b 1
)

if /I "%~1"=="--check" (
    echo [OK] Python: %PYTHON_EXE%
    echo [OK] Annotation tool: %TOOL_DIR%\annotate_pages.py
    echo [OK] Dataset builder: %TOOL_DIR%\build_training_datasets.py
    exit /b 0
)

cd /d "%PROJECT_DIR%"

if /I "%~1"=="--prepare" (
    "%PYTHON_EXE%" "%TOOL_DIR%\prepare_annotation_set.py"
    goto :result
)

if /I "%~1"=="--stats" (
    "%PYTHON_EXE%" "%TOOL_DIR%\build_training_datasets.py" --check-only
    goto :result
)

if not exist "%ANNOTATION_SET%\labels" (
    echo Preparing the initial real-page annotation set...
    "%PYTHON_EXE%" "%TOOL_DIR%\prepare_annotation_set.py"
    if errorlevel 1 goto :result
)

"%PYTHON_EXE%" "%TOOL_DIR%\annotate_pages.py" %*

:result
if errorlevel 1 (
    echo.
    echo [ERROR] Manual annotation workflow exited with an error.
    pause
    exit /b 1
)

endlocal
