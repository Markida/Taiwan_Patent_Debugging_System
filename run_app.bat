@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

set "ENV_ROOT=%USERPROFILE%\anaconda3\envs\patent_pack_cpu"

if not exist "%ENV_ROOT%\python.exe" (
    set "ENV_ROOT=%LOCALAPPDATA%\anaconda3\envs\patent_pack_cpu"
)

if not exist "%ENV_ROOT%\python.exe" (
    echo [錯誤] 找不到 patent_pack_cpu 的 Python。
    echo 預期位置：%%USERPROFILE%%\anaconda3\envs\patent_pack_cpu\python.exe
    echo 請先執行 install_cpu.bat，或確認 Conda 環境位置。
    pause
    exit /b 1
)

set "PATH=%ENV_ROOT%;%ENV_ROOT%\Library\mingw-w64\bin;%ENV_ROOT%\Library\usr\bin;%ENV_ROOT%\Library\bin;%ENV_ROOT%\Scripts;%ENV_ROOT%\bin;%PATH%"
set "YOLO_CONFIG_DIR=%TEMP%"
set "KMP_DUPLICATE_LIB_OK=TRUE"
set "OMP_NUM_THREADS=1"
set "MKL_NUM_THREADS=1"

if /i "%~1"=="--check" (
    "%ENV_ROOT%\python.exe" -c "import PySide6, torch, ultralytics; print('PASS: 啟動環境正常')"
    exit /b %errorlevel%
)

"%ENV_ROOT%\python.exe" main.py

if errorlevel 1 (
    echo.
    echo [錯誤] 程式異常結束，請保留上方訊息。
    pause
)

endlocal
