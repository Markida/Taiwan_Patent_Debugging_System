@echo off
setlocal
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "KMP_DUPLICATE_LIB_OK=TRUE"
set "OMP_NUM_THREADS=1"
set "MKL_NUM_THREADS=1"
set "CONDA_PREFIX=%ROOT%runtime"
set "PATH=%ROOT%runtime;%ROOT%runtime\Library\bin;%ROOT%runtime\Scripts;%PATH%"

if not exist "%ROOT%runtime\pythonw.exe" (
    echo [錯誤] 找不到內附的 Python 執行環境。
    echo 請保留整個 Saint-Island_Patent_MDS 資料夾，不要只複製啟動檔。
    pause
    exit /b 2
)

start "Saint-Island Patent OCR" /wait "%ROOT%runtime\pythonw.exe" "%ROOT%app\main.py"
exit /b %errorlevel%
