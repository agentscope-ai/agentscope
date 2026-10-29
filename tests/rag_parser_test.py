# -*- coding: utf-8 -*-
"""Unit tests for the file parsers in :mod:`agentscope.rag._parser`.

PDF / PPTX fixtures are produced in-memory via :mod:`reportlab` and
:mod:`python-pptx` so the tests have no on-disk dependencies and can
run anywhere ``agentscope[rag]`` is installed.
"""
import base64
import io
import json
import os
import zipfile
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.rag import (
    ImageParser,
    PDFParser,
    PPTParser,
    TextParser,
    WordParser,
    ExcelParser,
)


# Smallest valid PNG ever — a single transparent pixel.  Used wherever
# a test needs "some image bytes" without depending on Pillow.
_PNG_PIXEL: bytes = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgAAIAAAUA"
    "AarVyFEAAAAASUVORK5CYII=",
)
_PNG_PIXEL_B64: str = base64.b64encode(_PNG_PIXEL).decode("utf-8")


def _make_pdf(pages: list[str]) -> bytes:
    """Render the supplied page strings to a PDF in memory."""
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for page in pages:
        pdf.drawString(72, 720, page)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _make_pptx_simple(slides: list[str]) -> bytes:
    """Build a PPTX in memory with one text shape per slide."""
    from pptx import Presentation

    prs = Presentation()
    blank_layout = prs.slide_layouts[5]  # title-only layout
    for text in slides:
        slide = prs.slides.add_slide(blank_layout)
        slide.shapes.title.text = text
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def _make_pptx_rich() -> bytes:
    """Build a richer PPTX with text, a table, and an embedded image.

    Slide 1: a single title text shape.
    Slide 2: a title text shape, a 2x2 table, and a trailing text shape.
    Slide 3: an embedded PNG image only (blank title).
    """
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    blank_layout = prs.slide_layouts[5]

    slide1 = prs.slides.add_slide(blank_layout)
    slide1.shapes.title.text = "Hello"

    slide2 = prs.slides.add_slide(blank_layout)
    slide2.shapes.title.text = "Header"
    table = slide2.shapes.add_table(
        rows=2,
        cols=2,
        left=Inches(1),
        top=Inches(2),
        width=Inches(4),
        height=Inches(1),
    ).table
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    table.cell(1, 0).text = "1"
    table.cell(1, 1).text = "2"
    trailing = slide2.shapes.add_textbox(
        Inches(1),
        Inches(4),
        Inches(4),
        Inches(1),
    )
    trailing.text_frame.text = "Footer"

    slide3 = prs.slides.add_slide(blank_layout)
    slide3.shapes.title.text = ""  # blank title
    slide3.shapes.add_picture(
        io.BytesIO(_PNG_PIXEL),
        Inches(1),
        Inches(1),
        Inches(1),
        Inches(1),
    )

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def _make_pptx_with_image(image_bytes: bytes) -> bytes:
    """Build a PPTX containing a single picture on a blank slide."""
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_picture(io.BytesIO(image_bytes), 0, 0)
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def _make_pptx_with_group() -> bytes:
    """Build a PPTX whose slide holds a text box and a group of two boxes.

    A group carries no text frame of its own, so a loop over ``slide.shapes``
    that only looks at text frames, tables and pictures never reaches it.
    """
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    standalone = slide.shapes.add_textbox(
        Inches(0.5),
        Inches(0.2),
        Inches(4),
        Inches(0.5),
    )
    standalone.text_frame.text = "Standalone"

    first = slide.shapes.add_textbox(
        Inches(0.5),
        Inches(2.0),
        Inches(2),
        Inches(0.5),
    )
    first.text_frame.text = "Grouped one"
    second = slide.shapes.add_textbox(
        Inches(3.0),
        Inches(2.0),
        Inches(2),
        Inches(0.5),
    )
    second.text_frame.text = "Grouped two"
    slide.shapes.add_group_shape([first, second])

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def _make_pptx_with_nested_group() -> bytes:
    """Build a PPTX whose slide holds a group inside a group."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    inner_a = slide.shapes.add_textbox(
        Inches(0.5),
        Inches(0.5),
        Inches(2),
        Inches(0.5),
    )
    inner_a.text_frame.text = "Innermost"
    inner_b = slide.shapes.add_textbox(
        Inches(3.0),
        Inches(0.5),
        Inches(2),
        Inches(0.5),
    )
    inner_b.text_frame.text = "Sibling"
    inner_group = slide.shapes.add_group_shape([inner_a, inner_b])

    outer = slide.shapes.add_textbox(
        Inches(0.5),
        Inches(2.0),
        Inches(2),
        Inches(0.5),
    )
    outer.text_frame.text = "Outer"
    slide.shapes.add_group_shape([inner_group, outer])

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def _make_pptx_with_special_table_cells() -> bytes:
    """Build a PPTX table with pipes and a multi-line cell."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    table = slide.shapes.add_table(
        rows=2,
        cols=2,
        left=Inches(1),
        top=Inches(1),
        width=Inches(4),
        height=Inches(1),
    ).table
    table.cell(0, 0).text = "A|B"
    table.cell(0, 1).text = r"Path \| label"
    table.cell(1, 0).text = "1|2"
    table.cell(1, 1).text = "Line 1\vLine 2"

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def _make_docx_simple(paragraphs: list[str]) -> bytes:
    """Build a DOCX in memory with plain text paragraphs."""
    from docx import Document as DocxDocument

    doc = DocxDocument()
    for text in paragraphs:
        doc.add_paragraph(text)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _make_docx_with_table() -> bytes:
    """Build a DOCX with a paragraph, a 2x2 table, and a trailing
    paragraph."""
    from docx import Document as DocxDocument

    doc = DocxDocument()
    doc.add_paragraph("Before table")

    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    table.cell(1, 0).text = "1"
    table.cell(1, 1).text = "2"

    doc.add_paragraph("After table")

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _make_docx_with_nested_table() -> bytes:
    """Build a DOCX whose outer table cell contains a nested table.

    A nested ``w:tbl`` lives inside the outer ``w:tc``, so its paragraphs are
    not direct children of that cell.
    """
    from docx import Document as DocxDocument

    doc = DocxDocument()
    doc.add_paragraph("Before table")

    outer = doc.add_table(rows=1, cols=1)
    outer_cell = outer.cell(0, 0)
    outer_cell.text = "Outer cell"

    nested = outer_cell.add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "Nested cell"

    doc.add_paragraph("After table")

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _make_docx_with_special_table_cells() -> bytes:
    """Build a DOCX table with pipes and a multi-line cell."""
    from docx import Document as DocxDocument

    doc = DocxDocument()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "A|B"
    table.cell(0, 1).text = r"Path \| label"
    table.cell(1, 0).text = "1|2"
    table.cell(1, 1).text = "Line 1\nLine 2"

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


_DOCX_TEXT_BOX_NS = " ".join(
    f'xmlns:{prefix}="{uri}"'
    for prefix, uri in (
        ("w", "http://schemas.openxmlformats.org/wordprocessingml/2006/main"),
        ("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006"),
        (
            "wps",
            "http://schemas.microsoft.com/office/word/2010/"
            "wordprocessingShape",
        ),
        ("a", "http://schemas.openxmlformats.org/drawingml/2006/main"),
        (
            "wp",
            "http://schemas.openxmlformats.org/drawingml/2006/"
            "wordprocessingDrawing",
        ),
        ("v", "urn:schemas-microsoft-com:vml"),
    )
)

_DOCX_MODERN_TEXT_BOX = """
<w:p {ns}>
  <w:r><w:t>{before}</w:t></w:r>
  <w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">
    <wp:extent cx="2743200" cy="914400"/>
    <wp:docPr id="1" name="Text Box 1"/>
    <a:graphic><a:graphicData
        uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">
      <wps:wsp><wps:txbx><w:txbxContent>
        <w:p><w:r><w:t>{inside}</w:t></w:r></w:p>
      </w:txbxContent></wps:txbx></wps:wsp>
    </a:graphicData></a:graphic>
  </wp:inline></w:drawing></w:r>
</w:p>
"""

_DOCX_TEXT_BOX_WITH_VML_FALLBACK = """
<w:p {ns}>
  <w:r><w:t>{before}</w:t></w:r>
  <w:r><mc:AlternateContent>
    <mc:Choice Requires="wps"><w:drawing>
      <wp:inline distT="0" distB="0" distL="0" distR="0">
        <wp:extent cx="2743200" cy="914400"/>
        <wp:docPr id="1" name="Text Box 1"/>
        <a:graphic><a:graphicData
            uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">
          <wps:wsp><wps:txbx><w:txbxContent>
            <w:p><w:r><w:t>{inside}</w:t></w:r></w:p>
          </w:txbxContent></wps:txbx></wps:wsp>
        </a:graphicData></a:graphic>
      </wp:inline></w:drawing></mc:Choice>
    <mc:Fallback><w:pict>
      <v:shape id="_x0000_s1026" type="#_x0000_t202"><v:textbox>
        <w:txbxContent>
          <w:p><w:r><w:t>{inside}</w:t></w:r></w:p>
        </w:txbxContent>
      </v:textbox></v:shape>
    </w:pict></mc:Fallback>
  </mc:AlternateContent></w:r>
</w:p>
"""


def _make_docx_with_text_box(
    template: str,
    before: str = "Paragraph text",
    inside: str = "Callout",
    text_box_count: int = 1,
) -> bytes:
    """Build a paragraph anchoring independently created text boxes."""
    import docx
    from lxml import etree

    document = docx.Document()
    body = document.element.body
    xml = template.format(
        ns=_DOCX_TEXT_BOX_NS,
        before=before,
        inside=inside,
    ).strip()
    paragraph = etree.fromstring(xml)
    for _ in range(1, text_box_count):
        # Each template ends with the run that anchors its text box.
        paragraph.append(etree.fromstring(xml)[-1])
    body.insert(len(body) - 1, paragraph)
    document.add_paragraph("After")

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _expected_word_sections(text: str) -> list[dict]:
    """Return the complete expected output for a single Word text section."""
    return [
        {
            "content": {
                "type": "text",
                "text": text,
                "id": AnyString(),
                "created_at": AnyString(),
                "finished_at": None,
            },
            "source": "demo.docx",
            "metadata": {},
        },
    ]


