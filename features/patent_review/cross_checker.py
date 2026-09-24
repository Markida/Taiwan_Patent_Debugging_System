"""Compare reviewed document symbols with editable OCR results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Sequence, Tuple

from features.patent_ocr.label_parser import (
    normalize_label_text,
    normalize_reference_label_text,
)
from features.patent_ocr.review_tools import detection_confidence

from .symbol_transfer import DocumentSymbolTransfer, FULL_SYMBOL_SOURCE


@dataclass
class ImageCrossCheck:
    image_name: str
    detected_labels: List[str] = field(default_factory=list)
    document_labels_not_on_image: List[str] = field(default_factory=list)
    image_labels_not_in_document: List[str] = field(default_factory=list)
    labels_needing_confirmation: List[str] = field(default_factory=list)


@dataclass
class PatentDrawingCrossCheck:
    source_file_name: str
    source_sha256: str
    transfer_ready_for_ocr: bool
    symbol_source: str
    confidence_threshold: float
    document_labels: List[str] = field(default_factory=list)
    detected_labels_all_images: List[str] = field(default_factory=list)
    document_labels_missing_from_all_images: List[str] = field(default_factory=list)
    detected_labels_missing_from_document: List[str] = field(default_factory=list)
    labels_needing_confirmation: List[str] = field(default_factory=list)
    images: List[ImageCrossCheck] = field(default_factory=list)
    generated_at_utc: str = ""
    schema_version: str = "3.0"

    @property
    def ready_for_final_comparison(self) -> bool:
        return self.transfer_ready_for_ocr and not self.labels_needing_confirmation

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["ready_for_final_comparison"] = self.ready_for_final_comparison
        payload["summary"] = {
            "document_label_count": len(self.document_labels),
            "detected_label_count": len(self.detected_labels_all_images),
            "missing_from_all_images_count": len(
                self.document_labels_missing_from_all_images
            ),
            "missing_from_document_count": len(
                self.detected_labels_missing_from_document
            ),
            "needs_confirmation_count": len(self.labels_needing_confirmation),
            "image_count": len(self.images),
        }
        return payload


def _unique(values: Iterable[str], *, preserve_reference_symbols: bool = False) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        normalized = (
            normalize_reference_label_text(value)
            if preserve_reference_symbols
            else normalize_label_text(value)
        )
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def _image_labels(
    result: Dict[str, object],
    confidence_threshold: float,
) -> Tuple[List[str], List[str]]:
    if "detections" not in result:
        return _unique(result.get("numbers", result.get("labels", [])) or []), []

    detected: List[str] = []
    uncertain: List[str] = []
    for detection in result.get("detections", []) or []:
        if detection.get("deleted") or detection.get("auto_filtered"):
            continue
        label = normalize_label_text(
            detection.get("label", detection.get("number", ""))
        )
        if not label:
            continue
        detected.append(label)
        manually_confirmed = bool(
            detection.get("manually_corrected") or detection.get("manual_added")
        )
        if (
            not manually_confirmed
            and detection_confidence(detection) < confidence_threshold
        ):
            uncertain.append(label)
    return _unique(detected), _unique(uncertain)


def compare_document_symbols_with_ocr(
    transfer: DocumentSymbolTransfer,
    all_results: Sequence[Dict[str, object]],
    *,
    confidence_threshold: float = 0.60,
    symbol_source: str = FULL_SYMBOL_SOURCE,
) -> PatentDrawingCrossCheck:
    """Compare labels without treating normal per-image absence as an error."""

    document_labels = _unique(
        (entry.label for entry in transfer.entries_for(symbol_source)),
        preserve_reference_symbols=True,
    )
    document_set = set(document_labels)
    image_checks: List[ImageCrossCheck] = []
    all_detected: List[str] = []
    all_uncertain: List[str] = []

    for index, result in enumerate(all_results, start=1):
        detected, uncertain = _image_labels(result, confidence_threshold)
        all_detected.extend(detected)
        all_uncertain.extend(uncertain)
        detected_set = set(detected)
        image_checks.append(
            ImageCrossCheck(
                image_name=str(result.get("image_name") or f"Pic_{index:02d}"),
                detected_labels=detected,
                document_labels_not_on_image=[
                    label for label in document_labels if label not in detected_set
                ],
                image_labels_not_in_document=[
                    label for label in detected if label not in document_set
                ],
                labels_needing_confirmation=uncertain,
            )
        )

    detected_labels = _unique(all_detected)
    detected_set = set(detected_labels)
    uncertain_labels = _unique(all_uncertain)
    return PatentDrawingCrossCheck(
        source_file_name=transfer.file_name,
        source_sha256=transfer.source_sha256,
        transfer_ready_for_ocr=transfer.is_source_ready(symbol_source),
        symbol_source=symbol_source,
        confidence_threshold=float(confidence_threshold),
        document_labels=document_labels,
        detected_labels_all_images=detected_labels,
        document_labels_missing_from_all_images=[
            label for label in document_labels if label not in detected_set
        ],
        detected_labels_missing_from_document=[
            label for label in detected_labels if label not in document_set
        ],
        labels_needing_confirmation=uncertain_labels,
        images=image_checks,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
    )
