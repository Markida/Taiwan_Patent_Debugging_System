"""Cross-check embodiment paragraphs against labels detected on cited figures."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from features.patent_ocr.label_parser import normalize_label_text
from features.patent_ocr.figure_identifiers import (
    FIGURE_IDENTIFIER_EXPRESSION,
    FigureIdentifier,
    expand_figure_identifier_range,
    figure_sort_key,
    normalize_figure_identifier,
    ordered_unique_figures,
    parse_figure_number_mapping,
)

from .models import PatentDocument, PatentParagraph
from .symbol_transfer import extract_document_symbols


_FIGURE_IDENTIFIER_EXPRESSION = FIGURE_IDENTIFIER_EXPRESSION
_FIGURE_CLAUSE_EXPRESSION = (
    rf"圖\s*{_FIGURE_IDENTIFIER_EXPRESSION}"
    rf"(?:"
    rf"\s*(?:到|至|~|～|-)\s*(?:圖\s*)?{_FIGURE_IDENTIFIER_EXPRESSION}"
    rf"|\s*(?:、|[，,]|(?:[，,]\s*)?(?:以及|及|與|和))\s*"
    rf"(?:圖\s*)?{_FIGURE_IDENTIFIER_EXPRESSION}"
    rf")*"
)
_BRIEF_DESCRIPTION_LEADING_FIGURE_CLAUSE = re.compile(
    rf"^\s*(?:[【〖]\s*\d{{1,4}}\s*[】〗]\s*)?"
    rf"(?P<clause>{_FIGURE_CLAUSE_EXPRESSION})",
    re.IGNORECASE,
)
_DRAWING_CAPTION_TRAILING_PUNCTUATION = re.compile(
    r"\s*(?:(?:[；;]\s*(?:以及|及)?)|[。．.])\s*$"
)
_DRAWING_CAPTION_PREDICATE = re.compile(
    r"^\s*(?:為|是|係|乃|示出|顯示|繪示|揭示|表示|描繪)\s*"
)
_DRAWING_CAPTION_DISTRIBUTIVE = re.compile(
    r"^\s*(?:(?:為|是|係|乃)\s*)?分別"
)
_DRAWING_CAPTION_NAME_ENDING = re.compile(r"(?:圖像|畫面|圖)$")
_DRAWING_CAPTION_NAME_REFERENCE = re.compile(
    rf"圖\s*{_FIGURE_IDENTIFIER_EXPRESSION}",
    re.IGNORECASE,
)
_DRAWING_CAPTION_NAME_DESCRIPTORS = (
    "使用狀態", "操作狀態", "局部", "部分", "細部", "放大", "分解",
    "組合", "立體", "平面", "正面", "背面", "前視", "後視",
    "左視", "右視", "側視", "正視", "俯視", "府視", "仰視",
    "頂視", "底視", "剖視", "剖面", "斷面", "截面", "電路",
    "功能", "系統", "控制", "工作", "流程", "配置", "結構",
    "架構", "外觀", "波形", "時序", "關係", "原理", "概略",
    "透視", "投影", "展開", "爆炸", "示意", "方塊",
)
_DRAWING_CAPTION_STANDARD_NAME = re.compile(
    r"(?P<name>(?:"
    + "|".join(
        re.escape(value)
        for value in sorted(
            _DRAWING_CAPTION_NAME_DESCRIPTORS,
            key=len,
            reverse=True,
        )
    )
    + r")+(?:圖像|畫面|圖))$"
)
_ADDITIVE_FIGURE_REFERENCE_CUE_EXPRESSION = (
    r"(?:例如|舉例)(?:可)?(?:為|是)?"
)
_FIGURE_REFERENCE_CUE_EXPRESSION = (
    rf"(?:{_ADDITIVE_FIGURE_REFERENCE_CUE_EXPRESSION}|"
    r"(?:請|可|另請|另|再)?"
    r"(?:參閱|參照|參考|參見|詳見|參|見|如|在|於|由))"
)
_ADDITIVE_FIGURE_REFERENCE_CUE = re.compile(
    rf"^{_ADDITIVE_FIGURE_REFERENCE_CUE_EXPRESSION}$"
)
_FIGURE_REFERENCE_CANDIDATE = re.compile(
    rf"(?P<cue>{_FIGURE_REFERENCE_CUE_EXPRESSION})?\s*"
    rf"(?P<clause>{_FIGURE_CLAUSE_EXPRESSION})"
    rf"(?P<tail>\s*(?:所示|中|分別)?)"
)
_FIGURE_RANGE = re.compile(
    r"(?:圖\s*)?(?P<start>\d+[A-Za-z]*)"
    r"(?![A-Za-z'′’])\s*(?:到|至|~|～|-)\s*"
    r"(?:圖\s*)?(?P<end>\d+[A-Za-z]*)(?![A-Za-z'′’])"
)
_EXPLICIT_FIGURE_NAME = re.compile(
    rf"(?:圖|fig(?:ure)?)\s*[_-]?\s*"
    rf"(?P<identifier>{_FIGURE_IDENTIFIER_EXPRESSION})",
    re.IGNORECASE,
)
_FIGURE_LABEL = re.compile(r"^[0-9A-Za-z]+(?:')?$")
_FIGURE_TOKEN = re.compile(rf"圖\s*{_FIGURE_IDENTIFIER_EXPRESSION}")
_VISIBLE_PARAGRAPH_NUMBER = re.compile(r"^\s*[【〖]\s*\d{1,4}\s*[】〗]")
_LEADING_PREFIX = re.compile(r"^\s*(?:[【〖]\s*\d{1,4}\s*[】〗]\s*)?$")
_BARE_FIGURE_REFERENCE_VERB = re.compile(
    r"^\s*(?:分別)?(?:示出|顯示|繪示|揭示|釋出|為|係|是)"
)
_CONTEXTUAL_DISPLAY_REFERENCE_PREFIX = re.compile(
    r"(?:也可以|亦可以|可以|也可|亦可|可|能夠|能)\s*"
    r"(?:顯示|呈現|展示|播放|輸出|產生)\s*$"
)
_CONTEXTUAL_DISPLAY_REFERENCE_SUFFIX = re.compile(r"^\s*(?:的|之)")
_ANY_FIGURE_MENTION = re.compile(
    rf"圖\s*(?P<figure>{_FIGURE_IDENTIFIER_EXPRESSION})",
    re.IGNORECASE,
)
_SECTION_VIEW_WORD = re.compile(r"剖視|剖面|截面|斷面")
_ROMAN_CUT_LINE = re.compile(
    r"(?<![A-Za-z])(?P<left>[IVXLCDM\u2160-\u2188]{1,15})\s*"
    r"(?:-|－|—|–|~|～|至|到)\s*"
    r"(?P<right>[IVXLCDM\u2160-\u2188]{1,15})(?![A-Za-z])",
    re.IGNORECASE,
)
MAX_FIGURE_RANGE_SIZE = 100


@dataclass(frozen=True)
class FigureReference:
    figures: Tuple[FigureIdentifier, ...]
    start: int
    end: int
    text: str
    additive: bool = False


@dataclass
class EmbodimentFigureMismatch:
    anchor_paragraph: PatentParagraph
    paragraph_indices: List[int]
    referenced_figures: List[FigureIdentifier]
    paragraph_labels: List[str] = field(default_factory=list)
    drawing_labels: List[str] = field(default_factory=list)
    labels_not_in_drawings: List[str] = field(default_factory=list)
    labels_not_in_paragraph: List[str] = field(default_factory=list)
    missing_figures: List[FigureIdentifier] = field(default_factory=list)
    inherited_reference: bool = False
    comparison_mode: str = "exact"
    char_start: int = 0
    char_end: int = 0
    highlight_text: str = ""


@dataclass(frozen=True)
class OcrFigurePage:
    page_index: int
    figures: Tuple[FigureIdentifier, ...]
    labels: Tuple[str, ...]
    image_path: str = ""
    image_name: str = ""


@dataclass
class EmbodimentFigureComparison:
    """One logical embodiment paragraph and its currently referenced figures."""

    group_index: int
    paragraphs: Tuple[PatentParagraph, ...]
    referenced_figures: List[FigureIdentifier] = field(default_factory=list)
    paragraph_labels: List[str] = field(default_factory=list)
    drawing_labels: List[str] = field(default_factory=list)
    relevant_page_indices: List[int] = field(default_factory=list)
    labels_not_in_drawings: List[str] = field(default_factory=list)
    labels_not_in_paragraph: List[str] = field(default_factory=list)
    missing_figures: List[FigureIdentifier] = field(default_factory=list)
    inherited_reference: bool = False
    comparison_mode: str = "exact"
    reference: Optional[FigureReference] = None
    reference_paragraph: Optional[PatentParagraph] = None
    scope_text: str = ""

    @property
    def has_mismatch(self) -> bool:
        return bool(
            self.missing_figures
            or self.labels_not_in_drawings
        )


@dataclass
class _ReferenceScope:
    figures: List[FigureIdentifier]
    fragments: List[str] = field(default_factory=list)
    inherited_reference: bool = True
    reference: Optional[FigureReference] = None
    reference_paragraph: Optional[PatentParagraph] = None
    additive_reference: bool = False


@dataclass(frozen=True)
class CrossSectionReference:
    paragraph: PatentParagraph
    target_figure: FigureIdentifier
    source_figures: Tuple[FigureIdentifier, ...]
    roman_text: str
    roman_right_text: str
    roman_value: Optional[int]
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class CrossSectionMismatch:
    reference: CrossSectionReference
    reason: str
    source_drawing_labels: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UnusedFigure:
    figure: FigureIdentifier
    anchor_paragraph: Optional[PatentParagraph] = None


@dataclass(frozen=True)
class SupplementalFigureBinding:
    """A component-label pair locally assigned to figures by a parenthesis."""

    label: str
    figures: Tuple[FigureIdentifier, ...]
    paragraph: PatentParagraph
    start: int
    end: int
    text: str


def _normalize_figure_identifier(value: object) -> FigureIdentifier:
    return normalize_figure_identifier(value)


def _ordered_unique_figures(
    values: Iterable[FigureIdentifier],
) -> List[FigureIdentifier]:
    return ordered_unique_figures(values)


def _figure_sort_key(value: FigureIdentifier) -> Tuple[int, int, str, int]:
    return figure_sort_key(value)


def roman_numeral_to_int(value: object) -> Optional[int]:
    """Return a strict Roman-numeral value, rejecting non-canonical text."""

    text = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
    if not text or re.fullmatch(r"[IVXLCDM]+", text) is None:
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    previous = 0
    for character in reversed(text):
        current = values[character]
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    if not 0 < total < 4000 or _int_to_roman(total) != text:
        return None
    return total


def _int_to_roman(value: int) -> str:
    output = []
    remainder = int(value)
    for number, token in (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ):
        while remainder >= number:
            output.append(token)
            remainder -= number
    return "".join(output)


def _figure_numeric_part(value: FigureIdentifier) -> Optional[int]:
    match = re.match(r"\d+", str(value))
    return int(match.group(0)) if match is not None else None


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


def _figure_identifiers_from_clause(clause: str) -> Tuple[FigureIdentifier, ...]:
    normalized = unicodedata.normalize("NFKC", clause)
    figures: List[FigureIdentifier] = []
    for token in re.findall(_FIGURE_IDENTIFIER_EXPRESSION, normalized):
        try:
            figures.append(_normalize_figure_identifier(token))
        except ValueError:
            continue
    for range_match in _FIGURE_RANGE.finditer(normalized):
        try:
            expanded = expand_figure_identifier_range(
                range_match.group("start"),
                range_match.group("end"),
            )
        except ValueError:
            continue
        figures.extend(expanded)
    return tuple(sorted(_ordered_unique_figures(figures), key=_figure_sort_key))


def _parenthetical_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    stack: List[Tuple[str, int]] = []
    pairs = {"(": ")", "（": "）"}
    for index, character in enumerate(text):
        if character in pairs:
            stack.append((character, index))
        elif character in pairs.values() and stack:
            opening, start = stack[-1]
            if pairs[opening] == character:
                stack.pop()
                spans.append((start, index + 1))
    return spans


def _is_contextual_display_reference(source: str, match) -> bool:
    """Return whether ``如圖...的...`` describes displayable content only."""

    if (match.group("cue") or "") != "如":
        return False
    prefix = source[max(0, match.start() - 28):match.start()]
    suffix = source[match.end():match.end() + 8]
    return bool(
        _CONTEXTUAL_DISPLAY_REFERENCE_PREFIX.search(prefix)
        and _CONTEXTUAL_DISPLAY_REFERENCE_SUFFIX.match(suffix)
    )


def parse_figure_references(text: str) -> List[FigureReference]:
    """Parse active figure references at a paragraph start or mid-paragraph.

    Parenthetical notes such as「元件3（見圖1）」are deliberately excluded:
    they explain one component label and must not switch the active drawing for
    the following sentence.
    """

    source = text or ""
    parenthetical_spans = _parenthetical_spans(source)
    references: List[FigureReference] = []
    for match in _FIGURE_REFERENCE_CANDIDATE.finditer(source):
        if any(start <= match.start() < end for start, end in parenthetical_spans):
            continue
        if _is_contextual_display_reference(source, match):
            continue
        cue = match.group("cue") or ""
        tail = (match.group("tail") or "").strip()
        leading = _LEADING_PREFIX.fullmatch(source[:match.start()]) is not None
        following = source[match.end():match.end() + 16]
        bare_reference = bool(
            tail in {"中", "所示", "分別"}
            or _BARE_FIGURE_REFERENCE_VERB.match(following)
        )
        if not cue and not leading and not bare_reference:
            continue
        figures = _figure_identifiers_from_clause(match.group("clause"))
        if not figures:
            continue
        references.append(
            FigureReference(
                figures=figures,
                start=match.start(),
                end=match.end(),
                text=source[match.start():match.end()],
                additive=(
                    bool(cue)
                    and _ADDITIVE_FIGURE_REFERENCE_CUE.fullmatch(cue)
                    is not None
                ),
            )
        )
    return references


def parse_leading_figure_reference(text: str) -> Optional[FigureReference]:
    """Return the first active figure reference when it starts a paragraph."""

    source = text or ""
    for reference in parse_figure_references(source):
        if _LEADING_PREFIX.fullmatch(source[:reference.start]) is not None:
            return reference
    return None


def infer_figure_number(
    value: object,
    fallback: FigureIdentifier,
) -> FigureIdentifier:
    match = _EXPLICIT_FIGURE_NAME.search(
        unicodedata.normalize("NFKC", str(value or ""))
    )
    if match is not None:
        return _normalize_figure_identifier(match.group("identifier"))
    return fallback


def _result_figure_numbers(
    result: Dict[str, object],
    fallback: int,
) -> List[FigureIdentifier]:
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
        number = _normalize_figure_identifier(explicit)
    except ValueError:
        number = 0
    if number != 0:
        return [number]
    for key in ("source_image_name", "original_image_path", "image_path"):
        value = str(result.get(key, ""))
        inferred = infer_figure_number(value, 0)
        if inferred != 0:
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


def _nonempty_line_spans(text: str) -> Iterable[Tuple[str, int, int]]:
    offset = 0
    parts = text.splitlines(keepends=True) or ([text] if text else [])
    for part in parts:
        line = part.rstrip("\r\n")
        left = len(line) - len(line.lstrip())
        right = len(line.rstrip())
        if right > left:
            yield line[left:right], offset + left, offset + right
        offset += len(part)


def _iter_drawing_description_caption_parts(
    document: Optional[PatentDocument],
) -> Iterable[Tuple[Tuple[FigureIdentifier, ...], str]]:
    """Yield each caption's leading figures and remaining description."""

    if document is None:
        return
    for paragraph in document.paragraphs:
        if paragraph.section_key != "brief_description_of_drawings":
            continue
        source = paragraph.content_text
        if not source and not paragraph.is_heading:
            source = paragraph.normalized_text or paragraph.text
        for line, _start, _end in _nonempty_line_spans(source or ""):
            match = _BRIEF_DESCRIPTION_LEADING_FIGURE_CLAUSE.match(line)
            if match is None:
                continue
            figures = _figure_identifiers_from_clause(match.group("clause"))
            if figures:
                yield figures, line[match.end():]


