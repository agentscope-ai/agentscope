# -*- coding: utf-8 -*-
"""Test the RAG reader implementations."""
import os
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.rag import TextReader, PDFReader, WordReader


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

        import json

        print(
            json.dumps(
                [_.metadata.content.get("text") for _ in docs],
                indent=4,
                ensure_ascii=False,
            ),
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
