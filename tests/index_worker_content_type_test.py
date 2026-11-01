# -*- coding: utf-8 -*-
"""Regression tests for Content-Type normalisation in ``IndexWorker``."""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from agentscope.app._service._index_worker import IndexWorker
from agentscope.rag import Section
from agentscope.message import TextBlock


class _Parser:
    """Minimal parser stub that records the selected upload."""

    supported_media_types = ["text/markdown"]

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str]] = []

    async def parse(self, file: bytes, filename: str) -> list[Section]:
        """Return one text section and record the parser invocation."""
        self.calls.append((file, filename))
        return [
            Section(
                content=TextBlock(text=file.decode()),
                source=filename,
            ),
        ]


class _Storage:
    """Minimal storage stub for the indexing pipeline."""

    def __init__(self, content_type: str) -> None:
        self.record = SimpleNamespace(
            data=SimpleNamespace(
                content_type=content_type,
                filename="README.md",
                blob_uri="blob://README.md",
                size=7,
            ),
        )
        self.statuses: list[str] = []

    async def get_knowledge_document(self, *args: str) -> object:
        """Return the document carrying the parameterised content type."""
        del args
        return self.record

    async def update_knowledge_document_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        status: str,
        **kwargs: object,
    ) -> None:
        """Record lifecycle transitions without requiring a database."""
        del user_id, knowledge_base_id, document_id, kwargs
        self.statuses.append(status)


class _Knowledge:
    """Minimal vector-store facade used by the worker."""

    def __init__(self) -> None:
        self.inserted_metadata: dict[str, object] = {}

    async def delete_document(self, document_id: str) -> None:
        """Accept the worker's cleanup call."""
        del document_id

    async def insert_document(
        self,
        *,
        chunks: list[object],
        document_id: str,
        document_metadata: dict[str, object],
    ) -> None:
        """Record the metadata produced for the indexed document."""
        del chunks, document_id
        self.inserted_metadata = document_metadata


class _Manager:
    """Minimal knowledge-base manager for the worker."""

    def __init__(self) -> None:
        self.knowledge = _Knowledge()

    async def get_knowledge_base(self, *args: str) -> object:
        """Return a legacy knowledge base using the default chunker."""
        del args
        return SimpleNamespace(
            id="kb-1",
            data=SimpleNamespace(chunker_config=None),
        )

    async def get_knowledge(self, *args: str) -> _Knowledge:
        """Return the vector-store facade."""
        del args
        return self.knowledge


class _TestIndexWorker(IndexWorker):
    """Index worker with an in-memory blob source."""

    async def run_pipeline(self) -> None:
        """Expose the pipeline entry point for the regression test."""
        await self._run_pipeline("user-1", "kb-1", "doc-1")

    async def _read_blob(self, blob_uri: str) -> bytes:
        """Return the fixed Markdown payload used by the test."""
        del blob_uri
        return b"# AgentScope"


class IndexWorkerContentTypeTest(IsolatedAsyncioTestCase):
    """Content-Type parameters must not prevent parser dispatch."""

    async def test_parameterised_and_mixed_case_content_type_is_normalised(
        self,
    ) -> None:
        """Route equivalent Markdown media types to the Markdown parser."""
        for content_type in (
            "text/markdown; charset=utf-8",
            " TEXT/MARKDOWN ; charset=binary ",
        ):
            with self.subTest(content_type=content_type):
                storage = _Storage(content_type)
                manager = _Manager()
                parser = _Parser()
                worker = _TestIndexWorker(
                    storage=storage,  # type: ignore[arg-type]
                    blob_store=object(),  # type: ignore[arg-type]
                    knowledge_base_manager=manager,  # type: ignore[arg-type]
                    parsers=[parser],  # type: ignore[list-item]
                    node_id="test-node",
                )

                await worker.run_pipeline()

                self.assertListEqual(
                    parser.calls,
                    [(b"# AgentScope", "README.md")],
                )
                self.assertListEqual(
                    storage.statuses,
                    ["parsing", "chunking", "indexing", "ready"],
                )
                self.assertDictEqual(
                    manager.knowledge.inserted_metadata,
                    {
                        "filename": "README.md",
                        "media_type": "text/markdown",
                        "size_bytes": 7,
                    },
                )
