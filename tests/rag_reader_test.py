# -*- coding: utf-8 -*-
"""Test the RAG reader implementations."""
import os
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.rag import TextReader, PDFReader, ExcelReader


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

    async def test_excel__reader(self) -> None:
        """Test the ExcelReader implementation."""
        reader = ExcelReader(
            chunk_size=512,
            split_by="paragraph",
        )
        excel_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "../examples/functionality/rag/example.xlsx",
        )
        docs = await reader(excel_path=excel_path)

        # Verify document count (should contain content from two sheets)
        self.assertEqual(len(docs), 11)

        # Verify exact document content
        doc_texts = [doc.metadata.content["text"] for doc in docs]

        # Verify sheet headers
        self.assertEqual(doc_texts[0], "Sheet: Employee Info")
        self.assertEqual(doc_texts[6], "Sheet: Product Info")

        # Verify employee data rows
        self.assertEqual(
            doc_texts[1],
            "John Smith | 25 | Engineering | 8000 | 2020-01-15",
        )
        self.assertEqual(
            doc_texts[2],
            "Jane Doe | 30 | Sales | 12000 | 2019-03-20",
        )
        self.assertEqual(
            doc_texts[3],
            "Mike Johnson | 35 | HR | 9000 | 2021-06-10",
        )
        self.assertEqual(
            doc_texts[4],
            "Sarah Wilson | 28 | Finance | 10000 | 2020-09-05",
        )
        self.assertEqual(
            doc_texts[5],
            "David Brown | 32 | Marketing | 11000 | 2018-12-01",
        )

        # Verify product data rows
        self.assertEqual(
            doc_texts[7],
            "Product A | 100 | 50 | High-quality Product A, suitable for "
            "various scenarios.",
        )
        self.assertEqual(
            doc_texts[8],
            "Product B | 200 | 30 | Product B offers excellent performance.",
        )
        self.assertEqual(
            doc_texts[9],
            "Product C | 300 | 20 | Product C is a market-leading solution.",
        )
        self.assertEqual(
            doc_texts[10],
            "Product D | 400 | 40 | Product D provides comprehensive "
            "functionality.",
        )
