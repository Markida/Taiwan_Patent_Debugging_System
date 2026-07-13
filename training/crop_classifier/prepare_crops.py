"""Extract real character crops from the manually boxed detector dataset."""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

import cv2

from common import normalize_label, read_manifest, write_manifest


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_DATASET = WORKSPACE_ROOT / "dataset"
DEFAULT_OUTPUT = SCRIPT_DIR / "review_dataset"


def configure_conda_dll_paths():
    environment_root = Path(sys.executable).resolve().parent
    candidates = [
        environment_root,
        environment_root / "Library" / "mingw-w64" / "bin",
        environment_root / "Library" / "usr" / "bin",
        environment_root / "Library" / "bin",
        environment_root / "Scripts",
        environment_root / "bin",
    ]
    existing = [str(path) for path in candidates if path.exists()]

    if existing:
        os.environ["PATH"] = os.pathsep.join(existing + [os.environ.get("PATH", "")])

    if hasattr(os, "add_dll_directory"):
        return [
            os.add_dll_directory(str(path))
            for path in candidates
            if path.exists()
        ]

    return []


CONDA_DLL_HANDLES = configure_conda_dll_paths()
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract boxed patent characters and optionally add EasyOCR suggestions."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pad", type=int, default=6)
    parser.add_argument("--auto-label", action="store_true")
    parser.add_argument(
        "--rotation-search",
        action="store_true",
        help="Normalize legacy training crops only; runtime recognition stays single-angle.",
    )
    parser.add_argument("--allowlist", default="0123456789")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def rotate_image(image, angle):
    angle = int(angle) % 360

    if angle == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)

    return image


def preprocess_for_ocr(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    if min(gray.shape[:2]) >= 11:
        gray = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11,
            2,
        )

    return cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)


def create_reader():
    import easyocr

    return easyocr.Reader(
        ["en"],
        gpu=False,
        model_storage_directory=str(PROJECT_ROOT / "easyocr_models"),
        download_enabled=False,
    )


def recognize_crop(reader, crop, allowlist, rotation_search):
    angles = (0, 90, 180, 270) if rotation_search else (0,)
    best_label = ""
    best_confidence = 0.0
    best_rotation = 0

    for angle in angles:
        candidate = preprocess_for_ocr(rotate_image(crop, angle))
        height, width = candidate.shape[:2]
        results = reader.recognize(
            candidate,
            horizontal_list=[[0, width, 0, height]],
            free_list=[],
            allowlist=allowlist,
            detail=1,
            rotation_info=None,
            paragraph=False,
        )

        for result in results or []:
            if len(result) < 3:
                continue

            label = normalize_label(result[1])
            confidence = float(result[2])

            if label and confidence > best_confidence:
                best_label = label
                best_confidence = confidence
                best_rotation = angle

    return best_label, best_confidence, best_rotation


def yolo_box_to_pixels(parts, image_width, image_height, pad):
    center_x, center_y, width, height = map(float, parts[1:5])
    x1 = max(0, round((center_x - width / 2) * image_width) - pad)
    y1 = max(0, round((center_y - height / 2) * image_height) - pad)
    x2 = min(image_width, round((center_x + width / 2) * image_width) + pad)
    y2 = min(image_height, round((center_y + height / 2) * image_height) + pad)
    return x1, y1, x2, y2


def normalize_page_rotations(rows):
    """Use one orientation per source page, matching the app's page rotation UI."""

    pages = defaultdict(list)

    for row in rows:
        pages[row["source_image"]].append(row)

    for page_rows in pages.values():
        votes = defaultdict(float)

        # 0/1/8 are too symmetric to be reliable orientation voters.
        for row in page_rows:
            if row.get("label") not in set("2345679"):
                continue

            confidence = float(row.get("confidence") or 0.0)

            if confidence >= 0.80:
                votes[int(row.get("rotation") or 0) % 360] += confidence

        if not votes:
            for row in page_rows:
                confidence = float(row.get("confidence") or 0.0)
                votes[int(row.get("rotation") or 0) % 360] += confidence

        if not votes:
            continue

        page_rotation = max(votes.items(), key=lambda item: item[1])[0]

        for row in page_rows:
            row["rotation"] = page_rotation


def main():
    args = parse_args()
    dataset_root = args.dataset.resolve()
    output_root = args.output.resolve()
    manifest_path = output_root / "manifest.csv"
    existing_rows = read_manifest(manifest_path)

    if existing_rows and not args.resume:
        raise FileExistsError(
            f"Manifest already exists: {manifest_path}\nUse --resume to keep existing work."
        )

    rows = list(existing_rows)
    completed_ids = {row["id"] for row in existing_rows}
    reader = create_reader() if args.auto_label else None
    processed = 0

    for split in ("train", "val"):
        image_dir = dataset_root / "images" / split
        label_dir = dataset_root / "labels" / split
        crop_dir = output_root / "crops" / split
        crop_dir.mkdir(parents=True, exist_ok=True)

        for image_path in sorted(image_dir.glob("*.png")):
            image = cv2.imread(str(image_path))

            if image is None:
                raise ValueError(f"Cannot read image: {image_path}")

            image_height, image_width = image.shape[:2]
            label_path = label_dir / f"{image_path.stem}.txt"
            annotations = [
                line.split()
                for line in label_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            for box_index, parts in enumerate(annotations, start=1):
                crop_id = f"{split}_{image_path.stem}_{box_index:04d}"

                if crop_id in completed_ids:
                    continue

                x1, y1, x2, y2 = yolo_box_to_pixels(
                    parts,
                    image_width,
                    image_height,
                    args.pad,
                )
                crop = image[y1:y2, x1:x2]

                if crop.size == 0:
                    continue

                crop_relative = Path("crops") / split / f"{crop_id}.png"
                crop_path = output_root / crop_relative

                if not cv2.imwrite(str(crop_path), crop):
                    raise RuntimeError(f"Cannot write crop: {crop_path}")

                suggestion = ""
                confidence = 0.0
                rotation = 0

                if reader is not None:
                    suggestion, confidence, rotation = recognize_crop(
                        reader,
                        crop,
                        args.allowlist,
                        args.rotation_search,
                    )

                rows.append(
                    {
                        "id": crop_id,
                        "split": split,
                        "source_image": str(image_path),
                        "box_index": box_index,
                        "crop_path": crop_relative.as_posix(),
                        "rotation": rotation,
                        "label": suggestion,
                        "confidence": f"{confidence:.6f}",
                        "status": "suggested" if suggestion else "pending",
                    }
                )
                processed += 1

                if processed % 100 == 0:
                    write_manifest(manifest_path, rows)
                    print(f"Prepared {processed} new crops...")

    if args.rotation_search:
        normalize_page_rotations(rows)

    write_manifest(manifest_path, rows)
    print(f"Manifest: {manifest_path}")
    print(f"Total rows: {len(rows)} (new: {processed})")


if __name__ == "__main__":
    main()
