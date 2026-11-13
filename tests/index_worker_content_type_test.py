# -*- coding: utf-8 -*-
"""Regression tests for Content-Type dispatch in ``IndexWorker``."""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.app._service._index_worker import IndexWorker
from agentscope.rag import Chunk, Section, TextParser
from agentscope.message import TextBlock


class _Parser:
    """Minimal parser stub that records the selected upload."""

    supported_media_types = ["text/markdown"]
    extensions = [".md", ".markdown"]

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str]] = []

    @classmethod
    def supported_extensions(cls) -> list[str]:
        """Return the extensions declared by this stub."""
        return cls.extensions

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

    def __init__(
        self,
        content_type: str,
        *,
        filename: str = "README.md",
        size: int = 7,
    ) -> None:
        self.record = SimpleNamespace(
            status="pending",
            data=SimpleNamespace(
                content_type=content_type,
                filename=filename,
                blob_uri=f"blob://{filename}",
                size=size,
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
        self.inserted_chunks: list[Chunk] = []
        self.inserted_metadata: dict[str, object] = {}

    async def delete_document(self, document_id: str) -> None:
        """Accept the worker's cleanup call."""
        del document_id

    async def insert_document(
        self,
        *,
        chunks: list[Chunk],
        document_id: str,
        document_metadata: dict[str, object],
    ) -> None:
        """Record the chunks and metadata produced for the document."""
        del document_id
        self.inserted_chunks = chunks
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

    blob_content = b"# AgentScope"

    async def run_pipeline(self) -> None:
        """Expose the pipeline entry point for the regression test."""
        await self._run_pipeline("user-1", "kb-1", "doc-1")

    async def _read_blob(self, blob_uri: str) -> bytes:
        """Return the in-memory payload used by the test."""
        del blob_uri
        return self.blob_content


class IndexWorkerContentTypeTest(IsolatedAsyncioTestCase):
    """Supported Content-Type values must reach the correct parser."""

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

    async def test_yaml_content_types_are_indexed_as_text(self) -> None:
        """Index YAML using its registered type and the legacy alias."""
        payload = b"name: AgentScope\nenabled: true\n"
        for content_type, filename in (
            ("application/yaml", "config.yaml"),
            ("application/yaml", "config.yml"),
            ("application/x-yaml", "config.yaml"),
            ("application/x-yaml", "config.yml"),
        ):
            with self.subTest(
                content_type=content_type,
                filename=filename,
            ):
                storage = _Storage(
                    content_type,
                    filename=filename,
                    size=len(payload),
                )
                manager = _Manager()
                worker = _TestIndexWorker(
                    storage=storage,  # type: ignore[arg-type]
                    blob_store=object(),  # type: ignore[arg-type]
                    knowledge_base_manager=manager,  # type: ignore[arg-type]
                    parsers=[TextParser()],
                    node_id="test-node",
                )
                worker.blob_content = payload

                await worker.run_pipeline()

                self.assertListEqual(
                    storage.statuses,
                    ["parsing", "chunking", "indexing", "ready"],
                )
                self.assertEqual(
                    [
                        chunk.model_dump()
                        for chunk in manager.knowledge.inserted_chunks
                    ],
                    [
                        {
                            "content": {
                                "type": "text",
                                "text": payload.decode(),
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                            "source": filename,
                            "chunk_index": 0,
                            "total_chunks": 1,
                            "metadata": {},
                        },
                    ],
                )
                self.assertDictEqual(
                    manager.knowledge.inserted_metadata,
                    {
                        "filename": filename,
                        "media_type": content_type,
                        "size_bytes": len(payload),
                    },
                )


_PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml"
    ".presentation"
)


class _PptxParser(_Parser):
    """Stub standing in for ``PPTParser``'s declaration."""

    supported_media_types = [_PPTX_MEDIA_TYPE]
    extensions = [".pptx"]


def _worker(
    storage: _Storage,
    parsers: list[object],
) -> tuple[_TestIndexWorker, _Manager]:
    """Build a worker over the given storage and parser registry."""
    manager = _Manager()
    worker = _TestIndexWorker(
        storage=storage,  # type: ignore[arg-type]
        blob_store=object(),  # type: ignore[arg-type]
        knowledge_base_manager=manager,  # type: ignore[arg-type]
        parsers=parsers,  # type: ignore[arg-type]
        node_id="test-node",
    )
    return worker, manager


class IndexWorkerGenericContentTypeTest(IsolatedAsyncioTestCase):
    """A generic multipart Content-Type must not shadow the filename."""

    async def test_generic_octet_stream_routes_by_extension(self) -> None:
        """Index a deck sent as ``application/octet-stream``."""
        storage = _Storage(
            "application/octet-stream",
            filename="lesson.pptx",
        )
        parser = _PptxParser()
        worker, manager = _worker(storage, [parser])

        await worker.run_pipeline()

        self.assertListEqual(
            parser.calls,
            [(b"# AgentScope", "lesson.pptx")],
        )
        self.assertListEqual(
            storage.statuses,
            ["parsing", "chunking", "indexing", "ready"],
        )
        self.assertEqual(
            manager.knowledge.inserted_metadata["media_type"],
            _PPTX_MEDIA_TYPE,
        )

    async def test_extension_match_is_case_insensitive(self) -> None:
        """An upper-case suffix still routes to the declared parser."""
        storage = _Storage(
            "application/octet-stream",
            filename="LESSON.PPTX",
        )
        parser = _PptxParser()
        worker, _ = _worker(storage, [parser])

        await worker.run_pipeline()

        self.assertListEqual(parser.calls, [(b"# AgentScope", "LESSON.PPTX")])

    async def test_registered_generic_parser_stays_authoritative(self) -> None:
        """A parser registered for the generic type still wins."""
        storage = _Storage(
            "application/octet-stream",
            filename="lesson.pptx",
        )
        generic = _Parser()
        generic.supported_media_types = ["application/octet-stream"]
        generic.extensions = [".pptx"]
        by_extension = _PptxParser()
        worker, _ = _worker(storage, [by_extension, generic])

        await worker.run_pipeline()

        self.assertListEqual(generic.calls, [(b"# AgentScope", "lesson.pptx")])
        self.assertListEqual(by_extension.calls, [])

    async def test_specific_unregistered_type_is_not_overridden(self) -> None:
        """An explicit specific type stays authoritative."""
        storage = _Storage("image/png", filename="lesson.pptx")
        parser = _PptxParser()
        worker, _ = _worker(storage, [parser])

        with self.assertRaisesRegex(
            ValueError,
            "No parser registered for media type 'image/png'",
        ):
            await worker.run_pipeline()
        self.assertListEqual(parser.calls, [])

    async def test_generic_type_with_unknown_extension_still_fails(
        self,
    ) -> None:
        """The filename fallback must not invent a route."""
        storage = _Storage(
            "application/octet-stream",
            filename="lesson.unknownext",
        )
        parser = _PptxParser()
        worker, _ = _worker(storage, [parser])

        with self.assertRaisesRegex(
            ValueError,
            "No parser registered for media type "
            "'application/octet-stream'",
        ):
            await worker.run_pipeline()
        self.assertListEqual(parser.calls, [])
