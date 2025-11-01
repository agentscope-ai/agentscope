# -*- coding: utf-8 -*-
"""Test the RAG reader implementations."""
import os
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.rag import TextReader, PDFReader, PowerPointReader


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

    async def test_ppt_reader(self) -> None:
        """Test the PowerPointReader implementation."""
        reader = PowerPointReader(
            chunk_size=200,
            split_by="sentence",
        )
        ppt_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "../examples/functionality/rag/example.pptx",
        )
        docs = await reader(ppt_path=ppt_path)

        # Verify document count (should contain content from slides)
        self.assertEqual(len(docs), 6)

        # Verify exact document content
        doc_texts = [doc.metadata.content["text"] for doc in docs]

        # Verify slide content matches exactly
        self.assertEqual(
            doc_texts[0],
            "Slide 1\nAgentScope\nAgentScope is an innovative multi-agent "
            "framework designed for building intelligent agent systems.",
        )
        self.assertEqual(
            doc_texts[1],
            "It provides powerful tools for agent communication, "
            "task coordination, and distributed problem solving.",
        )
        self.assertEqual(
            doc_texts[2],
            "Slide 2\nTransparent to Developers\nTransparent is "
            "our\xa0FIRST principle.",
        )
        self.assertEqual(
            doc_texts[3],
            "Prompt engineering, API invocation, agent building, workflow "
            "orchestration, all are visible and controllable for developers.",
        )
        self.assertEqual(
            doc_texts[4],
            "No deep encapsulation or implicit magic.",
        )
        self.assertEqual(
            doc_texts[5],
            "Slide 3\nHighly Customizable\nTools, prompt, agent, workflow, "
            "third-party libs & visualization, customization is encouraged "
            "everywhere.",
        )
