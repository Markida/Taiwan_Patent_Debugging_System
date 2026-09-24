"""Export reviewed OCR mistakes as a compact retraining package."""

from __future__ import annotations

import json
import re
import shutil
import socket
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from app.config import REVIEW_REPORT_NETWORK_DIR
from app.paths import get_output_base_dir
from features.patent_ocr.review_tools import detection_confidence


PACKAGE_PREFIX = "Saint-Island_Patent_MDS_review"


class ReviewPackageTransferError(RuntimeError):
    """Raised when a valid local package could not reach the network share."""

    def __init__(self, network_dir, local_backup_path, reason):
        self.network_dir = Path(network_dir)
        self.local_backup_path = Path(local_backup_path)
        self.reason = reason
        super().__init__(
            "錯誤回報 ZIP 已建立，但無法傳送到公司區域網路。\n\n"
            f"目標資料夾：{self.network_dir}\n"
            f"本機備份：{self.local_backup_path}\n\n"
            f"失敗原因：{reason}\n\n"
            "請確認已連上公司區域網路，稍後再重新按一次「匯出錯誤回報」。"
        )


def _safe_computer_name():
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", socket.gethostname()).strip("-_")
    return name or "PC"


def _package_filename(created_at):
    timestamp = created_at.strftime("%Y%m%d_%H%M%S_%f")
    return (
        f"{PACKAGE_PREFIX}_{timestamp}_{_safe_computer_name()}_"
        f"{uuid4().hex[:8]}.zip"
    )


