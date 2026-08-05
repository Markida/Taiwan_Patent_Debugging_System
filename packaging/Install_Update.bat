@echo off
setlocal EnableExtensions

rem ASCII-only installer: safe on company computers using any CMD code page.
set "PACKAGE=%~dp0"
set "SOURCE=%PACKAGE%app"
set "TARGET="

if not "%~1"=="" (
    if exist "%~1\Saint-Island_Patent_MDS.exe" set "TARGET=%~f1"
    if not defined TARGET if exist "%~1\Saint-IslandPatentOCR.exe" set "TARGET=%~f1"
)

if not defined TARGET (
    if exist "%PACKAGE%..\Saint-Island_Patent_MDS.exe" set "TARGET=%PACKAGE%.."
    if not defined TARGET if exist "%PACKAGE%..\Saint-IslandPatentOCR.exe" set "TARGET=%PACKAGE%.."
)

if not defined TARGET (
    if exist "%PACKAGE%Saint-Island_Patent_MDS.exe" set "TARGET=%PACKAGE%"
    if not defined TARGET if exist "%PACKAGE%Saint-IslandPatentOCR.exe" set "TARGET=%PACKAGE%"
)

if not defined TARGET (
    echo [ERROR] Saint-Island_Patent_MDS root folder was not found.
    echo Extract this update folder inside the company application folder.
    echo Or drag the company application folder onto Install_Update.bat.
    pause
    exit /b 1
)

for %%I in ("%TARGET%") do set "TARGET=%%~fI"
echo Target: "%TARGET%"

for %%F in (
    "main.py"
    "app\config.py"
    "app\main_window.py"
    "app\paths.py"
    "app\workflow_context.py"
    "app\features\snake\__init__.py"
    "app\features\snake\score_store.py"
    "app\resources\app_icon.ico"
    "app\resources\app_icon.png"
    "app\styles.py"
    "features\registry.py"
    "features\patent_review\custom_rules.py"
    "features\patent_review\docx_reader.py"
    "features\patent_review\figure_ocr_checker.py"
    "features\patent_review\models.py"
    "features\patent_review\rule_engine.py"
    "features\patent_review\section_parser.py"
    "features\patent_review\symbol_transfer.py"
    "ui\custom_text_rule_dialog.py"
    "ui\demo_tool_page.py"
    "ui\embodiment_figure_compare_page.py"
    "ui\feature_navigation.py"
    "ui\file_drop.py"
    "ui\patent_review_page.py"
    "ui\recognition_page.py"
    "ui\snake_game_page.py"
) do (
    if not exist "%SOURCE%\%%~F" (
        echo [ERROR] Update package is incomplete: app\%%~F
        pause
        exit /b 2
    )
)
if not exist "%PACKAGE%Saint-Island_Patent_MDS.exe" (
    echo [ERROR] Update package is missing the v2.0.4 launcher.
    pause
    exit /b 2
)

if not exist "%TARGET%\runtime\python.exe" (
    echo [ERROR] Missing company runtime\python.exe.
    pause
    exit /b 3
)
if not exist "%TARGET%\runtime\pythonw.exe" (
    echo [ERROR] Missing company runtime\pythonw.exe.
    pause
    exit /b 3
)

robocopy "%SOURCE%" "%TARGET%\app" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
if errorlevel 8 (
    echo [ERROR] Failed to copy the application update.
    echo Close Saint-Island_Patent_MDS and try again.
    pause
    exit /b 4
)

copy /Y "%PACKAGE%Saint-Island_Patent_MDS.exe" "%TARGET%\Saint-Island_Patent_MDS.exe" >nul
if errorlevel 1 (
    echo [ERROR] Failed to update Saint-Island_Patent_MDS.exe.
    echo Close the application and try again.
    pause
    exit /b 4
)
copy /Y "%PACKAGE%Offline_Check.bat" "%TARGET%\Offline_Check.bat" >nul
copy /Y "%PACKAGE%Startup_Diagnostic.bat" "%TARGET%\Startup_Diagnostic.bat" >nul

set "REPORT=%TEMP%\SaintIsland_v204_update_check.json"
if exist "%REPORT%" del /q "%REPORT%"
"%TARGET%\Saint-Island_Patent_MDS.exe" --offline-self-test "%REPORT%"
set "SELFTEST=%ERRORLEVEL%"

if exist "%REPORT%" (
    type "%REPORT%"
) else (
    echo [ERROR] Offline self-test report was not created.
    set "SELFTEST=1"
)

if not "%SELFTEST%"=="0" (
    echo [FAILED] Offline self-test failed.
    echo Run Startup_Diagnostic.bat and return startup_diagnostic.txt.
    pause
    exit /b 5
)

echo.
echo Running GUI startup test...
"%TARGET%\Saint-Island_Patent_MDS.exe" --gui-smoke-test
set "GUI_TEST=%ERRORLEVEL%"
if not "%GUI_TEST%"=="0" (
    echo [FAILED] GUI startup test failed.
    echo Run Startup_Diagnostic.bat and return startup_diagnostic.txt.
    pause
    exit /b 6
)

echo.
echo [PASS] v2.0.4 update installed, offline inference passed, and GUI started.
pause
exit /b 0
