@echo off
setlocal EnableExtensions

rem Keep this batch file ASCII-only so it works on every Windows CMD code page.
set "APP_DIR=%~dp0"
set "REPORT=%TEMP%\Saint-IslandPatentOCR_offline_check.json"
set "APP_EXE=%APP_DIR%Saint-Island_Patent_MDS.exe"

if not exist "%APP_EXE%" set "APP_EXE=%APP_DIR%Saint-IslandPatentOCR.exe"
if not exist "%APP_EXE%" set "APP_EXE=%APP_DIR%SantoPatentOCR.exe"

if not exist "%APP_EXE%" (
    echo [ERROR] Saint-Island_Patent_MDS.exe was not found beside Offline_Check.bat.
    echo Keep the EXE, app, and runtime folders in the same directory.
    pause
    exit /b 1
)

if exist "%REPORT%" del /q "%REPORT%"
"%APP_EXE%" --offline-self-test "%REPORT%"
set "RESULT=%ERRORLEVEL%"

if exist "%REPORT%" (
    type "%REPORT%"
) else (
    echo [ERROR] The application did not create an offline-check report.
    set "RESULT=1"
)

echo.
if "%RESULT%"=="0" (
    echo [PASS] Offline environment check passed.
) else (
    echo [FAILED] Offline environment check failed. Keep the report above.
)

pause
exit /b %RESULT%
