conda activate patent_pack_cpu

python -m nuitka `
  --standalone `
  --windows-console-mode=disable `
  --enable-plugin=pyside6 `
  --module-parameter=torch-disable-jit=yes `
  --include-package=ultralytics `
  --include-package=easyocr `
  --include-package=torch `
  --include-package=torchvision `
  --include-package=cv2 `
  --include-package=pymupdf `
  --include-package-data=easyocr `
  --include-package-data=pymupdf `
  --windows-icon-from-ico=app\resources\app_icon.ico `
  --include-data-dir=app\resources=app\resources `
  --include-data-dir=models=models `
  --include-data-dir=easyocr_models=easyocr_models `
  --output-dir=build_release_cpu `
  --output-filename=Saint-Island_Patent_MDS.exe `
  main.py
