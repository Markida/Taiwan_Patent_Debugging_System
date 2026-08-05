"""Build the code-only v2.0.3 patent claim rule hotfix."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from zipfile import ZIP_DEFLATED, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = PROJECT_ROOT / "release"
VERSION = "2.0.3"
DESCRIPTION = "請求項長修飾語先行揭露小更新"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build() -> tuple[Path, Path, Path]:
    package_name = (
        f"Saint-Island_Patent_MDS_v{VERSION}_{DESCRIPTION}_{date.today():%Y%m%d}"
    )
    package_dir = RELEASE_ROOT / package_name
    zip_path = RELEASE_ROOT / f"{package_name}.zip"
    checksum_path = RELEASE_ROOT / f"{package_name}.zip.sha256.txt"
    for output in (package_dir, zip_path, checksum_path):
        if output.exists():
            raise FileExistsError(f"Release output already exists: {output}")

    source_rule = (
        PROJECT_ROOT / "features" / "patent_review" / "rule_engine.py"
    )
    destination_rule = (
        package_dir / "app" / "features" / "patent_review" / "rule_engine.py"
    )
    destination_rule.parent.mkdir(parents=True)
    shutil.copy2(source_rule, destination_rule)
    shutil.copy2(
        PROJECT_ROOT / "packaging" / "Install_Rule_Hotfix.bat",
        package_dir / "Install_Update.bat",
    )

    notes = f"""Saint-Island_Patent_MDS v{VERSION} 請求項規則小更新
====================================================

本更新僅包含 rule_engine.py，不含模型、EXE 或 runtime。
安裝前必須已完成 Saint-Island_Patent_MDS v2.0.3 完整更新。

修正內容
1. 支援「一與……相間隔的第一快拆螺孔」等長前置修飾語。
2. 支援「一與……相鄰的頂面」及「一位於……下方的底面」。
3. 同一請求項後續使用「該第一快拆螺孔／該頂面」時，不再誤報缺少先行揭露。
4. 選擇距離元件最近的有效數量詞，避免套用前一元件的數量詞。

安裝方式
1. 完全關閉 Saint-Island_Patent_MDS。
2. 完整解壓本 ZIP。
3. 將解壓後的資料夾放進公司程式根目錄，雙擊 Install_Update.bat。
4. 看到 [PASS] v2.0.3 claim modifier hotfix installed 即完成。

安裝器會保留一份舊 rule_engine.py 備份，並自動執行語法、離線推論及 GUI 啟動測試。
"""
    (package_dir / "更新說明.txt").write_text(
        notes, encoding="utf-8-sig", newline="\r\n"
    )

    payload_files = sorted(path for path in package_dir.rglob("*") if path.is_file())
    manifest = {
        "product": "Saint-Island_Patent_MDS",
        "version": VERSION,
        "package_type": "code_only_rule_hotfix",
        "requires_full_version": VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_included": False,
        "models_included": False,
        "files": [
            {
                "path": path.relative_to(package_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in payload_files
        ],
    }
    manifest_path = package_dir / "PACKAGE_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with ZipFile(zip_path, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(
                    path,
                    (Path(package_name) / path.relative_to(package_dir)).as_posix(),
                )
    checksum_path.write_text(f"{sha256(zip_path)}\n", encoding="ascii")
    return package_dir, zip_path, checksum_path


if __name__ == "__main__":
    directory, archive, checksum = build()
    print(f"PACKAGE_DIR={directory}")
    print(f"ZIP={archive}")
    print(f"SHA256={checksum}")
