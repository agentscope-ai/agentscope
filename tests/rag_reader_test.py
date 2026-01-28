# -*- coding: utf-8 -*-
"""Test the RAG reader implementations."""
import os
import json
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.rag import (
    TextReader,
    PDFReader,
    WordReader,
    ExcelReader,
    PowerPointReader,
)


class RAGReaderText(IsolatedAsyncioTestCase):
    """Test cases for RAG reader implementations."""

    async def test_text_reader(self) -> None:
        """Test the TextReader implementation."""
        # Split by char
        reader = TextReader(
            chunk_size=10,
            split_by="char",
        )
        docs = await reader(
            text="".join(str(i) for i in range(22)),
        )
        self.assertEqual(len(docs), 4)
        self.assertEqual(
            docs[0].metadata.content["text"],
            "0123456789",
        )
        self.assertEqual(
            docs[1].metadata.content["text"],
            "1011121314",
        )
        self.assertEqual(
            docs[2].metadata.content["text"],
            "1516171819",
        )
        self.assertEqual(
            docs[3].metadata.content["text"],
            "2021",
        )

        # Split by sentence
        reader = TextReader(
            chunk_size=10,
            split_by="sentence",
        )
        docs = await reader(
            text="012345678910111213. 141516171819! 2021? 22",
        )
        self.assertEqual(
            [_.metadata.content["text"] for _ in docs],
            ["0123456789", "10111213.", "1415161718", "19!", "2021?", "22"],
        )

        docs = await reader(
            text="01234. 56789! 10111213? 14151617..",
        )
        self.assertEqual(
            [_.metadata.content["text"] for _ in docs],
            ["01234.", "56789!", "10111213?", "14151617.."],
        )

        # Split by paragraph
        reader = TextReader(
            chunk_size=5,
            split_by="paragraph",
        )
        docs = await reader(
            text="01234\n\n5678910111213.\n\n\n1415",
        )
        self.assertEqual(
            [_.metadata.content["text"] for _ in docs],
            ["01234", "56789", "10111", "213.", "1415"],
        )

    async def test_pdf_reader(self) -> None:
        """Test the PDFReader implementation."""
        reader = PDFReader(
            chunk_size=200,
            split_by="sentence",
        )
        pdf_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "../examples/functionality/rag/example.pdf",
        )
        docs = await reader(pdf_path=pdf_path)
        self.assertEqual(len(docs), 17)
        self.assertEqual(
            [_.metadata.content["text"] for _ in docs][:2],
            [
                "1\nThe Great Transformations: From Print to Space\n"
                "The invention of the printing press in the 15th century "
                "marked a revolutionary change in \nhuman history.",
                "Johannes Gutenberg's innovation democratized knowledge and "
                "made books \naccessible to the common people.",
            ],
        )

    async def test_word_reader_with_images_and_tables(self) -> None:
        """Test the WordReader implementation with images and table
        separation."""
        # Test with images and table separation enabled
        reader = WordReader(
            chunk_size=200,
            split_by="sentence",
            include_image=True,
            separate_table=True,
        )
        word_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "../tests/test.docx",
        )
        docs = await reader(word_path=word_path)

        self.assertListEqual(
            [_.metadata.content["type"] for _ in docs],
            ["text"] * 4 + ["image"] * 2 + ["text", "image", "text", "text"],
        )

        self.assertEqual(
            [_.metadata.content.get("text") for _ in docs],
            [
                "AgentScope\n"
                "标题2\n"
                "This is a test file for AgentScope word reader.",
                "标题3\nTest table:",
                "| Header1 | Header2 | Header3 | Header4 |\n"
                "| --- | --- | --- | --- |\n"
                "| 1 | 2 | 3 | 4 |\n"
                "| 5 | 6 | 7 | 8 |",
                "\nTest list:\nAlice\nBob\nCharlie\nDavid\nTest image:",
                None,  # image
                None,  # image
                "\nText between images",
                None,  # image
                "\nText between image and table",
                "| a | b | c |\n| --- | --- | --- |\n| d\ne | f | g |",
            ],
        )

        self.assertEqual(
            [
                _.metadata.content["source"]["media_type"]
                for _ in docs
                if _.metadata.content["type"] == "image"
            ],
            ["image/png", "image/png", "image/png"],
        )

    async def test_excel_reader_with_images_and_tables(self) -> None:
        """Test the ExcelReader implementation with images and table
        separation."""
        # Test with images and table separation enabled
        reader = ExcelReader(
            chunk_size=200,
            split_by="sentence",
            include_image=True,
            separate_table=True,
            include_cell_coordinates=True,
            table_format="markdown",
        )
        excel_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "test.xlsx",
        )
        docs = await reader(excel_path=excel_path)

        # Verify document types match expected sequence
        # Expected: table blocks from first sheet, then image (row 9),
        # then table blocks from second sheet
        # Order is based on row positions: table (row 0-5) → image (row 9)
        # → table (row 0-4)
        # Note: with include_cell_coordinates=True, cell coordinates are added
        # to each cell (e.g., [A1], [B1], etc.), which increases text length
        # and results in more chunks
        self.assertListEqual(
            [_.metadata.content["type"] for _ in docs],
            ["text"] * 3 + ["image"] * 1 + ["text"] * 5,
        )

        # Verify exact document content
        doc_texts = [_.metadata.content.get("text") for _ in docs]

        # Verify sheet headers and table content with cell coordinates
        # First text block should contain Employee Info sheet header and table
        # Note: Due to chunk_size=200, the rows are truncated
        # Order: table (row 0-5) → image (row 9) → table (row 0-4)
        self.assertEqual(
            doc_texts[0],
            "Sheet: Employee Info\n"
            "| [A1] John Smith | [B1] 25 | [C1] Engineering | "
            "[D1] 8000 | [E1] 2020-01-15 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| [A2] Jane Doe | [B2] 30 | [C2] Sales | "
            "[D2] 12000 | [E2] 2019-03-2",
        )
        # Second text block continues the employee table
        self.assertEqual(
            doc_texts[1],
            "0 |\n"
            "| [A3] Mike \\| Johnson | [B3] 35 | [C3] HR | "
            "[D3] 9000 | [E3] 2021-06-10 |\n"
            "| [A4] Sarah Wilson | [B4] 28 | [C4] Finance | "
            "[D4] 10000 | [E4] 2020-09-05 |\n"
            "| [A5] David Brown | [B5] 32 | [C5] Marketi",
        )
        # Third text block continues the employee table
        self.assertEqual(
            doc_texts[2],
            "ng | [D5] 11000 | [E5] 2018-12-01 |",
        )
        # Image block (text is None)
        self.assertIsNone(doc_texts[3])
        # Fourth text block should contain Product Info sheet header and
        # start of table
        self.assertEqual(
            doc_texts[4],
            "Sheet: Product Info\n"
            "| [A1] Product A | [B1] 100 | [C1] 50 | "
            "[D1] High-quality Product A, suitable for various scenarios.",
        )
        # Remaining blocks continue the product table
        self.assertEqual(
            doc_texts[5],
            "|\n"
            "| --- | --- | --- | --- |\n"
            "| [A2] Product B | [B2] 200 | [C2] 30 | "
            "[D2] Product B offers excellent performance.",
        )
        self.assertEqual(
            doc_texts[6],
            "|\n"
            "| [A3] Product C | [B3] 300 | [C3] 20 | "
            "[D3] Product C is a market-leading solution.",
        )
        self.assertEqual(
            doc_texts[7],
            "|\n"
            "| [A4] Product D | [B4] 400 | [C4] 40 | "
            "[D4] Product D provides comprehensive functionality.",
        )

        # Verify image media types
        self.assertEqual(
            [
                _.metadata.content["source"]["media_type"]
                for _ in docs
                if _.metadata.content["type"] == "image"
            ],
            ["image/png"],
        )

    async def test_ppt_reader_with_images_and_tables(self) -> None:
        """Test the PowerPointReader implementation with images and table
        separation."""
        # Test with images and table separation enabled (using defaults)
        reader = PowerPointReader(
            chunk_size=200,
            split_by="sentence",
            separate_table=True,
        )
        ppt_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "../tests/test.pptx",
        )
        docs = await reader(ppt_path=ppt_path)

        # Verify document types match expected sequence
        # Expected: text blocks from slides, then table, then image
        self.assertListEqual(
            [_.metadata.content["type"] for _ in docs],
            ["text"] * 5 + ["image"] * 1 + ["text"],
        )

        # Verify exact document content
        doc_texts = [_.metadata.content.get("text") for _ in docs]

        # Verify slide content (with slide tags by default)
        self.assertEqual(
            doc_texts[0],
            "<slide index=1>\nAgentScope\nText content in slide 1\n</slide>",
        )
        self.assertEqual(
            doc_texts[1],
            "<slide index=2>\nTitle 2\nText content above table",
        )
        # Table should be extracted as a separate block with Markdown format
        self.assertEqual(
            doc_texts[2],
            "| Name | Age | Career |\n"
            "| --- | --- | --- |\n"
            "| Alice | 25 | Teacher |\n"
            "| Bob | 26 | Doctor |",
        )
        self.assertEqual(
            doc_texts[3],
            "Text content below table\n</slide>",
        )
        self.assertEqual(
            doc_texts[4],
            "<slide index=3>\nTitle 3\ntext content above image",
        )
        # Image block
        self.assertIsNone(doc_texts[5])
        self.assertEqual(
            doc_texts[6],
            "text content below image\n</slide>",
        )

        # Verify image media types
        self.assertEqual(
            [
                _.metadata.content["source"]["media_type"]
                for _ in docs
                if _.metadata.content["type"] == "image"
            ],
            ["image/png"],
        )

    async def test_ppt_reader_with_json_table_format(self) -> None:
        """Test the PowerPointReader with JSON table format."""

        reader = PowerPointReader(
            chunk_size=500,
            split_by="sentence",
            include_image=False,
            separate_table=True,
            table_format="json",
        )
        ppt_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "../tests/test.pptx",
        )
        docs = await reader(ppt_path=ppt_path)

        # Find the table block
        table_texts = [
            _.metadata.content.get("text")
            for _ in docs
            if _.metadata.content.get("text")
            and "JSON array" in _.metadata.content.get("text", "")
        ]

        # Verify we have a table in JSON format
        self.assertEqual(len(table_texts), 1)

        # Extract JSON part (after the system-info tag)
        table_text = table_texts[0]
        json_part = table_text.split("\n", 1)[1]

        # Verify it's valid JSON and matches expected table content
        parsed = json.loads(json_part)
        self.assertEqual(
            parsed,
            [
                ["Name", "Age", "Career"],
                ["Alice", "25", "Teacher"],
                ["Bob", "26", "Doctor"],
            ],
        )

    async def test_ppt_reader_without_image(self) -> None:
        """Test the PowerPointReader without image extraction."""
        reader = PowerPointReader(
            chunk_size=200,
            split_by="sentence",
            include_image=False,
            separate_table=True,
        )
        ppt_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "../tests/test.pptx",
        )
        docs = await reader(ppt_path=ppt_path)

        # Verify no image blocks are present
        image_blocks = [
            _ for _ in docs if _.metadata.content["type"] == "image"
        ]
        self.assertEqual(len(image_blocks), 0)

        # All blocks should be text type
        self.assertTrue(
            all(_.metadata.content["type"] == "text" for _ in docs),
        )

    async def test_ppt_reader_merged_table(self) -> None:
        """Test the PowerPointReader with merged table
        (separate_table=False)."""
        reader = PowerPointReader(
            chunk_size=500,
            split_by="sentence",
            include_image=False,
            separate_table=False,
        )
        ppt_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "../tests/test.pptx",
        )
        docs = await reader(ppt_path=ppt_path)

        # When separate_table=False, table should be merged with adjacent text
        # Find the document that contains the table
        table_doc = None
        for doc in docs:
            text = doc.metadata.content.get("text", "")
            if "Name" in text and "Age" in text and "Career" in text:
                table_doc = doc
                break

        self.assertIsNotNone(table_doc)
        # The table should be merged with surrounding text (with slide tags)
        table_text = table_doc.metadata.content.get("text", "")
        self.assertEqual(
            table_text,
            "<slide index=2>\n"
            "Title 2\n"
            "Text content above table\n"
            "| Name | Age | Career |\n"
            "| --- | --- | --- |\n"
            "| Alice | 25 | Teacher |\n"
            "| Bob | 26 | Doctor |\n"
            "\n"
            "Text content below table\n"
            "</slide>",
        )

    async def test_ppt_reader_without_slide_tags(self) -> None:
        """Test the PowerPointReader without slide prefix/suffix XML tags."""
        reader = PowerPointReader(
            chunk_size=500,
            split_by="sentence",
            include_image=False,
            separate_table=False,
            slide_prefix=None,
            slide_suffix=None,
        )
        ppt_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "../tests/test.pptx",
        )
        docs = await reader(ppt_path=ppt_path)

        # Without slide_prefix/suffix, content should not have XML tags
        doc_texts = [_.metadata.content.get("text") for _ in docs]

        # Verify exact content without slide tags
        self.assertEqual(
            doc_texts[0],
            "AgentScope\nText content in slide 1",
        )
        self.assertEqual(
            doc_texts[1],
            "Title 2\n"
            "Text content above table\n"
            "| Name | Age | Career |\n"
            "| --- | --- | --- |\n"
            "| Alice | 25 | Teacher |\n"
            "| Bob | 26 | Doctor |\n"
            "\n"
            "Text content below table",
        )
        self.assertEqual(
            doc_texts[2],
            "Title 3\ntext content above image\ntext content below image",
        )
