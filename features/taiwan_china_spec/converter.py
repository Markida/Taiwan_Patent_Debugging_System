from __future__ import annotations

import ctypes
from ctypes import wintypes
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W_NS, "m": M_NS}

ET.register_namespace("w", W_NS)
ET.register_namespace("m", M_NS)


def _w(local_name: str) -> str:
    return f"{{{W_NS}}}{local_name}"


def _parse_xml(payload: bytes) -> ET.Element:
    """Parse XML while retaining namespace prefixes used by mc:Ignorable.

    Word checks the literal prefix names listed in mc:Ignorable. Letting
    ElementTree rename w14/w15/wp14 to ns1/ns2/ns3 produces well-formed XML
    that Word nevertheless reports as damaged.
    """

    for _event, namespace in ET.iterparse(io.BytesIO(payload), events=("start-ns",)):
        prefix, uri = namespace
        try:
            ET.register_namespace(prefix or "", uri)
        except ValueError:
            # Reserved/internal prefixes are already handled by ElementTree.
            pass
    return ET.fromstring(payload)


class ConversionError(RuntimeError):
    """Raised when a source document cannot be converted safely."""


@dataclass
class ParagraphRecord:
    element: ET.Element
    text: str
    numbered: bool


@dataclass
class ParsedSpecification:
    kind: str
    title: str
    abstract: list[str]
    technology_field: list[str]
    background: list[str]
    invention_content: list[str]
    drawing_description: list[str]
    embodiments: list[str]
    claims: list[str]
    table_count: int = 0
    drawing_count: int = 0
    equation_count: int = 0


@dataclass
class ConversionReport:
    source: str
    output: str
    template: str
    specification_kind: str = ""
    sections_found: dict[str, int] = field(default_factory=dict)
    claim_count: int = 0
    replacement_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ok"] = self.ok
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class ConversionPreviewParagraph:
    """One final-China-format paragraph and its pre-conversion comparison text."""

    source_text: str
    converted_text: str
    prefix: str = ""
    role: str = "body"


@dataclass
class ConversionPreview:
    """Text-only preview used by the desktop UI before writing a DOCX."""

    source: str
    specification_kind: str
    paragraphs: list[ConversionPreviewParagraph] = field(default_factory=list)
    replacement_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    fixed_content_layout: bool = False


@dataclass(frozen=True)
class EditedClaimsContentReplacement:
    """An atomic preview update; all edit offsets refer to the original text.

    Offsets count Python Unicode characters, not Qt UTF-16 code units. Apply
    ``edits`` from last to first inside one editor undo transaction.
    """

    text: str
    warnings: tuple[str, ...]
    claim_count: int
    content_start: int
    content_end: int
    replacement_text: str
    edits: tuple[tuple[int, int, str], ...]


EDITABLE_PREVIEW_HEADINGS = (
    ("abstract", "說明書摘要"),
    ("claims", "權利要求書"),
    ("specification", "說明書"),
    ("technology_field", "技術領域"),
    ("background", "背景技術"),
    ("invention_content", "發明內容"),
    ("drawing_description", "附圖說明"),
    ("embodiments", "具體實施方式"),
)

EDITABLE_UTILITY_MODEL_CONTENT_HEADING = "實用新型內容"

_EDITABLE_HEADING_ALIASES = (
    ("abstract", {"說明書摘要", "说明书摘要"}),
    ("claims", {"權利要求書", "权利要求书"}),
    ("specification", {"說明書", "说明书"}),
    ("technology_field", {"技術領域", "技术领域"}),
    ("background", {"背景技術", "背景技术"}),
    ("invention_content", {"發明內容", "发明内容", "實用新型內容", "实用新型内容"}),
    ("drawing_description", {"附圖說明", "附图说明"}),
    ("embodiments", {"具體實施方式", "具体实施方式"}),
)


SECTION_MARKERS = {
    "abstract_cn": ("【中文】",),
    "abstract_end": ("【英文】", "【指定代表圖】", "【指定代表图】"),
    "spec_start": ("【發明說明書】", "【新型說明書】"),
    "technology_field": ("【技術領域】",),
    "background": ("【先前技術】",),
    "invention_content": ("【發明內容】", "【新型內容】"),
    "drawing_description": ("【圖式簡單說明】",),
    "embodiments": ("【實施方式】",),
    "symbol_description": ("【符號說明】",),
    "claims": (
        "【發明申請專利範圍】",
        "【新型申請專利範圍】",
        "【发明申请专利范围】",
        "【新型申请专利范围】",
        "【实用新型申请专利范围】",
        "【申請專利範圍】",
        "【申请专利范围】",
    ),
}

PLAIN_CLAIMS_HEADINGS = (
    "發明申請專利範圍",
    "新型申請專利範圍",
    "发明申请专利范围",
    "新型申请专利范围",
    "实用新型申请专利范围",
    "申請專利範圍",
    "申请专利范围",
)

TEMPLATE_SLOTS = {
    "abstract": "說明書摘要...開始段落",
    "claims": "權利要求書...開始段落",
    "title": "申請標的名稱...開始段落",
    "technology_field": "技術領域...開始段落",
    "background": "背景技術...開始段落",
    "invention_content": "發明內容...開始段落",
    "drawing_description": "附圖說明...開始段落",
    "embodiments": "具體實施方式...開始段落",
}


def _resource_dir() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "app"
        / "resources"
        / "taiwan_china_spec"
    )


def default_terminology_path() -> Path:
    return _resource_dir() / "terminology.tsv"


def available_templates() -> dict[str, Path]:
    resources = _resource_dir()
    return {
        "beijing_taiji": resources / "BeijingTaijiTemplate.docx",
        "shanghai_yipin": resources / "ShanghaiYiPin.docx",
    }


