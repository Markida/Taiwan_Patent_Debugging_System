"""Read patent DOCX files directly from OOXML without altering the source."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
import posixpath
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import unquote
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .models import EmbeddedImage, PatentDocument, PatentParagraph, TextRunSpan
from .section_parser import assign_sections
from .text_normalizer import normalize_patent_text


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
V_NS = "urn:schemas-microsoft-com:vml"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"

NS = {"w": W_NS}
W_P = f"{{{W_NS}}}p"
W_TBL = f"{{{W_NS}}}tbl"
W_TR = f"{{{W_NS}}}tr"
W_TC = f"{{{W_NS}}}tc"
W_R = f"{{{W_NS}}}r"
W_T = f"{{{W_NS}}}t"
W_TAB = f"{{{W_NS}}}tab"
W_BR = f"{{{W_NS}}}br"
W_CR = f"{{{W_NS}}}cr"
W_NO_BREAK_HYPHEN = f"{{{W_NS}}}noBreakHyphen"
W_SOFT_HYPHEN = f"{{{W_NS}}}softHyphen"
R_ID = f"{{{R_NS}}}id"
R_EMBED = f"{{{R_NS}}}embed"

MAX_XML_PART_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


class PatentDocxError(ValueError):
    """Raised when a DOCX cannot be safely interpreted as OOXML."""


def _safe_parse_xml(data: bytes, part_name: str) -> ET.Element:
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise PatentDocxError(f"{part_name} 含有不允許的 XML 實體宣告")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise PatentDocxError(f"無法解析 {part_name}: {exc}") from exc


def _read_part(
    archive: ZipFile,
    part_name: str,
    *,
    required: bool = False,
) -> Optional[bytes]:
    try:
        info = archive.getinfo(part_name)
    except KeyError:
        if required:
            raise PatentDocxError(f"DOCX 缺少必要檔案：{part_name}")
        return None
    if info.file_size > MAX_XML_PART_BYTES:
        raise PatentDocxError(f"{part_name} 過大，已停止解析")
    return archive.read(info)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_styles(archive: ZipFile) -> Dict[str, str]:
    data = _read_part(archive, "word/styles.xml")
    if data is None:
        return {}
    root = _safe_parse_xml(data, "word/styles.xml")
    styles: Dict[str, str] = {}
    for style in root.findall("w:style", NS):
        style_id = style.get(f"{{{W_NS}}}styleId", "")
        name = style.find("w:name", NS)
        if style_id:
            styles[style_id] = name.get(f"{{{W_NS}}}val", style_id) if name is not None else style_id
    return styles


def _read_style_numbering(archive: ZipFile) -> Dict[str, Tuple[int, int]]:
    data = _read_part(archive, "word/styles.xml")
    if data is None:
        return {}
    root = _safe_parse_xml(data, "word/styles.xml")
    result: Dict[str, Tuple[int, int]] = {}
    for style in root.findall("w:style", NS):
        style_id = style.get(f"{{{W_NS}}}styleId", "")
        num_id_node = style.find("w:pPr/w:numPr/w:numId", NS)
        if not style_id or num_id_node is None:
            continue
        num_id_text = num_id_node.get(f"{{{W_NS}}}val", "")
        level_node = style.find("w:pPr/w:numPr/w:ilvl", NS)
        level_text = (
            level_node.get(f"{{{W_NS}}}val", "0")
            if level_node is not None
            else "0"
        )
        if num_id_text.isdigit() and level_text.isdigit():
            result[style_id] = (int(num_id_text), int(level_text))
    return result


def _read_numbering_definitions(
    archive: ZipFile,
) -> Dict[Tuple[int, int], Tuple[int, str, str]]:
    """Return (start, format, level text) for each concrete numId/level."""

    data = _read_part(archive, "word/numbering.xml")
    if data is None:
        return {}
    root = _safe_parse_xml(data, "word/numbering.xml")
    abstract_levels: Dict[Tuple[int, int], Tuple[int, str, str]] = {}
    for abstract in root.findall("w:abstractNum", NS):
        abstract_id_text = abstract.get(f"{{{W_NS}}}abstractNumId", "")
        if not abstract_id_text.isdigit():
            continue
        abstract_id = int(abstract_id_text)
        for level in abstract.findall("w:lvl", NS):
            level_text = level.get(f"{{{W_NS}}}ilvl", "0")
            if not level_text.isdigit():
                continue
            start_node = level.find("w:start", NS)
            format_node = level.find("w:numFmt", NS)
            template_node = level.find("w:lvlText", NS)
            start_text = (
                start_node.get(f"{{{W_NS}}}val", "1")
                if start_node is not None
                else "1"
            )
            abstract_levels[(abstract_id, int(level_text))] = (
                int(start_text) if start_text.isdigit() else 1,
                format_node.get(f"{{{W_NS}}}val", "decimal")
                if format_node is not None
                else "decimal",
                template_node.get(f"{{{W_NS}}}val", "%1")
                if template_node is not None
                else "%1",
            )

    result: Dict[Tuple[int, int], Tuple[int, str, str]] = {}
    for numbering in root.findall("w:num", NS):
        num_id_text = numbering.get(f"{{{W_NS}}}numId", "")
        abstract_node = numbering.find("w:abstractNumId", NS)
        if not num_id_text.isdigit() or abstract_node is None:
            continue
        abstract_text = abstract_node.get(f"{{{W_NS}}}val", "")
        if not abstract_text.isdigit():
            continue
        num_id = int(num_id_text)
        abstract_id = int(abstract_text)
        overrides: Dict[int, int] = {}
        for override in numbering.findall("w:lvlOverride", NS):
            level_text = override.get(f"{{{W_NS}}}ilvl", "0")
            start_override = override.find("w:startOverride", NS)
            if (
                level_text.isdigit()
                and start_override is not None
                and start_override.get(f"{{{W_NS}}}val", "").isdigit()
            ):
                overrides[int(level_text)] = int(
                    start_override.get(f"{{{W_NS}}}val", "1")
                )
        for (wanted_abstract, level), definition in abstract_levels.items():
            if wanted_abstract != abstract_id:
                continue
            start, number_format, level_template = definition
            result[(num_id, level)] = (
                overrides.get(level, start),
                number_format,
                level_template,
            )
    return result


def _read_metadata(archive: ZipFile) -> Dict[str, str]:
    data = _read_part(archive, "docProps/core.xml")
    if data is None:
        return {}
    root = _safe_parse_xml(data, "docProps/core.xml")
    fields = {
        "title": f"{{{DC_NS}}}title",
        "subject": f"{{{DC_NS}}}subject",
        "creator": f"{{{DC_NS}}}creator",
        "description": f"{{{DC_NS}}}description",
        "last_modified_by": f"{{{CP_NS}}}lastModifiedBy",
        "created": f"{{{DCTERMS_NS}}}created",
        "modified": f"{{{DCTERMS_NS}}}modified",
    }
    metadata: Dict[str, str] = {}
    for key, tag in fields.items():
        node = root.find(tag)
        if node is not None and node.text:
            metadata[key] = node.text.strip()
    return metadata


def _read_content_types(archive: ZipFile) -> Tuple[Dict[str, str], Dict[str, str]]:
    data = _read_part(archive, "[Content_Types].xml")
    if data is None:
        return {}, {}
    root = _safe_parse_xml(data, "[Content_Types].xml")
    defaults: Dict[str, str] = {}
    overrides: Dict[str, str] = {}
    for node in root:
        if node.tag == f"{{{CT_NS}}}Default":
            defaults[node.get("Extension", "").lower()] = node.get("ContentType", "")
        elif node.tag == f"{{{CT_NS}}}Override":
            overrides[node.get("PartName", "").lstrip("/")] = node.get("ContentType", "")
    return defaults, overrides


def _read_image_relationships(
    archive: ZipFile,
) -> Tuple[Dict[str, str], List[str]]:
    data = _read_part(archive, "word/_rels/document.xml.rels")
    if data is None:
        return {}, []
    root = _safe_parse_xml(data, "word/_rels/document.xml.rels")
    relationships: Dict[str, str] = {}
    warnings: List[str] = []
    for relation in root.findall(f"{{{PKG_REL_NS}}}Relationship"):
        relation_type = relation.get("Type", "")
        if not relation_type.endswith("/image"):
            continue
        relation_id = relation.get("Id", "")
        if relation.get("TargetMode") == "External":
            warnings.append(f"略過外部圖片連結：{relation_id}")
            continue
        target = unquote(relation.get("Target", ""))
        archive_path = posixpath.normpath(posixpath.join("word", target))
        if archive_path.startswith("../") or archive_path.startswith("/"):
            warnings.append(f"略過不安全的圖片路徑：{target}")
            continue
        if relation_id:
            relationships[relation_id] = archive_path
    return relationships, warnings


def _iter_paragraph_elements(
    body: ET.Element,
) -> Iterator[Tuple[ET.Element, str, str, Optional[int], Optional[int], Optional[int]]]:
    table_counter = [0]

    def walk_container(
        container: ET.Element,
        prefix: str,
        table_index: Optional[int] = None,
        row_index: Optional[int] = None,
        cell_index: Optional[int] = None,
    ) -> Iterator[Tuple[ET.Element, str, str, Optional[int], Optional[int], Optional[int]]]:
        paragraph_number = 0
        nested_table_number = 0
        for child in list(container):
            if child.tag == W_P:
                path = f"{prefix}/p[{paragraph_number}]"
                paragraph_number += 1
                kind = "table" if table_index is not None else "body"
                yield child, path, kind, table_index, row_index, cell_index
            elif child.tag == W_TBL:
                current_table_index = table_counter[0]
                table_counter[0] += 1
                table_path = f"{prefix}/tbl[{nested_table_number}]"
                nested_table_number += 1
                rows = [node for node in list(child) if node.tag == W_TR]
                for current_row, row in enumerate(rows):
                    cells = [node for node in list(row) if node.tag == W_TC]
                    for current_cell, cell in enumerate(cells):
                        cell_path = f"{table_path}/tr[{current_row}]/tc[{current_cell}]"
                        yield from walk_container(
                            cell,
                            cell_path,
                            current_table_index,
                            current_row,
                            current_cell,
                        )

    yield from walk_container(body, "body")


def _run_text(run: ET.Element) -> str:
    pieces: List[str] = []
    for node in run.iter():
        if node.tag == W_T:
            pieces.append(node.text or "")
        elif node.tag == W_TAB:
            pieces.append("\t")
        elif node.tag in {W_BR, W_CR}:
            pieces.append("\n")
        elif node.tag == W_NO_BREAK_HYPHEN:
            pieces.append("-")
        elif node.tag == W_SOFT_HYPHEN:
            pieces.append("\u00ad")
    return "".join(pieces)


def _paragraph_text(paragraph: ET.Element) -> Tuple[str, List[TextRunSpan]]:
    pieces: List[str] = []
    spans: List[TextRunSpan] = []
    offset = 0
    for run_index, run in enumerate(paragraph.iter(W_R)):
        text = _run_text(run)
        if not text:
            continue
        start = offset
        pieces.append(text)
        offset += len(text)
        spans.append(TextRunSpan(run_index=run_index, start=start, end=offset, text=text))
    return "".join(pieces), spans


def _paragraph_style(paragraph: ET.Element, styles: Dict[str, str]) -> Tuple[str, str]:
    style = paragraph.find("w:pPr/w:pStyle", NS)
    if style is None:
        return "", ""
    style_id = style.get(f"{{{W_NS}}}val", "")
    return style_id, styles.get(style_id, style_id)


def _paragraph_numbering(
    paragraph: ET.Element,
    style_id: str,
    style_numbering: Dict[str, Tuple[int, int]],
) -> Tuple[Optional[int], Optional[int]]:
    num_id_node = paragraph.find("w:pPr/w:numPr/w:numId", NS)
    level_node = paragraph.find("w:pPr/w:numPr/w:ilvl", NS)
    if num_id_node is not None:
        num_id_text = num_id_node.get(f"{{{W_NS}}}val", "")
        level_text = (
            level_node.get(f"{{{W_NS}}}val", "0")
            if level_node is not None
            else "0"
        )
        if num_id_text.isdigit() and int(num_id_text) > 0:
            return int(num_id_text), int(level_text) if level_text.isdigit() else 0
        return None, None
    return style_numbering.get(style_id, (None, None))


def _render_numbering_text(value: int, number_format: str, template: str) -> str:
    if number_format == "decimalZero":
        # TIPO patent paragraphs use a four-digit Arabic value.  Word's
        # decimalZero token is otherwise implementation-dependent in width.
        rendered_value = f"{value:04d}" if "〖" in template or "【" in template else f"{value:02d}"
    else:
        rendered_value = str(value)
    return template.replace("%1", rendered_value)


def _paragraph_images(paragraph: ET.Element) -> Tuple[List[str], List[str]]:
    relationship_ids: List[str] = []
    alt_texts: List[str] = []
    for node in paragraph.iter():
        if node.tag == f"{{{A_NS}}}blip":
            relation_id = node.get(R_EMBED, "")
            if relation_id and relation_id not in relationship_ids:
                relationship_ids.append(relation_id)
        elif node.tag == f"{{{V_NS}}}imagedata":
            relation_id = node.get(R_ID, "")
            if relation_id and relation_id not in relationship_ids:
                relationship_ids.append(relation_id)
        elif node.tag == f"{{{WP_NS}}}docPr":
            for attribute in ("title", "descr", "name"):
                value = (node.get(attribute) or "").strip()
                if value and value not in alt_texts:
                    alt_texts.append(value)
    return relationship_ids, alt_texts


def _content_type(
    archive_path: str,
    defaults: Dict[str, str],
    overrides: Dict[str, str],
) -> str:
    if archive_path in overrides:
        return overrides[archive_path]
    extension = posixpath.splitext(archive_path)[1].lstrip(".").lower()
    return defaults.get(extension) or mimetypes.guess_type(archive_path)[0] or "application/octet-stream"


def parse_docx(path: Path | str) -> PatentDocument:
    """Parse a DOCX into a read-only :class:`PatentDocument`.

    Source formatting and XML are never modified.  The returned run spans are
    retained so a later checkpoint can implement safe, targeted write-back.
    """

    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise PatentDocxError(f"找不到 DOCX：{source_path}")
    if source_path.suffix.lower() != ".docx":
        raise PatentDocxError("第一階段僅支援 .docx，不支援舊式 .doc")

    try:
        with ZipFile(source_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise PatentDocxError("DOCX 內含過多檔案，已停止解析")
            if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
                raise PatentDocxError("DOCX 解壓後內容過大，已停止解析")

            document_data = _read_part(
                archive,
                "word/document.xml",
                required=True,
            )
            document_root = _safe_parse_xml(document_data or b"", "word/document.xml")
            body = document_root.find("w:body", NS)
            if body is None:
                raise PatentDocxError("word/document.xml 缺少文件本文")

            styles = _read_styles(archive)
            style_numbering = _read_style_numbering(archive)
            numbering_definitions = _read_numbering_definitions(archive)
            numbering_counters: Dict[Tuple[int, int], int] = {}
            metadata = _read_metadata(archive)
            defaults, overrides = _read_content_types(archive)
            image_relationships, warnings = _read_image_relationships(archive)

            images_by_id: Dict[str, EmbeddedImage] = {}
            for relation_id, archive_path in image_relationships.items():
                try:
                    size_bytes = archive.getinfo(archive_path).file_size
                except KeyError:
                    size_bytes = 0
                    warnings.append(f"圖片關聯 {relation_id} 找不到檔案：{archive_path}")
                images_by_id[relation_id] = EmbeddedImage(
                    relationship_id=relation_id,
                    archive_path=archive_path,
                    filename=posixpath.basename(archive_path),
                    content_type=_content_type(archive_path, defaults, overrides),
                    size_bytes=size_bytes,
                )

            paragraphs: List[PatentParagraph] = []
            for index, item in enumerate(_iter_paragraph_elements(body)):
                element, xml_path, source_kind, table_index, row_index, cell_index = item
                text, run_spans = _paragraph_text(element)
                style_id, style_name = _paragraph_style(element, styles)
                numbering_id, numbering_level = _paragraph_numbering(
                    element,
                    style_id,
                    style_numbering,
                )
                numbering_value: Optional[int] = None
                numbering_text = ""
                numbering_format = ""
                if numbering_id is not None and numbering_level is not None:
                    definition = numbering_definitions.get(
                        (numbering_id, numbering_level)
                    )
                    if definition is not None:
                        start, numbering_format, template = definition
                        counter_key = (numbering_id, numbering_level)
                        numbering_value = numbering_counters.get(
                            counter_key,
                            start - 1,
                        ) + 1
                        numbering_counters[counter_key] = numbering_value
                        numbering_text = _render_numbering_text(
                            numbering_value,
                            numbering_format,
                            template,
                        )
                relationship_ids, alt_texts = _paragraph_images(element)
                paragraph = PatentParagraph(
                    index=index,
                    text=text,
                    normalized_text=normalize_patent_text(text),
                    source_path=xml_path,
                    source_kind=source_kind,
                    style_id=style_id,
                    style_name=style_name,
                    numbering_id=numbering_id,
                    numbering_level=numbering_level,
                    numbering_value=numbering_value,
                    numbering_text=numbering_text,
                    numbering_format=numbering_format,
                    table_index=table_index,
                    row_index=row_index,
                    cell_index=cell_index,
                    run_spans=run_spans,
                    image_relationship_ids=relationship_ids,
                )
                paragraphs.append(paragraph)
                for relation_id in relationship_ids:
                    image = images_by_id.get(relation_id)
                    if image is None:
                        warnings.append(
                            f"段落 {index} 使用未知圖片關聯：{relation_id}"
                        )
                        continue
                    if index not in image.paragraph_indices:
                        image.paragraph_indices.append(index)
                    for alt_text in alt_texts:
                        if alt_text not in image.alt_texts:
                            image.alt_texts.append(alt_text)

            sections, patent_type, patent_title = assign_sections(paragraphs)
            if not sections:
                warnings.append("未辨識到標準專利章節，請人工確認標題格式")

            return PatentDocument(
                source_path=str(source_path),
                file_name=source_path.name,
                file_size_bytes=source_path.stat().st_size,
                sha256=_sha256(source_path),
                metadata=metadata,
                patent_type=patent_type,
                patent_title=patent_title or metadata.get("title", ""),
                paragraphs=paragraphs,
                sections=sections,
                images=list(images_by_id.values()),
                warnings=warnings,
            )
    except BadZipFile as exc:
        raise PatentDocxError("檔案不是有效的 DOCX/ZIP 文件") from exc
