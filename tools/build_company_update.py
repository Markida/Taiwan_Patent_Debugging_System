"""Build the small, runtime-preserving company update package."""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = PROJECT_ROOT / "release"
UPDATE_DESCRIPTION = "文件偵錯與拖放更新"
MODEL_FILES = (
    "class_map.json",
    "patent_char_v4_company_approved_recall.onnx",
    "patent_char_v4_company_approved_recall.names.json",
    "patent_char_v3_consensus.onnx",
    "patent_char_v3_consensus.names.json",
    "patent_label_group_v1.onnx",
)
REQUIRED_PACKAGE_FILES = (
    "Install_Update.bat",
    "Offline_Check.bat",
    "Saint-Island_Patent_MDS.exe",
    "Startup_Diagnostic.bat",
    "更新說明.txt",
    "app/main.py",
    "app/app/config.py",
    "app/app/main_window.py",
    "app/app/workflow_context.py",
    "app/features/registry.py",
    "app/features/patent_review/docx_reader.py",
    "app/features/patent_review/rule_engine.py",
    "app/ui/file_drop.py",
    "app/ui/patent_review_page.py",
    "app/ui/recognition_page.py",
    "app/models/patent_char_v4_company_approved_recall.onnx",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_version() -> str:
    namespace = {}
    config_path = PROJECT_ROOT / "app" / "config.py"
    exec(compile(config_path.read_text(encoding="utf-8"), str(config_path), "exec"), namespace)
    return str(namespace["APP_VERSION"])


def copy_source_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def compile_launcher(destination: Path) -> None:
    csc = (
        Path(os.environ.get("WINDIR", r"C:\Windows"))
        / "Microsoft.NET"
        / "Framework64"
        / "v4.0.30319"
        / "csc.exe"
    )
    if not csc.is_file():
        raise FileNotFoundError(f".NET Framework C# compiler is missing: {csc}")
    subprocess.run(
        [
            str(csc),
            "/nologo",
            "/target:winexe",
            "/platform:x64",
            "/optimize+",
            "/reference:System.Windows.Forms.dll",
            f"/out:{destination}",
            str(PROJECT_ROOT / "packaging" / "Saint-IslandPatentOCR.Launcher.cs"),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def release_notes(version: str) -> str:
    return f"""Saint-Island_Patent_MDS v{version} 公司端更新
================================================

本包只更新應用程式與模型備援檔，不會更換公司電腦既有的 runtime，
也不需要安裝 Python、Conda、套件或連線到外網。

主要更新
1. 新增「專利文件偵錯」頁，可唯讀擷取完整專利說明書 DOCX。
2. 新增 22 條格式與一致性檢核，包含段號、章節、圖號、符號及請求項。
3. 完整符號說明與代表圖符號說明可分別送到圖片 OCR 比對。
4. OCR 頁新增「回文件偵錯」按鈕，往返頁面時保留暫存資料。
5. 文件頁可拖入單一 DOCX；OCR 頁可拖入單一 PDF。
6. 副檔名錯誤、多檔案或非本機檔案會顯示原因，不會清除現有結果。
7. 保留 v4 高召回模型、字母誤判過濾、低信心人工修正及結果排序。
8. 更新啟動器，修復舊版離線自測可能永久等待且不回報錯誤的問題。

安裝方式
1. 完全關閉 Saint-Island_Patent_MDS。
2. 將本更新資料夾完整解壓到公司程式根目錄內。
3. 雙擊 Install_Update.bat。
4. 安裝器會依序執行離線模型推論與 GUI 啟動測試。
5. 看到 [PASS] v{version} update installed 即完成。

若自動找不到程式根目錄，可把 Saint-Island_Patent_MDS 根資料夾拖到
Install_Update.bat 上。安裝失敗時執行 Startup_Diagnostic.bat，並回傳
產生的 startup_diagnostic.txt。

重要
- 請勿只複製單一 EXE；公司端原有 app、runtime、models 必須保持同層結構。
- 本程式不修改或輸出修正版 Word；正式修訂仍由使用者回原始文件完成。
"""


def build_package(output_root: Path, package_name: str) -> tuple[Path, Path, Path]:
    package_dir = output_root / package_name
    zip_path = output_root / f"{package_name}.zip"
    checksum_path = output_root / f"{package_name}.zip.sha256.txt"
    if package_dir.exists() or zip_path.exists() or checksum_path.exists():
        raise FileExistsError(
            "Release output already exists; choose a new name or remove the previous generated release."
        )

    app_dir = package_dir / "app"
    app_dir.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "main.py", app_dir / "main.py")
    for folder in ("app", "features", "ui"):
        copy_source_tree(PROJECT_ROOT / folder, app_dir / folder)

    model_dir = app_dir / "models"
    model_dir.mkdir()
    for file_name in MODEL_FILES:
        source = PROJECT_ROOT / "models" / file_name
        if not source.is_file():
            raise FileNotFoundError(f"Required model file is missing: {source}")
        shutil.copy2(source, model_dir / file_name)

    shutil.copy2(PROJECT_ROOT / "packaging" / "Install_Update.bat", package_dir)
    shutil.copy2(PROJECT_ROOT / "packaging" / "Startup_Diagnostic.bat", package_dir)
    compile_launcher(package_dir / "Saint-Island_Patent_MDS.exe")
    shutil.copy2(PROJECT_ROOT / "Offline_Check.bat", package_dir)

    version = read_version()
    (package_dir / "更新說明.txt").write_text(
        release_notes(version), encoding="utf-8-sig", newline="\r\n"
    )

    for relative_path in REQUIRED_PACKAGE_FILES:
        if not (package_dir / relative_path).is_file():
            raise FileNotFoundError(f"Incomplete update package: {relative_path}")

    files = sorted(path for path in package_dir.rglob("*") if path.is_file())
    manifest = {
        "product": "Saint-Island_Patent_MDS",
        "version": version,
        "package_name": package_name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_included": False,
        "requires_existing_runtime": True,
        "files": [
            {
                "path": path.relative_to(package_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in files
        ],
    }
    manifest_path = package_dir / "PACKAGE_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with ZipFile(zip_path, "w", ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(
                    path,
                    (Path(package_name) / path.relative_to(package_dir)).as_posix(),
                )

    checksum = sha256(zip_path)
    checksum_path.write_text(f"{checksum}\n", encoding="ascii")
    return package_dir, zip_path, checksum_path


def main() -> int:
    version = read_version()
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=RELEASE_ROOT)
    parser.add_argument(
        "--name",
        default=(
            f"Saint-Island_Patent_MDS_v{version}_{UPDATE_DESCRIPTION}_"
            f"{date.today():%Y%m%d}"
        ),
    )
    args = parser.parse_args()
    package_dir, zip_path, checksum_path = build_package(
        args.output_root.resolve(), args.name
    )
    print(f"PACKAGE_DIR={package_dir}")
    print(f"ZIP={zip_path}")
    print(f"SHA256={checksum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