def _save_legacy_doc_as_docx(source: Path, target: Path) -> None:
    """Use the installed Microsoft Word for the old interface's .doc support."""

    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    cscript = windows_dir / "System32" / "cscript.exe"
    if not cscript.is_file():
        raise ConversionError("Windows 找不到 cscript.exe，無法開啟舊式 .doc。")

    script_path = target.with_suffix(".vbs")
    script_path.write_text(
        "\n".join(
            (
                "On Error Resume Next",
                "Set word = CreateObject(\"Word.Application\")",
                "If Err.Number <> 0 Then WScript.Quit 11",
                "word.Visible = False",
                "word.DisplayAlerts = 0",
                "word.AutomationSecurity = 3",
                "Set doc = word.Documents.Open(WScript.Arguments.Item(0), False, True)",
                "If Err.Number <> 0 Then word.Quit : WScript.Quit 12",
                "doc.SaveAs2 WScript.Arguments.Item(1), 16",
                "If Err.Number <> 0 Then doc.Close False : word.Quit : WScript.Quit 13",
                "doc.Close False",
                "word.Quit",
            )
        ),
        encoding="ascii",
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                str(cscript),
                "//NoLogo",
                str(script_path),
                str(source),
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=creation_flags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConversionError(f"Word 轉換舊式 .doc 失敗：{exc}") from exc
    if completed.returncode != 0 or not target.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f"（Word 相容性代碼 {completed.returncode}）"
        if detail:
            suffix += f"：{detail}"
        raise ConversionError(
            "無法以 Microsoft Word 開啟舊式 .doc；請確認已安裝 Word，"
            f"或先另存為 .docx。{suffix}"
        )


def _prepare_docx(source: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    if source.suffix.lower() == ".docx":
        return source, None
    if source.suffix.lower() != ".doc":
        raise ConversionError("僅支援 Word 文件（.doc 或 .docx）。")
    temporary = tempfile.TemporaryDirectory(prefix="taiwan-china-spec-")
    converted = Path(temporary.name) / f"{source.stem}.docx"
    try:
        _save_legacy_doc_as_docx(source, converted)
    except Exception:
        temporary.cleanup()
        raise
    return converted, temporary


def _paragraph_text(paragraph: ET.Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == _w("t"):
            pieces.append(node.text or "")
        elif node.tag == _w("tab"):
            pieces.append("\t")
        elif node.tag in {_w("br"), _w("cr")}:
            pieces.append("\n")
    return "".join(pieces).replace("\r", "").strip()


def _normalized(text: str) -> str:
    return re.sub(r"[\s\u3000]+", "", text or "")


def _body_records(root: ET.Element) -> tuple[list[ParagraphRecord], int]:
    body = root.find("w:body", NS)
    if body is None:
        raise ConversionError("DOCX 缺少 word/document.xml 的 w:body。")

    records: list[ParagraphRecord] = []
    table_count = 0
    for child in list(body):
        if child.tag == _w("p"):
            numbered = child.find("w:pPr/w:numPr", NS) is not None
            records.append(ParagraphRecord(child, _paragraph_text(child), numbered))
        elif child.tag == _w("tbl"):
            table_count += 1
    return records, table_count


def _find_record_index(
    records: list[ParagraphRecord],
    markers: Iterable[str],
    start: int = 0,
    end: int | None = None,
) -> int | None:
    normalized_markers = tuple(_normalized(marker) for marker in markers)
    stop = len(records) if end is None else min(end, len(records))
    for index in range(max(0, start), stop):
        text = _normalized(records[index].text)
        if any(marker in text for marker in normalized_markers):
            return index
    return None


def _required_index(
    records: list[ParagraphRecord],
    marker_key: str,
    start: int = 0,
    end: int | None = None,
) -> int:
    index = _find_record_index(records, SECTION_MARKERS[marker_key], start, end)
    if index is None:
        labels = "／".join(SECTION_MARKERS[marker_key])
        raise ConversionError(f"找不到必要段落標記：{labels}")
    return index


def _match_claims_heading(text: str) -> str | None:
    """Return text after a real claims heading, or None for ordinary prose.

    Some Taiwan specifications mention ``申請專利範圍`` inside the final
    embodiment paragraph.  Treating that bare phrase as a substring marker
    makes the following symbol list become a false first claim.  Bracketed
    headings must start the paragraph; unbracketed legacy headings must occupy
    the entire paragraph.  A first claim placed after a bracketed heading in
    the same paragraph is retained.
    """

    stripped = (text or "").lstrip("\ufeff \t\r\n\u3000")
    for marker in SECTION_MARKERS["claims"]:
        if stripped.startswith(marker):
            return stripped[len(marker) :].lstrip(" ：:\t\u3000")

    normalized = _normalized(stripped).rstrip("：:")
    if normalized in {_normalized(marker) for marker in PLAIN_CLAIMS_HEADINGS}:
        return ""
    return None


def _find_claims_section(
    records: list[ParagraphRecord], start: int = 0
) -> tuple[int, str]:
    for index in range(max(0, start), len(records)):
        trailing_text = _match_claims_heading(records[index].text)
        if trailing_text is not None:
            return index, trailing_text
    labels = "／".join(SECTION_MARKERS["claims"])
    raise ConversionError(f"找不到必要段落標記：{labels}")


def _texts_between(
    records: list[ParagraphRecord], start_index: int, end_index: int
) -> list[str]:
    return [
        record.text
        for record in records[start_index + 1 : end_index]
        if record.text and record.text != "\x0c"
    ]


def _extract_title(record_text: str, markers: tuple[str, ...]) -> str:
    normalized = record_text.strip()
    for marker in markers:
        position = normalized.find(marker)
        if position >= 0:
            return normalized[position + len(marker) :].replace("【中文寫於此行】", "").strip()
    return ""


def _strip_manual_claim_number(text: str) -> str:
    return re.sub(r"^\s*(?:第\s*)?\d+\s*[\.、．]\s*", "", text).strip()


def _group_claims(records: list[ParagraphRecord]) -> list[str]:
    claims: list[list[str]] = []
    current: list[str] = []

    for record in records:
        text = _strip_manual_claim_number(record.text)
        if not text or text == "\x0c":
            continue

        looks_numbered = bool(re.match(r"^\s*(?:第\s*)?\d+\s*[\.、．]", record.text))
        starts_new = record.numbered or looks_numbered
        if starts_new:
            if current:
                claims.append(current)
            current = [text]
        elif current:
            current.append(text)
        else:
            current = [text]

    if current:
        claims.append(current)

    return [re.sub(r"\s+", " ", " ".join(parts)).strip() for parts in claims]


def parse_taiwan_specification(source_path: str | os.PathLike[str]) -> ParsedSpecification:
    source = Path(source_path).resolve()
    if source.suffix.lower() != ".docx":
        raise ConversionError("目前整合版僅接受 .docx；請先在 Word 將舊 .doc 另存為 .docx。")
    if not source.is_file():
        raise ConversionError(f"找不到來源文件：{source}")

    try:
        with zipfile.ZipFile(source, "r") as package:
            document_xml = package.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ConversionError("來源檔不是有效的 Word DOCX。") from exc

    root = _parse_xml(document_xml)
    records, table_count = _body_records(root)

    spec_start = _required_index(records, "spec_start")
    claims_start, first_claim_text = _find_claims_section(records, start=spec_start)
    tech_start = _required_index(records, "technology_field", start=spec_start, end=claims_start)
    background_start = _required_index(records, "background", start=tech_start, end=claims_start)
    content_start = _required_index(records, "invention_content", start=background_start, end=claims_start)
    drawing_start = _required_index(records, "drawing_description", start=content_start, end=claims_start)
    embodiment_start = _required_index(records, "embodiments", start=drawing_start, end=claims_start)
    symbol_start = _find_record_index(
        records, SECTION_MARKERS["symbol_description"], embodiment_start, claims_start
    )
    embodiment_end = claims_start if symbol_start is None else symbol_start

    abstract_start = _required_index(records, "abstract_cn", end=spec_start)
    abstract_end = _find_record_index(
        records, SECTION_MARKERS["abstract_end"], abstract_start + 1, spec_start
    )
    if abstract_end is None:
        abstract_end = spec_start

    spec_header = records[spec_start].text
    is_invention = "發明" in spec_header
    kind = "invention" if is_invention else "utility_model"
    title_markers = ("【中文發明名稱】",) if is_invention else ("【中文新型名稱】",)
    title_index = _find_record_index(records, title_markers, spec_start + 1, tech_start)
    if title_index is None:
        raise ConversionError(f"找不到必要段落標記：{title_markers[0]}")
    title = _extract_title(records[title_index].text, title_markers)
    if not title:
        raise ConversionError("中文申請標的名稱為空白。")

    claim_records = records[claims_start + 1 :]
    if first_claim_text:
        claim_records = [
            ParagraphRecord(
                records[claims_start].element,
                first_claim_text,
                records[claims_start].numbered,
            )
        ] + claim_records
    claims = _group_claims(claim_records)
    if not claims:
        raise ConversionError("申請專利範圍內沒有可辨識的請求項。")

    drawing_count = len(root.findall(".//w:drawing", NS)) + len(root.findall(".//w:pict", NS))
    equation_count = len(root.findall(".//m:oMath", NS)) + len(root.findall(".//m:oMathPara", NS))

    return ParsedSpecification(
        kind=kind,
        title=title,
        abstract=_texts_between(records, abstract_start, abstract_end),
        technology_field=_texts_between(records, tech_start, background_start),
        background=_texts_between(records, background_start, content_start),
        invention_content=_texts_between(records, content_start, drawing_start),
        drawing_description=_texts_between(records, drawing_start, embodiment_start),
        embodiments=_texts_between(records, embodiment_start, embodiment_end),
        claims=claims,
        table_count=table_count,
        drawing_count=drawing_count,
        equation_count=equation_count,
    )


def _terminology_lines_from_docx(source: Path) -> list[str]:
    try:
        with zipfile.ZipFile(source, "r") as package:
            root = _parse_xml(package.read("word/document.xml"))
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ConversionError("大陸用語辭典不是有效的 Word 文件。") from exc
    return [
        _paragraph_text(paragraph)
        for paragraph in root.findall(".//w:body/w:p", NS)
    ]


def _parse_terminology_lines(raw_lines: Iterable[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line_number, raw_line in enumerate(raw_lines, 1):
        line = raw_line.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" in line:
            left, right = line.split("\t", 1)
        elif "：" in line:
            left, right = line.split("：", 1)
        elif ":" in line:
            left, right = line.split(":", 1)
        else:
            raise ConversionError(f"用語規則第 {line_number} 行缺少分隔符號。")
        left, right = left.strip(), right.strip()
        if not left:
            raise ConversionError(f"用語規則第 {line_number} 行的來源詞為空白。")
        pairs.append((left, right))

    return normalize_terminology_pairs(pairs)


def normalize_terminology_pairs(
    pairs: Iterable[tuple[object, object]],
) -> list[tuple[str, str]]:
    """Validate an editable terminology snapshot without changing its order.

    Terminology replacements intentionally form an ordered pipeline.  Repeated
    sources and repeated pairs are therefore meaningful and must not be sorted
    or collapsed.
    """

    normalized: list[tuple[str, str]] = []
    for item_number, item in enumerate(pairs, 1):
        try:
            source_value, target_value = item
        except (TypeError, ValueError) as exc:
            raise ConversionError(
                f"用語規則第 {item_number} 筆必須包含台灣用語與大陸用語。"
            ) from exc
        source = str(source_value or "").strip()
        target = str(target_value or "").strip()
        if not source:
            raise ConversionError(f"用語規則第 {item_number} 筆的台灣用語為空白。")
        if any(character in source + target for character in "\r\n"):
            raise ConversionError(f"用語規則第 {item_number} 筆只能使用單行文字。")
        normalized.append((source, target))

    return normalized


def parse_terminology_text(text: object) -> list[tuple[str, str]]:
    """Parse TSV or legacy colon-separated terminology entered in the UI."""

    return _parse_terminology_lines(str(text or "").splitlines())


def _load_terminology(path: Path | None = None) -> list[tuple[str, str]]:
    source = path or default_terminology_path()
    if not source.is_file():
        raise ConversionError(f"找不到用語規則：{source}")

    temporary = None
    if source.suffix.lower() in {".doc", ".docx"}:
        prepared, temporary = _prepare_docx(source)
        try:
            raw_lines = _terminology_lines_from_docx(prepared)
        finally:
            if temporary is not None:
                temporary.cleanup()
    else:
        try:
            raw_lines = source.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError:
            raw_lines = source.read_text(encoding="cp950").splitlines()
    return _parse_terminology_lines(raw_lines)


def _windows_chinese_mapping(text: str, flag: int, locale: str) -> str:
    if not text or os.name != "nt":
        return text

    # Windows performs the same offline character conversion used by many
    # desktop applications and avoids an additional runtime package.
    kernel32 = ctypes.windll.kernel32
    kernel32.LCMapStringEx.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.LPWSTR,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.LPARAM,
    )
    kernel32.LCMapStringEx.restype = ctypes.c_int
    # LCMapStringEx counts UTF-16 code units, whereas Python counts Unicode
    # code points. A supplementary character must contribute two units or the
    # final character can be truncated (including half of a surrogate pair).
    source_length = len(text.encode("utf-16-le")) // 2
    required = kernel32.LCMapStringEx(locale, flag, text, source_length, None, 0, None, None, 0)
    if required <= 0:
        return text
    buffer = ctypes.create_unicode_buffer(required)
    written = kernel32.LCMapStringEx(
        locale, flag, text, source_length, buffer, required, None, None, 0
    )
    return buffer.value[:written] if written > 0 else text


def _windows_simplified_chinese(text: str) -> str:
    # LCMAP_SIMPLIFIED_CHINESE
    return _windows_chinese_mapping(text, 0x02000000, "zh-CN")


def _windows_traditional_chinese(text: str) -> str:
    # LCMAP_TRADITIONAL_CHINESE.  The editable preview deliberately remains in
    # Traditional Chinese; only the final Word output is simplified.
    return _windows_chinese_mapping(text, 0x04000000, "zh-TW")


def _normalize_patent_structure(text: str) -> str:
    text = re.sub(r"^\s*【\d{4}】\s*", "", text or "")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text).strip()
    text = text.replace(",", "，").replace(";", "；").replace(":", "：")
    return text


def _normalize_patent_wording(text: str) -> str:
    text = text.replace("之目的", "的目的").replace("之功效", "的功效")
    text = text.replace("本發明之", "本發明的").replace("本新型之", "本新型的")
    text = text.replace("所述之", "所述的")
    return text


def _convert_text(
    text: str,
    terminology: list[tuple[str, str]],
    counts: Counter[str],
    apply_terminology: bool,
) -> str:
    # Company terminology is an explicit ordered pipeline.  Only structural
    # cleanup may run before it; built-in wording cleanup runs afterwards so it
    # cannot make an approved source phrase disappear before its turn.
    converted = _normalize_patent_structure(text)
    if apply_terminology:
        for source, target in terminology:
            occurrences = converted.count(source)
            if occurrences:
                converted = converted.replace(source, target)
                counts[f"{source}→{target}"] += occurrences
    converted = _normalize_patent_wording(converted)
    return _windows_simplified_chinese(converted)


def _claim_reference_numbers(text: str) -> list[int]:
    references: list[int] = []
    for match in re.finditer(r"(?:請求項|权利要求)\s*(\d+)", text):
        references.append(int(match.group(1)))
    return references


def _convert_claim(
    text: str,
    index: int,
    terminology: list[tuple[str, str]],
    counts: Counter[str],
    apply_terminology: bool,
    insert_feature_phrase: bool,
    warnings: list[str],
) -> str:
    original = text
    references = _claim_reference_numbers(original)
    for reference in references:
        if reference >= index:
            warnings.append(f"權利要求 {index} 引用了未在前的權利要求 {reference}，請人工確認。")

    text = re.sub(
        r"如\s*請求項\s*([0-9、，,至～~\-或及]+)\s*所述(?:之|的)",
        r"根据权利要求\1所述的",
        text,
    )
    text = re.sub(r"如\s*权利要求", "根据权利要求", text)
    text = _convert_text(text, terminology, counts, apply_terminology)
    text = re.sub(r"其特[徵征]在[於于]", "其特征在于", text)

    if insert_feature_phrase and "其特征在于" not in text:
        if "，其中" in text:
            text = text.replace("，其中", "，其特征在于：", 1)
            counts["自動插入其特征在于"] += 1
        else:
            comma_position = text.find("，")
            if comma_position >= 0:
                prefix = text[: comma_position + 1]
                suffix = text[comma_position + 1 :].lstrip(" ：:，,")
                text = prefix + "其特征在于：" + suffix
                counts["自動插入其特征在于"] += 1
            else:
                warnings.append(f"權利要求 {index} 找不到可插入「其特征在于」的逗號。")

    # Existing source wording and a second preview/export pass can both leave
    # punctuation immediately after the feature connector. Keep one colon and
    # no following comma, without touching punctuation elsewhere in the claim.
    text = re.sub(r"其特征在于\s*[：:]?\s*[，,]+\s*", "其特征在于：", text)

    return text


@dataclass(frozen=True)
class _ContentParagraph:
    source_text: str
    text: str
    dependent: bool = False


_PATENT_OWNER = r"本(?:發明|发明|實用新型|实用新型|新型)"
_PURPOSE_PREFIX = re.compile(_PATENT_OWNER + r"(?:之|的)?目的")
_BENEFIT_PREFIX = re.compile(
    _PATENT_OWNER
    + r"(?:之|的)?(?:有益效果|功效)(?:在[於于]|為|为|是)?[：:]?"
)
_CLAIM_LABEL = r"(?:請求項|请求项|權利要求|权利要求|申請專利範圍|申请专利范围)"
_CLAIM_NUMBER = r"(?:第\s*)?[0-9０-９]+(?:\s*[項项])?"
_DEPENDENT_CLAIM = re.compile(
    r"^(?:如|根據|根据|依據|依据|依照|按照|依)\s*"
    + _CLAIM_LABEL
    + r"\s*" + _CLAIM_NUMBER
    + r"(?:\s*[、，,至到～~\-—－或及與与和]\s*(?:"
    + _CLAIM_LABEL
    + r"\s*)?" + _CLAIM_NUMBER + r")*"
    + r"\s*(?:(?:之|中|其中)?(?:任一|任意一|任何一)(?:項|项)?)?"
    + r"\s*所(?:述|記載|记载)(?:之|的)?\s*"
    + r"(?P<subject>[^，,。；;\n]+)(?P<comma>[，,])(?P<body>.+)$"
)
_CONTENT_FEATURE_PREFIX = re.compile(
    r"^\s*(?:其特[徵征]在[於于]|其中)\s*[：:，,]*\s*"
)
_INDEPENDENT_CONTENT_FEATURE_PREFIX = re.compile(
    r"^\s*其特[徵征]在[於于]\s*[：:，,]*\s*"
)
_CONTENT_SENTENCE = re.compile(r"[^。！？!?]*(?:[。！？!?]+|$)")
_SOURCE_PURPOSE_SENTENCE = re.compile(
    r"^因此\s*[，,]?\s*"
    + _PATENT_OWNER
    + r"(?:之|的)?目的\s*[，,]?\s*(?:即\s*)?(?:在\s*)?(?:[於于]\s*)?"
    + r"提供\s*一[種种](?P<tail>.+)$"
)
_SOURCE_MECHANISM_SENTENCE = re.compile(
    r"^[於于]是\s*[，,]\s*" + _PATENT_OWNER
)


def _dependent_content_body(text: str) -> str:
    # These are claim-opening connectors, not technical features. Strip only
    # the opening occurrence; quoted wording later in the body must survive.
    # Taiwan's opening "其中" becomes "其特征在于：" in converted claims.
    return _CONTENT_FEATURE_PREFIX.sub("", text, count=1)


def _independent_content_body(text: str) -> str:
    """Remove only a claim-opening feature connector from content prose."""

    return _INDEPENDENT_CONTENT_FEATURE_PREFIX.sub("", text, count=1)


def _adapt_existing_dependent_narrative(text: str) -> str:
    if re.match(r"^" + _PATENT_OWNER + r"(?:之|的)", text):
        subject, comma, body = text.partition("，")
        if comma:
            return subject + comma + _dependent_content_body(body).lstrip("，, ")
    return text


def _convert_content_paragraph(
    item: _ContentParagraph,
    terminology: list[tuple[str, str]],
    counts: Counter[str],
    apply_terminology: bool,
) -> str:
    # Run the ordered company dictionary exactly once before removing the
    # connector, so rules whose source includes "其中" can still match.
    text = _convert_text(item.text, terminology, counts, apply_terminology)
    return _adapt_existing_dependent_narrative(text) if item.dependent else text


def _content_exact_key(text: str) -> str:
    """Keep technical token boundaries while collapsing formatting whitespace."""

    text = _windows_simplified_chinese(_normalize_patent_structure(text))
    return re.sub(r"\s+", " ", text).strip()


def _content_claim_match_key(text: str) -> str:
    """Exact claim-copy key without rerunning conversion or erasing spacing."""

    return (
        (text or "").replace("\u00a0", " ").strip()
        .replace(",", "，").replace(";", "；").replace(":", "：")
    )


def _content_equivalence_key(text: str) -> str:
    """Only coalesce whole, equivalent paragraphs; never fuzzy-match features."""

    text = _normalize_patent_structure(text)
    text = re.sub(
        r"^" + _PATENT_OWNER + r"(?:之|的)?目的在[於于]\s*，?\s*提供\s*", "", text
    )
    text = re.sub(r"^" + _PATENT_OWNER + r"(?:之|的)", "", text)
    text = re.sub(r"^(?:一種|一种|一個|一个)", "", text)
    # Collapse repeated whitespace, but never erase technical token boundaries:
    # AB/A B and 12/1 2 can describe different features. Punctuation stays exact.
    return _content_exact_key(text)


def _normalize_content_purpose_owner(text: str, owner: str) -> str:
    if owner == "本實用新型":
        return re.sub(
            r"^本(?:新型|實用新型|实用新型)(?:之|的)?目的",
            "本實用新型的目的",
            text,
            count=1,
        )
    return text


def _source_content_sentences(paragraphs: Iterable[str]) -> list[str]:
    """Split source content at sentence punctuation while retaining it."""

    sentences: list[str] = []
    for paragraph in paragraphs:
        normalized = _normalize_patent_structure(paragraph)
        for match in _CONTENT_SENTENCE.finditer(normalized):
            sentence = match.group().strip()
            if sentence:
                sentences.append(sentence)
    return sentences


def _mainland_mouse_component_wording(text: str) -> str:
    """Use 鼠標 for components while preserving a patent subject such as 滑鼠組."""

    return re.sub(r"滑鼠(?!組)", "鼠標", text)


def _fixed_source_intro(
    parsed: ParsedSpecification,
    owner: str,
) -> tuple[_ContentParagraph, _ContentParagraph] | None:
    """Return the required purpose and implementation source sentences.

    The strict two-sentence layout is enabled only when both company-standard
    source phrases are present. Older documents without either marker retain
    the established lossless fallback in the content builder.
    """

    purpose: _ContentParagraph | None = None
    mechanism: _ContentParagraph | None = None
    for sentence in _source_content_sentences(parsed.invention_content):
        purpose_match = _SOURCE_PURPOSE_SENTENCE.fullmatch(sentence)
        if purpose is None and purpose_match is not None:
            purpose = _ContentParagraph(
                sentence,
                f"{owner}的目的在於提供一種{purpose_match.group('tail').lstrip()}",
            )
        if mechanism is None and _SOURCE_MECHANISM_SENTENCE.match(sentence):
            normalized_owner = re.sub(
                r"^[於于]是\s*[，,]\s*", "", sentence, count=1
            )
            if owner == "本實用新型":
                normalized_owner = re.sub(
                    r"^本(?:新型|實用新型|实用新型)",
                    "本實用新型",
                    normalized_owner,
                    count=1,
                )
            mechanism = _ContentParagraph(
                sentence, _mainland_mouse_component_wording(normalized_owner)
            )
    if purpose is None or mechanism is None:
        return None
    return purpose, mechanism


def _strip_component_determiner(text: str) -> str:
    return re.sub(r"^(?:所述|該|该|一個|一个|一)\s*", "", text.strip(), count=1)


def _content_component_key(text: str) -> str:
    return re.sub(
        r"\s+", "", _windows_simplified_chinese(_strip_component_determiner(text))
    )


def _claims_with_source_components(
    parsed: ParsedSpecification,
    warnings: list[str],
) -> list[str]:
    """Complete claim 1's component lead-in from the source 於是 sentence."""

    claims = list(parsed.claims)
    if not claims:
        return claims
    mechanism = next((
        sentence
        for sentence in _source_content_sentences(parsed.invention_content)
        if _SOURCE_MECHANISM_SENTENCE.match(sentence)
    ), None)
    if mechanism is None:
        return claims

    source_components_match = re.search(
        r"(?:並|并)包含\s*[：:]?\s*(?P<components>[^。！？!?]+)[。！？!?]*$",
        mechanism,
    )
    if source_components_match is None:
        warnings.append("來源的「於是」句未找到可辨識的「並包含」構件清單；權利要求1維持原文。")
        return claims
    components = source_components_match.group("components").strip(" \t，,：:")
    source_first_component = re.split(r"[、，,；;]|(?:及|與|与|和)", components, maxsplit=1)[0]

    claim_match = re.search(
        r"(?P<lead>(?:並|并)包含)\s*[：:]?\s*"
        r"(?P<first>[^，,；;：:]+?)\s*[，,]\s*(?P<verb>包括|包含)",
        claims[0],
    )
    if claim_match is None:
        compact_components = re.sub(r"\s+", "", components)
        if compact_components not in re.sub(r"\s+", "", claims[0]):
            warnings.append("權利要求1未找到可安全補入「於是」句構件清單的位置；已維持原文。")
        return claims
    if _content_component_key(source_first_component) != _content_component_key(
        claim_match.group("first")
    ):
        warnings.append("「於是」句的第一個構件與權利要求1不一致；未自動合併構件清單。")
        return claims

    first_component = _strip_component_determiner(claim_match.group("first"))
    replacement = (
        f"{claim_match.group('lead')}{components}，該{first_component}"
        f"{claim_match.group('verb')}"
    )
    enriched_claim = claims[0][:claim_match.start()] + replacement + claims[0][claim_match.end():]
    claims[0] = _mainland_mouse_component_wording(enriched_claim)
    return claims


def _claim_content_paragraph(
    claim: str,
    index: int,
    owner: str,
    needs_purpose: bool,
    warnings: list[str],
    *,
    preserve_edited_text: bool = False,
) -> _ContentParagraph:
    text = claim.strip() if preserve_edited_text else _normalize_patent_structure(claim)
    dependent = _DEPENDENT_CLAIM.fullmatch(text)
    if dependent and dependent.group("subject").strip() not in {"", "之", "的"}:
        subject = dependent.group("subject").strip()
        return _ContentParagraph(
            claim, f"{owner}的{subject}，{dependent.group('body')}", dependent=True
        )

    # An unfamiliar dependency is retained verbatim, rather than mistaking
    # reference syntax for a subject and silently deleting technical wording.
    if re.match(r"^.{0,16}" + _CLAIM_LABEL, text):
        warnings.append(
            f"權利要求 {index} 的依附開頭無法安全辨識；"
            "已完整保留於內容段落，請人工修訂開頭。"
        )
        return _ContentParagraph(claim, text)

    if _PURPOSE_PREFIX.match(text):
        return _ContentParagraph(claim, _normalize_content_purpose_owner(text, owner))
    if needs_purpose:
        subject_match = re.search(r"[，,]", text)
        if subject_match:
            subject = text[:subject_match.end()]
            body = _independent_content_body(text[subject_match.end():])
            return _ContentParagraph(claim, f"{owner}的目的在於提供{subject}{body}")
        return _ContentParagraph(claim, f"{owner}的目的在於提供{text}")

    subject_match = re.search(r"[，,]", text)
    subject, comma, body = (
        (text[:subject_match.start()], subject_match.group(), text[subject_match.end():])
        if subject_match else (text, "", "")
    )
    if comma and subject and body:
        subject = re.sub(r"^(?:一種|一种|一個|一个)", "", subject).strip()
        return _ContentParagraph(
            claim, f"{owner}的{subject}，{_independent_content_body(body)}"
        )
    warnings.append(
        f"權利要求 {index} 無法安全區分標的與技術內容；"
        "已完整保留於內容段落，請人工確認。"
    )
    return _ContentParagraph(claim, text)


def _fixed_claim_one_component_details(
    paragraph: _ContentParagraph,
    warnings: list[str],
) -> _ContentParagraph:
    """Remove claim 1's lead-in already stated by the fixed mechanism sentence."""

    text = _normalize_patent_structure(paragraph.text)
    contains = re.search(r"(?:並|并)包含\s*[：:]?\s*", text)
    if contains is not None:
        tail = text[contains.end():]
        details = re.search(
            r"(?:^|[，,；;])\s*"
            r"(?P<body>(?:所述|該|该)\s*[^，,；;。]*?"
            r"(?:包括|包含|具有|設有|设有|配置有|連接|连接|形成|界定|"
            r"用於|用于).*)$",
            tail,
        )
        if details is not None:
            body = re.sub(
                r"^(?:該|该)\s*", "所述", details.group("body"), count=1
            )
            return _ContentParagraph(paragraph.source_text, body)

    warnings.append(
        "權利要求 1 未找到可安全銜接的「所述……」元件敘述；"
        "已保留原內容，請人工確認。"
    )
    return paragraph


def _build_source_content(
    parsed: ParsedSpecification, warnings: list[str] | None = None
) -> list[_ContentParagraph]:
    """Keep source disclosure and insert claims 2 onward before benefits.

    Consume only the two source sentences used for the fixed introduction,
    even when a Word paragraph also contains other disclosure. Claim 1 is not
    copied. Later claims use the established narrative rules; terminology is
    applied later, once per paragraph, before stripping dependent connectors.
    """
    owner = "本發明" if parsed.kind == "invention" else "本實用新型"
    fixed_intro = _fixed_source_intro(parsed, owner)
    remaining_intro = list(fixed_intro or ())
    body: list[_ContentParagraph] = []
    benefits: list[_ContentParagraph] = []
    for original in parsed.invention_content:
        text = _normalize_patent_structure(original)
        if not text:
            continue
        benefit = _BENEFIT_PREFIX.search(text)
        if benefit is not None:
            benefit_source = text[benefit.start():]
            colon = "：" if benefit.group().endswith(("：", ":")) else ""
            benefits.append(_ContentParagraph(
                benefit_source,
                f"{owner}的有益效果在於{colon}{text[benefit.end():]}",
            ))
            text = text[:benefit.start()]

        retained = []
        for match in _CONTENT_SENTENCE.finditer(text):
            intro = next((
                item for item in remaining_intro
                if match.group().strip() == item.source_text
            ), None)
            if intro is not None:
                remaining_intro.remove(intro)
            else:
                retained.append(match.group())
        retained_text = "".join(retained).strip()
        if retained_text:
            body.append(_ContentParagraph(
                retained_text,
                _normalize_content_purpose_owner(retained_text, owner),
            ))
    claim_warnings = warnings if warnings is not None else []
    appended_claims = [
        _claim_content_paragraph(claim, index, owner, False, claim_warnings)
        for index, claim in enumerate(parsed.claims[1:], 2)
    ]
    return [*(fixed_intro or ()), *body, *appended_claims, *benefits]


def _build_invention_content(
    parsed: ParsedSpecification, warnings: list[str], *, preserve_edited_claims: bool = False
) -> list[_ContentParagraph]:
    """Legacy claims-to-content backend, no longer used by normal conversion.

    Company-standard documents use the exact 因此/於是 source sentences, then
    claim narratives, then the original benefit. Legacy documents lacking
    either fixed marker keep the earlier lossless behavior: additional
    disclosure is retained and exact claim duplicates are emitted only once.
    """

    owner = "本發明" if parsed.kind == "invention" else "本實用新型"
    originals: list[_ContentParagraph] = []
    benefits: list[_ContentParagraph] = []
    for original in parsed.invention_content:
        text = _normalize_patent_structure(original)
        if not text:
            continue
        benefit = _BENEFIT_PREFIX.search(text)
        if benefit:
            # Sources may put the purpose/technical text and the benefit in
            # one Word paragraph. Move just the explicitly labelled benefit.
            if text[: benefit.start()].strip():
                before = text[: benefit.start()].strip()
                originals.append(_ContentParagraph(
                    before, _normalize_content_purpose_owner(before, owner)
                ))
            benefit_source = text[benefit.start() :]
            colon = "：" if benefit.group().endswith(("：", ":")) else ""
            benefits.append(
                _ContentParagraph(
                    benefit_source,
                    f"{owner}的有益效果在於{colon}{text[benefit.end():]}",
                )
            )
        else:
            originals.append(_ContentParagraph(
                original, _normalize_content_purpose_owner(text, owner)
            ))

    fixed_intro = _fixed_source_intro(parsed, owner)
    if fixed_intro is not None:
        claim_paragraphs = [
            _claim_content_paragraph(
                claim, index, owner, False, warnings,
                preserve_edited_text=preserve_edited_claims,
            )
            for index, claim in enumerate(parsed.claims, 1)
        ]
        if claim_paragraphs:
            claim_paragraphs[0] = _fixed_claim_one_component_details(
                claim_paragraphs[0], warnings
            )
        return [*fixed_intro, *claim_paragraphs, *benefits]

    has_purpose = any(_PURPOSE_PREFIX.match(item.text) for item in originals)
    claim_paragraphs = [
        _claim_content_paragraph(
            claim, index, owner, index == 1 and not has_purpose, warnings,
            preserve_edited_text=preserve_edited_claims,
        )
        for index, claim in enumerate(parsed.claims, 1)
    ]
    if not originals:
        return claim_paragraphs + benefits
    consumed: set[int] = set()
    for claim_index, claim in enumerate(claim_paragraphs):
        key = _content_equivalence_key(
            _adapt_existing_dependent_narrative(claim.text) if claim.dependent else claim.text
        )
        raw_claim_key = _content_exact_key(claim.source_text)
        narrative_matches: list[_ContentParagraph] = []
        for original_index, original in enumerate(originals):
            narrative = _ContentParagraph(
                original.source_text, original.text, dependent=claim.dependent,
            )
            equivalent = _content_equivalence_key(
                _adapt_existing_dependent_narrative(narrative.text)
                if claim.dependent else narrative.text
            )
            equivalent = equivalent == key
            raw_match = _content_exact_key(original.text) == raw_claim_key
            if not equivalent and not raw_match:
                continue
            consumed.add(original_index)
            # A source content paragraph may still be an unadapted claim.
            # Consume that exact duplicate, but keep the new narrative text
            # instead of restoring its old dependency/reference opening.
            if equivalent and (
                _PURPOSE_PREFIX.match(original.text)
                or re.match(r"^" + _PATENT_OWNER + r"(?:之|的)", original.text)
            ):
                narrative_matches.append(narrative)
        if narrative_matches:
            # Purpose takes precedence independent of source ordering. A
            # later equivalent narrative must never swallow the purpose.
            claim_paragraphs[claim_index] = next(
                (item for item in narrative_matches if _PURPOSE_PREFIX.match(item.text)),
                narrative_matches[0],
            )

    remaining = [item for index, item in enumerate(originals) if index not in consumed]
    purposes = [item for item in remaining if _PURPOSE_PREFIX.match(item.text)]
    additional = [item for item in remaining if not _PURPOSE_PREFIX.match(item.text)]
    return purposes + claim_paragraphs + additional + benefits


def _set_paragraph_text(paragraph: ET.Element, text: str) -> None:
    paragraph_properties = paragraph.find("w:pPr", NS)
    first_run_properties = paragraph.find("w:r/w:rPr", NS)

    for child in list(paragraph):
        if child is not paragraph_properties:
            paragraph.remove(child)

    run = ET.Element(_w("r"))
    if first_run_properties is not None:
        run.append(deepcopy(first_run_properties))
    text_node = ET.SubElement(run, _w("t"))
    if text[:1].isspace() or text[-1:].isspace():
        text_node.set(f"{{{XML_NS}}}space", "preserve")
    text_node.text = text
    paragraph.append(run)


def _remove_section_break(paragraph: ET.Element) -> None:
    paragraph_properties = paragraph.find("w:pPr", NS)
    if paragraph_properties is None:
        return
    section_properties = paragraph_properties.find("w:sectPr", NS)
    if section_properties is not None:
        paragraph_properties.remove(section_properties)


def _set_first_line_indent_chars(paragraph: ET.Element, chars: int) -> None:
    """Set a Word paragraph's first-line indent in hundredths of a character.

    Keep the matching twip value in step with the template so applications that
    do not honor ``firstLineChars`` still render the same visual indentation.
    """
    paragraph_properties = paragraph.find("w:pPr", NS)
    if paragraph_properties is None:
        paragraph_properties = ET.Element(_w("pPr"))
        paragraph.insert(0, paragraph_properties)

    indentation = paragraph_properties.find("w:ind", NS)
    if indentation is None:
        indentation = ET.SubElement(paragraph_properties, _w("ind"))

    first_line_chars = _w("firstLineChars")
    first_line = _w("firstLine")
    try:
        old_chars = int(indentation.get(first_line_chars, ""))
        old_twips = int(indentation.get(first_line, ""))
    except ValueError:
        old_chars = old_twips = 0

    indentation.attrib.pop(_w("hanging"), None)
    indentation.attrib.pop(_w("hangingChars"), None)
    indentation.set(first_line_chars, str(chars))
    if old_chars > 0 and old_twips >= 0:
        indentation.set(first_line, str(round(old_twips * chars / old_chars)))
    else:
        indentation.attrib.pop(first_line, None)


def _replace_slot(
    body: ET.Element,
    marker: str,
    texts: list[str],
    *,
    first_line_indent_chars: int | None = None,
) -> None:
    target: ET.Element | None = None
    for paragraph in body.findall("w:p", NS):
        if _normalized(marker) in _normalized(_paragraph_text(paragraph)):
            target = paragraph
            break
    if target is None:
        raise ConversionError(f"大陸範本缺少插槽：{marker}")

    output_texts = texts or [""]
    insertion_index = list(body).index(target)
    replacements: list[ET.Element] = []
    for item in output_texts:
        clone = deepcopy(target)
        _set_paragraph_text(clone, item)
        if first_line_indent_chars is not None:
            _set_first_line_indent_chars(clone, first_line_indent_chars)
        replacements.append(clone)

    # A section break attached to the placeholder belongs on the final inserted
    # paragraph only. Repeating it would create spurious blank pages.
    for replacement in replacements[:-1]:
        _remove_section_break(replacement)

    body.remove(target)
    for offset, replacement in enumerate(replacements):
        body.insert(insertion_index + offset, replacement)


def _copy_template_with_document_xml(template: Path, output: Path, document_xml: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=output.stem + "-", suffix=".docx", dir=output.parent, delete=False
    ) as handle:
        temporary_path = Path(handle.name)

    try:
        with zipfile.ZipFile(template, "r") as source_package, zipfile.ZipFile(
            temporary_path, "w"
        ) as target_package:
            for info in source_package.infolist():
                payload = document_xml if info.filename == "word/document.xml" else source_package.read(info.filename)
                target_package.writestr(info, payload)
        os.replace(temporary_path, output)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _validate_output(output: Path) -> list[str]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(output, "r") as package:
            bad_entry = package.testzip()
            document_xml = package.read("word/document.xml")
            visible_xml_parts = [
                package.read(name)
                for name in package.namelist()
                if name.startswith("word/")
                and name.endswith(".xml")
                and ("header" in name or name == "word/document.xml")
            ]
            if bad_entry:
                errors.append(f"輸出 DOCX 的 ZIP 項目損壞：{bad_entry}")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        return [f"輸出 DOCX 無法重新開啟：{exc}"]

    text = _parse_xml(document_xml)
    all_text = "".join(
        node.text or ""
        for payload in visible_xml_parts
        for node in _parse_xml(payload).findall(".//w:t", NS)
    )
    if "...開始段落" in all_text:
        errors.append("輸出仍含有範本開始段落標記。")
    compact_text = _normalized(all_text)
    for expected in ("说明书摘要", "权利要求书", "说明书", "技术领域", "背景技术"):
        if _normalized(expected) not in compact_text:
            errors.append(f"輸出缺少必要文字：{expected}")
    return errors


_EDITED_CLAIM_NUMBER = re.compile(
    r"^\s*(?P<number>[0-9０-９]+)\s*(?:[.．](?![0-9０-９])|、)\s*(?P<body>.*)$"
)


def _group_edited_claims(lines: list[str], *, require_numbering: bool = False) -> list[str]:
    """Join explicit claim continuations without treating 1.5 V as claim 1."""

    nonempty = [line.strip() for line in lines if line.strip()]
    if not nonempty:
        raise ConversionError("編輯預覽的「權利要求書」不能為空白。")
    numbered = any(_EDITED_CLAIM_NUMBER.match(line) for line in nonempty)
    if not numbered and not require_numbering:
        return nonempty
    if not _EDITED_CLAIM_NUMBER.match(nonempty[0]):
        raise ConversionError("權利要求必須由「1.」開始並依序編號；請保留項次後再更新內容。")

    claims: list[str] = []
    current: list[str] = []
    expected = 1
    for line in nonempty:
        marker = _EDITED_CLAIM_NUMBER.match(line)
        if marker:
            number = int(marker.group("number"))
            if number != expected:
                raise ConversionError(f"權利要求項次不連續或重複：預期第 {expected} 項，實際為第 {number} 項。")
            if current:
                if not current[-1].endswith(("。", ".", "．")):
                    raise ConversionError("權利要求分行或項次無法安全區分；請確認前一項已完整結束後再更新內容。")
                claims.append(" ".join(current))
            elif expected > 1:
                raise ConversionError(f"權利要求 {expected - 1} 為空白，請補齊後再更新內容。")
            current = [marker.group("body")] if marker.group("body") else []
            expected += 1
        else:
            if current and current[-1].endswith(("。", ".", "．")):
                raise ConversionError("權利要求完整句後出現未編號文字；請確認項次及分行後再更新內容。")
            current.append(line)
    if not current:
        raise ConversionError(f"權利要求 {expected - 1} 為空白，請補齊後再更新內容。")
    if require_numbering and not current[-1].endswith(("。", ".", "．")):
        raise ConversionError("最後一項權利要求尚未完整結束，請補齊後再更新內容。")
    claims.append(" ".join(current))
    return claims


def parse_edited_preview_text(text: str, specification_kind: str) -> ParsedSpecification:
    """Turn the plain-text preview back into the template's section fields.

    The visible headings are intentional editing boundaries.  Requiring them
    to remain in order prevents an accidental deletion in the editor from
    silently placing text in the wrong part of the formal Word document.
    Both character variants are accepted so pasted headings remain usable.
    """

    heading_aliases = _EDITABLE_HEADING_ALIASES
    alias_to_key = {
        alias: key
        for key, aliases in heading_aliases
        for alias in aliases
    }
    expected_keys = [key for key, _aliases in heading_aliases]
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    positions: dict[str, int] = {}
    encountered: list[str] = []
    for index, line in enumerate(lines):
        key = alias_to_key.get(line.strip())
        if key is None:
            continue
        if key in positions:
            raise ConversionError(f"編輯預覽中的區段標題重複：{line.strip()}")
        positions[key] = index
        encountered.append(key)

    missing = [key for key in expected_keys if key not in positions]
    if missing:
        labels = dict(EDITABLE_PREVIEW_HEADINGS)
        missing_labels = "、".join(labels[key] for key in missing)
        raise ConversionError(
            f"編輯預覽缺少必要區段標題：{missing_labels}。"
            "請復原標題後再轉換。"
        )
    if encountered != expected_keys:
        raise ConversionError("編輯預覽的區段標題順序已改變，請復原後再轉換。")

    section_lines: dict[str, list[str]] = {}
    for sequence, key in enumerate(expected_keys):
        start = positions[key] + 1
        end = (
            positions[expected_keys[sequence + 1]]
            if sequence + 1 < len(expected_keys)
            else len(lines)
        )
        section_lines[key] = [
            line.strip() for line in lines[start:end] if line.strip()
        ]

    title_lines = section_lines["specification"]
    if len(title_lines) != 1:
        raise ConversionError("編輯預覽的「說明書」標題下必須保留且僅保留一行申請標的名稱。")

    claims = _group_edited_claims(section_lines["claims"])

    return ParsedSpecification(
        kind=specification_kind,
        title=title_lines[0],
        abstract=section_lines["abstract"],
        technology_field=section_lines["technology_field"],
        background=section_lines["background"],
        invention_content=section_lines["invention_content"],
        drawing_description=section_lines["drawing_description"],
        embodiments=section_lines["embodiments"],
        claims=claims,
    )


def replace_content_from_edited_claims(
    text: str,
    specification_kind: str,
    previous_claims: Iterable[str] | None = None,
    claim_history: Iterable[Iterable[str]] | None = None,
    fixed_content_layout: bool = False,
) -> EditedClaimsContentReplacement:
    """Replace claim-derived content while retaining all other content lines.

    ``previous_claims`` is the last claims snapshot whose generated narratives
    are currently present in the editor. It lets the UI replace revised or
    deleted claim copies without guessing that unrelated disclosure or benefit
    paragraphs belong to a claim. The action never reruns terminology rules or
    character conversion. For previews built from the company-standard fixed
    content layout, the first two source sentences and the final benefit
    paragraph are structural boundaries; every paragraph between them is the
    replaceable claim copy.
    """

    if specification_kind not in {"invention", "utility_model"}:
        raise ConversionError("無法確認發明或實用新型類型，請重新載入預覽後再更新內容。")
    parsed = parse_edited_preview_text(text, specification_kind)
    aliases = {alias: key for key, values in _EDITABLE_HEADING_ALIASES for alias in values}
    positions: dict[str, tuple[int, int]] = {}
    offset = 0
    for line in text.splitlines(keepends=True):
        key = aliases.get(line.strip())
        if key is not None:
            positions[key] = (offset, offset + len(line))
        offset += len(line)
    keys = [key for key, _values in _EDITABLE_HEADING_ALIASES]
    spans = {
        key: (
            positions[key][1],
            positions[keys[index + 1]][0] if index + 1 < len(keys) else len(text),
        )
        for index, key in enumerate(keys)
    }
    content_heading = text[
        positions["invention_content"][0]:positions["invention_content"][1]
    ].strip()
    expected_content_headings = (
        {"發明內容", "发明内容"}
        if specification_kind == "invention"
        else {"實用新型內容", "实用新型内容"}
    )
    if content_heading not in expected_content_headings:
        expected = "發明內容" if specification_kind == "invention" else "實用新型內容"
        raise ConversionError(f"文件類型與「{content_heading}」標題不一致；應為「{expected}」，未更新內容。")
    claims_start, claims_end = spans["claims"]
    claims = _group_edited_claims(text[claims_start:claims_end].splitlines(), require_numbering=True)
    for index, claim in enumerate(claims, 1):
        normalized = _normalize_patent_structure(claim)
        if not _PURPOSE_PREFIX.match(normalized) and "，" not in normalized:
            raise ConversionError(f"權利要求 {index} 無法安全區分標的與技術內容；未更新內容。")
    warnings: list[str] = []
    paragraphs = _build_invention_content(
        replace(parsed, claims=claims, invention_content=[]), warnings,
        preserve_edited_claims=True,
    )
    if warnings:
        raise ConversionError("無法安全更新內容；原文未變更。\n" + "\n".join(warnings))
    rendered = [
        _adapt_existing_dependent_narrative(item.text) if item.dependent else item.text
        for item in paragraphs
    ]
    content_start, content_end = spans["invention_content"]
    heading = text[positions["invention_content"][0]:content_start]
    newline = "\r\n" if heading.endswith("\r\n") else "\r" if heading.endswith("\r") else "\n"

    # Generated previews are unnumbered. For a consistently numbered pasted
    # description, retain its bracket style and edit only later number tokens.
    paragraph_number = re.compile(
        r"^(?P<indent>[ \t]*)(?P<label>(?:【(?P<cjk_digits>[0-9０-９]{1,4})】|\[(?P<ascii_digits>[0-9０-９]{1,4})\]))(?P<space>[ \t]*)"
    )

    def number_value(match: re.Match[str]) -> int:
        return int(match.group("cjk_digits") or match.group("ascii_digits"))

    def number_label(match: re.Match[str], number: int) -> str:
        original = match.group("label")
        digits = match.group("cjk_digits") or match.group("ascii_digits")
        # Preserve the author's bracket and zero-padding convention. Width is
        # a minimum, so 【9】 correctly progresses to 【10】 rather than failing.
        rendered_digits = f"{number:0{len(digits)}d}"
        if all("０" <= character <= "９" for character in digits):
            rendered_digits = rendered_digits.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
        return f"{original[0]}{rendered_digits}{original[-1]}"
    description_lines: list[tuple[int, int, str, re.Match[str] | None]] = []
    for key in ("technology_field", "background", "invention_content", "drawing_description", "embodiments"):
        start, end = spans[key]
        line_offset = start
        for line in text[start:end].splitlines(keepends=True):
            if line.strip():
                description_lines.append((line_offset, line_offset + len(line), line, paragraph_number.match(line)))
            line_offset += len(line)
    numbered_lines = [item for item in description_lines if item[3] is not None]
    content_lines = [item for item in description_lines if content_start <= item[0] < content_end]

    def line_body(item: tuple[int, int, str, re.Match[str] | None]) -> str:
        _start, _end, line, match = item
        body = line[match.end():] if match is not None else line
        return body.rstrip("\r\n").strip()

    def narrative_variants(old_claims: Iterable[str]) -> list[set[str]]:
        owner = "本發明" if specification_kind == "invention" else "本實用新型"
        results: list[set[str]] = []
        for index, claim in enumerate(old_claims, 1):
            candidates: set[str] = set()
            local_warnings: list[str] = []
            modes = (True, False) if index == 1 else (False,)
            for needs_purpose in modes:
                item = _claim_content_paragraph(
                    claim, index, owner, needs_purpose, local_warnings,
                    preserve_edited_text=True,
                )
                candidate = (
                    _adapt_existing_dependent_narrative(item.text)
                    if item.dependent else item.text
                )
                candidates.add(_content_claim_match_key(candidate))
                if item.dependent and "，" in candidate:
                    candidates.add(_content_claim_match_key(candidate.replace("，", "，，", 1)))
            # Recognise previews created before claim 1's feature connector was
            # removed from content, so the first update also cleans that copy.
            if index == 1:
                candidates.add(_content_claim_match_key(f"{owner}的目的在於提供{claim.strip()}"))
            results.append(candidates)
        return results

    baselines = [tuple(items) for items in (claim_history or ())]
    if previous_claims is not None:
        baselines.append(tuple(previous_claims))
    baselines.append(tuple(claims))
    candidate_groups: list[set[str]] = []
    for baseline in baselines:
        for index, variants in enumerate(narrative_variants(baseline)):
            if index == len(candidate_groups):
                candidate_groups.append(set())
            candidate_groups[index].update(variants)
    matched_lines: list[tuple[int, int, str, re.Match[str] | None]] = []
    used_starts: set[int] = set()
    for candidates in candidate_groups:
        match = next((
            item for item in content_lines
            if item[0] not in used_starts and _content_claim_match_key(line_body(item)) in candidates
        ), None)
        if match is not None:
            matched_lines.append(match)
            used_starts.add(match[0])

    # A separately authored purpose paragraph is not a claim and must remain.
    # In that case claim 1 uses the ordinary owner/subject narrative form so
    # the update does not introduce a second competing purpose paragraph.
    retained_has_purpose = any(
        item[0] not in used_starts
        and _PURPOSE_PREFIX.match(_normalize_patent_structure(line_body(item)))
        for item in content_lines
    )
    if retained_has_purpose or fixed_content_layout:
        claim_items = [
            _claim_content_paragraph(
                claim, index, "本發明" if specification_kind == "invention" else "本實用新型",
                False, warnings, preserve_edited_text=True,
            )
            for index, claim in enumerate(claims, 1)
        ]
        if fixed_content_layout and claim_items:
            claim_items[0] = _fixed_claim_one_component_details(
                claim_items[0], warnings
            )
        rendered = [
            _adapt_existing_dependent_narrative(item.text) if item.dependent else item.text
            for item in claim_items
        ]

    benefit_line = next((
        item for item in content_lines
        if _BENEFIT_PREFIX.search(_normalize_patent_structure(line_body(item)))
    ), None)
    fixed_claim_insertion_start: int | None = None
    if fixed_content_layout:
        if len(content_lines) < 2:
            raise ConversionError("固定發明／實用新型內容的來源前兩段已缺失；未更新內容。")
        if not _PURPOSE_PREFIX.match(
            _normalize_patent_structure(line_body(content_lines[0]))
        ):
            raise ConversionError("固定發明／實用新型內容的目的段已變更或缺失；未更新內容。")
        if benefit_line is content_lines[1]:
            raise ConversionError("固定發明／實用新型內容的第二來源段已缺失；未更新內容。")

        fixed_claim_insertion_start = content_lines[1][1]
        claim_limit = (
            content_lines.index(benefit_line)
            if benefit_line is not None
            else len(content_lines)
        )
        matched_lines = list(content_lines[2:claim_limit])
        used_starts = {item[0] for item in matched_lines}

    if matched_lines:
        insertion_start = min(item[0] for item in matched_lines)
        first_line = next(item for item in matched_lines if item[0] == insertion_start)
        insertion_end = first_line[1]
    elif fixed_claim_insertion_start is not None:
        insertion_start = fixed_claim_insertion_start
        insertion_end = insertion_start
    else:
        insertion_start = benefit_line[0] if benefit_line is not None else content_start
        insertion_end = insertion_start

    label_edits: list[tuple[int, int, str]] = []
    if numbered_lines:
        if len(numbered_lines) != len(description_lines):
            raise ConversionError("說明書段落編號格式不一致，請統一編號或移除編號後再更新內容。")
        numbers = [number_value(match) for _start, _end, _line, match in numbered_lines]
        if numbers != list(range(numbers[0], numbers[0] + len(numbers))) or numbers[0] < 1:
            raise ConversionError("說明書段落編號不連續或重複，請修正後再更新內容。")
        numbered_content = [item for item in numbered_lines if content_start <= item[0] < content_end]
        if not numbered_content:
            raise ConversionError("原內容沒有可沿用的段落編號，請先補齊或移除編號。")
        insertion_line = next(
            (item for item in numbered_content if item[0] >= insertion_start),
            numbered_content[-1],
        )
        first_number = number_value(insertion_line[3])
        if insertion_start > insertion_line[0]:
            first_number += 1
        number_style = insertion_line[3]
        delta = len(rendered) - len(matched_lines)
        if first_number + len(rendered) - 1 > 9999 or numbers[-1] + delta > 9999:
            raise ConversionError("更新後段落編號超過四位數範圍，請人工調整。")
        rendered = [
            f"{number_style.group('indent')}{number_label(number_style, first_number + index)}{number_style.group('space')}{body}"
            for index, body in enumerate(rendered)
        ]
        for start, _end, _line, match in numbered_lines:
            if start >= insertion_start and start not in used_starts and delta:
                label = match.group("label")
                number = number_value(match) + delta
                label_edits.append((
                    start + match.start("label"), start + match.end("label"),
                    number_label(match, number),
                ))
    replacement_text = newline.join(rendered) + newline
    claim_edits: list[tuple[int, int, str]] = [
        (insertion_start, insertion_end, replacement_text)
    ]
    claim_edits.extend(
        (start, end, "")
        for start, end, _line, _match in matched_lines
        if start != insertion_start
    )
    edits = tuple(sorted((*claim_edits, *label_edits), key=lambda edit: edit[0]))
    updated = text
    for start, end, replacement_text_part in reversed(edits):
        updated = updated[:start] + replacement_text_part + updated[end:]
    if updated == text:
        edits = ()
    return EditedClaimsContentReplacement(
        text=updated,
        warnings=tuple(warnings),
        claim_count=len(claims),
        content_start=insertion_start,
        content_end=insertion_end,
        replacement_text=replacement_text,
        edits=edits,
    )


def build_conversion_preview(
    source_path: str | os.PathLike[str],
    template_key: str = "beijing_taiji",
    terminology_path: str | os.PathLike[str] | None = None,
    terminology_pairs: Iterable[tuple[object, object]] | None = None,
    apply_terminology: bool = True,
    insert_feature_phrase: bool = True,
    traditional_characters: bool = False,
) -> ConversionPreview:
    """Build a text-only mainland-format preview without creating an output file.

    Each preview paragraph retains its Taiwan source text solely for calculating
    changed spans in the UI.  The UI renders only ``converted_text`` so no
    Taiwan-format duplicate is exposed in the preview pane.
    """

    source = Path(source_path).resolve()
    if template_key not in available_templates():
        raise ConversionError(f"未知的大陸範本：{template_key}")
    if not source.is_file():
        raise ConversionError(f"找不到來源文件：{source}")
    if source.suffix.lower() not in {".doc", ".docx"}:
        raise ConversionError("台灣專利說明書僅支援 .doc 或 .docx。")
    if terminology_path is not None and terminology_pairs is not None:
        raise ConversionError("用語規則不可同時指定檔案與編輯內容。")

    prepared_source, temporary_source = _prepare_docx(source)
    try:
        parsed = parse_taiwan_specification(prepared_source)
    finally:
        if temporary_source is not None:
            temporary_source.cleanup()

    terminology = (
        normalize_terminology_pairs(terminology_pairs)
        if terminology_pairs is not None
        else _load_terminology(
            Path(terminology_path).resolve() if terminology_path else None
        )
    )
    counts: Counter[str] = Counter()
    warnings: list[str] = []
    paragraphs: list[ConversionPreviewParagraph] = []
    preview_claims = _claims_with_source_components(parsed, warnings)
    preview_parsed = replace(parsed, claims=preview_claims)

    def display_text(text: str) -> str:
        return _windows_traditional_chinese(text) if traditional_characters else text

    def add_heading(text: str) -> None:
        paragraphs.append(
            ConversionPreviewParagraph(text, display_text(text), role="heading")
        )

    def add_section(heading: str, items: Iterable[str | _ContentParagraph]) -> None:
        add_heading(heading)
        for item in items:
            source_text = item.source_text if isinstance(item, _ContentParagraph) else item
            conversion_text = item.text if isinstance(item, _ContentParagraph) else item
            if not conversion_text.strip():
                continue
            paragraphs.append(
                ConversionPreviewParagraph(
                    source_text,
                    display_text(
                        _convert_content_paragraph(item, terminology, counts, apply_terminology)
                        if isinstance(item, _ContentParagraph)
                        else _convert_text(conversion_text, terminology, counts, apply_terminology)
                    ),
                )
            )

    add_section("說明書摘要" if traditional_characters else "说明书摘要", parsed.abstract)
    add_heading("權利要求書" if traditional_characters else "权利要求书")
    for index, (source_claim, claim) in enumerate(
        zip(parsed.claims, preview_claims), 1
    ):
        paragraphs.append(
            ConversionPreviewParagraph(
                source_claim,
                display_text(
                    _convert_claim(
                        claim,
                        index,
                        terminology,
                        counts,
                        apply_terminology,
                        insert_feature_phrase,
                        warnings,
                    )
                ),
                prefix=f"{index}. ",
                role="claim",
            )
        )

    add_heading("說明書" if traditional_characters else "说明书")
    paragraphs.append(
        ConversionPreviewParagraph(
            parsed.title,
            display_text(
                _convert_text(parsed.title, terminology, counts, apply_terminology)
            ),
            role="title",
        )
    )
    add_section("技術領域" if traditional_characters else "技术领域", parsed.technology_field)
    add_section("背景技術" if traditional_characters else "背景技术", parsed.background)
    add_section(
        (
            "發明內容" if parsed.kind == "invention" else EDITABLE_UTILITY_MODEL_CONTENT_HEADING
        )
        if traditional_characters
        else ("发明内容" if parsed.kind == "invention" else "实用新型内容"),
        _build_source_content(preview_parsed, warnings),
    )
    add_section("附圖說明" if traditional_characters else "附图说明", parsed.drawing_description)
    add_section("具體實施方式" if traditional_characters else "具体实施方式", parsed.embodiments)

    if parsed.table_count:
        warnings.append(
            f"來源含 {parsed.table_count} 個表格；文字預覽不顯示表格，請在輸出文件中人工核對。"
        )
    if parsed.drawing_count:
        warnings.append(
            f"來源含 {parsed.drawing_count} 個內嵌圖形；文字預覽不顯示圖形。"
        )
    if parsed.equation_count:
        warnings.append(
            f"來源含 {parsed.equation_count} 個 Word 方程式；文字預覽僅顯示其可見文字。"
        )

    return ConversionPreview(
        source=str(source),
        specification_kind=parsed.kind,
        paragraphs=paragraphs,
        replacement_counts=dict(sorted(counts.items())),
        warnings=warnings,
    )


def convert_document(
    source_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    template_key: str = "beijing_taiji",
    terminology_path: str | os.PathLike[str] | None = None,
    terminology_pairs: Iterable[tuple[object, object]] | None = None,
    apply_terminology: bool = True,
    insert_feature_phrase: bool = True,
    edited_preview_text: str | None = None,
) -> ConversionReport:
    source = Path(source_path).resolve()
    output = Path(output_path).resolve()
    templates = available_templates()
    if template_key not in templates:
        raise ConversionError(f"未知的大陸範本：{template_key}")
    template = templates[template_key]

    report = ConversionReport(str(source), str(output), str(template))
    if not source.is_file():
        raise ConversionError(f"找不到來源文件：{source}")
    if source.suffix.lower() not in {".doc", ".docx"}:
        raise ConversionError("台灣專利說明書僅支援 .doc 或 .docx。")
    if source == output or template.resolve() == output:
        raise ConversionError("輸出檔不得覆蓋來源文件或內建範本。")
    if output.suffix.lower() != ".docx":
        raise ConversionError("輸出檔名必須使用 .docx。")
    if not template.is_file():
        raise ConversionError(f"找不到內建大陸範本：{template}")

    prepared_source, temporary_source = _prepare_docx(source)
    try:
        parsed = parse_taiwan_specification(prepared_source)
    finally:
        if temporary_source is not None:
            temporary_source.cleanup()
    report.specification_kind = parsed.kind

    if parsed.table_count:
        report.warnings.append(
            f"來源含 {parsed.table_count} 個表格；本整合版不會自動搬移表格，請人工核對。"
        )
    if parsed.drawing_count:
        report.warnings.append(
            f"來源含 {parsed.drawing_count} 個內嵌圖形；請另行準備並校驗 CNIPA 的說明書附圖 XML。"
        )
    if parsed.equation_count:
        report.warnings.append(
            f"來源含 {parsed.equation_count} 個 Word 方程式；本整合版僅轉換其所在段落的可見文字。"
        )

    if terminology_path is not None and terminology_pairs is not None:
        raise ConversionError("用語規則不可同時指定檔案與編輯內容。")
    terminology = (
        normalize_terminology_pairs(terminology_pairs)
        if terminology_pairs is not None
        else _load_terminology(
            Path(terminology_path).resolve() if terminology_path else None
        )
    )
    counts: Counter[str] = Counter()

    def convert_paragraphs(items: list[str]) -> list[str]:
        return [
            _convert_text(item, terminology, counts, apply_terminology)
            for item in items
            if item.strip()
        ]

    if edited_preview_text is not None:
        output_content = parse_edited_preview_text(
            edited_preview_text,
            parsed.kind,
        )
        # The preview has already gone through the ordered terminology and
        # patent-language pipeline.  User edits are authoritative: do not run
        # those substitutions a second time.  Only change character form for
        # the formal mainland document.
        converted_sections = {
            "abstract": [
                _windows_simplified_chinese(item)
                for item in output_content.abstract
            ],
            "technology_field": [
                _windows_simplified_chinese(item)
                for item in output_content.technology_field
            ],
            "background": [
                _windows_simplified_chinese(item)
                for item in output_content.background
            ],
            "invention_content": [
                _windows_simplified_chinese(item)
                for item in output_content.invention_content
            ],
            "drawing_description": [
                _windows_simplified_chinese(item)
                for item in output_content.drawing_description
            ],
            "embodiments": [
                _windows_simplified_chinese(item)
                for item in output_content.embodiments
            ],
        }
        converted_title = _windows_simplified_chinese(output_content.title)
        converted_claims = [
            _windows_simplified_chinese(item) for item in output_content.claims
        ]
    else:
        prepared_claims = _claims_with_source_components(parsed, report.warnings)
        prepared_parsed = replace(parsed, claims=prepared_claims)
        content_paragraphs = _build_source_content(prepared_parsed, report.warnings)
        output_content = replace(
            prepared_parsed,
            invention_content=[item.text for item in content_paragraphs],
        )
        converted_sections = {
            "abstract": convert_paragraphs(output_content.abstract),
            "technology_field": convert_paragraphs(output_content.technology_field),
            "background": convert_paragraphs(output_content.background),
            "invention_content": [
                _convert_content_paragraph(item, terminology, counts, apply_terminology)
                for item in content_paragraphs if item.text.strip()
            ],
            "drawing_description": convert_paragraphs(output_content.drawing_description),
            "embodiments": convert_paragraphs(output_content.embodiments),
        }
        converted_title = _convert_text(
            output_content.title, terminology, counts, apply_terminology
        )
        converted_claims = [
            _convert_claim(
                text,
                index,
                terminology,
                counts,
                apply_terminology,
                insert_feature_phrase,
                report.warnings,
            )
            for index, text in enumerate(output_content.claims, 1)
        ]

    report.claim_count = len(output_content.claims)
    report.sections_found = {
        "摘要": len(output_content.abstract),
        "技術領域": len(output_content.technology_field),
        "背景技術": len(output_content.background),
        "發明內容": len(output_content.invention_content),
        "圖式簡單說明": len(output_content.drawing_description),
        "實施方式": len(output_content.embodiments),
        "權利要求": len(output_content.claims),
    }

    with zipfile.ZipFile(template, "r") as package:
        root = _parse_xml(package.read("word/document.xml"))
    body = root.find("w:body", NS)
    if body is None:
        raise ConversionError("大陸範本缺少 w:body。")

    _replace_slot(body, TEMPLATE_SLOTS["abstract"], converted_sections["abstract"])
    _replace_slot(body, TEMPLATE_SLOTS["claims"], converted_claims)
    _replace_slot(
        body,
        TEMPLATE_SLOTS["title"],
        [converted_title],
    )
    specification_slots = (
        "technology_field",
        "background",
        "invention_content",
        "drawing_description",
        "embodiments",
    )
    for section_name in specification_slots:
        _replace_slot(
            body,
            TEMPLATE_SLOTS[section_name],
            converted_sections[section_name],
            first_line_indent_chars=300,
        )

    if output_content.kind == "utility_model":
        for paragraph in body.findall("w:p", NS):
            if _normalized(_paragraph_text(paragraph)) in {"發明內容", "发明内容"}:
                _set_paragraph_text(paragraph, EDITABLE_UTILITY_MODEL_CONTENT_HEADING)

    # Legacy templates intentionally use Traditional Chinese slot and body
    # headings. At this point all slots are filled, so the remaining body text
    # can be converted safely without losing the marker locators.
    for text_node in root.findall(".//w:t", NS):
        if text_node.text:
            text_node.text = _windows_simplified_chinese(text_node.text)

    # ElementTree removes namespace declarations that are no longer used by an
    # element or attribute. The legacy files list several such prefixes in
    # mc:Ignorable; leaving those names undeclared makes Word report the DOCX as
    # damaged even though generic XML parsers accept it. w14 remains in use by
    # paragraph identifiers and is the only ignorable prefix needed here.
    root.set(f"{{{MC_NS}}}Ignorable", "w14")

    document_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    _copy_template_with_document_xml(template, output, document_xml)
    report.replacement_counts = dict(sorted(counts.items()))
    report.errors.extend(_validate_output(output))
    if report.errors:
        raise ConversionError("；".join(report.errors))
    return report