def drawing_description_figure_numbers(
    document: Optional[PatentDocument],
) -> List[FigureIdentifier]:
    """Return unique figures introduced by drawing-description caption lines.

    Only a figure clause at the start of each non-empty line is counted.  A
    later cross-reference, such as the ``圖99`` in ``圖6是沿著圖99...`` does
    not describe an additional drawing and therefore must not affect the
    total.  ``content_text`` also preserves a caption that shares the same
    paragraph as the section heading.
    """

    figures: List[FigureIdentifier] = []
    for caption_figures, _description in _iter_drawing_description_caption_parts(
        document
    ):
        figures.extend(caption_figures)
    return _ordered_unique_figures(figures)


def _caption_description_body(value: object) -> Tuple[str, bool]:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    while True:
        trimmed = _DRAWING_CAPTION_TRAILING_PUNCTUATION.sub("", text)
        if trimmed == text:
            break
        text = trimmed.strip()
    text = text.lstrip(" \t，,:：")
    distributive = _DRAWING_CAPTION_DISTRIBUTIVE.match(text) is not None
    text = re.sub(r"^(?:為|是|係|乃)\s*", "", text)
    text = re.sub(r"^分別\s*", "", text)
    text = _DRAWING_CAPTION_PREDICATE.sub("", text)
    return text.strip(), distributive


