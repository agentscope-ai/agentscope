# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for issue #2176.

``lifespan`` builds the embedded :class:`IndexWorker` without a
``parser_executor``, so every parse — including CPU-bound ones for
PDF/Office uploads — runs ``await parser.parse(...)`` directly on the
event-loop thread instead of a subprocess. Any concurrent request
(a health check, another agent call) stalls behind it.

Two things are checked:

* **Wiring** — booting the app with ``enable_index_worker=True``
  (the default) constructs the embedded :class:`IndexWorker` with a
  real :class:`ProcessPoolExecutor`, and shuts that pool down when
  the app's lifespan exits.
* **Behavior** — the mechanism the wiring is for: with a
  ``parser_executor`` wired in, a slow parse no longer blocks a
  concurrent coroutine on the same event loop; without one (the
  pre-fix default), it does.
"""
import asyncio
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

import fakeredis.aioredis
from fastapi.testclient import TestClient

import agentscope.app._lifespan as lifespan_module
from agentscope.app import create_app
from agentscope.app._service import IndexWorker
from agentscope.app.rag.blob_store import LocalBlobStore
from agentscope.app.rag.knowledge_base_manager import (
    KnowledgeBaseManagerBase,
)
from agentscope.app.rag.knowledge_base_manager._dimension_policy import (
    DimensionPolicy,
    DimensionPolicyKind,
)
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import (
    EmbeddingModelConfig,
    KnowledgeBaseRecord,
    RedisStorage,
)
from agentscope.app.workspace_manager._base import WorkspaceManagerBase
from agentscope.message import TextBlock
from agentscope.rag import ParserBase, Section
from agentscope.rag._vdb._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
)
from agentscope.rag._vdb._vector_store import VectorStoreBase


# Long enough that a blocked event loop clearly misses several of the
# 20 ms ticks below, short enough not to make the suite slow.
_PARSE_SECONDS = 0.3


class _SlowParser(ParserBase):
    """A parser whose ``parse()`` does blocking, CPU-bound-shaped work.

    Module-level and stateless so a :class:`ProcessPoolExecutor` can
    pickle it across the process boundary, standing in for a real
    byte-oriented parser (PDF, PPTX) that holds the GIL while it runs.
    """

    supported_media_types: list[str] = ["application/x-slow-test"]

    async def parse(self, file: bytes | str, filename: str) -> list:
        time.sleep(_PARSE_SECONDS)  # noqa: ASYNC101 — the point of the test
        return [Section(content=TextBlock(text="ok"), source=filename)]


class _LockHoldingParser(ParserBase):
    """A thread-safe-but-not-picklable parser (a valid ``ParserBase``
    per its contract, which requires stateless-or-thread-safe, not
    picklable). Standing in for a custom parser holding a lock, an
    HTTP client, or other process-bound state."""

    supported_media_types: list[str] = ["application/x-lock-test"]

    def __init__(self) -> None:
        self._lock = threading.Lock()

    async def parse(self, file: bytes | str, filename: str) -> list:
        with self._lock:
            return [Section(content=TextBlock(text="ok"), source=filename)]


# ----------------------------------------------------------------------
# Fakes shared with the other create_app wiring tests.
# ----------------------------------------------------------------------


class _FakeVectorStore(VectorStoreBase):
    """Bare minimum to satisfy create_app's vector-store wiring."""

    def __init__(self) -> None:
        self._collections: dict[str, list[VectorRecord]] = {}

    async def create_collection(self, name: str, dimensions: int) -> None:
        self._collections.setdefault(name, [])

    async def delete_collection(self, name: str) -> None:
        self._collections.pop(name, None)

    async def has_collection(self, name: str) -> bool:
        return name in self._collections

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        self._collections.setdefault(collection, []).extend(records)

    async def delete(self, collection: str, document_id: str) -> None:
        self._collections[collection] = [
            r
            for r in self._collections.get(collection, [])
            if r.document_id != document_id
        ]

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        return []

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        return []


