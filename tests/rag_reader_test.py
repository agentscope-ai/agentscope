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

    async def test_word_reader(self) -> None:
        """Test the WordReader implementation."""
        # Test default configuration (without images and table separation)
        reader = WordReader(
            chunk_size=200,
            split_by="sentence",
            include_image=False,
            separate_table=False,
        )
        word_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "../examples/functionality/rag/example.docx",
        )
        docs = await reader(word_path=word_path)
        self.assertEqual(len(docs), 15)
        self.assertEqual(
            [_.metadata.content["text"] for _ in docs][:2],
            [
                "The Great Transformations: From Print to Space\n\n"
                "The invention of the printing press in the 15th century "
                "marked a revolutionary change in human history.",
                "Johannes Gutenberg's innovation democratized knowledge and "
                "made books accessible to the common people.",
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
            "../examples/functionality/rag/example.docx",
        )
        docs = await reader(word_path=word_path)

        # Should have 17 documents (15 text + 1 image + 1 table)
        self.assertEqual(len(docs), 17)

        # Count document types
        text_count = 0
        image_count = 0
        table_count = 0

        for doc in docs:
            content = doc.metadata.content
            if isinstance(content, dict) and "type" in content:
                if content["type"] == "text":
                    # Check if it's a table by looking for table
                    # characteristics
                    if "|" in content["text"] and "\n" in content["text"]:
                        table_count += 1
                    else:
                        text_count += 1
                elif content["type"] == "image":
                    image_count += 1

        self.assertEqual(text_count, 15)
        self.assertEqual(image_count, 1)
        self.assertEqual(table_count, 1)  # This document has 1 table

        # Verify image document structure
        image_doc = None
        for _, doc in enumerate(docs):
            content = doc.metadata.content
            if isinstance(content, dict) and content.get("type") == "image":
                image_doc = doc

        self.assertIsNotNone(image_doc, "Should have found an image document")

        # Verify image document structure
        image_content = image_doc.metadata.content
        self.assertEqual(image_content["type"], "image")
        self.assertIn("source", image_content)
        self.assertEqual(image_content["source"]["type"], "base64")
        self.assertEqual(image_content["source"]["media_type"], "image/png")

        # Verify table document structure
        table_doc = None
        for doc in docs:
            content = doc.metadata.content
            if isinstance(content, dict) and content.get("type") == "text":
                if "|" in content["text"] and "\n" in content["text"]:
                    table_doc = doc
                    break

        self.assertIsNotNone(table_doc, "Should have found a table document")
        table_content = table_doc.metadata.content
        self.assertEqual(table_content["type"], "text")
        self.assertIn("|", table_content["text"])
        self.assertIn("\n", table_content["text"])
