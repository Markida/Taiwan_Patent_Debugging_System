import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from features.patent_review import PatentDocxError, parse_docx
from features.patent_review.docx_reader import _render_numbering_text
from features.patent_review.section_parser import split_section_heading
from features.patent_review.symbol_transfer import extract_document_symbols
from tools.inspect_patent_docx import SYSTEM_FULL_NAME, SYSTEM_NAME, build_reports


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
 <w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>【發明名稱】</w:t></w:r></w:p>
  <w:p><w:r><w:t>測試Ａ</w:t></w:r><w:r><w:t>裝置</w:t></w:r></w:p>
  <w:p><w:r><w:t>【技術領域】 本發明涉及測試技術。</w:t></w:r></w:p>
  <w:p><w:r><w:t>摘要說明如下，但這不是章節標題。</w:t></w:r></w:p>
  <w:p><w:r><w:t>【符號說明】</w:t></w:r></w:p>
  <w:tbl>
   <w:tr><w:tc><w:p><w:r><w:t>10：殼體</w:t></w:r></w:p></w:tc></w:tr>
   <w:tr>
    <w:tc><w:p><w:r><w:t>20</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>底座</w:t></w:r></w:p></w:tc>
   </w:tr>
  </w:tbl>
  <w:p><w:r><w:t>【圖式】</w:t></w:r></w:p>
  <w:p>
   <w:r><w:drawing><wp:inline><wp:docPr id="1" name="Figure 1" descr="代表圖"/><a:graphic><a:graphicData><a:blip r:embed="rId1"/></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>
  </w:p>
  <w:p><w:r><w:t>【申請專利範圍】</w:t></w:r></w:p>
  <w:p><w:r><w:t>1. 一種測試裝置，包括一殼體(10)。</w:t></w:r></w:p>
  <w:sectPr/>
 </w:body>
</w:document>"""

DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
</Relationships>"""

STYLES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="標題 1"/></w:style>
</w:styles>"""

CORE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties
 xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/">
 <dc:title>合成測試文件</dc:title>
 <dc:creator>Saint-Island</dc:creator>
 <dcterms:created>2026-07-28T00:00:00Z</dcterms:created>
</cp:coreProperties>"""

NUMBERED_DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:body>
  <w:p><w:r><w:t>【中文新型名稱】段號測試裝置</w:t></w:r></w:p>
  <w:p><w:r><w:t>【技術領域】</w:t></w:r></w:p>
  <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="10"/></w:numPr></w:pPr><w:r><w:t>第一段</w:t></w:r></w:p>
  <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="10"/></w:numPr></w:pPr><w:r><w:t>第二段</w:t></w:r></w:p>
  <w:sectPr/>
 </w:body>
</w:document>"""

NUMBERING_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:abstractNum w:abstractNumId="9">
  <w:lvl w:ilvl="0">
   <w:start w:val="1"/>
   <w:numFmt w:val="decimalZero"/>
   <w:lvlText w:val="〖%1〗"/>
  </w:lvl>
 </w:abstractNum>
 <w:num w:numId="10"><w:abstractNumId w:val="9"/></w:num>
</w:numbering>"""


def build_test_docx(path: Path) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", DOCUMENT_XML)
        archive.writestr("word/_rels/document.xml.rels", DOCUMENT_RELS)
        archive.writestr("word/styles.xml", STYLES_XML)
        archive.writestr("docProps/core.xml", CORE_XML)
        archive.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\nsynthetic")


def build_numbered_docx(path: Path) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", NUMBERED_DOCUMENT_XML)
        archive.writestr("word/numbering.xml", NUMBERING_XML)