class _FakeKbManager(KnowledgeBaseManagerBase):
    """KB manager that resolves knowledge bases via storage only."""

    async def get_dimension_policy(self) -> DimensionPolicy:
        return DimensionPolicy(kind=DimensionPolicyKind.ANY, dimension=None)

    async def create_knowledge_base(
        self,
        user_id: str,
        name: str,
        description: str,
        embedding_model_config: EmbeddingModelConfig,
    ) -> KnowledgeBaseRecord:
        raise NotImplementedError

    async def delete_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> bool:
        return False

    async def get_knowledge(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> Any:
        raise NotImplementedError  # unused — this test never uploads


class _NoopWorkspaceManager(WorkspaceManagerBase):
    """Workspace manager that does nothing."""

    async def get_workspace(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def close(self, workspace_id: str) -> None:
        return None

    async def close_all(self) -> None:
        return None


def _make_storage(fr: fakeredis.aioredis.FakeRedis) -> RedisStorage:
    class _FakeStorage(RedisStorage):
        async def __aenter__(self) -> "_FakeStorage":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _FakeStorage()


def _make_bus(fr: fakeredis.aioredis.FakeRedis) -> RedisMessageBus:
    class _FakeBus(RedisMessageBus):
        async def __aenter__(self) -> "_FakeBus":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _FakeBus()


class LifespanParserExecutorWiringTest(IsolatedAsyncioTestCase):
    """Booting the app wires a real ``ProcessPoolExecutor`` into the
    embedded ``IndexWorker`` and shuts it down on exit."""

    async def asyncSetUp(self) -> None:
        # pylint: disable-next=consider-using-with
        self._tmp = tempfile.TemporaryDirectory()
        self._fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        storage = _make_storage(self._fr)
        self._app = create_app(
            storage=storage,
            message_bus=_make_bus(self._fr),
            workspace_manager=_NoopWorkspaceManager(),
            knowledge_base_manager=_FakeKbManager(
                storage=storage,
                vector_store=_FakeVectorStore(),
            ),
            blob_store=LocalBlobStore(root_dir=self._tmp.name),
        )

    async def asyncTearDown(self) -> None:
        await self._fr.aclose()
        self._tmp.cleanup()

    async def test_embedded_worker_gets_a_real_process_pool(self) -> None:
        """The lifespan passes a live ``ProcessPoolExecutor`` to
        ``IndexWorker``, and shuts it down when the app stops."""
        captured: dict[str, Any] = {}
        real_index_worker = lifespan_module.IndexWorker

        def _spy(*args: Any, **kwargs: Any) -> IndexWorker:
            captured["parser_executor"] = kwargs.get("parser_executor")
            return real_index_worker(*args, **kwargs)

        with patch.object(
            lifespan_module,
            "IndexWorker",
            side_effect=_spy,
        ):
            with TestClient(self._app):
                pool = captured.get("parser_executor")
                self.assertIsInstance(pool, ProcessPoolExecutor)
                self.assertFalse(pool._shutdown_thread)

        # TestClient's __exit__ ran the lifespan shutdown path.
        self.assertTrue(pool._shutdown_thread)

    async def test_parser_pool_size_defaults_to_worker_concurrency(
        self,
    ) -> None:
        """``parser_max_workers`` defaults to ``index_worker_max_concurrency``
        rather than the process's CPU count, so the pool never holds more
        idle workers than the pipeline can keep busy."""
        captured: dict[str, Any] = {}
        real_pool = lifespan_module.ProcessPoolExecutor

        def _spy(*args: Any, **kwargs: Any) -> ProcessPoolExecutor:
            captured["max_workers"] = kwargs.get("max_workers")
            return real_pool(*args, **kwargs)

        with patch.object(
            lifespan_module,
            "ProcessPoolExecutor",
            side_effect=_spy,
        ):
            with TestClient(self._app):
                pass
        self.assertEqual(captured["max_workers"], 4)

    async def test_offload_parsing_false_skips_the_process_pool(
        self,
    ) -> None:
        """``offload_parsing=False`` builds the embedded ``IndexWorker``
        with ``parser_executor=None``: parsing stays in-process, so a
        custom parser holding non-picklable state keeps working."""
        self._app.state.offload_parsing = False
        captured: dict[str, Any] = {}
        real_index_worker = lifespan_module.IndexWorker

        def _spy(*args: Any, **kwargs: Any) -> IndexWorker:
            captured["parser_executor"] = kwargs.get("parser_executor")
            return real_index_worker(*args, **kwargs)

        with patch.object(
            lifespan_module,
            "IndexWorker",
            side_effect=_spy,
        ):
            with TestClient(self._app):
                pass
        self.assertIsNone(captured["parser_executor"])


class IndexWorkerParseOffloadTest(IsolatedAsyncioTestCase):
    """The behavior the wiring above exists for: with a
    ``parser_executor``, a slow parse no longer stalls the event loop
    that also has to keep serving other coroutines."""

    @staticmethod
    def _make_worker(
        parser_executor: ProcessPoolExecutor | None,
    ) -> IndexWorker:
        return IndexWorker(
            storage=None,
            blob_store=None,
            knowledge_base_manager=None,
            parsers=[],
            chunker=None,
            node_id="test-node",
            parser_executor=parser_executor,
        )

    async def _ticks_during_parse(
        self,
        parser_executor: ProcessPoolExecutor | None,
    ) -> int:
        worker = self._make_worker(parser_executor)
        ticks = 0
        stop = False

        async def _tick() -> None:
            nonlocal ticks
            while not stop:
                ticks += 1
                await asyncio.sleep(0.02)

        ticker = asyncio.create_task(_tick())
        await worker._parse(_SlowParser(), b"irrelevant", "slow.bin")
        stop = True
        await ticker
        return ticks

    async def test_no_executor_blocks_the_event_loop(self) -> None:
        """Pre-fix default (``parser_executor=None``): the parse runs
        inline and the concurrent ticker barely advances."""
        ticks = await self._ticks_during_parse(parser_executor=None)
        self.assertLessEqual(ticks, 1, ticks)

    async def test_executor_keeps_the_event_loop_responsive(self) -> None:
        """With a real pool wired in, the ticker keeps advancing while
        the parse runs in a subprocess."""
        pool = ProcessPoolExecutor(max_workers=1)
        try:
            ticks = await self._ticks_during_parse(parser_executor=pool)
        finally:
            pool.shutdown(wait=True)
        self.assertGreater(ticks, 5, ticks)

    async def test_non_picklable_parser_needs_offload_disabled(self) -> None:
        """A parser holding a lock (valid per ``ParserBase`` — stateless
        or thread-safe, not picklable) fails across a real process pool,
        and succeeds with ``parser_executor=None`` (``offload_parsing=
        False``'s effect)."""
        pool = ProcessPoolExecutor(max_workers=1)
        try:
            offloaded_worker = self._make_worker(pool)
            with self.assertRaises(TypeError):
                await offloaded_worker._parse(
                    _LockHoldingParser(),
                    b"irrelevant",
                    "locked.bin",
                )
        finally:
            pool.shutdown(wait=True)

        inline_worker = self._make_worker(None)
        sections = await inline_worker._parse(
            _LockHoldingParser(),
            b"irrelevant",
            "locked.bin",
        )
        self.assertEqual(sections[0].source, "locked.bin")