def _strip_caption_name_article(value: str) -> str:
    text = re.sub(r"^(?:一個|一種|一幅|一張|其|該)\s*", "", value.strip())
    return re.sub(
        r"^一(?=(?:立體|平面|正面|背面|前視|後視|左視|右視|側視|"
        r"正視|俯視|府視|仰視|頂視|底視|剖視|剖面|斷面|截面|"
        r"局部|部分|細部|放大|分解|組合|電路|功能|系統|控制|"
        r"流程|配置|結構|架構|外觀|波形|時序|關係|原理|概略|"
        r"透視|投影|展開|爆炸|示意|方塊))",
        "",
        text,
    )


def _is_reliable_caption_name(value: str) -> bool:
    return bool(
        value
        and len(value) <= 40
        and _DRAWING_CAPTION_NAME_ENDING.search(value)
        and _DRAWING_CAPTION_NAME_REFERENCE.search(value) is None
        and not any(mark in value for mark in "，,；;。．.：:")
    )


def _extract_drawing_caption_name(value: object) -> str:
    text, _distributive = _caption_description_body(value)
    if not text:
        return ""

    possessives = list(re.finditer(r"[的之]", text))
    for match in reversed(possessives):
        candidate = _strip_caption_name_article(text[match.end():])
        if _is_reliable_caption_name(candidate):
            return candidate

    candidate = _strip_caption_name_article(text)
    if not any(mark in candidate for mark in "、，,；;。．.：:"):
        suffix = _DRAWING_CAPTION_STANDARD_NAME.search(candidate)
        if suffix is not None:
            return suffix.group("name")

    return (
        candidate
        if _is_reliable_caption_name(candidate) and len(candidate) <= 40
        else ""
    )


