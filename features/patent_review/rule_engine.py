"""Deterministic Stage 2 text checks with source-character localization."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .models import (
    PatentDocument,
    PatentIssue,
    PatentParagraph,
    PatentTextReview,
    RuleDefinition,
)


RULE_CATALOG: Tuple[RuleDefinition, ...] = (
    RuleDefinition(
        "STR001",
        "缺少必要章節",
        "structure",
        "error",
        "依專利類型確認名稱、摘要、說明書主要章節及申請專利範圍是否存在。",
    ),
    RuleDefinition(
        "STR002",
        "必要章節沒有內容",
        "structure",
        "error",
        "章節標題存在，但標題後沒有可供檢核的文字。",
    ),
    RuleDefinition(
        "STR003",
        "章節重複",
        "structure",
        "warning",
        "標示通常只應出現一次的說明書章節重複出現。",
    ),
    RuleDefinition(
        "PNO001",
        "說明書段落缺少段號",
        "paragraph_numbering",
        "warning",
        "技術領域至實施方式的正文段落應具有可辨識的四位數連續段號。",
    ),
    RuleDefinition(
        "PNO002",
        "說明書段號格式異常",
        "paragraph_numbering",
        "warning",
        "說明書段號應以括號包住四位數字，例如「〖0001〗」。",
    ),
    RuleDefinition(
        "PNO003",
        "說明書段號不連續",
        "paragraph_numbering",
        "warning",
        "說明書正文段號應由0001開始，依文件順序連續編排。",
    ),
    RuleDefinition(
        "ORD001",
        "說明書章節順序異常",
        "structure",
        "warning",
        "說明書主要章節通常應依技術領域、先前技術、發明／新型內容、圖式簡單說明、實施方式及符號說明排列。",
    ),
    RuleDefinition(
        "SYM001",
        "符號說明格式無法辨識",
        "symbols",
        "warning",
        "符號說明應逐行採用「符號:元件名稱」。",
    ),
    RuleDefinition(
        "SYM003",
        "相同符號對應不同名稱",
        "symbols",
        "error",
        "同一符號在同一份符號說明中不應對應互相衝突的元件名稱。",
    ),
    RuleDefinition(
        "SYM004",
        "代表圖符號未列入符號說明",
        "symbols",
        "error",
        "代表圖之符號簡單說明所列符號，應能在完整符號說明中找到。",
    ),
    RuleDefinition(
        "SYM005",
        "代表圖與完整符號說明名稱不同",
        "symbols",
        "warning",
        "相同符號在代表圖與完整符號說明中應使用一致名稱。",
    ),
    RuleDefinition(
        "FIG001",
        "圖式編號不連續",
        "drawings",
        "warning",
        "圖式簡單說明中的圖號應由圖1開始並依序列出。",
    ),
    RuleDefinition(
        "FIG002",
        "指定代表圖未列入圖式簡單說明",
        "drawings",
        "warning",
        "指定代表圖的圖號應可在圖式簡單說明中找到。",
    ),
    RuleDefinition(
        "REF001",
        "內文標號未列入符號說明",
        "references",
        "warning",
        "摘要、說明書內文或申請專利範圍使用的元件標號，應列入完整符號說明。",
    ),
    RuleDefinition(
        "REF002",
        "內文元件名稱與標號不一致",
        "references",
        "warning",
        "已列入完整符號說明的元件名稱，在摘要、內文及申請專利範圍中應搭配相同標號。",
    ),
    RuleDefinition(
        "CLM001",
        "請求項缺少明確編號",
        "claims",
        "warning",
        "請求項段落應以「【請求項N】」或可辨識的數字編號開頭。",
    ),
    RuleDefinition(
        "CLM002",
        "請求項編號不連續",
        "claims",
        "error",
        "請求項應由1開始並依文件順序連續編號。",
    ),
    RuleDefinition(
        "CLM003",
        "請求項依附關係無效",
        "claims",
        "error",
        "附屬項只能引用已存在且編號較小的請求項。",
    ),
    RuleDefinition(
        "CLM004",
        "多項附屬項未以選擇式記載",
        "claims",
        "error",
        "多項附屬項引用二項以上請求項時，應以「任一項」或「或」等選擇式記載。",
    ),
    RuleDefinition(
        "CLM005",
        "多項附屬項依附另一多項附屬項",
        "claims",
        "error",
        "多項附屬項不得直接或間接依附於另一多項附屬項。",
    ),
    RuleDefinition(
        "TXT001",
        "全形英數字",
        "typography",
        "info",
        "標示可安全轉換為半形的全形英文字母或數字。",
    ),
    RuleDefinition(
        "TXT002",
        "非標準 prime mark",
        "typography",
        "info",
        "將相似的 prime／全形撇號統一為半形 apostrophe。",
    ),
)

_RULE_BY_ID = {definition.rule_id: definition for definition in RULE_CATALOG}
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_SYMBOL_SECTIONS = {
    "drawing_symbol_description",
    "representative_drawing_symbols",
}
_NON_SYMBOL_SEPARATORS = {
    "新型專利說明書",
    "發明專利說明書",
    "新型摘要",
    "發明摘要",
}
_SYMBOL_LINE = re.compile(
    r"^\s*(?P<symbol>[0-9A-Za-z]+(?:['′])?(?:\s*[,，~\-]\s*[0-9A-Za-z]+(?:['′])?)*)"
    r"\s*(?P<separator>[:：])\s*(?P<name>\S(?:.*\S)?)\s*$"
)
_CLAIM_PREFIXES = (
    re.compile(r"^\s*[【〖]\s*請求項\s*(?P<number>\d+)\s*[】〗]"),
    re.compile(r"^\s*(?P<number>\d+)\s*[.．、]\s*"),
)
_CLAIM_RANGE = re.compile(
    r"(?:申請專利範圍)?請求項\s*(?P<start>\d+)\s*(?:至|到|~|－|-)\s*(?P<end>\d+)"
)
_CLAIM_LIST = re.compile(
    r"(?:申請專利範圍)?請求項\s*(?P<numbers>\d+(?:\s*(?:、|,|，|及|或)\s*\d+)+)"
)
_CLAIM_SINGLE = re.compile(r"(?:申請專利範圍)?請求項\s*(?P<number>\d+)")
_PARAGRAPH_NUMBER = re.compile(
    r"^\s*(?P<display>[【〖\[]\s*(?P<number>\d+)\s*[】〗\]])"
)
_VALID_PARAGRAPH_NUMBER = re.compile(r"^[【〖\[]\d{4}[】〗\]]$")
_NARRATIVE_SECTIONS = {
    "technical_field",
    "background_art",
    "disclosure",
    "brief_description_of_drawings",
    "embodiments",
}
_DESCRIPTION_SECTION_ORDER = (
    "technical_field",
    "background_art",
    "disclosure",
    "brief_description_of_drawings",
    "embodiments",
    "drawing_symbol_description",
)
_FIGURE_AT_LINE_START = re.compile(
    r"^\s*圖\s*(?P<start>\d+)(?:\s*(?:至|到|~|～|－|-)\s*(?P<end>\d+))?"
)
_FIGURE_REFERENCE = re.compile(r"圖\s*(?P<number>\d+)")
_REFERENCE_SECTIONS = {"abstract_zh", "disclosure", "embodiments", "claims"}
_COMPONENT_REFERENCE = re.compile(
    r"(?P<name>[\u4e00-\u9fff]{1,12})(?P<label>\d{1,3}[A-Za-z]?(?:['′])?)"
)
_COMPONENT_NAME_SUFFIXES = (
    "元件", "模組", "構件", "組件", "本體", "箱體", "殼體", "板", "蓋",
    "件", "片", "孔", "槽", "桿", "軸", "座", "箱", "端", "臂", "部",
)
_FULLWIDTH_ALNUMERIC = re.compile(r"[０-９Ａ-Ｚａ-ｚ]+")
_NONSTANDARD_PRIME = {"′": "'", "＇": "'"}


def _paragraph_by_index(document: PatentDocument) -> Dict[int, PatentParagraph]:
    return {paragraph.index: paragraph for paragraph in document.paragraphs}


def _run_indices(paragraph: PatentParagraph, start: int, end: int) -> List[int]:
    return [
        span.run_index
        for span in paragraph.run_spans
        if span.start < end and span.end > start
    ]


def _issue(
    rule_id: str,
    message: str,
    suggestion: str,
    *,
    paragraph: Optional[PatentParagraph] = None,
    section_key: str = "",
    section_title: str = "",
    start: Optional[int] = None,
    end: Optional[int] = None,
    safe_auto_fix: bool = False,
    replacement: Optional[str] = None,
    details: Optional[Dict[str, object]] = None,
) -> PatentIssue:
    definition = _RULE_BY_ID[rule_id]
    if paragraph is not None:
        section_key = section_key or paragraph.section_key or ""
        section_title = section_title or paragraph.section_title
        if start is not None and end is not None:
            start = max(0, min(start, len(paragraph.text)))
            end = max(start, min(end, len(paragraph.text)))
            matched_text = paragraph.text[start:end]
            runs = _run_indices(paragraph, start, end)
        else:
            matched_text = ""
            runs = []
        paragraph_index = paragraph.index
        source_path = paragraph.source_path
    else:
        matched_text = ""
        runs = []
        paragraph_index = None
        source_path = ""

    identity = "|".join(
        [
            rule_id,
            str(paragraph_index),
            str(start),
            str(end),
            section_key,
            message,
        ]
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return PatentIssue(
        issue_id=f"{rule_id}-{digest}",
        rule_id=rule_id,
        severity=definition.default_severity,
        category=definition.category,
        message=message,
        suggestion=suggestion,
        section_key=section_key,
        section_title=section_title,
        paragraph_index=paragraph_index,
        source_path=source_path,
        char_start=start,
        char_end=end,
        matched_text=matched_text,
        run_indices=runs,
        safe_auto_fix=safe_auto_fix,
        replacement=replacement,
        details=dict(details or {}),
    )


def _required_sections(document: PatentDocument) -> Sequence[Tuple[str, str]]:
    title = (
        ("invention_title", "發明名稱")
        if document.patent_type == "invention"
        else ("utility_model_title", "新型名稱")
    )
    return (
        title,
        ("abstract_zh", "中文摘要"),
        ("technical_field", "技術領域"),
        ("background_art", "先前技術"),
        ("disclosure", "發明／新型內容"),
        ("embodiments", "實施方式"),
        ("claims", "申請專利範圍"),
    )


def _structure_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    paragraph_map = _paragraph_by_index(document)
    sections_by_key: Dict[str, List[object]] = defaultdict(list)
    for section in document.sections:
        sections_by_key[section.key].append(section)

    for key, title in _required_sections(document):
        occurrences = sections_by_key.get(key, [])
        if not occurrences:
            yield _issue(
                "STR001",
                f"找不到必要章節「{title}」。",
                f"請確認文件中是否具有明確的「{title}」標題與內容。",
                section_key=key,
                section_title=title,
                details={"required_section": key},
            )
            continue
        for section in occurrences:
            has_content = any(
                paragraph_map[index].content_text.strip()
                for index in section.paragraph_indices
                if index in paragraph_map
            )
            if not has_content:
                heading = paragraph_map.get(section.heading_paragraph_index)
                yield _issue(
                    "STR002",
                    f"章節「{title}」沒有可檢核的內容。",
                    "請在章節標題後補入內容，或確認標題與內容是否被錯誤分段。",
                    paragraph=heading,
                    section_key=key,
                    section_title=title,
                    start=0 if heading else None,
                    end=len(heading.text) if heading else None,
                )

    unique_keys = {
        "abstract_zh",
        "abstract_en",
        "technical_field",
        "background_art",
        "disclosure",
        "brief_description_of_drawings",
        "drawing_symbol_description",
        "representative_drawing_symbols",
        "embodiments",
        "claims",
    }
    for key in sorted(unique_keys):
        occurrences = sections_by_key.get(key, [])
        for section in occurrences[1:]:
            heading = paragraph_map.get(section.heading_paragraph_index)
            yield _issue(
                "STR003",
                f"章節「{section.title}」重複出現。",
                "請確認這是刻意分段；若不是，請合併內容或移除重複標題。",
                paragraph=heading,
                section_key=key,
                section_title=section.title,
                start=0 if heading else None,
                end=len(heading.text) if heading else None,
            )


def _paragraph_number_issues(
    document: PatentDocument,
) -> Iterable[PatentIssue]:
    """Validate the visible four-digit numbering used by TIPO descriptions."""

    numbered_paragraphs: List[Tuple[PatentParagraph, Optional[int], str]] = []
    for paragraph in document.paragraphs:
        if (
            paragraph.is_heading
            or paragraph.section_key not in _NARRATIVE_SECTIONS
            or not paragraph.content_text.strip()
        ):
            continue

        explicit = _PARAGRAPH_NUMBER.match(paragraph.text)
        if paragraph.numbering_value is not None:
            value = paragraph.numbering_value
            display = paragraph.numbering_text.strip()
        elif explicit is not None:
            value = int(explicit.group("number"))
            display = re.sub(r"\s+", "", explicit.group("display"))
        else:
            value = None
            display = ""

        numbered_paragraphs.append((paragraph, value, display))
        if value is None:
            yield _issue(
                "PNO001",
                f"「{paragraph.section_title}」的正文段落缺少可辨識段號。",
                "請回到 Word 將此段設為說明書的四位數連續編號清單。",
                paragraph=paragraph,
                start=0,
                end=min(len(paragraph.text), 36),
            )
            continue
        if not _VALID_PARAGRAPH_NUMBER.fullmatch(display):
            yield _issue(
                "PNO002",
                f"段號顯示為「{display or value}」，不是括號內四位數格式。",
                f"請確認 Word 顯示的段號是否應為「〖{value:04d}〗」。",
                paragraph=paragraph,
                start=explicit.start("display") if explicit else 0,
                end=(
                    explicit.end("display")
                    if explicit
                    else min(len(paragraph.text), 36)
                ),
                details={"actual_display": display, "numbering_value": value},
            )

    for expected, (paragraph, value, display) in enumerate(
        numbered_paragraphs, start=1
    ):
        if value is None or value == expected:
            continue
        explicit = _PARAGRAPH_NUMBER.match(paragraph.text)
        yield _issue(
            "PNO003",
            f"此段為「{display or value}」，依文件順序預期應為「〖{expected:04d}〗」。",
            "請檢查前後段落的 Word 自動編號，並由0001起連續編排。",
            paragraph=paragraph,
            start=explicit.start("display") if explicit else 0,
            end=(
                explicit.end("display")
                if explicit
                else min(len(paragraph.text), 36)
            ),
            details={"expected_number": expected, "actual_number": value},
        )


def _section_order_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    order = {key: position for position, key in enumerate(_DESCRIPTION_SECTION_ORDER)}
    first_sections = {}
    for section in document.sections:
        if section.key in order and section.key not in first_sections:
            first_sections[section.key] = section
    present = sorted(
        first_sections.values(), key=lambda section: section.heading_paragraph_index
    )
    previous_position = -1
    paragraph_map = _paragraph_by_index(document)
    for section in present:
        current_position = order[section.key]
        if current_position < previous_position:
            heading = paragraph_map.get(section.heading_paragraph_index)
            expected_titles = "、".join(
                first_sections[key].title
                for key in _DESCRIPTION_SECTION_ORDER
                if key in first_sections
            )
            yield _issue(
                "ORD001",
                f"章節「{section.title}」出現在不符合一般格式的順序位置。",
                f"請人工確認主要章節順序是否應為：{expected_titles}。",
                paragraph=heading,
                section_key=section.key,
                section_title=section.title,
                start=0 if heading else None,
                end=len(heading.text) if heading else None,
            )
            return
        previous_position = current_position


def _drawing_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    figure_records: List[Tuple[int, PatentParagraph, int, int]] = []
    for paragraph in document.paragraphs:
        if (
            paragraph.is_heading
            or paragraph.section_key != "brief_description_of_drawings"
        ):
            continue
        for line, line_start, _line_end in _line_spans(paragraph.text):
            match = _FIGURE_AT_LINE_START.match(line)
            if match is None:
                continue
            start_number = int(match.group("start"))
            end_text = match.group("end")
            end_number = int(end_text) if end_text else start_number
            low, high = sorted((start_number, end_number))
            for number in range(low, high + 1):
                figure_records.append(
                    (
                        number,
                        paragraph,
                        line_start + match.start(),
                        line_start + match.end(),
                    )
                )

    actual_numbers = [record[0] for record in figure_records]
    for expected, record in enumerate(figure_records, start=1):
        number, paragraph, start, end = record
        if number == expected:
            continue
        yield _issue(
            "FIG001",
            f"圖式簡單說明依序出現圖{number}，此處預期為圖{expected}。",
            "請確認圖號是否由圖1開始且沒有跳號、重複或順序顛倒。",
            paragraph=paragraph,
            start=start,
            end=end,
            details={"expected_number": expected, "actual_number": number},
        )
        break

    if not actual_numbers:
        return
    described = set(actual_numbers)
    for paragraph in document.paragraphs:
        if paragraph.section_key != "designated_representative_drawing":
            continue
        for match in _FIGURE_REFERENCE.finditer(paragraph.text):
            number = int(match.group("number"))
            if number in described:
                continue
            yield _issue(
                "FIG002",
                f"指定代表圖為圖{number}，但圖式簡單說明沒有列出該圖號。",
                "請確認指定代表圖圖號，或在圖式簡單說明補上對應圖號。",
                paragraph=paragraph,
                start=match.start(),
                end=match.end(),
                details={"representative_figure": number},
            )


def _line_spans(text: str) -> Iterable[Tuple[str, int, int]]:
    offset = 0
    parts = text.splitlines(keepends=True) or ([text] if text else [])
    for part in parts:
        line = part.rstrip("\r\n")
        left = len(line) - len(line.lstrip())
        right = len(line.rstrip())
        if right > left:
            yield line[left:right], offset + left, offset + right
        offset += len(part)


def _canonical_symbol(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", "", normalized).replace("，", ",").replace("′", "'")


def _canonical_name(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _symbol_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    records: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for paragraph in document.paragraphs:
        if paragraph.is_heading or paragraph.section_key not in _SYMBOL_SECTIONS:
            continue
        if paragraph.text.strip() in _NON_SYMBOL_SEPARATORS:
            continue
        for line, line_start, line_end in _line_spans(paragraph.text):
            match = _SYMBOL_LINE.match(line)
            if match is None:
                yield _issue(
                    "SYM001",
                    f"無法辨識符號說明格式：「{line}」。",
                    "請改為一行一組「符號:元件名稱」；半形或全形冒號皆可。",
                    paragraph=paragraph,
                    start=line_start,
                    end=line_end,
                )
                continue
            symbol = _canonical_symbol(match.group("symbol"))
            name = match.group("name").strip()
            symbol_start = line_start + match.start("symbol")
            symbol_end = line_start + match.end("symbol")
            records[paragraph.section_key].append(
                {
                    "symbol": symbol,
                    "name": name,
                    "canonical_name": _canonical_name(name),
                    "paragraph": paragraph,
                    "start": symbol_start,
                    "end": symbol_end,
                }
            )

    for section_key, section_records in records.items():
        seen: Dict[str, Dict[str, object]] = {}
        for record in section_records:
            symbol = str(record["symbol"])
            previous = seen.get(symbol)
            if previous is None:
                seen[symbol] = record
                continue
            if previous["canonical_name"] != record["canonical_name"]:
                paragraph = record["paragraph"]
                yield _issue(
                    "SYM003",
                    f"符號「{symbol}」同時對應「{previous['name']}」與「{record['name']}」。",
                    "請確認正確元件名稱，並統一所有相同符號的說明。",
                    paragraph=paragraph,
                    start=int(record["start"]),
                    end=int(record["end"]),
                    details={
                        "previous_name": previous["name"],
                        "previous_paragraph_index": previous["paragraph"].index,
                    },
                )

    full_map = {
        str(record["symbol"]): record
        for record in records.get("drawing_symbol_description", [])
    }
    for representative in records.get("representative_drawing_symbols", []):
        symbol = str(representative["symbol"])
        paragraph = representative["paragraph"]
        full = full_map.get(symbol)
        if full is None:
            yield _issue(
                "SYM004",
                f"代表圖符號「{symbol}」未出現在完整符號說明。",
                "請在符號說明補入該符號，或修正代表圖符號。",
                paragraph=paragraph,
                start=int(representative["start"]),
                end=int(representative["end"]),
            )
        elif full["canonical_name"] != representative["canonical_name"]:
            yield _issue(
                "SYM005",
                f"符號「{symbol}」在代表圖寫作「{representative['name']}」，完整符號說明則寫作「{full['name']}」。",
                "請確認並統一兩處元件名稱。",
                paragraph=paragraph,
                start=int(representative["start"]),
                end=int(representative["end"]),
                details={"full_description_name": full["name"]},
            )


def _reference_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    """Conservatively compare body name/label pairs with the complete list."""

    # Import locally to keep the rule model independent during package startup.
    from .symbol_transfer import extract_document_symbols

    transfer = extract_document_symbols(document)
    expected_by_name: Dict[str, set[str]] = defaultdict(set)
    defined_labels = set()
    for entry in transfer.full_entries:
        canonical_name = _canonical_name(entry.name)
        if canonical_name:
            expected_by_name[canonical_name].add(entry.label)
        defined_labels.add(entry.label)
    if not defined_labels:
        return

    emitted: set[Tuple[str, int, int, str]] = set()

    def emit_once(
        rule_id: str,
        paragraph: PatentParagraph,
        start: int,
        end: int,
        message: str,
        suggestion: str,
        **details,
    ) -> Optional[PatentIssue]:
        key = (rule_id, paragraph.index, start, message)
        if key in emitted:
            return None
        emitted.add(key)
        return _issue(
            rule_id,
            message,
            suggestion,
            paragraph=paragraph,
            start=start,
            end=end,
            details=details,
        )

    known_names = sorted(expected_by_name, key=len, reverse=True)
    for paragraph in document.paragraphs:
        if paragraph.section_key not in _REFERENCE_SECTIONS:
            continue
        text = unicodedata.normalize("NFKC", paragraph.text).replace("′", "'")

        # Strong comparison: an exact name from the list is followed by a label.
        for name in known_names:
            pattern = re.compile(
                re.escape(name) + r"\s*(?P<label>\d{1,3}[A-Za-z]?(?:')?)"
            )
            for match in pattern.finditer(text):
                label = _canonical_symbol(match.group("label"))
                expected = expected_by_name[name]
                label_start = match.start("label")
                label_end = match.end("label")
                if label not in defined_labels:
                    issue = emit_once(
                        "REF001",
                        paragraph,
                        label_start,
                        label_end,
                        f"內文使用標號「{label}」，但完整符號說明沒有此標號。",
                        "請確認標號是否正確；若為圖式元件，請補入完整符號說明。",
                        label=label,
                        component_name=name,
                    )
                    if issue is not None:
                        yield issue
                if label not in expected:
                    issue = emit_once(
                        "REF002",
                        paragraph,
                        match.start(),
                        match.end(),
                        f"元件「{name}」在完整符號說明對應「{'、'.join(sorted(expected))}」，此處卻寫作「{label}」。",
                        "請回到 Word 確認此處的元件名稱與標號是否配對正確。",
                        label=label,
                        component_name=name,
                        expected_labels=sorted(expected),
                    )
                    if issue is not None:
                        yield issue

        # Low-risk fallback for previously undefined component-like names.
        for match in _COMPONENT_REFERENCE.finditer(text):
            name = match.group("name")
            label = _canonical_symbol(match.group("label"))
            if label in defined_labels or not name.endswith(_COMPONENT_NAME_SUFFIXES):
                continue
            issue = emit_once(
                "REF001",
                paragraph,
                match.start("label"),
                match.end("label"),
                f"內文使用標號「{label}」，但完整符號說明沒有此標號。",
                "此規則採保守文字比對；請人工確認它是否為圖式元件標號。",
                label=label,
                component_name=name,
                confidence="conservative_text_match",
            )
            if issue is not None:
                yield issue


def _claim_prefix(text: str) -> Optional[re.Match[str]]:
    for pattern in _CLAIM_PREFIXES:
        match = pattern.match(text)
        if match is not None:
            return match
    return None


def _claim_dependencies(text: str, body_start: int) -> List[Tuple[List[int], int, int]]:
    dependencies: List[Tuple[List[int], int, int]] = []
    body = text[body_start:]
    occupied: List[Tuple[int, int]] = []
    for match in _CLAIM_RANGE.finditer(body):
        start_number = int(match.group("start"))
        end_number = int(match.group("end"))
        low, high = sorted((start_number, end_number))
        dependencies.append(
            (list(range(low, high + 1)), body_start + match.start(), body_start + match.end())
        )
        occupied.append((match.start(), match.end()))
    for match in _CLAIM_LIST.finditer(body):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        numbers = [int(value) for value in re.findall(r"\d+", match.group("numbers"))]
        dependencies.append(
            (numbers, body_start + match.start(), body_start + match.end())
        )
        occupied.append((match.start(), match.end()))
    for match in _CLAIM_SINGLE.finditer(body):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        dependencies.append(
            ([int(match.group("number"))], body_start + match.start(), body_start + match.end())
        )
    return dependencies


def _claim_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    entries: List[Dict[str, object]] = []
    for paragraph in document.paragraphs:
        if paragraph.is_heading or paragraph.section_key != "claims":
            continue
        text = paragraph.text.strip()
        if not text:
            continue
        prefix = _claim_prefix(paragraph.text)
        if prefix is None:
            yield _issue(
                "CLM001",
                "請求項段落缺少可辨識的編號。",
                "請在段首加入「【請求項N】」並確認編號。",
                paragraph=paragraph,
                start=0,
                end=min(len(paragraph.text), 24),
            )
            continue
        entries.append(
            {
                "number": int(prefix.group("number")),
                "paragraph": paragraph,
                "number_start": prefix.start("number"),
                "number_end": prefix.end("number"),
                "body_start": prefix.end(),
            }
        )

    for entry in entries:
        dependencies = _claim_dependencies(
            entry["paragraph"].text, int(entry["body_start"])
        )
        entry["dependency_groups"] = dependencies
        entry["dependencies"] = sorted(
            {
                reference
                for references, _start, _end in dependencies
                for reference in references
            }
        )
        entry["is_multiple"] = len(entry["dependencies"]) > 1

    actual_numbers = {int(entry["number"]) for entry in entries}
    for position, entry in enumerate(entries, start=1):
        number = int(entry["number"])
        paragraph = entry["paragraph"]
        if number != position:
            yield _issue(
                "CLM002",
                f"文件中的第{position}個請求項標為請求項{number}。",
                f"請確認是否應改為請求項{position}，並同步檢查所有依附關係。",
                paragraph=paragraph,
                start=int(entry["number_start"]),
                end=int(entry["number_end"]),
                details={"expected_number": position, "actual_number": number},
            )

        invalid_dependencies: List[int] = []
        dependency_spans: List[Tuple[int, int]] = []
        for references, start, end in entry["dependency_groups"]:
            invalid = [
                reference
                for reference in references
                if reference not in actual_numbers or reference >= number
            ]
            if invalid:
                invalid_dependencies.extend(invalid)
                dependency_spans.append((start, end))
        if invalid_dependencies:
            start = min(item[0] for item in dependency_spans)
            end = max(item[1] for item in dependency_spans)
            values = sorted(set(invalid_dependencies))
            yield _issue(
                "CLM003",
                f"請求項{number}引用無效或尚未出現的請求項：{', '.join(map(str, values))}。",
                "請將依附對象改為已存在且編號較小的請求項。",
                paragraph=paragraph,
                start=start,
                end=end,
                details={"invalid_dependencies": values},
            )

        dependencies = list(entry["dependencies"])
        if entry["is_multiple"] and not re.search(
            r"(?:任一(?:請求項|項)?|或者|或)",
            paragraph.text[int(entry["body_start"]):],
        ):
            spans = list(entry["dependency_groups"])
            start = min(item[1] for item in spans)
            end = max(item[2] for item in spans)
            yield _issue(
                "CLM004",
                f"請求項{number}引用多個請求項，但未辨識到「任一項」或「或」等選擇式文字。",
                "請人工確認此多項附屬項是否應改為選擇式記載。",
                paragraph=paragraph,
                start=start,
                end=end,
                details={"dependencies": dependencies},
            )

    entries_by_number = {int(entry["number"]): entry for entry in entries}

    def reaches_multiple_claim(claim_number: int, visited: set[int]) -> set[int]:
        if claim_number in visited:
            return set()
        visited = visited | {claim_number}
        claim = entries_by_number.get(claim_number)
        if claim is None:
            return set()
        hits = {claim_number} if claim["is_multiple"] else set()
        for dependency in claim["dependencies"]:
            hits.update(reaches_multiple_claim(dependency, visited))
        return hits

    for entry in entries:
        if not entry["is_multiple"]:
            continue
        forbidden = set()
        for dependency in entry["dependencies"]:
            forbidden.update(reaches_multiple_claim(dependency, set()))
        if not forbidden:
            continue
        paragraph = entry["paragraph"]
        spans = list(entry["dependency_groups"])
        number = int(entry["number"])
        yield _issue(
            "CLM005",
            f"多項附屬項請求項{number}直接或間接依附另一多項附屬項：{', '.join(map(str, sorted(forbidden)))}。",
            "請重新安排依附關係，避免多項附屬項直接或間接依附另一多項附屬項。",
            paragraph=paragraph,
            start=min(item[1] for item in spans),
            end=max(item[2] for item in spans),
            details={
                "dependencies": list(entry["dependencies"]),
                "multiple_claim_ancestors": sorted(forbidden),
            },
        )


def _typography_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    for paragraph in document.paragraphs:
        for match in _FULLWIDTH_ALNUMERIC.finditer(paragraph.text):
            replacement = unicodedata.normalize("NFKC", match.group(0))
            yield _issue(
                "TXT001",
                f"發現可轉為半形的文字「{match.group(0)}」。",
                f"建議改為「{replacement}」。",
                paragraph=paragraph,
                start=match.start(),
                end=match.end(),
                safe_auto_fix=True,
                replacement=replacement,
            )
        for index, character in enumerate(paragraph.text):
            replacement = _NONSTANDARD_PRIME.get(character)
            if replacement is None:
                continue
            yield _issue(
                "TXT002",
                f"發現非標準 prime mark「{character}」。",
                "建議統一為半形 apostrophe「'」。",
                paragraph=paragraph,
                start=index,
                end=index + 1,
                safe_auto_fix=True,
                replacement=replacement,
            )


def review_document(document: PatentDocument) -> PatentTextReview:
    """Run all Stage 2 rules without modifying the parsed document or DOCX."""

    issues: List[PatentIssue] = []
    for producer in (
        _structure_issues,
        _section_order_issues,
        _paragraph_number_issues,
        _symbol_issues,
        _drawing_issues,
        _reference_issues,
        _claim_issues,
        _typography_issues,
    ):
        issues.extend(producer(document))
    issues.sort(
        key=lambda issue: (
            _SEVERITY_ORDER.get(issue.severity, 99),
            -1 if issue.paragraph_index is None else issue.paragraph_index,
            -1 if issue.char_start is None else issue.char_start,
            issue.rule_id,
            issue.issue_id,
        )
    )
    return PatentTextReview(
        source_path=document.source_path,
        file_name=document.file_name,
        sha256=document.sha256,
        patent_type=document.patent_type,
        patent_title=document.patent_title,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        issues=issues,
        rule_catalog=list(RULE_CATALOG),
        parse_warnings=list(document.warnings),
    )
