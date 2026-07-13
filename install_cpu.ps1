# 建議使用 Anaconda Prompt 或 PowerShell 執行
# 目的：建立 CPU-only 測試 / 打包環境

conda create -n patent_pack_cpu python=3.10 -y
conda activate patent_pack_cpu

python -m pip install --upgrade pip setuptools wheel

# 先安裝 CPU-only PyTorch
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 再安裝其他專案套件
python -m pip install -r requirements.txt

# 打包工具
python -m pip install nuitka ordered-set zstandard

Write-Host ""
Write-Host "Installation finished."
Write-Host "Run the app with:"
Write-Host "python main.py"