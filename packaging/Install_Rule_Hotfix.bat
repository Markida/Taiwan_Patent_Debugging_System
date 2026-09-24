@echo off
setlocal EnableExtensions

rem ASCII-only v2.0.3 rule hotfix installer.
set "PACKAGE=%~dp0"
set "SOURCE=%PACKAGE%app\features\patent_review\rule_engine.py"
set "TARGET="
set "LAUNCHER="

if not "%~1"=="" (
    if exist "%~1\Saint-Island_Patent_MDS.exe" set "TARGET=%~f1"
    if not defined TARGET if exist "%~1\Saint-IslandPatentOCR.exe" set "TARGET=%~f1"
)

if not defined TARGET (
    if exist "%PACKAGE%..\Saint-Island_Patent_MDS.exe" set "TARGET=%PACKAGE%.."
    if not defined TARGET if exist "%PACKAGE%..\Saint-IslandPatentOCR.exe" set "TARGET=%PACKAGE%.."
)

if not defined TARGET (
    echo [ERROR] Saint-Island_Patent_MDS root folder was not found.
    echo Extract this hotfix folder inside the company application folder.
    echo Or drag the company application folder onto Install_Update.bat.
    pause
    exit /b 1
)

for %%I in ("%TARGET%") do set "TARGET=%%~fI"
if exist "%TARGET%\Saint-Island_Patent_MDS.exe" set "LAUNCHER=%TARGET%\Saint-Island_Patent_MDS.exe"
if not defined LAUNCHER if exist "%TARGET%\Saint-IslandPatentOCR.exe" set "LAUNCHER=%TARGET%\Saint-IslandPatentOCR.exe"
echo Target: "%TARGET%"

if not exist "%SOURCE%" (
    echo [ERROR] Hotfix package is incomplete: rule_engine.py is missing.
    pause
    exit /b 2
)
if not exist "%TARGET%\app\features\patent_review\rule_engine.py" (
    echo [ERROR] Existing patent review rule engine was not found.
    pause
    exit /b 3
)
if not exist "%TARGET%\app\features\patent_review\models.py" (
    echo [ERROR] Existing patent review models.py was not found.
    pause
    exit /b 3
)
findstr /C:"claim_subjects" "%TARGET%\app\features\patent_review\models.py" >nul
if errorlevel 1 (
    echo [ERROR] This hotfix requires the full v2.0.3 update first.
    pause
    exit /b 3
)
if not exist "%TARGET%\runtime\python.exe" (
    echo [ERROR] Missing company runtime\python.exe.
    pause
    exit /b 3
)

set "DEST=%TARGET%\app\features\patent_review\rule_engine.py"
set "BACKUP=%DEST%.v203_before_modifier_hotfix.bak"
if not exist "%BACKUP%" copy /Y "%DEST%" "%BACKUP%" >nul

copy /Y "%SOURCE%" "%DEST%" >nul
if errorlevel 1 (
    echo [ERROR] Failed to update rule_engine.py.
    echo Close Saint-Island_Patent_MDS and try again.
    pause
    exit /b 4
)

"%TARGET%\runtime\python.exe" -m py_compile "%DEST%"
if errorlevel 1 (
    echo [ERROR] Updated rule_engine.py failed the syntax check.
    pause
    exit /b 5
)

set "REPORT=%TEMP%\SaintIsland_v203_modifier_hotfix_check.json"
if exist "%REPORT%" del /q "%REPORT%"
"%LAUNCHER%" --offline-self-test "%REPORT%"
set "SELFTEST=%ERRORLEVEL%"
if exist "%REPORT%" type "%REPORT%"
if not "%SELFTEST%"=="0" (
    echo [FAILED] Offline self-test failed.
    pause
    exit /b 6
)

echo.
echo Running GUI startup test...
"%LAUNCHER%" --gui-smoke-test
if errorlevel 1 (
    echo [FAILED] GUI startup test failed.
    pause
    exit /b 7
)

echo.
echo [PASS] v2.0.3 claim modifier hotfix installed.
pause
exit /b 0
