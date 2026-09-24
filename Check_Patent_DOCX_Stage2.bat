@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0.venv_train_v3\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%USERPROFILE%\anaconda3\envs\patent_pack_cpu\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%LOCALAPPDATA%\anaconda3\envs\patent_pack_cpu\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python runtime was not found.
    echo Run this checkpoint on the development computer first.
    pause
    exit /b 1
)

set "DOCX_PATH=%~1"
if not defined DOCX_PATH (
    for /f "usebackq delims=" %%I in (`powershell.exe -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.OpenFileDialog; $d.Filter='Word document (*.docx)|*.docx'; if($d.ShowDialog() -eq 'OK'){[Console]::Write($d.FileName)}"`) do set "DOCX_PATH=%%I"
)

if not defined DOCX_PATH (
    echo No DOCX was selected.
    exit /b 0
)

"%PYTHON_EXE%" tools\check_patent_text.py "%DOCX_PATH%"
if errorlevel 1 (
    echo.
    echo [ERROR] Stage 2 text review failed.
    pause
    exit /b 1
)

for %%F in ("%DOCX_PATH%") do set "REPORT_STEM=%%~nF"
start "" "output\patent_review_stage2\%REPORT_STEM%_stage2_review.html"

echo.
echo Report created under output\patent_review_stage2
pause
endlocal