def drawing_description_figure_names(
    document: Optional[PatentDocument],
) -> Dict[FigureIdentifier, str]:
    """Extract a concise drawing name for each figure in the brief description."""

    names: Dict[FigureIdentifier, str] = {}
    for figures, raw_description in _iter_drawing_description_caption_parts(
        document
    ):
        description, distributive = _caption_description_body(raw_description)
        if distributive:
            parts = [
                part.strip()
                for part in re.split(
                    r"\s*(?:以及|及|與|和|、|，|,)\s*",
                    description,
                )
                if part.strip()
            ]
            part_names = [
                _extract_drawing_caption_name(part) for part in parts
            ]
            if len(part_names) == len(figures) and all(part_names):
                for figure, name in zip(figures, part_names):
                    names.setdefault(figure, name)
            continue

        name = _extract_drawing_caption_name(description)
        if not name:
            continue
        for figure in figures:
            names.setdefault(figure, name)
    return names


def mapped_ocr_figure_numbers(
    ocr_results: Sequence[Dict[str, object]],
) -> List[FigureIdentifier]:
    """Return unique figure identifiers configured on the figure-label page."""

    return _ordered_unique_figures(
        figure
        for page in build_ocr_figure_pages(ocr_results)
        for figure in page.figures
    )


def _normalized_roman_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().upper()


