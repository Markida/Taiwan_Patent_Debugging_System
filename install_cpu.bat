@echo off
chcp 65001

cd /d "%~dp0"

call conda create -n patent_pack_cpu python=3.10 -y
call conda activate patent_pack_cpu

python -m pip install --upgrade pip setuptools wheel

python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

python -m pip install -r requirements.txt

python -m pip install nuitka ordered-set zstandard

echo.
echo Installation finished.
echo Run the app with:
echo python main.py

pause