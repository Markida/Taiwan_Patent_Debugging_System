@echo off
chcp 65001

cd /d "%~dp0"

call conda activate patent_pack_cpu

python main.py

pause