def parse_cross_section_references(
    document: PatentDocument,
) -> List[CrossSectionReference]:
    """Extract flexible ``source figure / Roman cut line / section view`` links.

    The trigger is deliberately semantic instead of sentence-specific: a line
    in the brief description must contain a section-view word and a repeated
    Roman cut-line expression such as ``VI-VI``.  The first figure is the
    section-view target; subsequent figure references are its source drawing.
    """

    references: List[CrossSectionReference] = []
    leading_figure = re.compile(
        rf"^(?:[【〖]\s*\d{{1,4}}\s*[】〗]\s*)?"
        rf"圖\s*(?P<figure>{_FIGURE_IDENTIFIER_EXPRESSION})",
        re.IGNORECASE,
    )
    for paragraph in document.paragraphs:
        if (
            paragraph.is_heading
            or paragraph.section_key != "brief_description_of_drawings"
        ):
            continue
        for line, line_start, _line_end in _nonempty_line_spans(paragraph.text):
            if _SECTION_VIEW_WORD.search(line) is None:
                continue
            roman_match = _ROMAN_CUT_LINE.search(line)
            target_match = leading_figure.search(line)
            if roman_match is None or target_match is None:
                continue
            try:
                target = _normalize_figure_identifier(target_match.group("figure"))
            except ValueError:
                continue

            source_figures: List[FigureIdentifier] = []
            parsed_references = parse_figure_references(line)
            target_consumed = False
            for parsed in parsed_references:
                figures = list(parsed.figures)
                if not target_consumed and target in figures:
                    target_consumed = True
                    figures.remove(target)
                if target_consumed:
                    source_figures.extend(figures)
            if not source_figures:
                mentions: List[FigureIdentifier] = []
                for match in _ANY_FIGURE_MENTION.finditer(line):
                    try:
                        mentions.append(
                            _normalize_figure_identifier(match.group("figure"))
                        )
                    except ValueError:
                        continue
                if mentions and mentions[0] == target:
                    source_figures = mentions[1:]

            left = _normalized_roman_text(roman_match.group("left"))
            right = _normalized_roman_text(roman_match.group("right"))
            roman_value = roman_numeral_to_int(left) if left == right else None
            references.append(
                CrossSectionReference(
                    paragraph=paragraph,
                    target_figure=target,
                    source_figures=tuple(_ordered_unique_figures(source_figures)),
                    roman_text=left,
                    roman_right_text=right,
                    roman_value=roman_value,
                    start=line_start + roman_match.start(),
                    end=line_start + roman_match.end(),
                    text=line,
                )
            )
    return references


def find_cross_section_mismatches(
    document: PatentDocument,
    ocr_results: Sequence[Dict[str, object]],
) -> List[CrossSectionMismatch]:
    """Verify cross-section captions against OCR labels on their source figures."""

    if not ocr_results:
        return []
    pages = build_ocr_figure_pages(ocr_results)
    mismatches: List[CrossSectionMismatch] = []
    for reference in parse_cross_section_references(document):
        if reference.roman_text != reference.roman_right_text:
            mismatches.append(
                CrossSectionMismatch(reference=reference, reason="roman_pair_mismatch")
            )
            continue
        if reference.roman_value is None:
            mismatches.append(
                CrossSectionMismatch(reference=reference, reason="invalid_roman")
            )
            continue
        target_number = _figure_numeric_part(reference.target_figure)
        if target_number != reference.roman_value:
            mismatches.append(
                CrossSectionMismatch(reference=reference, reason="target_mismatch")
            )
        if not reference.source_figures:
            mismatches.append(
                CrossSectionMismatch(reference=reference, reason="source_not_identified")
            )
            continue

        source_set = set(reference.source_figures)
        relevant_pages = [
            page for page in pages if source_set.intersection(page.figures)
        ]
        loaded_figures = {
            figure for page in relevant_pages for figure in page.figures
        }
        missing_sources = [
            figure for figure in reference.source_figures if figure not in loaded_figures
        ]
        source_labels = tuple(
            _unique_labels(label for page in relevant_pages for label in page.labels)
        )
        if missing_sources:
            mismatches.append(
                CrossSectionMismatch(
                    reference=reference,
                    reason="source_not_loaded",
                    source_drawing_labels=source_labels,
                )
            )
        normalized_labels = {
            _normalized_roman_text(label) for label in source_labels
        }
        if relevant_pages and reference.roman_text not in normalized_labels:
            mismatches.append(
                CrossSectionMismatch(
                    reference=reference,
                    reason="roman_not_detected",
                    source_drawing_labels=source_labels,
                )
            )
    return mismatches


def _document_used_figures(document: PatentDocument) -> List[FigureIdentifier]:
    used: List[FigureIdentifier] = []
    for paragraph in document.paragraphs:
        for match in re.finditer(_FIGURE_CLAUSE_EXPRESSION, paragraph.text):
            used.extend(_figure_identifiers_from_clause(match.group(0)))
    return _ordered_unique_figures(used)


def find_unused_ocr_figures(
    document: PatentDocument,
    ocr_results: Sequence[Dict[str, object]],
) -> List[UnusedFigure]:
    """Return OCR-loaded drawings never mentioned anywhere in the document."""

    if not ocr_results:
        return []
    available = _ordered_unique_figures(
        figure
        for page in build_ocr_figure_pages(ocr_results)
        for figure in page.figures
    )
    used = set(_document_used_figures(document))
    anchor = next(
        (
            paragraph
            for paragraph in document.paragraphs
            if paragraph.section_key == "brief_description_of_drawings"
        ),
        None,
    )
    return [
        UnusedFigure(figure=figure, anchor_paragraph=anchor)
        for figure in sorted(available, key=_figure_sort_key)
        if figure not in used
    ]


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
    comparable_groups: List[List[PatentParagraph]] = []
    for group in groups:
        if not any(item.text.strip() for item in group):
            continue
        if any(
            "綜上所述" in re.sub(
                r"\s+",
                "",
                unicodedata.normalize("NFKC", item.text or ""),
            )
            for item in group
        ):
            # 「綜上所述」starts the concluding portion of embodiments.  The
            # complete numbered paragraph containing it, plus every later
            # paragraph, is outside the drawing-comparison scope and must not
            # appear as a row on the interactive comparison page.
            break
        comparable_groups.append(group)
    return comparable_groups