def _make_docx_with_image() -> bytes:
    """Build a DOCX with a paragraph and an embedded PNG image."""
    from docx import Document as DocxDocument
    from docx.shared import Inches

    doc = DocxDocument()
    doc.add_paragraph("Text before image")
    doc.add_picture(io.BytesIO(_PNG_PIXEL), width=Inches(1))
    doc.add_paragraph("Text after image")

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _make_docx_with_image_in_table_cell() -> bytes:
    """Build a DOCX whose 1x1 table cell contains text and an embedded
    PNG image."""
    from docx import Document as DocxDocument
    from docx.shared import Inches

    doc = DocxDocument()
    doc.add_paragraph("Before table")
    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.text = "see screenshot"
    cell.add_paragraph().add_run().add_picture(
        io.BytesIO(_PNG_PIXEL),
        width=Inches(1),
    )
    doc.add_paragraph("After table")

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _make_docx_with_image_in_table_text_box() -> bytes:
    """Build a DOCX with an image nested in a table-cell text box."""
    from docx import Document as DocxDocument
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls, qn
    from docx.shared import Inches

    doc = DocxDocument()
    doc.add_paragraph("Before table")
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    outer_paragraph = cell.paragraphs[0]

    image_paragraph = cell.add_paragraph()
    image_paragraph.add_run().add_picture(
        io.BytesIO(_PNG_PIXEL),
        width=Inches(1),
    )
    image_element = image_paragraph._p  # pylint: disable=protected-access
    image_element.getparent().remove(image_element)

    text_box_run = parse_xml(
        f"<w:r {nsdecls('w')} "
        f'xmlns:v="urn:schemas-microsoft-com:vml">'
        f"<w:pict><v:shape><v:textbox>"
        f"<w:txbxContent/>"
        f"</v:textbox></v:shape></w:pict>"
        f"</w:r>",
    )
    text_box_content = text_box_run.find(
        ".//" + qn("w:txbxContent"),
    )
    assert text_box_content is not None
    text_box_content.append(image_element)
    outer_paragraph._p.append(  # pylint: disable=protected-access
        text_box_run,
    )
    doc.add_paragraph("After table")

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _make_docx_content_control(*, wrapped: bool) -> bytes:
    """Build a DOCX whose middle content is in ``w:sdt``, or is plain.

    ``wrapped=True`` puts a paragraph, a table and a nested control
    inside a block-level content control, which is how Word writes an
    automatic table of contents; ``wrapped=False`` writes the same
    content directly under the body.

    Args:
        wrapped (`bool`):
            Whether the middle content is wrapped in ``w:sdt``.

    Returns:
        `bytes`:
            The DOCX bytes.
    """
    from docx import Document as DocxDocument
    from docx.oxml import parse_xml

    ns = f'xmlns:w="{_W_NS}"'
    heading = f"<w:p {ns}><w:r><w:t>Controlled heading</w:t></w:r></w:p>"
    table = (
        f"<w:tbl {ns}><w:tr>"
        "<w:tc><w:p><w:r><w:t>cell-a</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>cell-b</w:t></w:r></w:p></w:tc>"
        "</w:tr></w:tbl>"
    )
    nested = f"<w:p {ns}><w:r><w:t>Nested control</w:t></w:r></w:p>"
    if wrapped:
        fragments = [
            f"<w:sdt {ns}><w:sdtContent>"
            f"{heading}{table}"
            f"<w:sdt><w:sdtContent>{nested}</w:sdtContent></w:sdt>"
            f"</w:sdtContent></w:sdt>",
        ]
    else:
        fragments = [heading, table, nested]

    doc = DocxDocument()
    doc.add_paragraph("Before.")
    after = doc.add_paragraph("After.")
    for fragment in fragments:
        # ``w:sectPr`` has to stay the last body child, so insert rather
        # than append.
        after._element.addprevious(  # pylint: disable=protected-access
            parse_xml(fragment),
        )

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _make_xlsx_simple(
    sheets: dict[str, list[list[str]]],
) -> bytes:
    """Build an XLSX in memory from a dict of sheet_name → rows.

    Each row is a list of cell strings; the first row becomes the
    header in pandas.
    """
    import pandas as pd

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name, rows in sheets.items():
            if rows:
                df = pd.DataFrame(rows[1:], columns=rows[0])
            else:
                df = pd.DataFrame()
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    return buffer.getvalue()


class TextParserTest(IsolatedAsyncioTestCase):
    """Behavioural coverage for :class:`TextParser`."""

    async def test_decode_bytes(self) -> None:
        """Bytes are decoded with the configured encoding."""
        parser = TextParser()
        sections = await parser.parse(b"hello", "x.txt")
        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "hello",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "x.txt",
                    "metadata": {},
                },
            ],
        )

    async def test_pre_decoded_string_round_trips(self) -> None:
        """``str`` inputs skip the decode step when no such file exists."""
        parser = TextParser()
        sections = await parser.parse("preset", "x.md")
        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "preset",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "x.md",
                    "metadata": {},
                },
            ],
        )

    async def test_string_input_treated_as_path(self) -> None:
        """A ``str`` that names an existing file is read as a path."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
        ) as f:
            f.write("from disk")
            path = f.name
        try:
            parser = TextParser()
            sections = await parser.parse(path, "x.txt")
            self.assertEqual(len(sections), 1)
            self.assertEqual(sections[0].content.text, "from disk")
        finally:
            os.unlink(path)

    async def test_bad_encoding_raises_value_error(self) -> None:
        """An undecodable byte sequence surfaces a clear error."""
        parser = TextParser(encoding="ascii")
        with self.assertRaises(ValueError):
            await parser.parse(b"\xff\xfe", "bad.txt")


class PDFParserTest(IsolatedAsyncioTestCase):
    """Behavioural coverage for :class:`PDFParser`."""

    async def test_one_section_per_page(self) -> None:
        """Each page in the PDF round-trips to its own Section."""
        pdf_bytes = _make_pdf(["First page text", "Second page text"])
        parser = PDFParser()
        sections = await parser.parse(pdf_bytes, "demo.pdf")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "First page text\n",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.pdf",
                    "metadata": {"page": 1},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "Second page text\n",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.pdf",
                    "metadata": {"page": 2},
                },
            ],
        )

    async def test_string_input_treated_as_path(self) -> None:
        """``str`` is interpreted as a filesystem path to the PDF."""
        import tempfile

        pdf_bytes = _make_pdf(["Hello"])
        with tempfile.NamedTemporaryFile(
            suffix=".pdf",
            delete=False,
        ) as f:
            f.write(pdf_bytes)
            path = f.name
        try:
            parser = PDFParser()
            sections = await parser.parse(path, "demo.pdf")
            self.assertEqual(len(sections), 1)
        finally:
            os.unlink(path)

    async def test_missing_path_raises_file_not_found(self) -> None:
        """A ``str`` pointing at a missing path raises FileNotFoundError."""
        parser = PDFParser()
        with self.assertRaises(FileNotFoundError):
            await parser.parse("/no/such/file.pdf", "x.pdf")

    async def test_invalid_bytes_raise_value_error(self) -> None:
        """Garbage bytes surface as :class:`ValueError`."""
        parser = PDFParser()
        with self.assertRaises(ValueError):
            await parser.parse(b"not a pdf", "broken.pdf")

    async def test_password_protected_pdf_raises_value_error(self) -> None:
        """Errors deferred until page iteration retain filename context."""
        from pypdf import PdfWriter
        from pypdf.errors import FileNotDecryptedError

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.encrypt("secret")
        buffer = io.BytesIO()
        writer.write(buffer)

        with self.assertRaisesRegex(
            ValueError,
            r"Failed to parse 'locked\.pdf' as PDF:",
        ) as context:
            await PDFParser().parse(buffer.getvalue(), "locked.pdf")
        self.assertIsInstance(
            context.exception.__cause__,
            FileNotDecryptedError,
        )

    async def test_empty_user_password_pdf_remains_readable(self) -> None:
        """An encrypted PDF that opens without a password is still parsed."""
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.encrypt(user_password="", owner_password="owner")
        buffer = io.BytesIO()
        writer.write(buffer)

        sections = await PDFParser().parse(buffer.getvalue(), "open.pdf")

        self.assertEqual(
            [section.model_dump() for section in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "open.pdf",
                    "metadata": {"page": 1},
                },
            ],
        )

    async def test_text_extraction_read_error_raises_value_error(self) -> None:
        """Read errors raised after page enumeration are wrapped as well."""
        from unittest.mock import patch
        from pypdf import PageObject
        from pypdf.errors import PdfReadError

        error = PdfReadError("broken content stream")
        with patch.object(PageObject, "extract_text", side_effect=error):
            with self.assertRaisesRegex(
                ValueError,
                r"Failed to parse 'broken-stream\.pdf' as PDF:",
            ) as context:
                await PDFParser().parse(
                    _make_pdf(["Hello"]),
                    "broken-stream.pdf",
                )
        self.assertIs(context.exception.__cause__, error)

    async def test_supported_extensions(self) -> None:
        """``.pdf`` is the only extension exposed to the file picker."""
        self.assertEqual(PDFParser.supported_extensions(), [".pdf"])


class ImageParserTest(IsolatedAsyncioTestCase):
    """Behavioural coverage for :class:`ImageParser`."""

    async def test_wraps_bytes_in_data_block(self) -> None:
        """A single Section is emitted with the image as a DataBlock."""
        parser = ImageParser()
        sections = await parser.parse(_PNG_PIXEL, "pixel.png")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "data",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                        "source": {
                            "type": "base64",
                            "data": _PNG_PIXEL_B64,
                            "media_type": "image/png",
                        },
                        "name": "pixel.png",
                    },
                    "source": "pixel.png",
                    "metadata": {"media_type": "image/png"},
                },
            ],
        )

    async def test_media_type_sniffed_from_jpeg(self) -> None:
        """JPEG magic bytes yield ``image/jpeg``."""
        jpeg_bytes = b"\xff\xd8\xff\xe0rest-of-jpeg"
        parser = ImageParser()
        sections = await parser.parse(jpeg_bytes, "x.jpg")
        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "data",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                        "source": {
                            "type": "base64",
                            "data": base64.b64encode(jpeg_bytes).decode(
                                "utf-8",
                            ),
                            "media_type": "image/jpeg",
                        },
                        "name": "x.jpg",
                    },
                    "source": "x.jpg",
                    "metadata": {"media_type": "image/jpeg"},
                },
            ],
        )

    async def test_string_input_treated_as_path(self) -> None:
        """``str`` is interpreted as a filesystem path to the image."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=".png",
            delete=False,
        ) as f:
            f.write(_PNG_PIXEL)
            path = f.name
        try:
            parser = ImageParser()
            sections = await parser.parse(path, "pixel.png")
            self.assertEqual(len(sections), 1)
        finally:
            os.unlink(path)

    async def test_missing_path_raises_file_not_found(self) -> None:
        """A ``str`` pointing at a missing path raises FileNotFoundError."""
        parser = ImageParser()
        with self.assertRaises(FileNotFoundError):
            await parser.parse("/no/such/file.png", "x.png")


