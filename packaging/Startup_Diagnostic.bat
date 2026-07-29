@echo off
setlocal EnableExtensions

rem ASCII-only diagnostic: captures startup failures without requiring Python installation.
set "ROOT=%~dp0"
set "LOG=%ROOT%startup_diagnostic.txt"
set "PYTHON=%ROOT%runtime\python.exe"
set "MAIN=%ROOT%app\main.py"
set "REPORT=%TEMP%\SaintIsland_startup_selftest.json"

>"%LOG%" echo Saint-Island_Patent_MDS startup diagnostic
>>"%LOG%" echo Date: %DATE% %TIME%
>>"%LOG%" echo Root: %ROOT%

if not exist "%PYTHON%" (
    >>"%LOG%" echo [ERROR] Missing runtime\python.exe
    goto SHOW_RESULT
)
if not exist "%ROOT%runtime\pythonw.exe" (
    >>"%LOG%" echo [ERROR] Missing runtime\pythonw.exe
    goto SHOW_RESULT
)
if not exist "%MAIN%" (
    >>"%LOG%" echo [ERROR] Missing app\main.py
    goto SHOW_RESULT
)

set "PATH=%ROOT%runtime;%ROOT%runtime\Library\bin;%ROOT%runtime\Scripts;%PATH%"
set "CONDA_PREFIX=%ROOT%runtime"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "KMP_DUPLICATE_LIB_OK=TRUE"
set "OMP_NUM_THREADS=1"
set "MKL_NUM_THREADS=1"
if defined LOCALAPPDATA (
    set "YOLO_CONFIG_DIR=%LOCALAPPDATA%\Saint-IslandPatentOCR"
) else (
    set "YOLO_CONFIG_DIR=%TEMP%\Saint-IslandPatentOCR"
)

if exist "%REPORT%" del /q "%REPORT%"
pushd "%ROOT%app"
"%PYTHON%" main.py --offline-self-test "%REPORT%" >>"%LOG%" 2>&1
set "SELFTEST=%ERRORLEVEL%"
if exist "%REPORT%" type "%REPORT%" >>"%LOG%"
>>"%LOG%" echo OfflineSelfTestExitCode: %SELFTEST%

"%PYTHON%" main.py --gui-smoke-test >>"%LOG%" 2>&1
set "GUI_TEST=%ERRORLEVEL%"
popd
>>"%LOG%" echo GuiSmokeTestExitCode: %GUI_TEST%

if "%SELFTEST%"=="0" if "%GUI_TEST%"=="0" (
    >>"%LOG%" echo [PASS] Offline inference and GUI startup passed.
    goto SHOW_RESULT
)
>>"%LOG%" echo [FAILED] Keep this report and return it to the maintainer.

:SHOW_RESULT
type "%LOG%"
echo.
echo Diagnostic report: "%LOG%"
pause
exit /b
