"""Locate reviewable ``一個`` tokens without changing the preview's text.

All public positions use UTF-16 code units, matching QTextCursor and
QTextDocument.contentsChange (rather than Python's Unicode character indexes).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


_SENTENCE_BREAKS = re.compile(r"[。！？!?\r\n]")
_PARAGRAPH_BREAKS = re.compile(r"\r\n|[\r\n]")
_ARTICLE = "一個"
_ARTICLE_UTF16_LENGTH = 2
ARTICLE_SECTIONS = (
    ("abstract", "說明書摘要"),
    ("claims", "權利要求書"),
    ("specification", "說明書"),
    ("invention_content", "發明/實用新型內容"),
    ("drawing_description", "附圖說明"),
)
_SECTION_HEADINGS = {
    "說明書摘要": "abstract",
    "说明书摘要": "abstract",
    "權利要求書": "claims",
    "权利要求书": "claims",
    "說明書": "specification",
    "说明书": "specification",
    "附圖說明": "drawing_description",
    "附图说明": "drawing_description",
    # These are subsections of the specification.  Listing them explicitly
    # returns the grouping to 說明書 after an 附圖說明 block.
    "技術領域": "specification",
    "技术领域": "specification",
    "背景技術": "specification",
    "背景技术": "specification",
    "發明內容": "invention_content",
    "发明内容": "invention_content",
    "新型內容": "invention_content",
    "新型内容": "invention_content",
    "實用新型內容": "invention_content",
    "实用新型内容": "invention_content",
    "具體實施方式": "specification",
    "具体实施方式": "specification",
    "實施例": "specification",
    "实施例": "specification",
    "符號說明": "specification",
    "符号说明": "specification",
}
_PRIORITY_PHRASES = (
    "其中另一個",
    "其中一個",
    "至少一個",
    "另一個",
    "第一個",
    "每一個",
    "各一個",
    "上一個",
    "下一個",
)


@dataclass(frozen=True)
class ArticleOccurrence:
    start: int
    end: int
    sentence_start: int
    sentence_end: int
    paragraph_index: int
    sentence: str
    priority_reason: str = ""
    section: str = "specification"

    @property
    def is_priority(self) -> bool:
        return bool(self.priority_reason)


def _utf16_offsets(text: str) -> list[int]:
    offsets = [0]
    for character in text:
        offsets.append(offsets[-1] + (2 if ord(character) > 0xFFFF else 1))
    return offsets


def _section_heading(paragraph: str) -> str | None:
    """Recognize only a standalone main heading, not a prose reference.

    Word headings sometimes include spacing between characters or full-width
    brackets. Normalize those presentation details without altering offsets
    in the original preview text.
    """
    heading = "".join(paragraph.split())
    if heading.startswith("【") and heading.endswith("】"):
        heading = heading[1:-1]
    return _SECTION_HEADINGS.get(heading)


def _priority_reason(text: str, start: int, end: int, paragraph_end: int) -> str:
    # Check longer phrases first so "其中另一個" keeps its full label rather
    # than being classified as the shorter suffix "另一個".
    for phrase in _PRIORITY_PHRASES:
        if text.endswith(phrase, 0, end):
            return phrase
    # The ten-character window counts Unicode characters, not UTF-16 units.
    # Include the two characters of the target phrase at its far boundary.
    following = text[end:min(paragraph_end, end + 12)]
    for target in ("空間", "空间"):
        index = following.find(target)
        if 0 <= index <= 10:
            return f"10字內接「{target}」"
    return ""


def find_article_occurrences(text: str) -> list[ArticleOccurrence]:
    """Return one item per exact token, in the original document order.

    Commas and semicolons remain part of the containing sentence. Paragraph
    numbering includes empty paragraphs; trailing sentence punctuation is
    retained, while surrounding whitespace and line endings are omitted.
    Standalone headings determine each item's section. Text before a heading,
    and documents without headings, remain in ``specification``.
    """
    offsets = _utf16_offsets(text)
    occurrences: list[ArticleOccurrence] = []
    paragraph_start = 0
    paragraph_index = 1
    section = "specification"

    def collect_paragraph(paragraph_end: int) -> None:
        nonlocal section
        heading_section = _section_heading(text[paragraph_start:paragraph_end])
        if heading_section is not None:
            section = heading_section
        sentence_start = paragraph_start
        sentence_bounds: list[tuple[int, int]] = []
        for delimiter in _SENTENCE_BREAKS.finditer(text, paragraph_start, paragraph_end):
            sentence_bounds.append((sentence_start, delimiter.end()))
            sentence_start = delimiter.end()
        sentence_bounds.append((sentence_start, paragraph_end))

        for left, right in sentence_bounds:
            while left < right and text[left].isspace():
                left += 1
            while right > left and text[right - 1].isspace():
                right -= 1
            sentence = text[left:right]
            for match in re.finditer(_ARTICLE, sentence):
                start = left + match.start()
                end = start + len(_ARTICLE)
                occurrences.append(ArticleOccurrence(
                    start=offsets[start],
                    end=offsets[end],
                    sentence_start=offsets[left],
                    sentence_end=offsets[right],
                    paragraph_index=paragraph_index,
                    sentence=sentence,
                    priority_reason=_priority_reason(text, start, end, paragraph_end),
                    section=section,
                ))

    for delimiter in _PARAGRAPH_BREAKS.finditer(text):
        collect_paragraph(delimiter.start())
        paragraph_start = delimiter.end()
        paragraph_index += 1
    collect_paragraph(len(text))
    return occurrences


def rebase_excluded_starts(
    starts: Iterable[int],
    position: int,
    chars_removed: int,
    chars_added: int,
) -> set[int]:
    """Rebase excluded token starts after a Qt UTF-16 text edit.

    Any edit overlapping the token invalidates its exclusion. An insertion
    exactly at its start moves the token; one exactly at its end leaves it
    untouched. A replacement is treated as one atomic remove/insert edit.
    """
    if min(position, chars_removed, chars_added) < 0:
        raise ValueError("Text edit positions and lengths must be nonnegative.")
    if chars_removed == chars_added == 0:
        return set(starts)

    removed_end = position + chars_removed
    shift = chars_added - chars_removed
    rebased: set[int] = set()
    for start in starts:
        end = start + _ARTICLE_UTF16_LENGTH
        if chars_removed:
            if position < end and removed_end > start:
                continue
            if removed_end <= start:
                rebased.add(start + shift)
            else:
                rebased.add(start)
        elif position <= start:
            rebased.add(start + chars_added)
        elif position >= end:
            rebased.add(start)
        # Inserting inside the two-character token breaks its identity.
    return rebased
