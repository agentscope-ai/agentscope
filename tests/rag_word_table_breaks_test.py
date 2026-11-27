# -*- coding: utf-8 -*-
"""Word table cell break handling."""
import io
import json
from unittest import IsolatedAsyncioTestCase

from docx import Document
from docx.enum.text import WD_BREAK

from agentscope.rag import WordParser


class WordTableBreaksTest(IsolatedAsyncioTestCase):
    """Only text-wrapping breaks become newlines in a cell."""

    async def test_break_types_in_table_cells(self) -> None:
        """Page and column breaks do not insert text line breaks."""
        for kind, expected in (
            (WD_BREAK.PAGE, "beforeafter"),
            (WD_BREAK.COLUMN, "beforeafter"),
            (WD_BREAK.LINE, "before\nafter"),
        ):
            with self.subTest(kind=kind):
                document = Document()
                para = (
                    document.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0]
                )
                para.add_run("before").add_break(kind)
                para.add_run("after")
                buf = io.BytesIO()
                document.save(buf)
                sections = await WordParser(
                    include_image=False,
                    separate_table=True,
                    table_format="json",
                ).parse(buf.getvalue(), "sample.docx")
                payload = sections[0].content.text.split("\n", 1)[1]
                self.assertEqual(json.loads(payload), [[expected]])
