@echo off
setlocal
chcp 65001 >nul

set "APP_DIR=%~dp0"
set "REPORT=%TEMP%\SantoPatentOCR_offline_check.json"

if not exist "%APP_DIR%SantoPatentOCR.exe" (
    echo [錯誤] 找不到 SantoPatentOCR.exe
    pause
    exit /b 1
)

if exist "%REPORT%" del /q "%REPORT%"
start "" /wait "%APP_DIR%SantoPatentOCR.exe" --offline-self-test "%REPORT%"
set "RESULT=%ERRORLEVEL%"

if exist "%REPORT%" (
    type "%REPORT%"
) else (
    echo [錯誤] 程式沒有產生環境檢查報告。
    set "RESULT=1"
)

echo.
if "%RESULT%"=="0" (
    echo [PASS] 離線環境檢查通過。
) else (
    echo [FAILED] 離線環境檢查失敗，請保留上方報告。
)
pause
exit /b %RESULT%
