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
    "app\background_tasks.py"
    "app\main_window.py"
    "app\paths.py"
    "app\startup_splash.py"
    "app\workflow_context.py"
    "app\features\arcade_cosmetics.py"
    "app\features\bulls_and_cows\__init__.py"
    "app\features\bulls_and_cows\engine.py"
    "app\features\bulls_and_cows\network.py"
    "app\features\chat_room\__init__.py"
    "app\features\chat_room\arcade_lan.py"
    "app\features\chat_room\game_ranking.py"
    "app\features\chat_room\game_report_avatar.jpg"
    "app\features\chat_room\store.py"
    "app\features\pong\__init__.py"
    "app\features\pong\network.py"
    "app\features\snake\__init__.py"
    "app\features\snake\score_store.py"
    "app\features\snake\network.py"
    "app\features\tetris\__init__.py"
    "app\features\tetris\network.py"
    "app\features\tetris\themes.py"
    "app\features\tank_battle\__init__.py"
    "app\features\tank_battle\network.py"
    "app\resources\app_icon.ico"
    "app\resources\app_icon.png"
    "app\resources\taiwan_china_spec\BeijingTaijiTemplate.docx"
    "app\resources\taiwan_china_spec\ShanghaiYiPin.docx"
    "app\resources\taiwan_china_spec\terminology.tsv"
    "app\styles.py"
    "features\registry.py"
    "features\taiwan_china_spec\__init__.py"
    "features\taiwan_china_spec\converter.py"
    "features\taiwan_china_spec\terminology_store.py"
    "features\taiwan_china_spec\article_review.py"
    "ui\spec_article_review.py"
    "features\patent_ocr\reference_reconciliation.py"
    "features\patent_ocr\compact_ocr_reader.py"
    "features\patent_ocr\onnx_detector.py"
    "features\patent_ocr\image_io.py"
    "features\patent_ocr\image_tools.py"
    "features\patent_ocr\figure_heading.py"
    "features\patent_ocr\figure_orientation_batch.py"
    "features\patent_ocr\result_store.py"
    "ui\figure_result_persistence.py"
    "features\patent_ocr\figure_heading_classes.py"
    "features\patent_ocr\figure_identifiers.py"
    "features\patent_review\custom_rules.py"
    "features\patent_review\docx_reader.py"
    "features\patent_review\figure_ocr_checker.py"
    "features\patent_review\models.py"
    "features\patent_review\rule_engine.py"
    "features\patent_review\claim_coverage.py"
    "features\patent_review\syntax_lab.py"
    "ui\syntax_lab_dialog.py"
    "features\patent_review\section_parser.py"
    "features\patent_review\symbol_transfer.py"
    "ui\custom_text_rule_dialog.py"
    "ui\arcade_navigation.py"
    "ui\arcade_particles.py"
    "ui\arcade_skin_art.py"
    "ui\arcade_skin_picker.py"
    "ui\bulls_and_cows_page.py"
    "ui\chat_room_page.py"
    "ui\demo_tool_page.py"
    "ui\embodiment_figure_compare_page.py"
    "ui\feature_navigation.py"
    "ui\file_drop.py"
    "ui\patent_review_page.py"
    "ui\pong_game_page.py"
    "ui\recognition_page.py"
    "ui\snake_game_page.py"
    "ui\tetris_game_page.py"
    "ui\tank_battle_page.py"
    "ui\taiwan_china_spec_page.py"
    "ui\workflow_pet.py"
    "ui\pixel_cat.py"
    "features\workflow_pet\__init__.py"
    "features\workflow_pet\guidance.py"
    "easyocr_models\english_g2.pth"
    "models\class_map.json"
    "models\patent_char_v4_company_approved_recall.onnx"
    "models\patent_char_v4_company_approved_recall.names.json"
    "models\patent_char_v3_consensus.onnx"
    "models\patent_char_v3_consensus.names.json"
    "models\patent_label_group_v2_gold_ft.onnx"
    "models\patent_label_group_v1.onnx"
) do (
    if not exist "%SOURCE%\%%~F" (
        echo [ERROR] Update package is incomplete: app\%%~F
        pause
        exit /b 2
    )
)
if not exist "%PACKAGE%Saint-Island_Patent_MDS.exe" (
    echo [ERROR] Update package is missing the v2.2.07 launcher.
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

robocopy "%SOURCE%" "%TARGET%\app" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XF custom_text_rules.json custom_text_rules.json.tmp
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

set "REPORT=%TEMP%\SaintIsland_v2207_update_check.json"
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
echo [PASS] v2.2.07 update installed, offline inference passed, and GUI started.
pause
exit /b 0
