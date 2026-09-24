"""Extract reviewed symbol lists for handoff from the document page to OCR."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Tuple

from features.patent_ocr.label_parser import normalize_reference_label_text

from .models import PatentDocument, PatentParagraph, PatentTextReview
from .section_parser import split_section_heading


FULL_SYMBOL_SOURCE = "full"
REPRESENTATIVE_SYMBOL_SOURCE = "representative"
SECTION_FOR_SOURCE = {
    FULL_SYMBOL_SOURCE: "drawing_symbol_description",
    REPRESENTATIVE_SYMBOL_SOURCE: "representative_drawing_symbols",
}
SOURCE_TITLES = {
    FULL_SYMBOL_SOURCE: "完整符號說明",
    REPRESENTATIVE_SYMBOL_SOURCE: "代表圖符號說明",
}
MAX_NUMERIC_RANGE_SIZE = 100
_SYMBOL_LINE = re.compile(
    r"^\s*[（(]?(?P<symbols>[^:：.．\r\n]+?)[）)]?"
    r"\s*[:：.．]\s*(?P<name>\S(?:.*\S)?)\s*$"
)
_SYMBOL_SPACE_LINE = re.compile(
    r"^\s*[（(]?(?P<symbols>[^\s:：.．,，、~～\-()（）]+)[）)]?"
    r"\s+(?P<name>\S(?:.*\S)?)\s*$"
)
_NUMERIC_RANGE = re.compile(r"^(?P<start>\d+)\s*(?:-|~|～|至)\s*(?P<end>\d+)$")
_PREFIX_NUMERIC_RANGE = re.compile(
    r"^(?P<prefix>[A-Za-z]+)(?P<start>\d+)\s*(?:-|~|～|至)\s*"
    r"(?:(?P<end_prefix>[A-Za-z]+))?(?P<end>\d+)$"
)
_REPRESENTATIVE_FIGURE = re.compile(
    r"圖\s*0*(?P<number>[1-9]\d*)(?P<suffix>[A-Za-z]?)",
    re.IGNORECASE,
)
_NON_SYMBOL_SEPARATORS = {
    "新型專利說明書",
    "發明專利說明書",
    "新型摘要",
    "發明摘要",
}


def _match_symbol_line(text: str):
    return _SYMBOL_LINE.fullmatch(text) or _SYMBOL_SPACE_LINE.fullmatch(text)


@dataclass(frozen=True)
class PatentSymbolEntry:
    """One OCR-compatible label extracted from a document symbol section."""

    label: str
    name: str
    raw_symbol: str
    symbol_source: str
    paragraph_index: int
    source_path: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class SymbolTransferWarning:
    code: str
    severity: str
    message: str
    blocking: bool
    symbol_source: str = ""
    section_key: str = ""
    paragraph_index: Optional[int] = None
    source_path: str = ""
    char_start: Optional[int] = None
    char_end: Optional[int] = None


@dataclass(frozen=True)
class TableSymbolCandidate:
    """One symbol/name pair reconstructed from adjacent Word table cells."""

    section_key: str
    expression: str
    name: str
    symbol_paragraph: PatentParagraph
    name_paragraph: PatentParagraph
    char_start: int
    char_end: int
    consumed_paragraph_indices: Tuple[int, ...]


@dataclass
class DocumentSymbolTransfer:
    """Two independent lists published by the future document review page."""

    source_path: str
    file_name: str
    source_sha256: str
    patent_title: str
    representative_figure_number: str = ""
    full_entries: List[PatentSymbolEntry] = field(default_factory=list)
    representative_entries: List[PatentSymbolEntry] = field(default_factory=list)
    warnings: List[SymbolTransferWarning] = field(default_factory=list)
    source_review_issue_ids: Dict[str, List[str]] = field(default_factory=dict)
    manual_override_sources: List[str] = field(default_factory=list)
    generated_at_utc: str = ""
    schema_version: str = "3.2"

    @property
    def entries(self) -> List[PatentSymbolEntry]:
        """Backward-compatible default: the complete symbol-description list."""

        return self.full_entries

    def entries_for(self, symbol_source: str) -> List[PatentSymbolEntry]:
        if symbol_source == FULL_SYMBOL_SOURCE:
            return self.full_entries
        if symbol_source == REPRESENTATIVE_SYMBOL_SOURCE:
            return self.representative_entries
        raise ValueError(f"Unknown symbol source: {symbol_source}")

    def is_source_ready(self, symbol_source: str) -> bool:
        entries = self.entries_for(symbol_source)
        section_key = SECTION_FOR_SOURCE[symbol_source]
        return (
            bool(entries)
            and not self.source_review_issue_ids.get(symbol_source, [])
            and not any(
                warning.blocking and warning.section_key == section_key
                for warning in self.warnings
            )
        )

    @property
    def ready_for_ocr(self) -> bool:
        """Default readiness remains tied to the complete symbol list."""

        return self.is_source_ready(FULL_SYMBOL_SOURCE)

    @property
    def available_sources(self) -> List[str]:
        return [
            source
            for source in (FULL_SYMBOL_SOURCE, REPRESENTATIVE_SYMBOL_SOURCE)
            if self.entries_for(source)
        ]

    def reference_items_for(self, symbol_source: str) -> List[Dict[str, str]]:
        return [
            {"number": entry.label, "name": entry.name}
            for entry in self.entries_for(symbol_source)
        ]

    def reference_text_for(self, symbol_source: str) -> str:
        return "\n".join(
            f"{entry.label}:{entry.name}" if entry.name else entry.label
            for entry in self.entries_for(symbol_source)
        )

    @property
    def reference_items(self) -> List[Dict[str, str]]:
        return self.reference_items_for(FULL_SYMBOL_SOURCE)

    @property
    def reference_text(self) -> str:
        return self.reference_text_for(FULL_SYMBOL_SOURCE)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["ready_for_ocr"] = self.ready_for_ocr
        payload["source_status"] = {
            source: {
                "title": SOURCE_TITLES[source],
                "ready_for_ocr": self.is_source_ready(source),
                "reference_items": self.reference_items_for(source),
                "reference_text": self.reference_text_for(source),
            }
            for source in (FULL_SYMBOL_SOURCE, REPRESENTATIVE_SYMBOL_SOURCE)
        }
        return payload


def _line_spans(text: str, base_offset: int) -> Iterable[Tuple[str, int, int]]:
    offset = 0
    for part in text.splitlines(keepends=True) or ([text] if text else []):
        line = part.rstrip("\r\n")
        left = len(line) - len(line.lstrip())
        right = len(line.rstrip())
        if right > left:
            yield line[left:right], base_offset + offset + left, base_offset + offset + right
        offset += len(part)


def _symbol_content(paragraph: PatentParagraph) -> Tuple[str, int]:
    if not paragraph.is_heading:
        return paragraph.text, 0
    heading = split_section_heading(paragraph.text)
    if heading is None or not heading.remainder:
        return "", 0
    start = paragraph.text.rfind(heading.remainder)
    return heading.remainder, max(0, start)


def _normalize_direct_label(raw_token: str) -> Optional[str]:
    compact = re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", raw_token),
    )
    return normalize_reference_label_text(compact) or None


def _expand_expression(expression: str) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    expanded: List[Tuple[str, str]] = []
    tokens = [token.strip() for token in re.split(r"[,，、]", expression)]
    if not tokens or any(not token for token in tokens):
        return [], "符號群組含有空白項目。"
    for token in tokens:
        normalized_token = unicodedata.normalize("NFKC", token)
        numeric_range = _NUMERIC_RANGE.fullmatch(normalized_token)
        if numeric_range:
            start = int(numeric_range.group("start"))
            end = int(numeric_range.group("end"))
            if end < start:
                return [], f"數字區間「{token}」為反向範圍，程式不會自行猜測。"
            if end - start + 1 > MAX_NUMERIC_RANGE_SIZE:
                return [], f"數字區間「{token}」超過 {MAX_NUMERIC_RANGE_SIZE} 個標號。"
            expanded.extend((str(number), token) for number in range(start, end + 1))
            continue
        prefix_range = _PREFIX_NUMERIC_RANGE.fullmatch(normalized_token)
        if prefix_range:
            prefix = prefix_range.group("prefix")
            end_prefix = prefix_range.group("end_prefix") or prefix
            start_digits = prefix_range.group("start")
            end_digits = prefix_range.group("end")
            start = int(start_digits)
            end = int(end_digits)
            if end_prefix != prefix:
                return [], f"英數區間「{token}」的前綴不一致，程式不會自行猜測。"
            if end < start:
                return [], f"英數區間「{token}」為反向範圍，程式不會自行猜測。"
            if end - start + 1 > MAX_NUMERIC_RANGE_SIZE:
                return [], f"英數區間「{token}」超過 {MAX_NUMERIC_RANGE_SIZE} 個標號。"
            preserve_width = start_digits.startswith("0") or end_digits.startswith("0")
            width = max(len(start_digits), len(end_digits)) if preserve_width else 0
            expanded.extend(
                (
                    f"{prefix}{str(number).zfill(width) if width else number}",
                    token,
                )
                for number in range(start, end + 1)
            )
            continue
        if any(separator in token for separator in ("-", "~", "～", "至")):
            return [], f"英數混合區間「{token}」具有歧義，請改成逐項列出。"
        label = _normalize_direct_label(token)
        if label is None:
            return [], f"「{token}」不是 OCR 支援的標號格式。"
        expanded.append((label, token))
    return expanded, None


_TABLE_SYMBOL_HEADERS = {"符號", "標號", "圖號", "元件標號"}
_TABLE_NAME_HEADERS = {"名稱", "元件名稱", "符號名稱", "說明"}


def _table_symbol_candidates(
    document: PatentDocument,
    section_keys: Iterable[str] = tuple(SECTION_FOR_SOURCE.values()),
) -> Tuple[List[TableSymbolCandidate], set[int]]:
    """Reconstruct symbol pairs from adjacent cells without flattening rows."""

    wanted_sections = set(section_keys)
    cells: Dict[
        Tuple[int, int, int],
        List[PatentParagraph],
    ] = {}
    for paragraph in document.paragraphs:
        if (
            paragraph.source_kind != "table"
            or paragraph.table_index is None
            or paragraph.row_index is None
            or paragraph.cell_index is None
            or paragraph.section_key not in wanted_sections
        ):
            continue
        cells.setdefault(
            (
                paragraph.table_index,
                paragraph.row_index,
                paragraph.cell_index,
            ),
            [],
        ).append(paragraph)

    rows: Dict[Tuple[int, int], List[Tuple[int, List[PatentParagraph]]]] = {}
    for (table_index, row_index, cell_index), paragraphs in cells.items():
        rows.setdefault((table_index, row_index), []).append(
            (cell_index, paragraphs)
        )

    candidates: List[TableSymbolCandidate] = []
    consumed: set[int] = set()
    for _row_key, row_cells in sorted(rows.items()):
        ordered = sorted(row_cells, key=lambda item: item[0])
        prepared: List[Tuple[str, List[PatentParagraph]]] = []
        for _cell_index, paragraphs in ordered:
            nonempty = [item for item in paragraphs if item.text.strip()]
            if not nonempty:
                continue
            prepared.append(
                (
                    " ".join(item.text.strip() for item in nonempty),
                    nonempty,
                )
            )
        if len(prepared) < 2:
            continue

        first_key = re.sub(r"\s+", "", prepared[0][0]).strip("：:.．")
        second_key = re.sub(r"\s+", "", prepared[1][0]).strip("：:.．")
        if first_key in _TABLE_SYMBOL_HEADERS and second_key in _TABLE_NAME_HEADERS:
            consumed.update(
                paragraph.index
                for _text, paragraphs in prepared[:2]
                for paragraph in paragraphs
            )
            continue

        index = 0
        while index + 1 < len(prepared):
            expression_text, symbol_paragraphs = prepared[index]
            name, name_paragraphs = prepared[index + 1]
            expression = expression_text.strip(" \t（()）：:.．")
            if _match_symbol_line(expression_text) is not None:
                index += 1
                continue
            expanded, error = _expand_expression(expression)
            if error or not expanded or not name.strip():
                index += 1
                continue
            # A leading row-number column followed by a symbol column should
            # pair the latter with the name cell, not pair two labels together.
            next_expression = name.strip(" \t（()）：:.．")
            next_expanded, next_error = _expand_expression(next_expression)
            if next_error is None and next_expanded:
                consumed.update(
                    paragraph.index for paragraph in symbol_paragraphs
                )
                index += 1
                continue
            symbol_paragraph = symbol_paragraphs[0]
            name_paragraph = name_paragraphs[0]
            pair_indices = tuple(
                paragraph.index
                for paragraph in symbol_paragraphs + name_paragraphs
            )
            consumed.update(pair_indices)
            candidates.append(
                TableSymbolCandidate(
                    section_key=symbol_paragraph.section_key or "",
                    expression=expression,
                    name=name.strip(),
                    symbol_paragraph=symbol_paragraph,
                    name_paragraph=name_paragraph,
                    char_start=0,
                    char_end=len(symbol_paragraph.text),
                    consumed_paragraph_indices=pair_indices,
                )
            )
            index += 2
    return candidates, consumed


def extract_document_symbols(
    document: PatentDocument,
    review: Optional[PatentTextReview] = None,
) -> DocumentSymbolTransfer:
    """Extract complete and representative symbol lists without editing DOCX."""

    representative_figures: List[str] = []
    for paragraph in document.paragraphs:
        if paragraph.section_key != "designated_representative_drawing":
            continue
        for match in _REPRESENTATIVE_FIGURE.finditer(paragraph.text):
            number = str(int(match.group("number")))
            suffix = (match.group("suffix") or "").upper()
            identifier = f"{number}{suffix}"
            if identifier not in representative_figures:
                representative_figures.append(identifier)
    # The document rule engine separately reports an ambiguous/missing
    # designation. Only publish a jump target when the source contains one
    # unambiguous representative figure.
    representative_figure_number = (
        representative_figures[0] if len(representative_figures) == 1 else ""
    )

    entries_by_source: Dict[str, List[PatentSymbolEntry]] = {
        source: [] for source in SECTION_FOR_SOURCE
    }
    seen_by_source: Dict[str, Dict[str, PatentSymbolEntry]] = {
        source: {} for source in SECTION_FOR_SOURCE
    }
    warnings: List[SymbolTransferWarning] = []
    found_sections = {section.key for section in document.sections}
    source_for_section = {
        section_key: source for source, section_key in SECTION_FOR_SOURCE.items()
    }
    table_candidates, consumed_table_paragraphs = _table_symbol_candidates(
        document
    )

    for paragraph in document.paragraphs:
        symbol_source = source_for_section.get(paragraph.section_key or "")
        if symbol_source is None:
            continue
        if paragraph.index in consumed_table_paragraphs:
            continue
        if (
            paragraph.section_key == "drawing_symbol_description"
            and paragraph.numbering_value is not None
        ):
            # The formal company template uses one numbered introductory
            # paragraph before the unnumbered symbol entries.
            continue
        section_key = SECTION_FOR_SOURCE[symbol_source]
        content, base_offset = _symbol_content(paragraph)
        for line, line_start, line_end in _line_spans(content, base_offset):
            if line.strip() in _NON_SYMBOL_SEPARATORS:
                continue
            match = _match_symbol_line(line)
            if match is None:
                warnings.append(SymbolTransferWarning(
                    code="SYMBOL_LINE_UNPARSED",
                    severity="warning",
                    message=f"無法自動匯入{SOURCE_TITLES[symbol_source]}：「{line}」。",
                    blocking=True,
                    symbol_source=symbol_source,
                    section_key=section_key,
                    paragraph_index=paragraph.index,
                    source_path=paragraph.source_path,
                    char_start=line_start,
                    char_end=line_end,
                ))
                continue

            expression = match.group("symbols")
            name = match.group("name").strip()
            expanded, error = _expand_expression(expression)
            symbol_start = line_start + match.start("symbols")
            symbol_end = line_start + match.end("symbols")
            if error:
                warnings.append(SymbolTransferWarning(
                    code="SYMBOL_EXPRESSION_AMBIGUOUS",
                    severity="warning",
                    message=error,
                    blocking=True,
                    symbol_source=symbol_source,
                    section_key=section_key,
                    paragraph_index=paragraph.index,
                    source_path=paragraph.source_path,
                    char_start=symbol_start,
                    char_end=symbol_end,
                ))
                continue

            for label, raw_symbol in expanded:
                entry = PatentSymbolEntry(
                    label=label,
                    name=name,
                    raw_symbol=raw_symbol,
                    symbol_source=symbol_source,
                    paragraph_index=paragraph.index,
                    source_path=paragraph.source_path,
                    char_start=symbol_start,
                    char_end=symbol_end,
                )
                previous = seen_by_source[symbol_source].get(label)
                if previous is None:
                    seen_by_source[symbol_source][label] = entry
                    entries_by_source[symbol_source].append(entry)
                elif previous.name != name:
                    warnings.append(SymbolTransferWarning(
                        code="SYMBOL_NAME_CONFLICT",
                        severity="error",
                        message=(
                            f"{SOURCE_TITLES[symbol_source]}的標號「{label}」同時對應"
                            f"「{previous.name}」與「{name}」，自動交接前必須人工確認。"
                        ),
                        blocking=True,
                        symbol_source=symbol_source,
                        section_key=section_key,
                        paragraph_index=paragraph.index,
                        source_path=paragraph.source_path,
                        char_start=symbol_start,
                        char_end=symbol_end,
                    ))

    for candidate in table_candidates:
        symbol_source = source_for_section.get(candidate.section_key)
        if symbol_source is None:
            continue
        section_key = SECTION_FOR_SOURCE[symbol_source]
        expanded, error = _expand_expression(candidate.expression)
        paragraph = candidate.symbol_paragraph
        if error:
            warnings.append(SymbolTransferWarning(
                code="SYMBOL_EXPRESSION_AMBIGUOUS",
                severity="warning",
                message=error,
                blocking=True,
                symbol_source=symbol_source,
                section_key=section_key,
                paragraph_index=paragraph.index,
                source_path=paragraph.source_path,
                char_start=candidate.char_start,
                char_end=candidate.char_end,
            ))
            continue
        for label, raw_symbol in expanded:
            entry = PatentSymbolEntry(
                label=label,
                name=candidate.name,
                raw_symbol=raw_symbol,
                symbol_source=symbol_source,
                paragraph_index=paragraph.index,
                source_path=paragraph.source_path,
                char_start=candidate.char_start,
                char_end=candidate.char_end,
            )
            previous = seen_by_source[symbol_source].get(label)
            if previous is None:
                seen_by_source[symbol_source][label] = entry
                entries_by_source[symbol_source].append(entry)
            elif previous.name != candidate.name:
                warnings.append(SymbolTransferWarning(
                    code="SYMBOL_NAME_CONFLICT",
                    severity="error",
                    message=(
                        f"{SOURCE_TITLES[symbol_source]}的標號「{label}」同時對應"
                        f"「{previous.name}」與「{candidate.name}」，自動交接前必須人工確認。"
                    ),
                    blocking=True,
                    symbol_source=symbol_source,
                    section_key=section_key,
                    paragraph_index=paragraph.index,
                    source_path=paragraph.source_path,
                    char_start=candidate.char_start,
                    char_end=candidate.char_end,
                ))

    for entries in entries_by_source.values():
        entries.sort(
            key=lambda entry: (
                entry.paragraph_index,
                entry.char_start,
                entry.char_end,
            )
        )

    for symbol_source, section_key in SECTION_FOR_SOURCE.items():
        if section_key not in found_sections:
            warnings.append(SymbolTransferWarning(
                code="SYMBOL_SECTION_MISSING",
                severity="info" if symbol_source == REPRESENTATIVE_SYMBOL_SOURCE else "error",
                message=f"文件沒有可辨識的「{SOURCE_TITLES[symbol_source]}」章節。",
                blocking=True,
                symbol_source=symbol_source,
                section_key=section_key,
            ))
        elif not entries_by_source[symbol_source]:
            warnings.append(SymbolTransferWarning(
                code="SYMBOL_LIST_EMPTY",
                severity="error",
                message=f"「{SOURCE_TITLES[symbol_source]}」沒有可匯入 OCR 的標號。",
                blocking=True,
                symbol_source=symbol_source,
                section_key=section_key,
            ))

    review_issue_ids: Dict[str, List[str]] = {
        source: [] for source in SECTION_FOR_SOURCE
    }
    if review is not None:
        for issue in review.issues:
            symbol_source = source_for_section.get(issue.section_key)
            if symbol_source is not None and issue.rule_id in {"SYM001", "SYM003"}:
                review_issue_ids[symbol_source].append(issue.issue_id)

    return DocumentSymbolTransfer(
        source_path=document.source_path,
        file_name=document.file_name,
        source_sha256=document.sha256,
        patent_title=document.patent_title,
        representative_figure_number=representative_figure_number,
        full_entries=entries_by_source[FULL_SYMBOL_SOURCE],
        representative_entries=entries_by_source[REPRESENTATIVE_SYMBOL_SOURCE],
        warnings=warnings,
        source_review_issue_ids=review_issue_ids,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def _manual_entries(
    text: str,
    symbol_source: str,
    original_entries: List[PatentSymbolEntry],
) -> Tuple[List[PatentSymbolEntry], List[SymbolTransferWarning]]:
    section_key = SECTION_FOR_SOURCE[symbol_source]
    previous_by_label = {entry.label: entry for entry in original_entries}
    entries: List[PatentSymbolEntry] = []
    warnings: List[SymbolTransferWarning] = []
    seen: Dict[str, PatentSymbolEntry] = {}

    for line, line_start, line_end in _line_spans(text, 0):
        match = _match_symbol_line(line)
        if match is None:
            warnings.append(SymbolTransferWarning(
                code="MANUAL_SYMBOL_LINE_UNPARSED",
                severity="warning",
                message=f"無法辨識手動清單內容：「{line}」。",
                blocking=True,
                symbol_source=symbol_source,
                section_key=section_key,
                source_path=f"manual/{symbol_source}",
                char_start=line_start,
                char_end=line_end,
            ))
            continue

        expression = match.group("symbols")
        name = match.group("name").strip()
        expanded, error = _expand_expression(expression)
        symbol_start = line_start + match.start("symbols")
        symbol_end = line_start + match.end("symbols")
        if error:
            warnings.append(SymbolTransferWarning(
                code="MANUAL_SYMBOL_EXPRESSION_AMBIGUOUS",
                severity="warning",
                message=error,
                blocking=True,
                symbol_source=symbol_source,
                section_key=section_key,
                source_path=f"manual/{symbol_source}",
                char_start=symbol_start,
                char_end=symbol_end,
            ))
            continue

        for label, raw_symbol in expanded:
            original = previous_by_label.get(label)
            entry = PatentSymbolEntry(
                label=label,
                name=name,
                raw_symbol=raw_symbol,
                symbol_source=symbol_source,
                paragraph_index=original.paragraph_index if original else -1,
                source_path=original.source_path if original else f"manual/{symbol_source}",
                char_start=original.char_start if original else symbol_start,
                char_end=original.char_end if original else symbol_end,
            )
            previous = seen.get(label)
            if previous is None:
                seen[label] = entry
                entries.append(entry)
            elif previous.name != name:
                warnings.append(SymbolTransferWarning(
                    code="MANUAL_SYMBOL_NAME_CONFLICT",
                    severity="error",
                    message=f"手動清單的標號「{label}」具有兩個不同名稱。",
                    blocking=True,
                    symbol_source=symbol_source,
                    section_key=section_key,
                    source_path=f"manual/{symbol_source}",
                    char_start=symbol_start,
                    char_end=symbol_end,
                ))

    if not entries:
        warnings.append(SymbolTransferWarning(
            code="MANUAL_SYMBOL_LIST_EMPTY",
            severity="error" if symbol_source == FULL_SYMBOL_SOURCE else "info",
            message=f"「{SOURCE_TITLES[symbol_source]}」目前沒有可送往 OCR 的標號。",
            blocking=True,
            symbol_source=symbol_source,
            section_key=section_key,
            source_path=f"manual/{symbol_source}",
        ))
    return entries, warnings


def rebuild_transfer_from_reference_texts(
    transfer: DocumentSymbolTransfer,
    full_text: str,
    representative_text: str,
) -> DocumentSymbolTransfer:
    """Create an audited handoff payload from explicitly confirmed GUI drafts."""

    full_entries, full_warnings = _manual_entries(
        full_text,
        FULL_SYMBOL_SOURCE,
        transfer.full_entries,
    )
    representative_entries, representative_warnings = _manual_entries(
        representative_text,
        REPRESENTATIVE_SYMBOL_SOURCE,
        transfer.representative_entries,
    )
    return DocumentSymbolTransfer(
        source_path=transfer.source_path,
        file_name=transfer.file_name,
        source_sha256=transfer.source_sha256,
        patent_title=transfer.patent_title,
        representative_figure_number=transfer.representative_figure_number,
        full_entries=full_entries,
        representative_entries=representative_entries,
        warnings=full_warnings + representative_warnings,
        source_review_issue_ids={
            FULL_SYMBOL_SOURCE: [],
            REPRESENTATIVE_SYMBOL_SOURCE: [],
        },
        manual_override_sources=[
            FULL_SYMBOL_SOURCE,
            REPRESENTATIVE_SYMBOL_SOURCE,
        ],
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
    )