def _try_unlink(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _encoded_png(image):
    import cv2

    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise RuntimeError("無法編碼回報圖片。")
    return encoded.tobytes()


def _bbox(detection, image):
    height, width = image.shape[:2]
    try:
        x1 = max(0, min(width, int(detection["x1"])))
        y1 = max(0, min(height, int(detection["y1"])))
        x2 = max(0, min(width, int(detection["x2"])))
        y2 = max(0, min(height, int(detection["y2"])))
    except (KeyError, TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def _issue_record(detection, image, threshold, display_name, issue_index, archive):
    confidence = detection_confidence(detection)
    original_label = str(detection.get("original_label", detection.get("label", "")))
    corrected_label = str(detection.get("label", detection.get("number", "")))
    manually_edited = bool(detection.get("manual_edited"))
    manually_added = bool(detection.get("manual_added"))
    deleted = bool(detection.get("deleted"))
    auto_filtered = bool(detection.get("auto_filtered"))
    low_confidence = not manually_added and confidence < float(threshold)

    issue_types = []
    if low_confidence:
        issue_types.append("low_confidence")
    if manually_edited:
        issue_types.append("manual_edit")
    if manually_added:
        issue_types.append("manual_add")
    if deleted:
        issue_types.append("deleted_false_positive")
    if auto_filtered:
        issue_types.append("auto_filtered_false_positive")
    if not issue_types:
        return None

    box = _bbox(detection, image)
    crop_file = None
    if box is not None:
        x1, y1, x2, y2 = box
        crop = image[y1:y2, x1:x2]
        if crop.size:
            crop_file = f"crops/{display_name}_{issue_index:03d}.png"
            archive.writestr(crop_file, _encoded_png(crop))

    return {
        "issue_types": issue_types,
        "original_label": original_label,
        "corrected_label": "" if deleted or auto_filtered else corrected_label,
        "confidence": round(confidence, 6),
        "ocr_confidence": detection.get("ocr_conf"),
        "yolo_confidence": detection.get("yolo_conf"),
        "bbox": box,
        "crop_file": crop_file,
        "rejection_reason": detection.get("rejection_reason"),
    }


def _create_review_archive(results, confidence_threshold, output_path, created_at):
    """Create and validate one review ZIP at ``output_path``."""

    manifest = {
        "format_version": 1,
        "project_name": "Saint-Island_Patent_MDS",
        "source_computer": socket.gethostname(),
        "created_at": created_at.isoformat(timespec="seconds"),
        "confidence_threshold": float(confidence_threshold),
        "images": [],
    }
    issue_count = 0

    try:
        with ZipFile(output_path, "w", ZIP_DEFLATED) as archive:
            for image_index, result in enumerate(results, start=1):
                display_name = str(
                    result.get("image_name") or f"Pic_{image_index:02d}"
                )
                image_path = Path(result.get("image_path", ""))
                from features.patent_ocr.image_io import read_image

                image = read_image(image_path)
                if image is None:
                    raise RuntimeError(f"匯出時無法讀取圖片：{image_path}")

                detections = list(result.get("detections", []))
                detections.extend(result.get("review_deleted", []))
                detections.extend(result.get("rejected_detections", []))
                issues = []
                for detection_index, detection in enumerate(detections, start=1):
                    issue = _issue_record(
                        detection,
                        image,
                        confidence_threshold,
                        display_name,
                        detection_index,
                        archive,
                    )
                    if issue is not None:
                        issues.append(issue)

                if not issues:
                    continue

                image_file = f"images/{display_name}.png"
                archive.writestr(image_file, _encoded_png(image))
                issue_count += len(issues)
                manifest["images"].append({
                    "display_name": display_name,
                    "original_filename": Path(
                        result.get("original_image_path", image_path)
                    ).name,
                    "rotation_degrees": int(result.get("rotation_degrees", 0)),
                    "image_file": image_file,
                    "corrected_numbers": list(result.get("numbers", [])),
                    "issues": issues,
                })

            if not issue_count:
                raise ValueError(
                    "目前沒有低信心、手動修改、新增、刪除"
                    "或自動過濾的結果可匯出。"
                )

            manifest["issue_count"] = issue_count
            manifest["image_count"] = len(manifest["images"])
            archive.writestr(
                "review.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )

        with ZipFile(output_path, "r") as archive:
            corrupt_member = archive.testzip()
        if corrupt_member is not None:
            raise RuntimeError(f"錯誤回報 ZIP 驗證失敗：{corrupt_member}")
    except Exception:
        _try_unlink(output_path)
        raise


def _send_to_network(local_path, network_dir):
    """Copy a valid local archive to the share without exposing partial ZIPs."""

    network_dir = Path(network_dir)
    destination = network_dir / local_path.name
    partial_path = network_dir / f".{local_path.name}.{uuid4().hex}.part"
    destination_created = False

    try:
        network_dir.mkdir(parents=True, exist_ok=True)
        while destination.exists():
            destination = network_dir / _package_filename(datetime.now())

        with local_path.open("rb") as source, partial_path.open("xb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)

        if partial_path.stat().st_size != local_path.stat().st_size:
            raise OSError("傳送後的檔案大小不一致。")

        partial_path.rename(destination)
        destination_created = True
        with ZipFile(destination, "r") as archive:
            corrupt_member = archive.testzip()
        if corrupt_member is not None:
            raise OSError(f"區網 ZIP 驗證失敗：{corrupt_member}")
    except Exception as error:
        _try_unlink(partial_path)
        if destination_created:
            _try_unlink(destination)
        raise ReviewPackageTransferError(
            network_dir,
            local_path,
            error,
        ) from error

    _try_unlink(local_path)
    return destination


def export_review_package(
    results,
    confidence_threshold=0.70,
    output_dir=None,
    network_dir=None,
    staging_dir=None,
):
    """Create a review ZIP and, by default, send it to the company share.

    ``output_dir`` is retained as an explicit local-export override for tools and
    tests.  Normal GUI calls omit it and use the configured network folder.
    """

    created_at = datetime.now().astimezone()
    filename = _package_filename(created_at)

    if output_dir is not None:
        local_output_dir = Path(output_dir)
        local_output_dir.mkdir(parents=True, exist_ok=True)
        output_path = local_output_dir / filename
        _create_review_archive(
            results,
            confidence_threshold,
            output_path,
            created_at,
        )
        return output_path

    local_staging_dir = Path(
        staging_dir or (get_output_base_dir() / "review_exports_pending")
    )
    local_staging_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_staging_dir / filename
    _create_review_archive(
        results,
        confidence_threshold,
        local_path,
        created_at,
    )
    return _send_to_network(
        local_path,
        network_dir or REVIEW_REPORT_NETWORK_DIR,
    )
