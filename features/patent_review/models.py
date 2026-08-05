"""Serializable data model shared by patent text and drawing review."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TextRunSpan:
    """A visible Word run and its character range in the source paragraph."""

    run_index: int
    start: int
    end: int
    text: str


@dataclass
class PatentParagraph:
    """One paragraph in document order, including paragraphs inside tables."""

    index: int
    text: str
    normalized_text: str
    source_path: str
    source_kind: str = "body"
    style_id: str = ""
    style_name: str = ""
    numbering_id: Optional[int] = None
    numbering_level: Optional[int] = None
    numbering_value: Optional[int] = None
    numbering_text: str = ""
    numbering_format: str = ""
    table_index: Optional[int] = None
    row_index: Optional[int] = None
    cell_index: Optional[int] = None
    run_spans: List[TextRunSpan] = field(default_factory=list)
    image_relationship_ids: List[str] = field(default_factory=list)
    major_section_key: Optional[str] = None
    major_section_title: str = ""
    section_key: Optional[str] = None
    section_title: str = ""
    is_heading: bool = False
    content_text: str = ""
    paragraph_alignment: str = ""
    font_size_half_points: Optional[int] = None
    complex_font_size_half_points: Optional[int] = None
    line_spacing: Optional[int] = None
    line_spacing_rule: str = ""
    left_indent: Optional[int] = None
    first_line_indent: Optional[int] = None
    first_line_chars: Optional[int] = None
    hanging_indent: Optional[int] = None
    page_break_before: bool = False
    has_hard_page_break: bool = False
    section_break_type: str = ""


@dataclass
class PatentSection:
    """A detected patent section and the paragraphs assigned to it."""

    key: str
    title: str
    heading_text: str
    heading_paragraph_index: int
    paragraph_indices: List[int] = field(default_factory=list)
    level: str = "medium"
    major_section_key: str = ""


@dataclass
class EmbeddedImage:
    """Metadata for an image part; binary data is not extracted in stage 1."""

    relationship_id: str
    archive_path: str
    filename: str
    content_type: str
    size_bytes: int
    paragraph_indices: List[int] = field(default_factory=list)
    alt_texts: List[str] = field(default_factory=list)


@dataclass
class PatentDocument:
    """Read-only, normalized representation of a patent DOCX."""

    source_path: str
    file_name: str
    file_size_bytes: int
    sha256: str
    metadata: Dict[str, str] = field(default_factory=dict)
    patent_type: str = "unknown"
    patent_title: str = ""
    paragraphs: List[PatentParagraph] = field(default_factory=list)
    sections: List[PatentSection] = field(default_factory=list)
    images: List[EmbeddedImage] = field(default_factory=list)
    page_layouts: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    schema_version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def section_text(self, key: str) -> str:
        """Return normalized content from all occurrences of a section."""

        wanted = {
            index
            for section in self.sections
            if section.key == key
            for index in section.paragraph_indices
        }
        return "\n".join(
            paragraph.content_text
            for paragraph in self.paragraphs
            if paragraph.index in wanted and paragraph.content_text
        )

    @property
    def unassigned_paragraph_indices(self) -> List[int]:
        return [
            paragraph.index
            for paragraph in self.paragraphs
            if paragraph.section_key is None and paragraph.normalized_text
        ]


@dataclass(frozen=True)
class RuleDefinition:
    """Human-readable metadata for one deterministic text rule."""

    rule_id: str
    title: str
    category: str
    default_severity: str
    description: str


@dataclass
class PatentIssue:
    """One review finding anchored to the original Word text when possible."""

    issue_id: str
    rule_id: str
    severity: str
    category: str
    message: str
    suggestion: str
    section_key: str = ""
    section_title: str = ""
    paragraph_index: Optional[int] = None
    source_path: str = ""
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    matched_text: str = ""
    run_indices: List[int] = field(default_factory=list)
    safe_auto_fix: bool = False
    replacement: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PatentTextReview:
    """Serializable Stage 2 result for one parsed patent document."""

    source_path: str
    file_name: str
    sha256: str
    patent_type: str
    patent_title: str
    generated_at_utc: str
    claim_subjects: List[str] = field(default_factory=list)
    issues: List[PatentIssue] = field(default_factory=list)
    rule_catalog: List[RuleDefinition] = field(default_factory=list)
    parse_warnings: List[str] = field(default_factory=list)
    schema_version: str = "2.0"

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        counts = {"error": 0, "warning": 0, "info": 0}
        for issue in self.issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        payload["summary"] = {
            "total": len(self.issues),
            "by_severity": counts,
            "rules_executed": len(self.rule_catalog),
        }
        return payload