class PPTParserTest(IsolatedAsyncioTestCase):
    """Behavioural coverage for :class:`PPTParser`."""

    @staticmethod
    def _deck_with_soft_break() -> bytes:
        """Build a deck whose text frame has one soft line break."""
        from pptx import Presentation
        from pptx.util import Inches

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
        box = slide.shapes.add_textbox(
            Inches(0.5),
            Inches(0.5),
            Inches(4),
            Inches(1),
        )
        paragraph = box.text_frame.paragraphs[0]
        paragraph.add_run().text = "one"
        paragraph.add_line_break()
        paragraph.add_run().text = "two"

        buffer = io.BytesIO()
        prs.save(buffer)
        return buffer.getvalue()

    async def test_soft_line_break_becomes_a_newline(self) -> None:
        """``add_line_break()`` shows up as ``\n``, not a control char."""
        parser = PPTParser(include_image=False)
        sections = await parser.parse(
            self._deck_with_soft_break(),
            "soft_break.pptx",
        )

        self.assertEqual(
            [section.model_dump() for section in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=1>\none\ntwo\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "soft_break.pptx",
                    "metadata": {"slide": 1},
                },
            ],
        )

    async def test_ordinary_newlines_are_preserved(self) -> None:
        """A paragraph per line still yields newline-separated text."""
        from pptx import Presentation
        from pptx.util import Inches

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
        box = slide.shapes.add_textbox(
            Inches(0.5),
            Inches(0.5),
            Inches(4),
            Inches(1),
        )
        frame = box.text_frame
        frame.text = "one"
        for line in ("two", "three"):
            paragraph = frame.add_paragraph()
            paragraph.text = line

        buffer = io.BytesIO()
        prs.save(buffer)
        parser = PPTParser(include_image=False)
        sections = await parser.parse(buffer.getvalue(), "paras.pptx")

        self.assertEqual(
            [section.model_dump() for section in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": ("<slide index=1>\none\ntwo\nthree\n</slide>"),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "paras.pptx",
                    "metadata": {"slide": 1},
                },
            ],
        )

    async def test_simple_deck_text_only(self) -> None:
        """A simple text-only deck round-trips through wrapping tags."""
        pptx_bytes = _make_pptx_simple(["Alpha", "Beta"])
        parser = PPTParser(include_image=False)
        sections = await parser.parse(pptx_bytes, "demo.pptx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=1>\nAlpha\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.pptx",
                    "metadata": {"slide": 1},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=2>\nBeta\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.pptx",
                    "metadata": {"slide": 2},
                },
            ],
        )

    async def test_without_slide_tags(self) -> None:
        """Disabling prefix/suffix removes the slide tags entirely."""
        pptx_bytes = _make_pptx_simple(["Alpha"])
        parser = PPTParser(
            include_image=False,
            slide_prefix=None,
            slide_suffix=None,
        )
        sections = await parser.parse(pptx_bytes, "demo.pptx")
        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "Alpha",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.pptx",
                    "metadata": {"slide": 1},
                },
            ],
        )

    async def test_table_merges_with_surrounding_text_by_default(
        self,
    ) -> None:
        """``separate_table=False`` merges the table into the running
        text section."""
        pptx_bytes = _make_pptx_rich()
        parser = PPTParser(include_image=False, separate_table=False)
        sections = await parser.parse(pptx_bytes, "rich.pptx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=1>\nHello\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 1},
                },
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "<slide index=2>\n"
                            "Header\n"
                            "| A | B |\n"
                            "| --- | --- |\n"
                            "| 1 | 2 |\n\n"
                            "Footer\n"
                            "</slide>"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 2},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=3>\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 3},
                },
            ],
        )

    async def test_markdown_table_escapes_special_cells(self) -> None:
        """Pipes and line breaks do not corrupt Markdown table rows."""
        pptx_bytes = _make_pptx_with_special_table_cells()
        parser = PPTParser(
            include_image=False,
            separate_table=True,
            slide_prefix=None,
            slide_suffix=None,
        )
        sections = await parser.parse(pptx_bytes, "special.pptx")

        expected_text = (
            "\n".join(
                [
                    r"| A\|B | Path \\\| label |",
                    "| --- | --- |",
                    r"| 1\|2 | Line 1<br>Line 2 |",
                ],
            )
            + "\n"
        )
        self.assertEqual(
            [section.model_dump() for section in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": expected_text,
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "special.pptx",
                    "metadata": {"slide": 1},
                },
            ],
        )

    async def test_table_separated_when_separate_table_true(self) -> None:
        """``separate_table=True`` flushes the running text around the
        table."""
        pptx_bytes = _make_pptx_rich()
        parser = PPTParser(include_image=False, separate_table=True)
        sections = await parser.parse(pptx_bytes, "rich.pptx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=1>\nHello\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 1},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=2>\nHeader",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 2},
                },
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "| A | B |\n" "| --- | --- |\n" "| 1 | 2 |\n"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 2},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "Footer\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 2},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=3>\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 3},
                },
            ],
        )

    async def test_image_emits_data_block(self) -> None:
        """An embedded picture becomes its own DataBlock section."""
        pptx_bytes = _make_pptx_rich()
        parser = PPTParser(include_image=True, separate_table=True)
        sections = await parser.parse(pptx_bytes, "rich.pptx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=1>\nHello\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 1},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=2>\nHeader",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 2},
                },
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "| A | B |\n" "| --- | --- |\n" "| 1 | 2 |\n"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 2},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "Footer\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 2},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=3>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 3},
                },
                {
                    "content": {
                        "type": "data",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                        "source": {
                            "type": "base64",
                            "data": _PNG_PIXEL_B64,
                            "media_type": "image/png",
                        },
                        "name": "rich.pptx",
                    },
                    "source": "rich.pptx",
                    "metadata": {
                        "slide": 3,
                        "media_type": "image/png",
                    },
                },
                {
                    "content": {
                        "type": "text",
                        "text": "</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 3},
                },
            ],
        )

    async def test_tiff_image_media_type_for_bytes_and_path(self) -> None:
        """TIFF bytes retain their MIME type for both PPTX input forms."""
        import tempfile

        from PIL import Image
        from pptx import Presentation

        image_buffer = io.BytesIO()
        Image.new("RGB", (2, 2), color=(17, 34, 51)).save(
            image_buffer,
            format="TIFF",
        )
        image_bytes = image_buffer.getvalue()
        pptx_bytes = _make_pptx_with_image(image_bytes)
        embedded = (
            Presentation(io.BytesIO(pptx_bytes)).slides[0].shapes[0].image
        )
        self.assertEqual(
            {"content_type": embedded.content_type, "blob": embedded.blob},
            {"content_type": "image/tiff", "blob": image_bytes},
        )

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tiff.pptx")
            with open(path, "wb") as fp:
                fp.write(pptx_bytes)
            for input_type, pptx_input in (
                ("bytes", pptx_bytes),
                ("path", path),
            ):
                for include_image in (True, False):
                    with self.subTest(
                        input_type=input_type,
                        include_image=include_image,
                    ):
                        sections = await PPTParser(
                            include_image=include_image,
                            slide_prefix=None,
                            slide_suffix=None,
                        ).parse(pptx_input, "tiff.pptx")
                        self.assertEqual(
                            [section.model_dump() for section in sections],
                            [
                                {
                                    "content": {
                                        "type": "data",
                                        "id": AnyString(),
                                        "created_at": AnyString(),
                                        "finished_at": None,
                                        "source": {
                                            "type": "base64",
                                            "data": base64.b64encode(
                                                image_bytes,
                                            ).decode("utf-8"),
                                            "media_type": "image/tiff",
                                        },
                                        "name": "tiff.pptx",
                                    },
                                    "source": "tiff.pptx",
                                    "metadata": {
                                        "slide": 1,
                                        "media_type": "image/tiff",
                                    },
                                },
                            ]
                            if include_image
                            else [],
                        )

    async def test_image_media_type_falls_back_when_unavailable(self) -> None:
        """Missing or unreadable MIME information preserves readable images."""
        from unittest.mock import PropertyMock, patch

        from pptx.parts.image import Image

        pptx_bytes = _make_pptx_with_image(_PNG_PIXEL)
        for content_type in (
            None,
            "",
            AttributeError("missing content type"),
            ValueError("unreadable content type"),
        ):
            with self.subTest(content_type=content_type):
                with patch.object(
                    Image,
                    "content_type",
                    new_callable=PropertyMock,
                ) as mocked_type:
                    if isinstance(content_type, Exception):
                        mocked_type.side_effect = content_type
                    else:
                        mocked_type.return_value = content_type
                    sections = await PPTParser(
                        slide_prefix=None,
                        slide_suffix=None,
                    ).parse(pptx_bytes, "fallback.pptx")
                self.assertEqual(
                    [section.model_dump() for section in sections],
                    [
                        {
                            "content": {
                                "type": "data",
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                                "source": {
                                    "type": "base64",
                                    "data": _PNG_PIXEL_B64,
                                    "media_type": "image/png",
                                },
                                "name": "fallback.pptx",
                            },
                            "source": "fallback.pptx",
                            "metadata": {
                                "slide": 1,
                                "media_type": "image/png",
                            },
                        },
                    ],
                )

    async def test_unreadable_image_is_skipped(self) -> None:
        """Unreadable picture objects or image bytes keep warning and skip."""
        from unittest.mock import PropertyMock, patch

        from pptx.parts.image import Image
        from pptx.shapes.picture import Picture

        pptx_bytes = _make_pptx_with_image(_PNG_PIXEL)
        for target, attribute in ((Picture, "image"), (Image, "blob")):
            with self.subTest(attribute=attribute):
                with patch.object(
                    target,
                    attribute,
                    new_callable=PropertyMock,
                    side_effect=ValueError("unreadable image"),
                ):
                    with self.assertLogs("as", level="WARNING") as captured:
                        sections = await PPTParser(
                            slide_prefix=None,
                            slide_suffix=None,
                        ).parse(pptx_bytes, "unreadable.pptx")
                self.assertEqual(
                    {
                        "sections": [
                            section.model_dump() for section in sections
                        ],
                        "warnings": captured.output,
                    },
                    {
                        "sections": [],
                        "warnings": [
                            "WARNING:as:Failed to extract image from PPT "
                            "shape: unreadable image",
                        ],
                    },
                )

    async def test_picture_placeholder_emits_data_block(self) -> None:
        """Pictures inserted into layout placeholders keep their content."""
        from pptx import Presentation

        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[8])
        slide.shapes.title.text = "Before picture"
        slide.placeholders[1].insert_picture(io.BytesIO(_PNG_PIXEL))
        slide.placeholders[2].text = "After picture"
        # An unfilled picture placeholder must not be treated as an image.
        presentation.slides.add_slide(presentation.slide_layouts[8])
        buffer = io.BytesIO()
        presentation.save(buffer)

        parser = PPTParser(slide_prefix=None, slide_suffix=None)
        sections = await parser.parse(buffer.getvalue(), "placeholder.pptx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "Before picture",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "placeholder.pptx",
                    "metadata": {"slide": 1},
                },
                {
                    "content": {
                        "type": "data",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                        "source": {
                            "type": "base64",
                            "data": _PNG_PIXEL_B64,
                            "media_type": "image/png",
                        },
                        "name": "placeholder.pptx",
                    },
                    "source": "placeholder.pptx",
                    "metadata": {"slide": 1, "media_type": "image/png"},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "After picture",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "placeholder.pptx",
                    "metadata": {"slide": 1},
                },
            ],
        )

    async def test_table_json_format(self) -> None:
        """``table_format="json"`` emits the JSON marker payload."""
        pptx_bytes = _make_pptx_rich()
        parser = PPTParser(
            include_image=False,
            separate_table=True,
            table_format="json",
        )
        sections = await parser.parse(pptx_bytes, "rich.pptx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=1>\nHello\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 1},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=2>\nHeader",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 2},
                },
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "<system-info>A table loaded as a JSON "
                            "array:</system-info>\n"
                            '[["A", "B"], ["1", "2"]]'
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 2},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "Footer\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 2},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "<slide index=3>\n</slide>",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.pptx",
                    "metadata": {"slide": 3},
                },
            ],
        )

    async def test_table_format_validation(self) -> None:
        """Unknown ``table_format`` raises :class:`ValueError`."""
        with self.assertRaises(ValueError):
            PPTParser(table_format="csv")  # type: ignore[arg-type]

    async def test_deferred_invalid_xml_raises_value_error(self) -> None:
        """Malformed slide XML is wrapped after the deck opens."""
        import tempfile
        from xml.etree import ElementTree

        from pptx import Presentation
        from pptx.exc import InvalidXmlError

        original = io.BytesIO(_make_pptx_simple(["Alpha", "Beta"]))
        corrupted = io.BytesIO()
        with zipfile.ZipFile(original) as source:
            with zipfile.ZipFile(corrupted, "w") as target:
                for member in source.infolist():
                    data = source.read(member.filename)
                    if member.filename == "ppt/slides/slide2.xml":
                        root = ElementTree.fromstring(data)
                        common_slide = root.find(
                            "{http://schemas.openxmlformats.org/"
                            "presentationml/2006/main}cSld",
                        )
                        assert common_slide is not None
                        root.remove(common_slide)
                        data = ElementTree.tostring(root)
                    target.writestr(member, data)
        pptx_bytes = corrupted.getvalue()

        # The required child is only checked when slide shapes are accessed.
        slides = list(Presentation(io.BytesIO(pptx_bytes)).slides)
        self.assertEqual(len(slides), 2)
        self.assertEqual(slides[0].shapes.title.text, "Alpha")

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "corrupt.pptx")
            with open(path, "wb") as fp:
                fp.write(pptx_bytes)
            for input_type, pptx_input in (
                ("bytes", pptx_bytes),
                ("path", path),
            ):
                with self.subTest(input_type=input_type):
                    with self.assertRaisesRegex(
                        ValueError,
                        r"Failed to parse 'corrupt\.pptx' as PPTX:",
                    ) as context:
                        await PPTParser().parse(pptx_input, "corrupt.pptx")
                    self.assertIsInstance(
                        context.exception.__cause__,
                        InvalidXmlError,
                    )

    async def test_invalid_slide_prefix_preserves_key_error(self) -> None:
        """An unknown prefix placeholder remains a configuration error."""
        parser = PPTParser(slide_prefix="{unknown}")
        with self.assertRaisesRegex(KeyError, "unknown"):
            await parser.parse(_make_pptx_simple(["Alpha"]), "demo.pptx")

    async def test_string_input_treated_as_path(self) -> None:
        """``str`` is interpreted as a filesystem path to the PPTX."""
        import tempfile

        pptx_bytes = _make_pptx_simple(["Alpha"])
        with tempfile.NamedTemporaryFile(
            suffix=".pptx",
            delete=False,
        ) as f:
            f.write(pptx_bytes)
            path = f.name
        try:
            parser = PPTParser(include_image=False)
            sections = await parser.parse(path, "demo.pptx")
            self.assertEqual(len(sections), 1)
        finally:
            os.unlink(path)

    async def test_missing_path_raises_file_not_found(self) -> None:
        """A ``str`` pointing at a missing path raises FileNotFoundError."""
        parser = PPTParser()
        with self.assertRaises(FileNotFoundError):
            await parser.parse("/no/such/file.pptx", "x.pptx")

    async def test_group_shape_text_is_read(self) -> None:
        """Text inside a group reaches the Sections, in shape-tree order."""
        parser = PPTParser(include_image=False)
        sections = await parser.parse(_make_pptx_with_group(), "demo.pptx")

        # Only "Standalone" was read before; the group held the other two.
        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "<slide index=1>\n"
                            "Standalone\n"
                            "Grouped one\n"
                            "Grouped two\n"
                            "</slide>"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.pptx",
                    "metadata": {"slide": 1},
                },
            ],
        )

    async def test_nested_group_shape_text_is_read(self) -> None:
        """A group can hold a group, so the descent has to recurse."""
        parser = PPTParser(include_image=False)
        sections = await parser.parse(
            _make_pptx_with_nested_group(),
            "demo.pptx",
        )

        # Depth first: the inner group is emptied before its sibling, and
        # both come before the shape that follows the outer group.
        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "<slide index=1>\n"
                            "Innermost\n"
                            "Sibling\n"
                            "Outer\n"
                            "</slide>"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.pptx",
                    "metadata": {"slide": 1},
                },
            ],
        )


