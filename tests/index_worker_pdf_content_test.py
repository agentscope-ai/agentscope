# -*- coding: utf-8 -*-
"""Index status for PDFs with raster-only pages."""
from io import BytesIO
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from PIL import Image
from pypdf import PdfWriter

from agentscope.rag import PDFParser

from index_worker_content_type_test import _Storage, _worker
from rag_parser_test import _make_pdf


class IndexWorkerPdfContentTest(IsolatedAsyncioTestCase):
    """Distinguish a scan from a mixed PDF with searchable pages."""

    async def test_index_status_reflects_searchable_pdf_content(self) -> None:
        """A scan needs OCR, while a mixed file indexes its text page."""
        scan = BytesIO()
        Image.new("RGB", (16, 16), "white").save(scan, format="PDF")
        mixed = BytesIO()
        writer = PdfWriter()
        writer.append(BytesIO(scan.getvalue()))
        writer.append(BytesIO(_make_pdf(["Cell structure"])))
        writer.write(mixed)

        for payload, expected in (
            (scan.getvalue(), "error"),
            (mixed.getvalue(), "ready"),
        ):
            with self.subTest(expected=expected):
                storage = _Storage("application/pdf", filename="lesson.pdf")
                storage.acquire_knowledge_document_lease = AsyncMock(
                    return_value=True,
                )
                storage.release_knowledge_document_lease = AsyncMock()
                storage.update_knowledge_document_status = AsyncMock(
                    wraps=storage.update_knowledge_document_status,
                )
                worker, manager = _worker(storage, [PDFParser()])
                worker.blob_content = payload

                await worker.process("user-1", "kb-1", "doc-1")

                self.assertEqual(storage.statuses[-1], expected)
                chunks = manager.knowledge.inserted_chunks
                if expected == "error":
                    self.assertFalse(chunks)
                    update = storage.update_knowledge_document_status
                    self.assertIn(
                        "apply OCR",
                        update.await_args.kwargs["error"],
                    )
                else:
                    self.assertEqual(len(chunks), 1)
                    self.assertEqual(chunks[0].metadata["page"], 2)
                    self.assertIn("Cell structure", chunks[0].content.text)
