# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression coverage for text moved with Word's Track Changes feature."""
import io
from typing import Any
from unittest import IsolatedAsyncioTestCase

from agentscope.rag import WordParser


def _append_tracked_move(paragraph: Any) -> None:
    """Add a complete move pair with text between its two locations."""
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls

    fragment = parse_xml(
        f'<w:p {nsdecls("w")}>'
        '<w:moveToRangeStart w:id="0" w:name="move1" '
        'w:author="Example" w:date="2026-11-16T00:00:00Z"/>'
        '<w:moveTo w:id="1" w:author="Example">'
        '<w:r><w:t xml:space="preserve">Moved sentence. </w:t></w:r>'
        '</w:moveTo><w:moveToRangeEnd w:id="0"/>'
        "<w:r><w:t>Other sentence.</w:t></w:r>"
        '<w:moveFromRangeStart w:id="2" w:name="move1" '
        'w:author="Example" w:date="2026-11-16T00:00:00Z"/>'
        '<w:moveFrom w:id="3" w:author="Example">'
        '<w:r><w:t xml:space="preserve">Moved sentence. </w:t>'
        "<w:tab/><w:br/></w:r>"
        '</w:moveFrom><w:moveFromRangeEnd w:id="2"/>'
        "</w:p>",
    )
    for element in list(fragment):
        paragraph.append(element)


def _document_bytes(document: Any) -> bytes:
    """Serialize an in-memory DOCX fixture."""
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class WordTrackedMovesTest(IsolatedAsyncioTestCase):
    """Keep moved text once, at its destination, in the public parser."""

    async def test_paragraph_keeps_only_move_destination(self) -> None:
        """Omit source runs and their separators without losing neighbours."""
        from docx import Document

        document = Document()
        document.add_paragraph("Before")
        paragraph = document.add_paragraph()
        _append_tracked_move(
            paragraph._element,
        )
        document.add_paragraph("After")

        sections = await WordParser(include_image=False).parse(
            _document_bytes(document),
            "moves.docx",
        )

        self.assertEqual(len(sections), 1)
        self.assertEqual(
            sections[0].content.text,
            "Before\nMoved sentence. Other sentence.\nAfter",
        )
        self.assertEqual(sections[0].source, "moves.docx")

    async def test_text_box_keeps_only_move_destination(self) -> None:
        """Apply the same revision handling to anchored text-box content."""
        from docx import Document
        from docx.oxml import parse_xml
        from docx.oxml.ns import nsdecls, qn

        document = Document()
        paragraph = document.add_paragraph("Outside")
        drawing = parse_xml(
            f'<w:r {nsdecls("w")} '
            'xmlns:v="urn:schemas-microsoft-com:vml">'
            "<w:pict><v:shape><v:textbox><w:txbxContent><w:p/>"
            "</w:txbxContent></v:textbox></v:shape></w:pict></w:r>",
        )
        text_box_paragraph = drawing.find(".//" + qn("w:p"))
        _append_tracked_move(text_box_paragraph)
        paragraph._element.append(drawing)

        sections = await WordParser(include_image=False).parse(
            _document_bytes(document),
            "moves.docx",
        )

        self.assertEqual(len(sections), 1)
        self.assertEqual(
            sections[0].content.text,
            "Outside\nMoved sentence. Other sentence.",
        )

    async def test_table_keeps_only_move_destination(self) -> None:
        """Markdown and JSON must both omit the obsolete cell text."""
        from docx import Document

        document = Document()
        table = document.add_table(rows=2, cols=1)
        table.cell(0, 0).text = "Header"
        paragraph = table.cell(1, 0).paragraphs[0]
        _append_tracked_move(
            paragraph._element,
        )
        expected = {
            "markdown": "| Header |\n| --- |\n"
            "| Moved sentence. Other sentence. |\n",
            "json": "<system-info>A table loaded as a JSON array:"
            '</system-info>\n[["Header"], '
            '["Moved sentence. Other sentence."]]',
        }
        for table_format, text in expected.items():
            with self.subTest(table_format=table_format):
                sections = await WordParser(
                    include_image=False,
                    separate_table=True,
                    table_format=table_format,
                ).parse(_document_bytes(document), "moves.docx")

                self.assertEqual(len(sections), 1)
                self.assertEqual(sections[0].content.text, text)