class ExcelParserTest(IsolatedAsyncioTestCase):
    """Behavioural coverage for :class:`ExcelParser`."""

    async def test_corrupt_sheet_does_not_return_partial_content(self) -> None:
        """A deferred worksheet error fails the whole workbook parse."""
        from xml.etree.ElementTree import ParseError

        xlsx_bytes = _make_xlsx_simple(
            {
                "Good": [["Key"], ["visible"]],
                "Broken": [["Key"], ["must not disappear"]],
            },
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as source:
            with zipfile.ZipFile(buffer, "w") as target:
                for entry in source.infolist():
                    data = source.read(entry.filename)
                    if entry.filename == "xl/worksheets/sheet2.xml":
                        data = data.replace(
                            b"</sheetData>",
                            b"</brokenSheetData>",
                        )
                    target.writestr(entry, data)

        for separate_sheet in (False, True):
            with self.subTest(separate_sheet=separate_sheet):
                parser = ExcelParser(separate_sheet=separate_sheet)
                with self.assertRaisesRegex(
                    ValueError,
                    "Failed to parse sheet 'Broken' in 'corrupt.xlsx'",
                ) as raised:
                    await parser.parse(buffer.getvalue(), "corrupt.xlsx")
                self.assertIsInstance(raised.exception.__cause__, ParseError)

    async def test_duplicate_and_blank_headers(self) -> None:
        """Headers remain cell values, without pandas-generated labels."""
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.append(["Name", "Name", None])
        workbook.active.append(["a", "b", "c"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        workbook.close()

        for table_format in ("markdown", "json"):
            for coordinates in (False, True):
                with self.subTest(
                    table_format=table_format,
                    coordinates=coordinates,
                ):
                    parser = ExcelParser(
                        table_format=table_format,
                        include_cell_coordinates=coordinates,
                        include_sheet_names=False,
                    )
                    sections = await parser.parse(
                        buffer.getvalue(),
                        "headers.xlsx",
                    )
                    self.assertEqual(len(sections), 1)
                    text = sections[0].content.text
                    if table_format == "markdown":
                        expected = (
                            "| [A1] Name | [B1] Name | [C1]  |\n"
                            "| --- | --- | --- |\n"
                            "| [A2] a | [B2] b | [C2] c |\n"
                            if coordinates
                            else "| Name | Name |  |\n"
                            "| --- | --- | --- |\n"
                            "| a | b | c |\n"
                        )
                        self.assertEqual(text, expected)
                    else:
                        rows = [
                            json.loads(line) for line in text.splitlines()[1:]
                        ]
                        self.assertEqual(
                            rows,
                            [
                                {"A1": "Name", "B1": "Name", "C1": ""},
                                {"A2": "a", "B2": "b", "C2": "c"},
                            ]
                            if coordinates
                            else [["Name", "Name", ""], ["a", "b", "c"]],
                        )

    async def test_invalid_input_errors(self) -> None:
        """Missing paths and invalid workbooks use documented errors."""
        parser = ExcelParser()
        with self.assertRaises(FileNotFoundError):
            await parser.parse("/no/such/report.xlsx", "report.xlsx")

        workbook = _make_xlsx_simple({"Data": [["value"]]})
        with self.assertRaises(ValueError):
            await parser.parse(workbook[: len(workbook) // 2], "bad.xlsx")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("notes.txt", "not a workbook")
        with self.assertRaises(ValueError):
            await parser.parse(buffer.getvalue(), "bad.xlsx")

    async def test_header_only_sheet(self) -> None:
        """A sheet with only a header row is kept as a table."""
        xlsx_bytes = _make_xlsx_simple({"Data": [["Revenue", "Year"]]})
        sections = await ExcelParser().parse(xlsx_bytes, "header.xlsx")

        self.assertListEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "Sheet: Data\n"
                            "| Revenue | Year |\n"
                            "| --- | --- |\n"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "header.xlsx",
                    "metadata": {},
                },
            ],
        )

    async def test_blank_sheet_has_no_sections(self) -> None:
        """A blank sheet produces neither a table nor images."""
        xlsx_bytes = _make_xlsx_simple({"Blank": []})
        sections = await ExcelParser().parse(xlsx_bytes, "blank.xlsx")

        self.assertListEqual(sections, [])

    async def test_image_only_sheet(self) -> None:
        """Images on a sheet without cell values are still extracted."""
        from openpyxl import Workbook
        from openpyxl.drawing.image import Image

        workbook = Workbook()
        workbook.active.title = "Images"
        workbook.active.add_image(Image(io.BytesIO(_PNG_PIXEL)), "A3")
        buffer = io.BytesIO()
        workbook.save(buffer)
        workbook.close()

        parser = ExcelParser(include_image=True)
        sections = await parser.parse(buffer.getvalue(), "images.xlsx")

        self.assertListEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "data",
                        "id": AnyString(),
                        "source": {
                            "type": "base64",
                            "data": _PNG_PIXEL_B64,
                            "media_type": "image/png",
                        },
                        "name": "images.xlsx",
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "images.xlsx",
                    "metadata": {"sheet": "Images", "media_type": "image/png"},
                },
            ],
        )

    async def test_single_sheet_markdown(self) -> None:
        """A single-sheet workbook produces one text Section with
        Markdown table."""
        xlsx_bytes = _make_xlsx_simple(
            {"Data": [["Name", "Age"], ["Alice", "25"]]},
        )
        parser = ExcelParser(table_format="markdown")
        sections = await parser.parse(xlsx_bytes, "demo.xlsx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "Sheet: Data\n"
                            "| Name | Age |\n"
                            "| --- | --- |\n"
                            "| Alice | 25 |\n"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.xlsx",
                    "metadata": {},
                },
            ],
        )

    async def test_markdown_table_escapes_special_cells(self) -> None:
        """Pipes and line breaks do not corrupt Markdown table rows."""
        xlsx_bytes = _make_xlsx_simple(
            {
                "S1": [
                    ["A|B", r"Path \| label"],
                    ["1|2", "Line 1\nLine 2"],
                ],
            },
        )
        parser = ExcelParser(include_sheet_names=False)
        sections = await parser.parse(xlsx_bytes, "special.xlsx")

        expected_text = (
            "\n".join(
                [
                    r"| A\|B | Path \\\| label |",
                    "| --- | --- |",
                    r"| 1\|2 | Line 1<br>Line 2 |",
                ],
            )
            + "\n"
        )
        self.assertEqual(
            [section.model_dump() for section in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": expected_text,
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "special.xlsx",
                    "metadata": {},
                },
            ],
        )

    async def test_single_sheet_json(self) -> None:
        """``table_format="json"`` emits JSON rows."""
        xlsx_bytes = _make_xlsx_simple(
            {"Sheet1": [["X", "Y"], ["1", "2"]]},
        )
        parser = ExcelParser(table_format="json")
        sections = await parser.parse(xlsx_bytes, "demo.xlsx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "Sheet: Sheet1\n"
                            "<system-info>A table loaded as a JSON "
                            "array:</system-info>\n"
                            '["X", "Y"]\n'
                            '["1", "2"]'
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.xlsx",
                    "metadata": {},
                },
            ],
        )

    async def test_text_and_na_like_values_kept(self) -> None:
        """Text like ``"00123"`` and ``"NA"`` is not coerced by pandas."""
        xlsx_bytes = _make_xlsx_simple(
            {"Data": [["Code", "Status"], ["00123", "NA"]]},
        )
        sections = await ExcelParser().parse(xlsx_bytes, "demo.xlsx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "Sheet: Data\n"
                            "| Code | Status |\n"
                            "| --- | --- |\n"
                            "| 00123 | NA |\n"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.xlsx",
                    "metadata": {},
                },
            ],
        )

    async def test_native_error_cells_preserved(self) -> None:
        """Stored Excel error values stay visible in both renderers.

        pandas' openpyxl reader turns type ``e`` cells into NaN.  The
        parser must copy those stored values back, while blanks, text,
        numbers, and unevaluated formulas stay as they were.
        """
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Errors"
        sheet.append(["Case", "Value", "Note"])
        sheet.append(["#NULL!", "text-after", 1])
        sheet.append(["native_div0", "#DIV/0!", None])
        sheet.append(["blank", None, "still-blank"])
        sheet.append(["text_na", "NA", "00123"])
        sheet.append(["integer", 42, None])
        sheet.append(["literal", "#GETTING_DATA", "N/A"])
        sheet.append(["formula", "=1/0", None])
        sheet.append(["native_na", "#N/A", "#VALUE!"])
        self.assertEqual(sheet["A2"].data_type, "e")
        self.assertEqual(sheet["A2"].value, "#NULL!")
        self.assertEqual(sheet["B3"].data_type, "e")
        self.assertEqual(sheet["B3"].value, "#DIV/0!")
        self.assertEqual(sheet["B9"].data_type, "e")
        self.assertEqual(sheet["C9"].data_type, "e")
        self.assertEqual(sheet["C9"].value, "#VALUE!")
        self.assertEqual(sheet["B7"].data_type, "s")
        self.assertEqual(sheet["B8"].data_type, "f")
        self.assertIsNone(sheet["B4"].value)
        buffer = io.BytesIO()
        workbook.save(buffer)
        workbook.close()
        payload = buffer.getvalue()

        rows = [
            ["Case", "Value", "Note"],
            ["#NULL!", "text-after", "1"],
            ["native_div0", "#DIV/0!", ""],
            ["blank", "", "still-blank"],
            ["text_na", "NA", "00123"],
            ["integer", "42", ""],
            ["literal", "#GETTING_DATA", "N/A"],
            ["formula", "", ""],
            ["native_na", "#N/A", "#VALUE!"],
        ]
        markdown = (
            "| Case | Value | Note |\n"
            "| --- | --- | --- |\n"
            "| #NULL! | text-after | 1 |\n"
            "| native_div0 | #DIV/0! |  |\n"
            "| blank |  | still-blank |\n"
            "| text_na | NA | 00123 |\n"
            "| integer | 42 |  |\n"
            "| literal | #GETTING_DATA | N/A |\n"
            "| formula |  |  |\n"
            "| native_na | #N/A | #VALUE! |\n"
        )
        markdown_coords = (
            "| [A1] Case | [B1] Value | [C1] Note |\n"
            "| --- | --- | --- |\n"
            "| [A2] #NULL! | [B2] text-after | [C2] 1 |\n"
            "| [A3] native_div0 | [B3] #DIV/0! | [C3]  |\n"
            "| [A4] blank | [B4]  | [C4] still-blank |\n"
            "| [A5] text_na | [B5] NA | [C5] 00123 |\n"
            "| [A6] integer | [B6] 42 | [C6]  |\n"
            "| [A7] literal | [B7] #GETTING_DATA | [C7] N/A |\n"
            "| [A8] formula | [B8]  | [C8]  |\n"
            "| [A9] native_na | [B9] #N/A | [C9] #VALUE! |\n"
        )

        for table_format in ("markdown", "json"):
            for coordinates in (False, True):
                with self.subTest(
                    table_format=table_format,
                    coordinates=coordinates,
                ):
                    parser = ExcelParser(
                        table_format=table_format,
                        include_cell_coordinates=coordinates,
                        include_sheet_names=False,
                    )
                    sections = await parser.parse(payload, "errors.xlsx")
                    self.assertEqual(len(sections), 1)
                    text = sections[0].content.text
                    if table_format == "markdown":
                        expected = markdown_coords if coordinates else markdown
                    elif coordinates:
                        body = "\n".join(
                            json.dumps(
                                {
                                    f"{'ABC'[col]}{row + 1}": cell
                                    for col, cell in enumerate(values)
                                },
                                ensure_ascii=False,
                            )
                            for row, values in enumerate(rows)
                        )
                        expected = (
                            "<system-info>A table loaded as a JSON "
                            f"array:</system-info>\n{body}"
                        )
                    else:
                        body = "\n".join(
                            json.dumps(values, ensure_ascii=False)
                            for values in rows
                        )
                        expected = (
                            "<system-info>A table loaded as a JSON "
                            f"array:</system-info>\n{body}"
                        )
                    self.assertEqual(
                        [section.model_dump() for section in sections],
                        [
                            {
                                "content": {
                                    "type": "text",
                                    "text": expected,
                                    "id": AnyString(),
                                    "created_at": AnyString(),
                                    "finished_at": None,
                                },
                                "source": "errors.xlsx",
                                "metadata": {},
                            },
                        ],
                    )
                    self.assertIn("#DIV/0!", text)
                    self.assertIn("#N/A", text)
                    self.assertIn("#VALUE!", text)
                    self.assertIn("#NULL!", text)

    async def test_multi_sheet_merged_by_default(self) -> None:
        """``separate_sheet=False`` (default) merges sheets into one
        Section."""
        xlsx_bytes = _make_xlsx_simple(
            {
                "Sales": [["Q", "Rev"], ["Q1", "100"]],
                "Costs": [["Q", "Cost"], ["Q1", "50"]],
            },
        )
        parser = ExcelParser()
        sections = await parser.parse(xlsx_bytes, "multi.xlsx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "Sheet: Sales\n"
                            "| Q | Rev |\n"
                            "| --- | --- |\n"
                            "| Q1 | 100 |\n\n"
                            "Sheet: Costs\n"
                            "| Q | Cost |\n"
                            "| --- | --- |\n"
                            "| Q1 | 50 |\n"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "multi.xlsx",
                    "metadata": {},
                },
            ],
        )

    async def test_multi_sheet_separated(self) -> None:
        """``separate_sheet=True`` keeps each sheet as its own Section."""
        xlsx_bytes = _make_xlsx_simple(
            {
                "Sales": [["Q", "Rev"], ["Q1", "100"]],
                "Costs": [["Q", "Cost"], ["Q1", "50"]],
            },
        )
        parser = ExcelParser(separate_sheet=True)
        sections = await parser.parse(xlsx_bytes, "multi.xlsx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "Sheet: Sales\n"
                            "| Q | Rev |\n"
                            "| --- | --- |\n"
                            "| Q1 | 100 |\n"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "multi.xlsx",
                    "metadata": {"sheet": "Sales"},
                },
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "Sheet: Costs\n"
                            "| Q | Cost |\n"
                            "| --- | --- |\n"
                            "| Q1 | 50 |\n"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "multi.xlsx",
                    "metadata": {"sheet": "Costs"},
                },
            ],
        )

    async def test_sheet_name_in_text(self) -> None:
        """``include_sheet_names=True`` prepends the sheet name."""
        xlsx_bytes = _make_xlsx_simple(
            {"MySheet": [["A"], ["1"]]},
        )
        parser = ExcelParser(include_sheet_names=True)
        sections = await parser.parse(xlsx_bytes, "demo.xlsx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "Sheet: MySheet\n" "| A |\n" "| --- |\n" "| 1 |\n"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.xlsx",
                    "metadata": {},
                },
            ],
        )

    async def test_sheet_name_excluded(self) -> None:
        """``include_sheet_names=False`` omits the sheet header."""
        xlsx_bytes = _make_xlsx_simple(
            {"MySheet": [["A"], ["1"]]},
        )
        parser = ExcelParser(include_sheet_names=False)
        sections = await parser.parse(xlsx_bytes, "demo.xlsx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": ("| A |\n" "| --- |\n" "| 1 |\n"),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.xlsx",
                    "metadata": {},
                },
            ],
        )

    async def test_cell_coordinates(self) -> None:
        """``include_cell_coordinates=True`` adds coordinate prefixes."""
        xlsx_bytes = _make_xlsx_simple(
            {"Data": [["Val"], ["X"]]},
        )
        parser = ExcelParser(include_cell_coordinates=True)
        sections = await parser.parse(xlsx_bytes, "demo.xlsx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "Sheet: Data\n"
                            "| [A1] Val |\n"
                            "| --- |\n"
                            "| [A2] X |\n"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.xlsx",
                    "metadata": {},
                },
            ],
        )

    async def test_string_input_treated_as_path(self) -> None:
        """``str`` is interpreted as a filesystem path."""
        import tempfile

        xlsx_bytes = _make_xlsx_simple(
            {"S": [["Col"], ["Val"]]},
        )
        with tempfile.NamedTemporaryFile(
            suffix=".xlsx",
            delete=False,
        ) as f:
            f.write(xlsx_bytes)
            path = f.name
        try:
            parser = ExcelParser()
            sections = await parser.parse(path, "demo.xlsx")
            self.assertGreaterEqual(len(sections), 1)
        finally:
            os.unlink(path)

    async def test_supported_extensions(self) -> None:
        """Both ``.xls`` and ``.xlsx`` are listed."""
        exts = ExcelParser.supported_extensions()
        self.assertIn(".xlsx", exts)
        self.assertIn(".xls", exts)

    async def test_table_format_validation(self) -> None:
        """Unknown ``table_format`` raises :class:`ValueError`."""
        with self.assertRaises(ValueError):
            ExcelParser(table_format="csv")  # type: ignore[arg-type]


