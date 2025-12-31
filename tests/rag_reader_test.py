# -*- coding: utf-8 -*-
"""Test the RAG reader implementations."""
import importlib
import importlib.abc
import os
import sys
import tempfile
from pathlib import Path
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.rag import TextReader, PDFReader


class RAGReaderText(IsolatedAsyncioTestCase):
    """Test cases for RAG reader implementations."""

    def test_py_typed_exists(self) -> None:
        """`py.typed` should exist in the agentscope package."""
        import agentscope

        pkg_root = Path(next(iter(getattr(agentscope, "__path__", [])), ""))
        self.assertTrue((pkg_root / "py.typed").is_file())

    def test_rag_import_does_not_eagerly_import_docx(self) -> None:
        """Importing agentscope.rag should not import python-docx eagerly."""

        forbidden_prefixes = ("docx",)

        saved = {}
        for name in list(sys.modules):
            if name == "agentscope.rag" or name.startswith("agentscope.rag."):
                saved[name] = sys.modules.pop(name)
            elif any(name == p or name.startswith(p + ".") for p in forbidden_prefixes):
                saved[name] = sys.modules.pop(name)

        class _Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):  # type: ignore[override]
                if any(
                    fullname == p or fullname.startswith(p + ".")
                    for p in forbidden_prefixes
                ):
                    raise AssertionError(
                        f"agentscope.rag import attempted forbidden module: {fullname}",
                    )
                return None

        blocker = _Blocker()
        sys.meta_path.insert(0, blocker)
        try:
            importlib.import_module("agentscope.rag")
        finally:
            sys.meta_path.remove(blocker)
            sys.modules.update(saved)

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

    async def test_word_reader_minimal_docx(self) -> None:
        """WordReader should parse a minimal generated .docx file."""
        try:
            from docx import Document as DocxDocument
        except ImportError as e:
            raise AssertionError(
                "python-docx is required for this test; install with "
                "`pip install -e .[dev]`.",
            ) from e

        from agentscope.rag import WordReader

        with tempfile.TemporaryDirectory() as tmpdir:
            doc_path = os.path.join(tmpdir, "example.docx")
            doc = DocxDocument()
            doc.add_paragraph("Hello WordReader.")
            doc.save(doc_path)

            reader = WordReader(
                chunk_size=64,
                split_by="paragraph",
                include_image=False,
            )
            docs = await reader(word_path=doc_path)

        self.assertGreaterEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata.content["type"], "text")
        self.assertIn("Hello WordReader.", docs[0].metadata.content["text"])

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
