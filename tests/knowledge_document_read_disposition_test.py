# -*- coding: utf-8 -*-
"""Tests for the ``Content-Disposition`` header of the KB document read
endpoint, which builds an RFC 5987 ``filename*`` value out of whatever the
uploading client sent as the file name."""
from email.message import EmailMessage
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.app._router._knowledge_base import read_knowledge_document


class _StubService:
    """Returns a fixed upload record and a tiny chunked body."""

    def __init__(self, filename: str) -> None:
        self._filename = filename

    async def stream_document_content(  # pylint: disable=unused-argument
        self,
        **kwargs: object,
    ) -> tuple[object, int, AsyncIterator[bytes]]:
        """Return the fixed record, size and a tiny chunked body."""

        async def _chunks() -> AsyncIterator[bytes]:
            yield b"payload"

        record = SimpleNamespace(
            data=SimpleNamespace(
                filename=self._filename,
                content_type="application/pdf",
            ),
        )
        return record, 7, _chunks()


class TestReadKnowledgeDocumentDisposition(IsolatedAsyncioTestCase):
    """The header must survive the round trip through an RFC 5987 parser."""

    async def _disposition(self, filename: str) -> str:
        response = await read_knowledge_document(
            knowledge_base_id="kb-1",
            document_id="doc-1",
            download=True,
            token=None,
            x_user_id="u-1",
            service=_StubService(filename),  # type: ignore[arg-type]
            download_secret="s3cret",
        )
        return response.headers["Content-Disposition"]

    @staticmethod
    def _parsed_filename(header: str) -> str | None:
        message = EmailMessage()
        message["Content-Disposition"] = header
        return message.get_filename()

    async def test_filename_with_a_path_separator_keeps_its_name(
        self,
    ) -> None:
        """A client-supplied name may contain "/" and must not be truncated."""
        header = await self._disposition("reports/quarterly.pdf")

        self.assertEqual(
            self._parsed_filename(header),
            "reports/quarterly.pdf",
        )

    async def test_non_ascii_filename_with_a_separator_survives(self) -> None:
        """Percent-encoding must work together with a non-ASCII name."""
        header = await self._disposition("uploads/报告 1.pdf")

        self.assertEqual(
            self._parsed_filename(header),
            "uploads/\u62a5\u544a 1.pdf",
        )

    async def test_plain_filename_is_unchanged(self) -> None:
        """A name without separators is emitted exactly as before."""
        header = await self._disposition("notes.pdf")

        self.assertEqual(header, "attachment; filename*=UTF-8''notes.pdf")
