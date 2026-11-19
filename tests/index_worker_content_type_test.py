# -*- coding: utf-8 -*-
"""Regression tests for Content-Type dispatch in ``IndexWorker``."""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from utils import AnyString

from agentscope.app._service._index_worker import IndexWorker
from agentscope.rag import Chunk, ExcelParser, Section, TextParser
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
        content_type: str | None,
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


class _JsonParser(_Parser):
    """Record calls to a specialized JSON parser."""

    supported_media_types = ["application/json"]
    extensions = [".json"]


class _OtherJsonParser(_JsonParser):
    """Declare a competing media type for the same extension."""

    supported_media_types = ["application/x-json"]


def _worker(
    storage: _Storage,
    parsers: list[object] | dict[str, object],
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

    async def test_filename_routing_preserves_custom_parser(self) -> None:
        """Use the registered JSON override for inferred media types."""
        for content_type in (None, "", "application/octet-stream"):
            for use_dict in (False, True):
                with self.subTest(
                    content_type=content_type,
                    use_dict=use_dict,
                ):
                    storage = _Storage(content_type, filename="data.json")
                    parser = _JsonParser()
                    parsers: list[object] | dict[str, object] = (
                        {
                            "text/plain": TextParser(),
                            "application/json": parser,
                        }
                        if use_dict
                        else [TextParser(), parser]
                    )
                    worker, manager = _worker(storage, parsers)

                    await worker.run_pipeline()

                    self.assertEqual(
                        {
                            "calls": parser.calls,
                            "statuses": storage.statuses,
                            "metadata": manager.knowledge.inserted_metadata,
                        },
                        {
                            "calls": [(b"# AgentScope", "data.json")],
                            "statuses": [
                                "parsing",
                                "chunking",
                                "indexing",
                                "ready",
                            ],
                            "metadata": {
                                "filename": "data.json",
                                "media_type": "application/json",
                                "size_bytes": 7,
                            },
                        },
                    )

    async def test_text_fallback_preserves_media_type(self) -> None:
        """Keep JSON metadata when the parser supports multiple types."""
        for content_type in (None, "", "application/octet-stream"):
            with self.subTest(content_type=content_type):
                storage = _Storage(content_type, filename="data.json")
                worker, manager = _worker(storage, [TextParser()])

                await worker.run_pipeline()

                self.assertEqual(
                    manager.knowledge.inserted_metadata,
                    {
                        "filename": "data.json",
                        "media_type": "application/json",
                        "size_bytes": 7,
                    },
                )

    async def test_extension_fallback_without_system_mime_mapping(
        self,
    ) -> None:
        """Resolve PPTX even when the platform MIME table lacks it."""
        for content_type in (None, "", "application/octet-stream"):
            with self.subTest(content_type=content_type):
                storage = _Storage(content_type, filename="LESSON.PPTX")
                parser = _PptxParser()
                worker, manager = _worker(storage, [parser])

                with patch("mimetypes.guess_type", return_value=(None, None)):
                    await worker.run_pipeline()

                self.assertEqual(
                    {
                        "calls": parser.calls,
                        "metadata": manager.knowledge.inserted_metadata,
                    },
                    {
                        "calls": [(b"# AgentScope", "LESSON.PPTX")],
                        "metadata": {
                            "filename": "LESSON.PPTX",
                            "media_type": _PPTX_MEDIA_TYPE,
                            "size_bytes": 7,
                        },
                    },
                )

    async def test_ambiguous_extension_does_not_invent_media_type(
        self,
    ) -> None:
        """Reject ambiguous declarations without a filename MIME mapping."""
        for parsers in (
            [TextParser()],
            [_JsonParser(), _OtherJsonParser()],
        ):
            storage = _Storage(
                "application/octet-stream",
                filename="data.json",
            )
            worker, manager = _worker(storage, parsers)

            with self.subTest(parsers=parsers):
                with patch("mimetypes.guess_type", return_value=(None, None)):
                    with self.assertRaisesRegex(
                        ValueError,
                        "No parser registered for media type "
                        "'application/octet-stream'",
                    ):
                        await worker.run_pipeline()

                self.assertEqual(storage.statuses, [])
                self.assertEqual(manager.knowledge.inserted_metadata, {})

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


class _LegacyExcelParser(_Parser):
    """Record calls routed to the legacy Excel media type."""

    supported_media_types = ["application/vnd.ms-excel"]
    extensions = [".xls"]


class IndexWorkerCsvLegacyExcelTypeTest(IsolatedAsyncioTestCase):
    """A ``.csv`` labelled as legacy Excel must be indexed as text."""

    payload = b"chapter,topic\n1,Photosynthesis\n"

    async def test_csv_with_legacy_excel_type_is_indexed_as_text(
        self,
    ) -> None:
        """Route explicit and inferred legacy Excel types to ``text/csv``."""
        for content_type in (
            "application/vnd.ms-excel",
            "Application/VND.MS-Excel; charset=utf-8",
            None,
            "",
            "application/octet-stream",
        ):
            with self.subTest(content_type=content_type):
                storage = _Storage(
                    content_type,
                    filename="lesson.CSV",
                    size=len(self.payload),
                )
                worker, manager = _worker(
                    storage,
                    [TextParser(), ExcelParser()],
                )
                worker.blob_content = self.payload

                # Windows' MIME table maps ``.csv`` to the legacy Excel type.
                with patch(
                    "mimetypes.guess_type",
                    return_value=("application/vnd.ms-excel", None),
                ):
                    await worker.run_pipeline()

                self.assertEqual(
                    {
                        "statuses": storage.statuses,
                        "text": [
                            chunk.content.text
                            for chunk in manager.knowledge.inserted_chunks
                        ],
                        "media_type": manager.knowledge.inserted_metadata[
                            "media_type"
                        ],
                    },
                    {
                        "statuses": [
                            "parsing",
                            "chunking",
                            "indexing",
                            "ready",
                        ],
                        "text": [self.payload.decode()],
                        "media_type": "text/csv",
                    },
                )

    async def test_xls_workbook_keeps_excel_route(self) -> None:
        """A real ``.xls`` workbook still reaches the Excel parser."""
        for content_type in ("application/vnd.ms-excel", None):
            with self.subTest(content_type=content_type):
                storage = _Storage(content_type, filename="lesson.xls")
                excel = _LegacyExcelParser()
                worker, manager = _worker(storage, [TextParser(), excel])

                await worker.run_pipeline()

                self.assertListEqual(
                    excel.calls,
                    [(b"# AgentScope", "lesson.xls")],
                )
                self.assertEqual(
                    manager.knowledge.inserted_metadata["media_type"],
                    "application/vnd.ms-excel",
                )

    async def test_csv_without_text_csv_parser_keeps_route(self) -> None:
        """Without a ``text/csv`` parser the caller's routing is kept."""
        storage = _Storage("application/vnd.ms-excel", filename="lesson.csv")
        excel = _LegacyExcelParser()
        worker, _ = _worker(storage, [excel])

        await worker.run_pipeline()

        self.assertListEqual(excel.calls, [(b"# AgentScope", "lesson.csv")])

    async def test_csv_with_unrelated_type_is_not_overridden(self) -> None:
        """An explicit unrelated type stays authoritative for ``.csv``."""
        storage = _Storage("image/png", filename="lesson.csv")
        worker, manager = _worker(storage, [TextParser(), ExcelParser()])

        with self.assertRaisesRegex(
            ValueError,
            "No parser registered for media type 'image/png'",
        ):
            await worker.run_pipeline()
        self.assertEqual(manager.knowledge.inserted_metadata, {})
