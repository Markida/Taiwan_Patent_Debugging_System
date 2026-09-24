"""Content inventory helpers for the figure-heading training dataset."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


INVENTORY_FILENAME = "dataset_inventory.json"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _combined_digest(files):
    serialized = json.dumps(
        files,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def write_dataset_inventory(dataset_root):
    dataset_root = Path(dataset_root).resolve()
    paths = [dataset_root / "data.yaml", dataset_root / "evaluation_manifest.json"]
    paths.extend(sorted((dataset_root / "images").glob("*/*")))
    paths.extend(sorted((dataset_root / "labels").glob("*/*")))
    files = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"資料集檔案缺漏：{path}")
        files.append(
            {
                "path": path.relative_to(dataset_root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    payload = {
        "version": 1,
        "file_count": len(files),
        "content_sha256": _combined_digest(files),
        "files": files,
    }
    inventory_path = dataset_root / INVENTORY_FILENAME
    inventory_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    payload["inventory_sha256"] = sha256_file(inventory_path)
    return payload


def verify_dataset_inventory(
    dataset_root,
    *,
    expected_content_sha256=None,
    expected_inventory_sha256=None,
):
    dataset_root = Path(dataset_root).resolve()
    inventory_path = dataset_root / INVENTORY_FILENAME
    if not inventory_path.is_file():
        raise FileNotFoundError(f"找不到資料集檔案清單：{inventory_path}")
    if (
        expected_inventory_sha256
        and sha256_file(inventory_path) != expected_inventory_sha256
    ):
        raise RuntimeError("資料集檔案清單 SHA-256 已變更。")
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    files = payload.get("files", [])
    if payload.get("file_count") != len(files):
        raise RuntimeError("資料集檔案清單的數量欄位不一致。")
    for item in files:
        path = dataset_root / item["path"]
        if not path.is_file():
            raise FileNotFoundError(f"資料集檔案缺漏：{path}")
        if path.stat().st_size != int(item["size"]):
            raise RuntimeError(f"資料集檔案大小已變更：{path}")
        if sha256_file(path) != item["sha256"]:
            raise RuntimeError(f"資料集檔案 SHA-256 已變更：{path}")
    content_digest = _combined_digest(files)
    if content_digest != payload.get("content_sha256"):
        raise RuntimeError("資料集內容總 SHA-256 與清單不一致。")
    if expected_content_sha256 and content_digest != expected_content_sha256:
        raise RuntimeError("資料集內容與建立報告不一致。")
    return payload
