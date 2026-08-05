"""Cross-check embodiment paragraphs against labels detected on cited figures."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from features.patent_ocr.label_parser import normalize_label_text

from .models import PatentDocument, PatentParagraph
from .symbol_transfer import extract_document_symbols


_LEADING_FIGURE_REFERENCE = re.compile(
    r"^\s*(?:[【〖]\s*\d{4}\s*[】〗]\s*)?參閱\s*"
    r"(?P<clause>"
    r"圖\s*\d+"
    r"(?:"
    r"\s*(?:到|至|~|～|-)\s*(?:圖\s*)?\d+"
    r"|\s*(?:、|以及|及|與|和)\s*(?:圖\s*)?\d+"
    r")*"
    r"(?:\s*所示)?"
    r")"
)
_FIGURE_RANGE = re.compile(
    r"(?:圖\s*)?(?P<start>\d+)\s*(?:到|至|~|～|-)\s*"
    r"(?:圖\s*)?(?P<end>\d+)"
)
_EXPLICIT_FIGURE_NAME = re.compile(
    r"(?:圖|fig(?:ure)?)\s*[_-]?\s*0*(?P<number>\d+)",
    re.IGNORECASE,
)
_FIGURE_LABEL = re.compile(r"^[0-9A-Za-z]+(?:')?$")
_FIGURE_TOKEN = re.compile(r"圖\s*\d+")
_VISIBLE_PARAGRAPH_NUMBER = re.compile(r"^\s*[【〖]\s*\d{4}\s*[】〗]")
MAX_FIGURE_RANGE_SIZE = 100


@dataclass(frozen=True)
class FigureReference:
    figures: Tuple[int, ...]
    start: int
    end: int
    text: str


@dataclass
class EmbodimentFigureMismatch:
    anchor_paragraph: PatentParagraph
    paragraph_indices: List[int]
    referenced_figures: List[int]
    paragraph_labels: List[str] = field(default_factory=list)
    drawing_labels: List[str] = field(default_factory=list)
    labels_not_in_drawings: List[str] = field(default_factory=list)
    labels_not_in_paragraph: List[str] = field(default_factory=list)
    missing_figures: List[int] = field(default_factory=list)
    inherited_reference: bool = False
    comparison_mode: str = "exact"
    char_start: int = 0
    char_end: int = 0
    highlight_text: str = ""


@dataclass(frozen=True)
class OcrFigurePage:
    page_index: int
    figures: Tuple[int, ...]
    labels: Tuple[str, ...]
    image_path: str = ""
    image_name: str = ""


@dataclass
class EmbodimentFigureComparison:
    """One logical embodiment paragraph and its currently referenced figures."""

    group_index: int
    paragraphs: Tuple[PatentParagraph, ...]
    referenced_figures: List[int] = field(default_factory=list)
    paragraph_labels: List[str] = field(default_factory=list)
    drawing_labels: List[str] = field(default_factory=list)
    relevant_page_indices: List[int] = field(default_factory=list)
    labels_not_in_drawings: List[str] = field(default_factory=list)
    labels_not_in_paragraph: List[str] = field(default_factory=list)
    missing_figures: List[int] = field(default_factory=list)
    inherited_reference: bool = False
    comparison_mode: str = "exact"
    reference: Optional[FigureReference] = None

    @property
    def has_mismatch(self) -> bool:
        return bool(
            self.missing_figures
            or self.labels_not_in_drawings
        )


def parse_figure_number_mapping(text: object) -> List[int]:
    """Parse editable page mappings such as ``1,2`` or ``1-4``."""

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = re.sub(r"圖\s*", "", normalized)
    normalized = re.sub(r"(?:以及|及|與|和)", ",", normalized)
    tokens = [token.strip() for token in re.split(r"[,，、]", normalized)]
    if not tokens or any(not token for token in tokens):
        raise ValueError("請輸入圖號，例如 1、1,2 或 1-4。")
    figures: List[int] = []
    for token in tokens:
        range_match = re.fullmatch(
            r"(?P<start>\d+)\s*(?:到|至|~|～|-)\s*(?P<end>\d+)",
            token,
        )
        if range_match is not None:
            start = int(range_match.group("start"))
            end = int(range_match.group("end"))
            if start <= 0 or end < start:
                raise ValueError(f"圖號區間「{token}」不是有效的遞增範圍。")
            if end - start + 1 > MAX_FIGURE_RANGE_SIZE:
                raise ValueError(
                    f"圖號區間一次最多 {MAX_FIGURE_RANGE_SIZE} 張。"
                )
            figures.extend(range(start, end + 1))
            continue
        if not token.isdigit() or int(token) <= 0:
            raise ValueError(f"「{token}」不是有效圖號。")
        figures.append(int(token))
    return list(dict.fromkeys(figures))


def _unique_labels(values: Iterable[object]) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        label = normalize_label_text(value)
        label = unicodedata.normalize("NFKC", label).replace("′", "'")
        if not _FIGURE_LABEL.fullmatch(label) or label in seen:
            continue
        seen.add(label)
        output.append(label)
    return output


def parse_leading_figure_reference(text: str) -> Optional[FigureReference]:
    """Parse ``參閱圖1至圖4及圖6`` only when it starts a paragraph."""

    source = text or ""
    match = _LEADING_FIGURE_REFERENCE.match(source)
    if match is None:
        return None
    clause = unicodedata.normalize("NFKC", match.group("clause"))
    figures = {
        int(number)
        for number in re.findall(r"\d+", clause)
        if int(number) > 0
    }
    for range_match in _FIGURE_RANGE.finditer(clause):
        start = int(range_match.group("start"))
        end = int(range_match.group("end"))
        if start <= 0 or end < start or end - start + 1 > MAX_FIGURE_RANGE_SIZE:
            continue
        figures.update(range(start, end + 1))
    if not figures:
        return None
    return FigureReference(
        figures=tuple(sorted(figures)),
        start=match.start(),
        end=match.end(),
        text=source[match.start():match.end()],
    )


def infer_figure_number(value: object, fallback: int) -> int:
    match = _EXPLICIT_FIGURE_NAME.search(
        unicodedata.normalize("NFKC", str(value or ""))
    )
    if match is not None:
        return int(match.group("number"))
    return fallback


def _result_figure_numbers(
    result: Dict[str, object],
    fallback: int,
) -> List[int]:
    configured = result.get("figure_numbers")
    if configured not in (None, "", []):
        try:
            if isinstance(configured, (list, tuple, set)):
                return parse_figure_number_mapping(
                    ",".join(str(number) for number in configured)
                )
            return parse_figure_number_mapping(configured)
        except ValueError:
            pass
    explicit = result.get("figure_number")
    try:
        number = int(explicit)
    except (TypeError, ValueError):
        number = 0
    if number > 0:
        return [number]
    for key in ("source_image_name", "original_image_path", "image_path"):
        value = str(result.get(key, ""))
        inferred = infer_figure_number(value, 0)
        if inferred > 0:
            return [inferred]
    return [fallback]


def _ocr_result_labels(result: Dict[str, object]) -> List[str]:
    detections = result.get("detections")
    if detections is not None:
        values = [
            detection.get("label", detection.get("number", ""))
            for detection in detections or []
            if not detection.get("deleted") and not detection.get("auto_filtered")
        ]
    else:
        values = result.get("numbers", result.get("labels", [])) or []
    return _unique_labels(values)


def build_ocr_figure_pages(
    ocr_results: Sequence[Dict[str, object]],
) -> List[OcrFigurePage]:
    """Normalize editable OCR results for UI display and rule evaluation."""

    output: List[OcrFigurePage] = []
    for index, result in enumerate(ocr_results, start=1):
        image_path = str(
            result.get("image_path")
            or result.get("original_image_path")
            or ""
        )
        output.append(
            OcrFigurePage(
                page_index=index,
                figures=tuple(_result_figure_numbers(result, index)),
                labels=tuple(_ocr_result_labels(result)),
                image_path=image_path,
                image_name=str(
                    result.get("source_image_name")
                    or result.get("image_name")
                    or (image_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] if image_path else "")
                    or f"Pic_{index:02d}"
                ),
            )
        )
    return output


def _logical_embodiment_groups(
    document: PatentDocument,
) -> List[List[PatentParagraph]]:
    paragraphs = [
        paragraph
        for paragraph in document.paragraphs
        if paragraph.section_key == "embodiments" and not paragraph.is_heading
    ]
    groups: List[List[PatentParagraph]] = []
    for paragraph in paragraphs:
        if paragraph.numbering_value is not None or not groups:
            groups.append([paragraph])
        else:
            groups[-1].append(paragraph)
    return [group for group in groups if any(item.text.strip() for item in group)]


def _paragraph_group_labels(
    paragraphs: Sequence[PatentParagraph],
    candidate_labels: Sequence[str],
) -> Tuple[List[str], List[str]]:
    if not candidate_labels:
        return [], []
    label_expression = "|".join(
        re.escape(label)
        for label in sorted(candidate_labels, key=len, reverse=True)
    )
    pattern = re.compile(
        r"(?<![0-9A-Za-z'])(?:"
        + label_expression
        + r")(?![0-9A-Za-z'])"
    )
    supplemental_pattern = re.compile(
        r"(?<![0-9A-Za-z'])(?P<label>"
        + label_expression
        + r")(?![0-9A-Za-z'])\s*[（(]\s*見\s*圖\s*\d+"
        r"[^()（）\r\n]{0,40}[）)]"
    )
    found: List[str] = []
    supplemental: List[str] = []
    for paragraph in paragraphs:
        text = unicodedata.normalize("NFKC", paragraph.text).replace("′", "'")
        text = _VISIBLE_PARAGRAPH_NUMBER.sub("", text)
        masked = list(text)
        for match in supplemental_pattern.finditer(text):
            supplemental.append(match.group("label"))
            for index in range(match.start("label"), match.end("label")):
                masked[index] = " "
        text = "".join(masked)
        text = _FIGURE_TOKEN.sub(lambda match: " " * len(match.group(0)), text)
        found.extend(match.group(0) for match in pattern.finditer(text))
    return _unique_labels(found), _unique_labels(supplemental)


def _anchor_span(
    group: Sequence[PatentParagraph],
    reference: Optional[FigureReference],
    reference_paragraph: Optional[PatentParagraph],
    paragraph_labels: Sequence[str],
) -> Tuple[PatentParagraph, int, int, str]:
    anchor = group[0]
    if reference is not None and reference_paragraph is not None:
        return (
            reference_paragraph,
            reference.start,
            reference.end,
            reference.text,
        )
    for paragraph in group:
        for label in paragraph_labels:
            match = re.search(
                rf"(?<![0-9A-Za-z']){re.escape(label)}(?![0-9A-Za-z'])",
                unicodedata.normalize("NFKC", paragraph.text).replace("′", "'"),
            )
            if match is not None:
                return (
                    paragraph,
                    match.start(),
                    match.end(),
                    paragraph.text[match.start():match.end()],
                )
    stripped = anchor.text.strip()
    if not stripped:
        return anchor, 0, 0, ""
    start = anchor.text.find(stripped)
    end = min(len(anchor.text), start + min(16, len(stripped)))
    return anchor, start, end, anchor.text[start:end]


def build_embodiment_figure_comparisons(
    document: PatentDocument,
    ocr_results: Sequence[Dict[str, object]],
) -> List[EmbodimentFigureComparison]:
    """Build every logical row used by the interactive third feature page."""

    figure_pages = build_ocr_figure_pages(ocr_results)
    document_labels = _unique_labels(
        entry.label for entry in extract_document_symbols(document).full_entries
    )
    all_ocr_labels = _unique_labels(
        label for page in figure_pages for label in page.labels
    )
    candidate_labels = _unique_labels([*document_labels, *all_ocr_labels])

    comparisons: List[EmbodimentFigureComparison] = []
    active_figures: Optional[List[int]] = None
    for group_index, group in enumerate(_logical_embodiment_groups(document), start=1):
        first_text_paragraph = next(
            (paragraph for paragraph in group if paragraph.text.strip()),
            group[0],
        )
        reference = parse_leading_figure_reference(first_text_paragraph.text)
        inherited = reference is None
        if reference is not None:
            active_figures = list(reference.figures)
        if not active_figures:
            comparisons.append(
                EmbodimentFigureComparison(
                    group_index=group_index,
                    paragraphs=tuple(group),
                    inherited_reference=inherited,
                    reference=reference,
                )
            )
            continue

        paragraph_labels, supplemental_labels = _paragraph_group_labels(
            group,
            candidate_labels,
        )
        active_figure_set = set(active_figures)
        relevant_pages = [
            page
            for page in figure_pages
            if active_figure_set.intersection(page.figures)
        ]
        drawing_labels = _unique_labels(
            label for page in relevant_pages for label in page.labels
        )
        # ``元件3（見圖1）`` explicitly tells the reader that label 3 belongs
        # to another figure.  Exclude it from the active-figure comparison when
        # it is absent there, but keep it as a normal match if the active figure
        # also happens to contain the label.
        paragraph_labels = _unique_labels(
            [
                *paragraph_labels,
                *(
                    label
                    for label in supplemental_labels
                    if label in drawing_labels
                ),
            ]
        )
        missing_figures = [
            figure
            for figure in active_figures
            if not any(figure in page.figures for page in figure_pages)
        ]
        exact_comparison = all(
            set(page.figures).issubset(active_figure_set)
            for page in relevant_pages
        )
        paragraph_set = set(paragraph_labels)
        drawing_set = set(drawing_labels)
        labels_not_in_drawings = [
            label for label in paragraph_labels if label not in drawing_set
        ]
        # The checker is intentionally one-way: labels present only in a
        # drawing can be harmless contextual content, so they are not treated
        # as a document error.  Only paragraph labels missing from the cited
        # drawings are actionable.
        labels_not_in_paragraph: List[str] = []
        comparisons.append(
            EmbodimentFigureComparison(
                group_index=group_index,
                paragraphs=tuple(group),
                referenced_figures=list(active_figures),
                paragraph_labels=paragraph_labels,
                drawing_labels=drawing_labels,
                relevant_page_indices=[page.page_index for page in relevant_pages],
                labels_not_in_drawings=labels_not_in_drawings,
                labels_not_in_paragraph=labels_not_in_paragraph,
                missing_figures=missing_figures,
                inherited_reference=inherited,
                comparison_mode=("exact" if exact_comparison else "page_subset"),
                reference=reference,
            )
        )
    return comparisons


def find_embodiment_figure_mismatches(
    document: PatentDocument,
    ocr_results: Sequence[Dict[str, object]],
) -> List[EmbodimentFigureMismatch]:
    """Compare each numbered embodiment paragraph with its active figures."""

    # OCR001 is not emitted before any drawing result exists.  The interactive
    # comparison page can still show the document-only rows via the builder.
    if not ocr_results:
        return []

    mismatches: List[EmbodimentFigureMismatch] = []
    for comparison in build_embodiment_figure_comparisons(document, ocr_results):
        if not comparison.has_mismatch or not comparison.referenced_figures:
            continue
        group = comparison.paragraphs
        first_text_paragraph = next(
            (paragraph for paragraph in group if paragraph.text.strip()),
            group[0],
        )
        anchor, start, end, highlight_text = _anchor_span(
            group,
            comparison.reference,
            first_text_paragraph,
            comparison.paragraph_labels,
        )
        mismatches.append(
            EmbodimentFigureMismatch(
                anchor_paragraph=anchor,
                paragraph_indices=[paragraph.index for paragraph in group],
                referenced_figures=list(comparison.referenced_figures),
                paragraph_labels=list(comparison.paragraph_labels),
                drawing_labels=list(comparison.drawing_labels),
                labels_not_in_drawings=list(comparison.labels_not_in_drawings),
                labels_not_in_paragraph=list(comparison.labels_not_in_paragraph),
                missing_figures=list(comparison.missing_figures),
                inherited_reference=comparison.inherited_reference,
                comparison_mode=comparison.comparison_mode,
                char_start=start,
                char_end=end,
                highlight_text=highlight_text,
            )
        )
    return mismatches