class PatentDocxParserTests(unittest.TestCase):
    def test_company_decimal_zero_template_renders_exactly_four_digits(self):
        self.assertEqual(
            _render_numbering_text(1, "decimalZero", "【00%1】"),
            "【0001】",
        )

    def test_accepts_tipo_heading_bracket_variants(self):
        black_bracket = split_section_heading("【中文摘要】合成摘要內容")
        tortoise_bracket = split_section_heading("〖實施方式〗")

        self.assertIsNotNone(black_bracket)
        self.assertEqual(black_bracket.key, "abstract_zh")
        self.assertEqual(black_bracket.remainder, "合成摘要內容")
        self.assertIsNotNone(tortoise_bracket)
        self.assertEqual(tortoise_bracket.key, "embodiments")

    def test_parses_sections_runs_tables_and_images_without_writing_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "公司測試.DOCX"
            build_test_docx(path)
            before = path.read_bytes()

            document = parse_docx(path)

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(document.patent_type, "invention")
            self.assertEqual(document.patent_title, "測試A裝置")
            self.assertEqual(document.metadata["creator"], "Saint-Island")
            self.assertEqual(len(document.sha256), 64)
            self.assertEqual(
                [section.key for section in document.sections],
                [
                    "invention_title",
                    "technical_field",
                    "drawing_symbol_description",
                    "drawings",
                    "claims",
                ],
            )
            self.assertIn("本發明涉及測試技術", document.section_text("technical_field"))
            self.assertIn("摘要說明如下", document.section_text("technical_field"))
            self.assertIn("殼體(10)", document.section_text("claims"))

            title_paragraph = document.paragraphs[1]
            self.assertEqual(title_paragraph.text, "測試Ａ裝置")
            self.assertEqual(title_paragraph.normalized_text, "測試A裝置")
            self.assertEqual(
                "".join(span.text for span in title_paragraph.run_spans),
                title_paragraph.text,
            )

            table_paragraph = next(
                paragraph
                for paragraph in document.paragraphs
                if paragraph.text == "10：殼體"
            )
            self.assertEqual(table_paragraph.source_kind, "table")
            self.assertEqual(table_paragraph.table_index, 0)
            self.assertEqual(table_paragraph.section_key, "drawing_symbol_description")
            split_symbol = next(
                paragraph for paragraph in document.paragraphs
                if paragraph.text == "20"
            )
            split_name = next(
                paragraph for paragraph in document.paragraphs
                if paragraph.text == "底座"
            )
            self.assertEqual(
                (split_symbol.table_index, split_symbol.row_index, split_symbol.cell_index),
                (0, 1, 0),
            )
            self.assertEqual(
                (split_name.table_index, split_name.row_index, split_name.cell_index),
                (0, 1, 1),
            )
            transfer = extract_document_symbols(document)
            self.assertEqual(
                transfer.reference_items,
                [
                    {"number": "10", "name": "殼體"},
                    {"number": "20", "name": "底座"},
                ],
            )

            self.assertEqual(len(document.images), 1)
            image = document.images[0]
            self.assertEqual(image.filename, "image1.png")
            self.assertEqual(image.content_type, "image/png")
            self.assertEqual(image.alt_texts, ["代表圖", "Figure 1"])
            self.assertEqual(len(image.paragraph_indices), 1)
            json.dumps(document.to_dict(), ensure_ascii=False)

    def test_rejects_old_doc_and_invalid_docx(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_doc = temp_path / "old.doc"
            old_doc.write_bytes(b"not supported")
            invalid_docx = temp_path / "broken.docx"
            invalid_docx.write_bytes(b"not a zip")

            with self.assertRaises(PatentDocxError):
                parse_docx(old_doc)
            with self.assertRaises(PatentDocxError):
                parse_docx(invalid_docx)

    def test_resolves_visible_ooxml_automatic_paragraph_numbers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "numbered.docx"
            build_numbered_docx(path)

            document = parse_docx(path)
            numbered = [
                paragraph
                for paragraph in document.paragraphs
                if paragraph.numbering_id == 10
            ]

            self.assertEqual([item.numbering_value for item in numbered], [1, 2])
            self.assertEqual(
                [item.numbering_text for item in numbered],
                ["〖0001〗", "〖0002〗"],
            )
            self.assertEqual(
                [item.numbering_format for item in numbered],
                ["decimalZero", "decimalZero"],
            )

    def test_builds_named_manual_checkpoint_reports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "sample.docx"
            output = temp_path / "report"
            build_test_docx(source)

            json_path, html_path = build_reports(source, output)

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html = html_path.read_text(encoding="utf-8")
            self.assertEqual(payload["schema_version"], "1.0")
            self.assertIn(SYSTEM_NAME, html)
            self.assertIn(SYSTEM_FULL_NAME, html)
            self.assertIn("人工斷點一", html)


if __name__ == "__main__":
    unittest.main()