def _legacy_paragraph_group_labels(
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
        + r")(?![0-9A-Za-z'])\s*[（(]\s*"
        + _FIGURE_REFERENCE_CUE_EXPRESSION
        + r"\s*圖\s*"
        + _FIGURE_IDENTIFIER_EXPRESSION
        + r"(?:\s*所示)?[^()（）\r\n]{0,40}[）)]"
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


def _paragraph_group_labels(
    paragraphs: Sequence[PatentParagraph],
    symbol_names_by_label: Dict[str, Sequence[str]],
    *,
    text_fragments: Optional[Sequence[str]] = None,
) -> Tuple[List[str], List[str]]:
    """Extract only symbol-list labels that follow their component names."""

    component_patterns: List[Tuple[str, re.Pattern[str]]] = []
    for label in sorted(symbol_names_by_label, key=len, reverse=True):
        names = [
            unicodedata.normalize("NFKC", str(name or "")).strip()
            for name in symbol_names_by_label[label]
        ]
        names = sorted({name for name in names if name}, key=len, reverse=True)
        if not names:
            continue
        name_expression = "|".join(re.escape(name) for name in names)
        component_patterns.append(
            (
                label,
                re.compile(
                    r"(?:"
                    + name_expression
                    + r")"
                    # Parenthetical aliases such as LED may sit between the
                    # component name and the component label.
                    r"(?:\s*[\(\uff08][^()\uff08\uff09\r\n]{1,40}[\)\uff09])?"
                    r"\s*(?P<label>"
                    + re.escape(label)
                    + r")(?![0-9A-Za-z'])"
                ),
            )
        )
    if not component_patterns:
        return [], []

    supplemental_figure_note = re.compile(
        r"^\s*[\(\uff08][^()\uff08\uff09\r\n]{0,40}"
        + _FIGURE_REFERENCE_CUE_EXPRESSION
        + r"\s*\u5716\s*"
        + _FIGURE_IDENTIFIER_EXPRESSION
        + r"(?:\s*\u6240\u793a)?[^()\uff08\uff09\r\n]{0,40}[\)\uff09]"
    )
    found: List[str] = []
    supplemental: List[str] = []
    sources = (
        list(text_fragments)
        if text_fragments is not None
        else [paragraph.text for paragraph in paragraphs]
    )
    for source in sources:
        text = unicodedata.normalize("NFKC", source).replace("\u2019", "'")
        text = _VISIBLE_PARAGRAPH_NUMBER.sub("", text)
        for label, pattern in component_patterns:
            for match in pattern.finditer(text):
                following = text[match.end("label"):match.end("label") + 90]
                if supplemental_figure_note.match(following):
                    supplemental.append(label)
                else:
                    found.append(label)
    return _unique_labels(found), _unique_labels(supplemental)


def _supplemental_figure_bindings(
    paragraphs: Sequence[PatentParagraph],
    symbol_names_by_label: Dict[str, Sequence[str]],
) -> List[SupplementalFigureBinding]:
    """Bind any symbol-list component to figures named in its following note.

    This intentionally uses the shared figure-reference cue vocabulary rather
    than one fixed phrase, so forms such as ``（見圖7）``, ``（如圖7所示）``,
    ``（參閱圖7）`` and ``（參考圖7）`` behave identically.
    """

    patterns: List[Tuple[str, re.Pattern[str]]] = []
    for label in sorted(symbol_names_by_label, key=len, reverse=True):
        names = sorted(
            {
                unicodedata.normalize("NFKC", str(name or "")).strip()
                for name in symbol_names_by_label[label]
                if str(name or "").strip()
            },
            key=len,
            reverse=True,
        )
        if not names:
            continue
        patterns.append(
            (
                label,
                re.compile(
                    r"(?:"
                    + "|".join(re.escape(name) for name in names)
                    + r")\s*"
                    r"(?P<label>"
                    + re.escape(label)
                    + r")(?![0-9A-Za-z'])\s*"
                    r"(?P<note>[（(]\s*[^()（）\r\n]{0,40}?"
                    + _FIGURE_REFERENCE_CUE_EXPRESSION
                    + r"\s*(?P<clause>"
                    + _FIGURE_CLAUSE_EXPRESSION
                    + r")\s*(?:所示)?[^()（）\r\n]{0,40}?[）)])",
                    re.IGNORECASE,
                ),
            )
        )

    bindings: List[SupplementalFigureBinding] = []
    for paragraph in paragraphs:
        text = unicodedata.normalize("NFKC", paragraph.text).replace("′", "'")
        for label, pattern in patterns:
            for match in pattern.finditer(text):
                figures = _figure_identifiers_from_clause(match.group("clause"))
                if not figures:
                    continue
                bindings.append(
                    SupplementalFigureBinding(
                        label=label,
                        figures=figures,
                        paragraph=paragraph,
                        start=match.start("label"),
                        end=match.end("note"),
                        text=text[match.start("label"):match.end("note")],
                    )
                )
    return bindings


def _reference_scopes_for_group(
    group: Sequence[PatentParagraph],
    inherited_figures: Optional[Sequence[FigureIdentifier]],
) -> Tuple[List[_ReferenceScope], Optional[List[FigureIdentifier]]]:
    """Split one logical paragraph whenever its active drawing changes.

    Ordinary references replace the active figure set.  Additive example
    references such as「例如為圖5」extend the inherited/leading figures, so
    the following text remains bound to both the original and example figures.
    """

    active = list(inherited_figures or [])
    scopes: List[_ReferenceScope] = []
    current: Optional[_ReferenceScope] = None

    def append_fragment(fragment: str, *, inherited: bool) -> None:
        nonlocal current
        if not fragment.strip() or not active:
            return
        if current is None or current.figures != active:
            current = _ReferenceScope(
                figures=list(active),
                inherited_reference=inherited,
            )
            scopes.append(current)
        current.fragments.append(fragment)

    for paragraph in group:
        source = paragraph.text or ""
        references = parse_figure_references(source)
        cursor = 0
        for reference in references:
            append_fragment(source[cursor:reference.start], inherited=True)
            if reference.additive:
                active = _ordered_unique_figures(
                    [*active, *reference.figures]
                )
            else:
                active = list(reference.figures)
            current = _ReferenceScope(
                figures=list(active),
                inherited_reference=False,
                reference=reference,
                reference_paragraph=paragraph,
                additive_reference=reference.additive,
            )
            scopes.append(current)
            cursor = reference.end
        append_fragment(source[cursor:], inherited=(not references))

    if not scopes:
        scopes.append(
            _ReferenceScope(
                figures=list(active),
                fragments=[paragraph.text for paragraph in group],
                inherited_reference=True,
            )
        )
    return scopes, (list(active) if active else None)


def _append_scope_comparison(
    comparisons: List[EmbodimentFigureComparison],
    comparison: EmbodimentFigureComparison,
    *,
    additive_reference: bool,
) -> None:
    """Keep additive sub-scopes accurate internally but show one group row."""

    if (
        not additive_reference
        or not comparisons
        or comparisons[-1].group_index != comparison.group_index
    ):
        comparisons.append(comparison)
        return

    base = comparisons[-1]
    base.referenced_figures = _ordered_unique_figures(
        [*base.referenced_figures, *comparison.referenced_figures]
    )
    base.paragraph_labels = _unique_labels(
        [*base.paragraph_labels, *comparison.paragraph_labels]
    )
    base.drawing_labels = _unique_labels(
        [*base.drawing_labels, *comparison.drawing_labels]
    )
    base.relevant_page_indices = list(
        dict.fromkeys(
            [*base.relevant_page_indices, *comparison.relevant_page_indices]
        )
    )
    # Preserve per-scope mismatches while presenting their union in the one
    # logical Word paragraph row.  A label before「例如為圖5」must not be
    # rescued merely because it happens to appear only in figure 5.
    base.labels_not_in_drawings = _unique_labels(
        [
            *base.labels_not_in_drawings,
            *comparison.labels_not_in_drawings,
        ]
    )
    base.labels_not_in_paragraph = _unique_labels(
        [
            *base.labels_not_in_paragraph,
            *comparison.labels_not_in_paragraph,
        ]
    )
    base.missing_figures = _ordered_unique_figures(
        [*base.missing_figures, *comparison.missing_figures]
    )
    base.inherited_reference = (
        base.inherited_reference and comparison.inherited_reference
    )
    if base.reference is None:
        base.reference = comparison.reference
        base.reference_paragraph = comparison.reference_paragraph
    base.scope_text = "".join(
        part for part in (base.scope_text, comparison.scope_text) if part
    )
    if comparison.comparison_mode != "exact":
        base.comparison_mode = comparison.comparison_mode


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
    symbol_names_by_label: Dict[str, List[str]] = {}
    for entry in extract_document_symbols(document).full_entries:
        normalized_labels = _unique_labels([entry.label])
        if not normalized_labels:
            continue
        names = symbol_names_by_label.setdefault(normalized_labels[0], [])
        name = unicodedata.normalize("NFKC", str(entry.name or "")).strip()
        if name and name not in names:
            names.append(name)

    comparisons: List[EmbodimentFigureComparison] = []
    active_figures: Optional[List[FigureIdentifier]] = None
    for group_index, group in enumerate(_logical_embodiment_groups(document), start=1):
        supplemental_bindings = _supplemental_figure_bindings(
            group,
            symbol_names_by_label,
        )
        active_bound_labels = set()
        scopes, active_figures = _reference_scopes_for_group(
            group,
            active_figures,
        )
        for scope_index, scope in enumerate(scopes):
            normalized_scope_text = unicodedata.normalize(
                "NFKC",
                "".join(scope.fragments),
            ).replace("′", "'")
            labels_to_remove = set(active_bound_labels)
            for binding in supplemental_bindings:
                binding_position = normalized_scope_text.find(binding.text)
                if binding_position < 0:
                    continue
                names = sorted(
                    symbol_names_by_label.get(binding.label, ()),
                    key=len,
                    reverse=True,
                )
                prior_component = False
                if names:
                    prior_component = re.search(
                        r"(?:"
                        + "|".join(re.escape(name) for name in names)
                        + r")\s*"
                        + re.escape(binding.label)
                        + r"(?![0-9A-Za-z'])",
                        normalized_scope_text[:binding_position],
                    ) is not None
                # If this is the first occurrence, move the label entirely to
                # its bound drawing.  If the same component appeared earlier
                # in this scope, keep the ordinary comparison for that earlier
                # occurrence and additionally create the bound comparison.
                if not prior_component:
                    labels_to_remove.add(binding.label)
                active_bound_labels.add(binding.label)
            if not scope.figures:
                _append_scope_comparison(
                    comparisons,
                    EmbodimentFigureComparison(
                        group_index=group_index,
                        paragraphs=tuple(group),
                        inherited_reference=scope.inherited_reference,
                        reference=scope.reference,
                        reference_paragraph=scope.reference_paragraph,
                        scope_text="".join(scope.fragments),
                    ),
                    additive_reference=scope.additive_reference,
                )
                continue

            paragraph_labels, supplemental_labels = _paragraph_group_labels(
                group,
                symbol_names_by_label,
                text_fragments=scope.fragments,
            )
            # A numbered embodiment paragraph may begin with ordinary prose
            # (for example「步驟E……」or「在本實施例中……」) and only then
            # introduce its figure reference.  When the prefix contains no
            # component label, presenting it as a separate row inherited from
            # the previous paragraph makes the UI appear to have missed the
            # mid-paragraph reference.  Keep meaningful inherited prefixes,
            # but omit this empty display-only scope.
            has_later_explicit_reference = any(
                later.reference is not None
                for later in scopes[scope_index + 1 :]
            )
            if (
                scope.inherited_reference
                and has_later_explicit_reference
                and not paragraph_labels
                and not supplemental_labels
            ):
                continue
            active_figure_set = set(scope.figures)
            relevant_pages = [
                page
                for page in figure_pages
                if active_figure_set.intersection(page.figures)
            ]
            drawing_labels = _unique_labels(
                label for page in relevant_pages for label in page.labels
            )
            # ``元件3（見圖1／如圖1／參閱圖1…）」explicitly tells the
            # reader that label 3 belongs to another figure.  Exclude it from
            # the active-figure comparison when it is absent there, but keep it
            # as a normal match if the active figure also contains the label.
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
            # Once a component is explicitly assigned to another drawing by a
            # parenthetical note in this numbered embodiment paragraph, keep
            # that component out of the paragraph's ordinary active-figure
            # comparison.  It is checked in a dedicated comparison below.
            paragraph_labels = [
                label for label in paragraph_labels if label not in labels_to_remove
            ]
            missing_figures = [
                figure
                for figure in scope.figures
                if not any(figure in page.figures for page in figure_pages)
            ]
            exact_comparison = all(
                set(page.figures).issubset(active_figure_set)
                for page in relevant_pages
            )
            drawing_set = set(drawing_labels)
            labels_not_in_drawings = [
                label for label in paragraph_labels if label not in drawing_set
            ]
            # The checker is intentionally one-way: labels present only in a
            # drawing can be harmless contextual content, so they are not
            # treated as a document error.
            labels_not_in_paragraph: List[str] = []
            _append_scope_comparison(
                comparisons,
                EmbodimentFigureComparison(
                    group_index=group_index,
                    paragraphs=tuple(group),
                    referenced_figures=list(scope.figures),
                    paragraph_labels=paragraph_labels,
                    drawing_labels=drawing_labels,
                    relevant_page_indices=[page.page_index for page in relevant_pages],
                    labels_not_in_drawings=labels_not_in_drawings,
                    labels_not_in_paragraph=labels_not_in_paragraph,
                    missing_figures=missing_figures,
                    inherited_reference=scope.inherited_reference,
                    comparison_mode=("exact" if exact_comparison else "page_subset"),
                    reference=scope.reference,
                    reference_paragraph=scope.reference_paragraph,
                    scope_text="".join(scope.fragments),
                ),
                additive_reference=scope.additive_reference,
            )

        bindings_by_figures: Dict[
            Tuple[FigureIdentifier, ...], List[SupplementalFigureBinding]
        ] = {}
        for binding in supplemental_bindings:
            bindings_by_figures.setdefault(binding.figures, []).append(binding)
        for figures, bindings in bindings_by_figures.items():
            active_figure_set = set(figures)
            relevant_pages = [
                page
                for page in figure_pages
                if active_figure_set.intersection(page.figures)
            ]
            # Preserve the prior exemption behavior if the specifically noted
            # drawing has not been OCR-loaded yet.  Once it is loaded, the
            # component is checked against that drawing like any other label.
            if not relevant_pages:
                continue
            paragraph_labels = _unique_labels(binding.label for binding in bindings)
            drawing_labels = _unique_labels(
                label for page in relevant_pages for label in page.labels
            )
            drawing_set = set(drawing_labels)
            first_binding = bindings[0]
            reference = FigureReference(
                figures=figures,
                start=first_binding.start,
                end=first_binding.end,
                text=first_binding.text,
            )
            comparisons.append(
                EmbodimentFigureComparison(
                    group_index=group_index,
                    paragraphs=tuple(group),
                    referenced_figures=list(figures),
                    paragraph_labels=paragraph_labels,
                    drawing_labels=drawing_labels,
                    relevant_page_indices=[page.page_index for page in relevant_pages],
                    labels_not_in_drawings=[
                        label
                        for label in paragraph_labels
                        if label not in drawing_set
                    ],
                    labels_not_in_paragraph=[],
                    missing_figures=[
                        figure
                        for figure in figures
                        if not any(figure in page.figures for page in figure_pages)
                    ],
                    inherited_reference=False,
                    comparison_mode=(
                        "exact"
                        if all(
                            set(page.figures).issubset(active_figure_set)
                            for page in relevant_pages
                        )
                        else "page_subset"
                    ),
                    reference=reference,
                    reference_paragraph=first_binding.paragraph,
                    scope_text="；".join(binding.text for binding in bindings),
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
            comparison.reference_paragraph or first_text_paragraph,
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