class WordParserTest(IsolatedAsyncioTestCase):
    """Behavioural coverage for :class:`WordParser`."""

    async def test_invalid_input_errors(self) -> None:
        """Missing paths and invalid documents use documented errors."""
        parser = WordParser()
        with self.assertRaises(FileNotFoundError):
            await parser.parse("/no/such/report.docx", "report.docx")

        with self.assertRaises(ValueError):
            await parser.parse(b"not a docx", "bad.docx")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("notes.txt", "not a document")
        with self.assertRaises(ValueError):
            await parser.parse(buffer.getvalue(), "bad.docx")

    async def test_simple_paragraphs(self) -> None:
        """Plain paragraphs are merged into a single text Section."""
        docx_bytes = _make_docx_simple(["Hello", "World"])
        parser = WordParser(include_image=False)
        sections = await parser.parse(docx_bytes, "demo.docx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "Hello\nWorld",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.docx",
                    "metadata": {},
                },
            ],
        )

    async def test_empty_paragraph_is_preserved_between_text(self) -> None:
        """A blank paragraph between text paragraphs remains a blank line."""
        docx_bytes = _make_docx_simple(
            ["Paragraph one.", "", "Paragraph two."],
        )
        sections = await WordParser(include_image=False).parse(
            docx_bytes,
            "demo.docx",
        )

        self.assertEqual(
            sections[0].content.text,
            "Paragraph one.\n\nParagraph two.",
        )

    async def test_consecutive_empty_paragraphs_are_preserved(self) -> None:
        """Consecutive blank paragraphs preserve each intervening line."""
        docx_bytes = _make_docx_simple(
            ["Paragraph one.", "", "", "Paragraph two."],
        )
        sections = await WordParser(include_image=False).parse(
            docx_bytes,
            "demo.docx",
        )

        self.assertEqual(
            sections[0].content.text,
            "Paragraph one.\n\n\nParagraph two.",
        )

    async def test_empty_paragraph_with_bookmark_is_preserved(self) -> None:
        """Word often leaves bookmarks (e.g. ``_GoBack``) on blank
        paragraphs, which are still blank lines."""
        from docx import Document as DocxDocument
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        doc = DocxDocument()
        doc.add_paragraph("Paragraph one.")
        blank = doc.add_paragraph("")
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), "0")
        start.set(qn("w:name"), "_GoBack")
        end = OxmlElement("w:bookmarkEnd")
        end.set(qn("w:id"), "0")
        blank._element.append(start)  # pylint: disable=protected-access
        blank._element.append(end)  # pylint: disable=protected-access
        doc.add_paragraph("Paragraph two.")
        buffer = io.BytesIO()
        doc.save(buffer)

        sections = await WordParser(include_image=False).parse(
            buffer.getvalue(),
            "demo.docx",
        )

        self.assertEqual(
            sections[0].content.text,
            "Paragraph one.\n\nParagraph two.",
        )

    async def test_trailing_empty_paragraphs_do_not_add_newlines(self) -> None:
        """Trailing blank paragraphs do not leave a trailing newline."""
        docx_bytes = _make_docx_simple(["Paragraph one.", "", ""])
        sections = await WordParser(include_image=False).parse(
            docx_bytes,
            "demo.docx",
        )

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].content.text, "Paragraph one.")

    async def test_table_merges_by_default(self) -> None:
        """``separate_table=False`` merges the table into surrounding
        text."""
        docx_bytes = _make_docx_with_table()
        parser = WordParser(include_image=False, separate_table=False)
        sections = await parser.parse(docx_bytes, "demo.docx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "Before table\n"
                            "| A | B |\n"
                            "| --- | --- |\n"
                            "| 1 | 2 |\n\n"
                            "After table"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.docx",
                    "metadata": {},
                },
            ],
        )

    async def test_markdown_table_escapes_special_cells(self) -> None:
        """Pipes and line breaks do not corrupt Markdown table rows."""
        docx_bytes = _make_docx_with_special_table_cells()
        parser = WordParser(include_image=False, separate_table=True)
        sections = await parser.parse(docx_bytes, "special.docx")

        expected_text = (
            "\n".join(
                [
                    r"| A\|B | Path \\\| label |",
                    "| --- | --- |",
                    r"| 1\|2 | Line 1<br>Line 2 |",
                ],
            )
            + "\n"
        )
        self.assertEqual(
            [section.model_dump() for section in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": expected_text,
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "special.docx",
                    "metadata": {},
                },
            ],
        )

    async def test_omitted_table_cells_keep_column_alignment(self) -> None:
        """Omitted cells must not shift columns or truncate later rows."""
        # python-docx has no public setter for omitted grid positions.
        # pylint: disable=protected-access
        from docx import Document
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        doc = Document()
        table = doc.add_table(rows=4, cols=4)
        values = [
            ["unused", "B", "C", "unused"],
            ["a", "b", "c", "d"],
            ["unused", "wide", "unused", "last"],
            ["unused", "unused", "tail", "unused"],
        ]
        for row, cells in zip(table.rows, values):
            for cell, text in zip(row.cells, cells):
                cell.text = text
        table.cell(2, 1).merge(table.cell(2, 2)).text = "wide"
        for row, (before, after) in zip(
            table.rows,
            [(1, 1), (0, 0), (1, 0), (2, 1)],
        ):
            for tag, count, index in (
                ("w:gridBefore", before, 0),
                ("w:gridAfter", after, -1),
            ):
                if count:
                    for _ in range(count):
                        row._tr.remove(row._tr.tc_lst[index])
                    omitted = OxmlElement(tag)
                    omitted.set(qn("w:val"), str(count))
                    row._tr.get_or_add_trPr().append(omitted)
        buffer = io.BytesIO()
        doc.save(buffer)

        for table_format in ("markdown", "json"):
            with self.subTest(table_format=table_format):
                sections = await WordParser(
                    table_format=table_format,
                    separate_table=True,
                ).parse(buffer.getvalue(), "omitted.docx")
                self.assertEqual(len(sections), 1)
                text = sections[0].content.text
                if table_format == "markdown":
                    self.assertEqual(
                        text,
                        "|  | B | C |  |\n"
                        "| --- | --- | --- | --- |\n"
                        "| a | b | c | d |\n"
                        "|  | wide |  | last |\n"
                        "|  |  | tail |  |\n",
                    )
                else:
                    self.assertEqual(
                        json.loads(text.split("\n", 1)[1]),
                        [
                            ["", "B", "C", ""],
                            ["a", "b", "c", "d"],
                            ["", "wide", "", "last"],
                            ["", "", "tail", ""],
                        ],
                    )

    async def test_nested_table_text_is_kept(self) -> None:
        """Text inside a table nested in another cell must survive parsing."""
        docx_bytes = _make_docx_with_nested_table()
        parser = WordParser(include_image=False, separate_table=False)
        sections = await parser.parse(docx_bytes, "nested.docx")

        self.assertListEqual(
            [section.model_dump() for section in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "Before table\n"
                        "| Outer cell<br>Nested cell |\n"
                        "| --- |\n\n"
                        "After table",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "nested.docx",
                    "metadata": {},
                },
            ],
        )

    async def test_table_separated(self) -> None:
        """``separate_table=True`` isolates the table from surrounding
        paragraphs."""
        docx_bytes = _make_docx_with_table()
        parser = WordParser(include_image=False, separate_table=True)
        sections = await parser.parse(docx_bytes, "demo.docx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "Before table",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.docx",
                    "metadata": {},
                },
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "| A | B |\n" "| --- | --- |\n" "| 1 | 2 |\n"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.docx",
                    "metadata": {},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "After table",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.docx",
                    "metadata": {},
                },
            ],
        )

    async def test_table_json_format(self) -> None:
        """``table_format="json"`` emits JSON instead of Markdown."""
        docx_bytes = _make_docx_with_table()
        parser = WordParser(
            include_image=False,
            separate_table=True,
            table_format="json",
        )
        sections = await parser.parse(docx_bytes, "demo.docx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "Before table",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.docx",
                    "metadata": {},
                },
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "<system-info>A table loaded as a JSON "
                            "array:</system-info>\n"
                            '[["A", "B"], ["1", "2"]]'
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.docx",
                    "metadata": {},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "After table",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "demo.docx",
                    "metadata": {},
                },
            ],
        )

    async def test_image_emits_data_block(self) -> None:
        """Embedded images become DataBlock sections."""
        docx_bytes = _make_docx_with_image()
        parser = WordParser(include_image=True)
        sections = await parser.parse(docx_bytes, "rich.docx")

        data_sections = [s for s in sections if s.content.type == "data"]
        self.assertGreaterEqual(len(data_sections), 1)
        ds = data_sections[0]
        self.assertEqual(ds.source, "rich.docx")
        self.assertEqual(ds.content.name, "rich.docx")
        self.assertIn("media_type", ds.metadata)

    async def test_image_inside_table_cell_emits_data_block(self) -> None:
        """Images pasted into table cells are not dropped."""
        docx_bytes = _make_docx_with_image_in_table_cell()
        parser = WordParser(include_image=True)
        sections = await parser.parse(docx_bytes, "table.docx")

        data_sections = [s for s in sections if s.content.type == "data"]
        self.assertEqual(len(data_sections), 1)
        ds = data_sections[0]
        self.assertEqual(ds.source, "table.docx")
        self.assertEqual(ds.metadata["media_type"], "image/png")

        texts = [s.content.text for s in sections if s.content.type == "text"]
        joined = "\n".join(texts)
        self.assertIn("Before table", joined)
        self.assertIn("see screenshot", joined)
        self.assertIn("After table", joined)

    async def test_image_in_table_text_box_emitted_once(self) -> None:
        """An image in a nested text-box paragraph is emitted once."""
        docx_bytes = _make_docx_with_image_in_table_text_box()
        sections = await WordParser(include_image=True).parse(
            docx_bytes,
            "text-box.docx",
        )

        self.assertEqual(
            [section.model_dump() for section in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "Before table\n|  |\n| --- |\n",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "text-box.docx",
                    "metadata": {},
                },
                {
                    "content": {
                        "type": "data",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                        "source": {
                            "type": "base64",
                            "data": _PNG_PIXEL_B64,
                            "media_type": "image/png",
                        },
                        "name": "text-box.docx",
                    },
                    "source": "text-box.docx",
                    "metadata": {"media_type": "image/png"},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "After table",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "text-box.docx",
                    "metadata": {},
                },
            ],
        )

    async def test_image_inside_table_cell_excluded_when_disabled(
        self,
    ) -> None:
        """``include_image=False`` keeps only text sections."""
        docx_bytes = _make_docx_with_image_in_table_cell()
        parser = WordParser(include_image=False)
        sections = await parser.parse(docx_bytes, "table.docx")

        self.assertEqual([s.content.type for s in sections], ["text"])
        self.assertIn(
            "see screenshot",
            "\n".join(s.content.text for s in sections),
        )

    async def test_image_excluded_when_disabled(self) -> None:
        """``include_image=False`` keeps only text sections."""
        docx_bytes = _make_docx_with_image()
        parser = WordParser(include_image=False)
        sections = await parser.parse(docx_bytes, "rich.docx")

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "Text before image\nText after image",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "rich.docx",
                    "metadata": {},
                },
            ],
        )

    async def test_string_input_treated_as_path(self) -> None:
        """``str`` is interpreted as a filesystem path to the DOCX."""
        import tempfile

        docx_bytes = _make_docx_simple(["Alpha"])
        with tempfile.NamedTemporaryFile(
            suffix=".docx",
            delete=False,
        ) as f:
            f.write(docx_bytes)
            path = f.name
        try:
            parser = WordParser(include_image=False)
            sections = await parser.parse(path, "demo.docx")
            self.assertEqual(len(sections), 1)
        finally:
            os.unlink(path)

    async def test_supported_extensions(self) -> None:
        """``.docx`` is the only extension."""
        self.assertEqual(WordParser.supported_extensions(), [".docx"])

    async def test_table_format_validation(self) -> None:
        """Unknown ``table_format`` raises :class:`ValueError`."""
        with self.assertRaises(ValueError):
            WordParser(table_format="csv")  # type: ignore[arg-type]


