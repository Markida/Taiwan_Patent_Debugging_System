import csv
from pathlib import Path


CLASS_NAMES = tuple("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ("prime",)
MANIFEST_COLUMNS = (
    "id",
    "split",
    "source_image",
    "box_index",
    "crop_path",
    "rotation",
    "label",
    "confidence",
    "status",
)


def normalize_label(value):
    text = str(value or "").strip().upper()

    if text in {"'", "PRIME"}:
        return "prime"

    if len(text) == 1 and text in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        return text

    return ""


def display_label(value):
    return "'" if value == "prime" else str(value or "")


def read_manifest(path):
    path = Path(path)

    if not path.exists():
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_manifest(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")

    with temporary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()

        for row in rows:
            writer.writerow({key: row.get(key, "") for key in MANIFEST_COLUMNS})

    temporary_path.replace(path)
