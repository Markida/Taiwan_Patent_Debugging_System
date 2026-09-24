@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%train_v3.ps1"
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
