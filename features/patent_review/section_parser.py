"""Detect standard Taiwan patent sections from Word paragraph headings."""

from dataclasses import dataclass
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .models import PatentParagraph, PatentSection


@dataclass(frozen=True)
class SectionDefinition:
    key: str
    title: str
    aliases: Sequence[str]
    patent_type_hint: str = ""
    level: str = "medium"


@dataclass(frozen=True)
class SectionHeadingMatch:
    key: str
    title: str
    heading_text: str
    remainder: str
    patent_type_hint: str = ""
    level: str = "medium"


SECTION_DEFINITIONS: Tuple[SectionDefinition, ...] = (
    SectionDefinition(
        "major_abstract", "發明摘要", ("發明摘要",), "invention", "major"
    ),
    SectionDefinition(
        "major_abstract", "新型摘要", ("新型摘要",), "utility_model", "major"
    ),
    SectionDefinition(
        "major_description", "發明說明書", ("發明說明書",), "invention", "major"
    ),
    SectionDefinition(
        "major_description", "新型說明書", ("新型說明書",), "utility_model", "major"
    ),
    SectionDefinition(
        "claims", "發明申請專利範圍", ("發明申請專利範圍",), "invention", "major"
    ),
    SectionDefinition(
        "claims", "新型申請專利範圍", ("新型申請專利範圍",), "utility_model", "major"
    ),
    SectionDefinition(
        "invention_title", "發明名稱", ("發明名稱", "中文發明名稱"), "invention"
    ),
    SectionDefinition(
        "utility_model_title", "新型名稱", ("新型名稱", "中文新型名稱"), "utility_model"
    ),
    SectionDefinition(
        "english_title", "英文發明名稱", ("英文發明名稱",), "invention"
    ),
    SectionDefinition(
        "english_title", "英文新型名稱", ("英文新型名稱",), "utility_model"
    ),
    SectionDefinition("abstract_zh", "中文", ("中文", "中文摘要", "摘要")),
    SectionDefinition("abstract_en", "英文", ("英文", "英文摘要")),
    SectionDefinition(
        "designated_representative_drawing",
        "指定代表圖",
        ("指定代表圖",),
    ),
    SectionDefinition("technical_field", "技術領域", ("技術領域",)),
    SectionDefinition("background_art", "先前技術", ("先前技術", "背景技術")),
    SectionDefinition(
        "disclosure", "發明／新型內容", ("發明內容", "發明概要"), "invention"
    ),
    SectionDefinition(
        "disclosure", "發明／新型內容", ("新型內容", "新型概要"), "utility_model"
    ),
    SectionDefinition(
        "brief_description_of_drawings",
        "圖式簡單說明",
        ("圖式簡單說明", "圖面簡單說明"),
    ),
    SectionDefinition(
        "drawing_symbol_description", "符號說明", ("符號說明", "圖式符號說明")
    ),
    SectionDefinition(
        "representative_drawing_symbols",
        "代表圖之符號簡單說明",
        ("代表圖之符號簡單說明", "代表圖符號簡單說明"),
    ),
    SectionDefinition(
        "embodiments", "實施方式", ("實施方式", "具體實施方式", "實施例")
    ),
    SectionDefinition(
        "claims", "申請專利範圍", ("申請專利範圍", "權利要求書")
    ),
    SectionDefinition("drawings", "圖式", ("圖式", "圖面")),
)


def _heading_key(text: str) -> str:
    text = re.sub(r"\s+", "", text or "")
    text = re.sub(r"^[一二三四五六七八九十百0-9]+[、.．]", "", text)
    return text.strip("：:【】〖〗[]")


_ALIAS_MAP: Dict[str, SectionDefinition] = {
    _heading_key(alias): definition
    for definition in SECTION_DEFINITIONS
    for alias in definition.aliases
}


def split_section_heading(text: str) -> Optional[SectionHeadingMatch]:
    """Return a match only for an explicit heading, not ordinary prose."""

    stripped = (text or "").strip()
    if not stripped:
        return None

    heading_text = stripped
    remainder = ""
    bracketed = re.match(
        r"^[【〖]\s*([^】〗]+?)\s*[】〗]\s*[:：]?\s*(.*)$",
        stripped,
        flags=re.DOTALL,
    )
    if bracketed:
        candidate = bracketed.group(1)
        remainder = bracketed.group(2).strip()
    else:
        colon = re.match(
            r"^([^:：]+?)\s*[:：]\s*(.*)$",
            stripped,
            flags=re.DOTALL,
        )
        if colon:
            candidate = colon.group(1)
            remainder = colon.group(2).strip()
        else:
            candidate = stripped

    definition = _ALIAS_MAP.get(_heading_key(candidate))
    if definition is None:
        return None

    return SectionHeadingMatch(
        key=definition.key,
        title=definition.title,
        heading_text=heading_text,
        remainder=remainder,
        patent_type_hint=definition.patent_type_hint,
        level=definition.level,
    )


def assign_sections(
    paragraphs: Iterable[PatentParagraph],
) -> Tuple[List[PatentSection], str, str]:
    """Assign paragraphs in place and return sections, type, and title."""

    paragraph_list = list(paragraphs)
    sections: List[PatentSection] = []
    current: Optional[PatentSection] = None
    current_major_key = ""
    current_major_title = ""
    patent_type = "unknown"
    patent_title = ""

    for paragraph in paragraph_list:
        if paragraph.source_kind == "table":
            # Table cells inherit the surrounding section but never create a
            # new structural heading.  This keeps text such as「技術領域」in a
            # data cell from changing the document outline while preserving
            # the cell for isolated text and symbol-list checks.
            paragraph.content_text = paragraph.normalized_text.strip()
            paragraph.major_section_key = current_major_key or None
            paragraph.major_section_title = current_major_title
            if current is not None:
                paragraph.section_key = current.key
                paragraph.section_title = current.title
                current.paragraph_indices.append(paragraph.index)
            continue
        match = split_section_heading(paragraph.normalized_text)
        if match is not None:
            paragraph.is_heading = True
            if match.level == "major":
                current_major_key = match.key
                current_major_title = match.title
            paragraph.major_section_key = current_major_key or None
            paragraph.major_section_title = current_major_title
            paragraph.section_key = match.key
            paragraph.section_title = match.title
            paragraph.content_text = match.remainder
            current = PatentSection(
                key=match.key,
                title=match.title,
                heading_text=match.heading_text,
                heading_paragraph_index=paragraph.index,
                level=match.level,
                major_section_key=current_major_key,
            )
            if match.remainder:
                current.paragraph_indices.append(paragraph.index)
            sections.append(current)
            if match.patent_type_hint and patent_type == "unknown":
                # Once a document type is established, keep all subsequent
                # structural checks inside that type.  A stray heading from
                # the other template must not switch the whole review into a
                # mixed/fallback mode that reports the wrong rule set.
                patent_type = match.patent_type_hint
            if (
                match.key in {"invention_title", "utility_model_title"}
                and not patent_title
            ):
                patent_title = match.remainder
            continue

        paragraph.content_text = paragraph.normalized_text.strip()
        paragraph.major_section_key = current_major_key or None
        paragraph.major_section_title = current_major_title
        if current is not None:
            paragraph.section_key = current.key
            paragraph.section_title = current.title
            current.paragraph_indices.append(paragraph.index)
            if (
                not patent_title
                and current.key in {"invention_title", "utility_model_title"}
                and paragraph.content_text
            ):
                patent_title = paragraph.content_text

    return sections, patent_type, patent_title