class WordParserInlineSeparatorTest(IsolatedAsyncioTestCase):
    """Regression coverage for inline tabs and breaks in Word paragraphs."""

    async def test_inline_tabs_and_soft_breaks_are_preserved(self) -> None:
        """Tabs and consecutive soft breaks remain inside a paragraph."""
        from docx import Document as DocxDocument

        expected = "Name\tAlice\nDepartment\t\tEngineering\n\nEnd"
        docx_bytes = _make_docx_simple([expected])
        self.assertEqual(
            DocxDocument(io.BytesIO(docx_bytes)).paragraphs[0].text,
            expected,
        )

        sections = await WordParser(include_image=False).parse(
            docx_bytes,
            "inline.docx",
        )

        self.assertEqual([s.content.text for s in sections], [expected])

    async def test_inline_separators_across_runs_keep_order(self) -> None:
        """Separators in their own runs retain their position and count."""
        from docx import Document as DocxDocument

        doc = DocxDocument()
        paragraph = doc.add_paragraph()
        parts = ["First", "\t", "second", "\n\t", "third", "\n", "last"]
        for part in parts:
            paragraph.add_run(part)
        buffer = io.BytesIO()
        doc.save(buffer)

        sections = await WordParser(include_image=False).parse(
            buffer.getvalue(),
            "runs.docx",
        )

        self.assertEqual(
            [s.content.text for s in sections],
            ["First\tsecond\n\tthird\nlast"],
        )

    async def test_inline_break_types(self) -> None:
        """Text wrapping and carriage returns differ from layout breaks."""
        from docx import Document as DocxDocument
        from docx.oxml import parse_xml
        from docx.oxml.ns import nsdecls

        doc = DocxDocument()
        paragraph = doc.add_paragraph()
        run = parse_xml(
            f'<w:r {nsdecls("w")}>'
            "<w:t>One</w:t><w:cr/><w:t>Two</w:t>"
            '<w:br w:type="textWrapping"/><w:t>Three</w:t>'
            '<w:br w:type="page"/><w:t>Four</w:t>'
            '<w:br w:type="column"/><w:t>Five</w:t></w:r>',
        )
        paragraph._element.append(run)  # pylint: disable=protected-access
        buffer = io.BytesIO()
        doc.save(buffer)

        sections = await WordParser(include_image=False).parse(
            buffer.getvalue(),
            "breaks.docx",
        )

        self.assertEqual(
            [s.content.text for s in sections],
            ["One\nTwo\nThreeFourFive"],
        )

    async def test_inline_separators_in_hyperlinks_and_revisions(self) -> None:
        """Nested runs keep both their text and their inline separators."""
        from docx import Document as DocxDocument
        from docx.oxml import parse_xml
        from docx.oxml.ns import nsdecls

        doc = DocxDocument()
        paragraph = doc.add_paragraph("Before ")
        hyperlink = parse_xml(
            f'<w:hyperlink {nsdecls("w")} w:anchor="bookmark">'
            "<w:r><w:t>linked</w:t><w:tab/><w:t>label</w:t></w:r>"
            "</w:hyperlink>",
        )
        # pylint: disable-next=protected-access
        paragraph._element.append(hyperlink)
        paragraph.add_run(" ")
        revision = parse_xml(
            f'<w:ins {nsdecls("w")} w:id="1" w:author="test">'
            "<w:r><w:t>revised</w:t><w:br/><w:t>text</w:t></w:r></w:ins>",
        )
        paragraph._element.append(revision)  # pylint: disable=protected-access
        paragraph.add_run(" after")
        buffer = io.BytesIO()
        doc.save(buffer)

        sections = await WordParser(include_image=False).parse(
            buffer.getvalue(),
            "nested-runs.docx",
        )

        self.assertEqual(
            [s.content.text for s in sections],
            ["Before linked\tlabel revised\ntext after"],
        )

    async def test_inline_separators_preserve_text_fallback(self) -> None:
        """Separators alone do not hide non-w:t text from the fallback."""
        from docx import Document as DocxDocument
        from docx.oxml import parse_xml
        from docx.oxml.ns import nsdecls

        for separator in ("tab", "br"):
            with self.subTest(separator=separator):
                doc = DocxDocument()
                paragraph = doc.add_paragraph()
                run = parse_xml(
                    f'<w:r {nsdecls("w")}>'
                    f"<w:noBreakHyphen/><w:{separator}/></w:r>",
                )
                # pylint: disable-next=protected-access
                paragraph._element.append(run)
                buffer = io.BytesIO()
                doc.save(buffer)

                sections = await WordParser(include_image=False).parse(
                    buffer.getvalue(),
                    "fallback.docx",
                )

                self.assertEqual([s.content.text for s in sections], ["-"])


class WordParserParagraphTest(IsolatedAsyncioTestCase):
    """Regression coverage for paragraph and text-box extraction."""

    async def test_blank_paragraph_with_a_run_is_preserved(self) -> None:
        """A blank-looking paragraph whose only run holds a space or a line
        break is still a blank line."""
        from docx import Document as DocxDocument

        doc = DocxDocument()
        doc.add_paragraph("Paragraph one.")
        doc.add_paragraph(" ")  # a run that holds only a space
        break_only = doc.add_paragraph()  # a run that holds only <w:br/>
        break_only.add_run().add_break()
        doc.add_paragraph("Paragraph two.")
        buffer = io.BytesIO()
        doc.save(buffer)

        sections = await WordParser(include_image=False).parse(
            buffer.getvalue(),
            "demo.docx",
        )

        self.assertEqual(
            sections[0].content.text,
            "Paragraph one.\n\n\nParagraph two.",
        )

    async def test_paragraph_with_a_field_code_is_not_a_blank_line(
        self,
    ) -> None:
        """A paragraph holding field instructions is not a blank line, even
        though the extractor returns no text for it."""
        from docx import Document as DocxDocument
        from docx.oxml import OxmlElement

        doc = DocxDocument()
        doc.add_paragraph("Paragraph one.")
        field_run = doc.add_paragraph().add_run()
        instr = OxmlElement("w:instrText")
        instr.text = " PAGE "
        # pylint: disable-next=protected-access
        field_run._element.append(instr)
        doc.add_paragraph("Paragraph two.")
        buffer = io.BytesIO()
        doc.save(buffer)

        sections = await WordParser(include_image=False).parse(
            buffer.getvalue(),
            "demo.docx",
        )

        self.assertEqual(
            sections[0].content.text,
            "Paragraph one.\nParagraph two.",
        )

    async def test_text_boxes_are_separate_and_read_once(self) -> None:
        """Read modern text boxes once, with or without a VML fallback."""
        for name, template in (
            ("modern", _DOCX_MODERN_TEXT_BOX),
            ("with_fallback", _DOCX_TEXT_BOX_WITH_VML_FALLBACK),
        ):
            with self.subTest(shape=name):
                sections = await WordParser(include_image=False).parse(
                    _make_docx_with_text_box(template),
                    "demo.docx",
                )
                self.assertEqual(
                    [section.model_dump() for section in sections],
                    _expected_word_sections("Paragraph text\nCallout\nAfter"),
                )

    async def test_inline_separators_with_text_box_branches(self) -> None:
        """Preserve inline separators while reading each text box once."""
        for name, template in (
            ("modern", _DOCX_MODERN_TEXT_BOX),
            ("with_fallback", _DOCX_TEXT_BOX_WITH_VML_FALLBACK),
        ):
            with self.subTest(shape=name):
                template = template.replace(
                    "<w:t>{before}</w:t>",
                    "<w:t>Before</w:t><w:tab/><w:t>text</w:t>",
                ).replace(
                    "<w:t>{inside}</w:t>",
                    "<w:t>Callout</w:t><w:tab/><w:t>value</w:t>"
                    "<w:br/><w:t>next</w:t>",
                )
                sections = await WordParser(include_image=False).parse(
                    _make_docx_with_text_box(template),
                    "demo.docx",
                )
                self.assertEqual(
                    [section.model_dump() for section in sections],
                    _expected_word_sections(
                        "Before\ttext\nCallout\tvalue\nnext\nAfter",
                    ),
                )

    async def test_separator_only_choice_keeps_readable_fallback(
        self,
    ) -> None:
        """Inline separators alone do not make a text-box branch readable."""
        for choice_xml in (
            "<w:tab/>",
            "<w:br/>",
            "<w:cr/>",
            "<w:tab/><w:br/><w:cr/>",
        ):
            with self.subTest(choice_xml=choice_xml):
                template = _DOCX_TEXT_BOX_WITH_VML_FALLBACK.replace(
                    "<w:t>{inside}</w:t>",
                    choice_xml,
                    1,
                )
                sections = await WordParser(include_image=False).parse(
                    _make_docx_with_text_box(template),
                    "demo.docx",
                )
                self.assertEqual(
                    [section.model_dump() for section in sections],
                    _expected_word_sections("Paragraph text\nCallout\nAfter"),
                )

    async def test_separator_only_text_box_keeps_paragraph_fallback(
        self,
    ) -> None:
        """Text-box separators do not hide non-w:t paragraph characters."""
        for name, template in (
            ("modern", _DOCX_MODERN_TEXT_BOX),
            ("with_fallback", _DOCX_TEXT_BOX_WITH_VML_FALLBACK),
        ):
            with self.subTest(shape=name):
                template = template.replace(
                    "<w:t>{before}</w:t>",
                    "<w:noBreakHyphen/><w:tab/>",
                ).replace(
                    "<w:t>{inside}</w:t>",
                    "<w:tab/><w:br/>",
                )
                sections = await WordParser(include_image=False).parse(
                    _make_docx_with_text_box(template),
                    "demo.docx",
                )
                self.assertEqual(
                    [section.model_dump() for section in sections],
                    _expected_word_sections("-\nAfter"),
                )

    async def test_text_box_fallback_is_used_without_readable_choice(
        self,
    ) -> None:
        """Keep fallback text when Choice has no readable Word text."""
        for choice_xml in ("", "<w:t/>", "<w:t>   </w:t>"):
            with self.subTest(choice_xml=choice_xml):
                template = _DOCX_TEXT_BOX_WITH_VML_FALLBACK.replace(
                    "<w:t>{inside}</w:t>",
                    choice_xml,
                    1,
                )
                sections = await WordParser(include_image=False).parse(
                    _make_docx_with_text_box(template),
                    "demo.docx",
                )
                self.assertEqual(
                    [section.model_dump() for section in sections],
                    _expected_word_sections("Paragraph text\nCallout\nAfter"),
                )

    async def test_distinct_text_boxes_with_identical_text_are_kept(
        self,
    ) -> None:
        """Deduplicate alternate representations, not equal text boxes."""
        sections = await WordParser(include_image=False).parse(
            _make_docx_with_text_box(
                _DOCX_TEXT_BOX_WITH_VML_FALLBACK,
                text_box_count=2,
            ),
            "demo.docx",
        )
        self.assertEqual(
            [section.model_dump() for section in sections],
            _expected_word_sections("Paragraph text\nCallout\nCallout\nAfter"),
        )

    async def test_paragraph_with_only_nonbreaking_hyphens_is_kept(
        self,
    ) -> None:
        """The paragraph text fallback preserves non-w:t characters."""
        from docx import Document as DocxDocument
        from docx.oxml import OxmlElement

        document = DocxDocument()
        document.add_paragraph("Before")
        run = document.add_paragraph().add_run()
        for _ in range(3):
            # pylint: disable-next=protected-access
            run._r.append(OxmlElement("w:noBreakHyphen"))
        document.add_paragraph("After")
        buffer = io.BytesIO()
        document.save(buffer)
        sections = await WordParser(include_image=False).parse(
            buffer.getvalue(),
            "demo.docx",
        )
        self.assertEqual(
            [section.model_dump() for section in sections],
            _expected_word_sections("Before\n---\nAfter"),
        )

    async def test_non_textbox_alternatives_are_unchanged(self) -> None:
        """Branch selection must not affect content outside text boxes."""
        from docx import Document as DocxDocument
        from docx.oxml import parse_xml

        document = DocxDocument()
        document.element.body.insert(
            0,
            parse_xml(
                f"<w:p {_DOCX_TEXT_BOX_NS}><w:r><mc:AlternateContent>"
                '<mc:Choice Requires="wps"><w:t>Choice</w:t></mc:Choice>'
                "<mc:Fallback><w:t>Fallback</w:t></mc:Fallback>"
                "</mc:AlternateContent></w:r></w:p>",
            ),
        )
        document.add_paragraph("After")
        buffer = io.BytesIO()
        document.save(buffer)
        sections = await WordParser(include_image=False).parse(
            buffer.getvalue(),
            "demo.docx",
        )
        self.assertEqual(
            [section.model_dump() for section in sections],
            _expected_word_sections("ChoiceFallback\nAfter"),
        )


class WordParserContentControlTest(IsolatedAsyncioTestCase):
    """Regression coverage for block-level Word content controls."""

    async def test_block_content_control_text_is_kept(self) -> None:
        """A block-level ``w:sdt`` does not hide what it contains."""
        sections = await WordParser(include_image=False).parse(
            _make_docx_content_control(wrapped=True),
            "toc.docx",
        )

        self.assertEqual(
            [s.model_dump() for s in sections],
            [
                {
                    "content": {
                        "type": "text",
                        "text": (
                            "Before.\n"
                            "Controlled heading\n"
                            "| cell-a | cell-b |\n"
                            "| --- | --- |\n"
                            "\n"
                            "Nested control\n"
                            "After."
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "toc.docx",
                    "metadata": {},
                },
            ],
        )

    async def test_content_control_is_transparent(self) -> None:
        """The same content parses the same wrapped or unwrapped."""
        parser = WordParser(include_image=False)
        wrapped = await parser.parse(
            _make_docx_content_control(wrapped=True),
            "d.docx",
        )
        plain = await parser.parse(
            _make_docx_content_control(wrapped=False),
            "d.docx",
        )

        self.assertEqual(
            [(s.content.text, s.source, s.metadata) for s in wrapped],
            [(s.content.text, s.source, s.metadata) for s in plain],
        )
