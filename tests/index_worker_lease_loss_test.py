# -*- coding: utf-8 -*-
"""Preserve document state when an indexing worker loses its lease."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import partial
from importlib import import_module
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase, SkipTest
from unittest.mock import MagicMock, patch

from utils import AnyValue

from agentscope.app._service._index_worker import IndexWorker
from agentscope.app.storage import (
    EmbeddingModelConfig,
    KnowledgeBaseData,
    KnowledgeBaseRecord,
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
    StorageBase,
)


@asynccontextmanager
async def _backend(name: str) -> AsyncIterator[StorageBase]:
    """Open an isolated Redis fake or SQLite database.

    Args:
        name (`str`):
            The backend name, ``redis`` or ``sql``.

    Yields:
        `StorageBase`:
            A real storage implementation with isolated test data.
    """
    if name == "redis":
        try:
            import fakeredis.aioredis
        except ImportError as exc:
            raise SkipTest("fakeredis is not installed") from exc
        from agentscope.app.storage import RedisStorage

        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        try:
            async with RedisStorage(
                connection_pool=client.connection_pool,
            ) as storage:
                yield storage
        finally:
            await client.aclose()
    else:
        try:
            import_module("aiosqlite")
            from agentscope.app.storage import AsyncSQLAlchemyStorage
        except ImportError as exc:
            raise SkipTest(
                "SQL storage dependencies are not installed",
            ) from exc
        async with AsyncSQLAlchemyStorage(
            "sqlite+aiosqlite:///:memory:",
        ) as sql_storage:
            yield sql_storage


async def _seed(
    storage: StorageBase,
    user_id: str = "u",
    knowledge_base_id: str = "kb",
    document_id: str = "doc",
) -> None:
    """Create one knowledge base and document without external services.

    Args:
        storage (`StorageBase`):
            The storage implementation under test.
        user_id (`str`):
            The owner of the test document.
        knowledge_base_id (`str`):
            The parent knowledge base ID.
        document_id (`str`):
            The test document ID.
    """
    await storage.upsert_knowledge_base(
        user_id,
        KnowledgeBaseRecord(
            id=knowledge_base_id,
            user_id=user_id,
            data=KnowledgeBaseData(
                name="Lease test",
                embedding_model_config=EmbeddingModelConfig(
                    type="openai_credential",
                    credential_id="unused",
                    model="unused",
                    dimensions=8,
                ),
                collection_name="unused",
            ),
        ),
    )
    await storage.upsert_knowledge_document(
        user_id,
        KnowledgeDocumentRecord(
            id=document_id,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            data=KnowledgeDocumentData(
                filename="fixture.txt",
                size=1,
                content_type="text/plain",
                blob_uri="unused://fixture",
            ),
        ),
    )


def _after_expiry(name: str, record: KnowledgeDocumentRecord) -> datetime:
    """Advance the acquisition clock, respecting SQL's naive UTC storage.

    Args:
        name (`str`):
            The backend name, ``redis`` or ``sql``.
        record (`KnowledgeDocumentRecord`):
            The persisted record containing the lease deadline.

    Returns:
        `datetime`:
            A time past the deadline, explicitly UTC for SQL storage.
    """
    assert record.lease_expires_at is not None
    now = record.lease_expires_at + timedelta(seconds=1)
    return now.replace(tzinfo=timezone.utc) if name == "sql" else now


class _EventWorker(IndexWorker):
    """Retain the real lease lifecycle with an event-controlled pipeline."""

    def __init__(self, storage: StorageBase, node: str = "old") -> None:
        """Set barriers for pipeline/heartbeat ordering.

        Args:
            storage (`StorageBase`):
                The storage implementation under test.
            node (`str`):
                The processing node ID used for lease ownership.
        """
        super().__init__(
            storage=storage,
            blob_store=MagicMock(),
            knowledge_base_manager=MagicMock(),
            parsers=[],
            node_id=node,
            lease_ttl=timedelta(seconds=30),
        )
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.allow_heartbeat = asyncio.Event()
        self.stopped = asyncio.Event()
        self.heartbeat_stopped = asyncio.Event()
        self.cancelled = False
        self.pipeline_error: Exception | None = None
        self._renew_interval = timedelta(0)

    async def _run_pipeline(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Pause after a real status write, then finish or fail on demand.

        Args:
            user_id (`str`):
                The document owner.
            knowledge_base_id (`str`):
                The parent knowledge base ID.
            document_id (`str`):
                The document being processed.
        """
        await self._storage.update_knowledge_document_status(
            user_id,
            knowledge_base_id,
            document_id,
            "parsing" if self._node_id == "old" else "indexing",
        )
        self.started.set()
        try:
            await self.finish.wait()
            if self.pipeline_error is not None:
                raise self.pipeline_error
            await self._storage.update_knowledge_document_status(
                user_id,
                knowledge_base_id,
                document_id,
                "ready",
                chunk_count=7,
            )
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.stopped.set()

    async def _heartbeat(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Run the real heartbeat after the test releases its barrier.

        Args:
            user_id (`str`):
                The document owner.
            knowledge_base_id (`str`):
                The parent knowledge base ID.
            document_id (`str`):
                The document whose lease is renewed.
        """
        try:
            await self.allow_heartbeat.wait()
            await super()._heartbeat(user_id, knowledge_base_id, document_id)
        finally:
            self.heartbeat_stopped.set()


@asynccontextmanager
async def _processing(
    worker: _EventWorker,
    user_id: str = "u",
    knowledge_base_id: str = "kb",
    document_id: str = "doc",
) -> AsyncIterator[asyncio.Task[None]]:
    """Start one worker and always drain it before closing its storage.

    Args:
        worker (`_EventWorker`):
            The event-controlled worker to start.
        user_id (`str`):
            The document owner.
        knowledge_base_id (`str`):
            The parent knowledge base ID.
        document_id (`str`):
            The document to process.

    Yields:
        `asyncio.Task[None]`:
            The running process task after its pipeline starts.
    """
    task = asyncio.create_task(
        worker.process(user_id, knowledge_base_id, document_id),
    )
    try:
        await asyncio.wait_for(worker.started.wait(), timeout=5)
        yield task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class IndexWorkerLeaseLossTest(IsolatedAsyncioTestCase):
    """Exercise persisted records as well as local task convergence."""

    async def test_successor_record_survives_old_worker_exit(self) -> None:
        """Lost renewal must preserve an indexing or completed successor."""
        for name in ("redis", "sql"):
            for phase in ("indexing", "ready"):
                with self.subTest(backend=name, successor=phase):
                    async with _backend(name) as storage:
                        await _seed(storage)
                        old = _EventWorker(storage)
                        new = _EventWorker(storage, "new")
                        async with _processing(old) as old_task:
                            leased = await storage.get_knowledge_document(
                                "u",
                                "kb",
                                "doc",
                            )
                            assert leased is not None
                            acquire = storage.acquire_knowledge_document_lease
                            with patch.object(
                                storage,
                                "acquire_knowledge_document_lease",
                                side_effect=partial(
                                    acquire,
                                    now=_after_expiry(name, leased),
                                ),
                            ):
                                async with _processing(new) as new_task:
                                    if phase == "ready":
                                        new.finish.set()
                                        await asyncio.wait_for(new_task, 5)
                                    before = (
                                        await storage.get_knowledge_document(
                                            "u",
                                            "kb",
                                            "doc",
                                        )
                                    )
                                    assert before is not None
                                    expected = leased.model_copy(deep=True)
                                    expected.status = phase
                                    expected.processing_node = (
                                        "new" if phase == "indexing" else None
                                    )
                                    expected.lease_expires_at = (
                                        (
                                            _after_expiry(name, leased)
                                            + timedelta(seconds=30)
                                        ).replace(tzinfo=None)
                                        if phase == "indexing"
                                        else None
                                    )
                                    expected.data.chunk_count = (
                                        7 if phase == "ready" else 0
                                    )
                                    self.assertEqual(
                                        before.model_dump(),
                                        {
                                            **expected.model_dump(),
                                            "updated_at": AnyValue(),
                                        },
                                    )
                                    old.allow_heartbeat.set()
                                    await asyncio.wait_for(old_task, 5)
                                    self.assertEqual(
                                        await storage.get_knowledge_document(
                                            "u",
                                            "kb",
                                            "doc",
                                        ),
                                        before,
                                    )
                                    self.assertEqual(
                                        (old.stopped.is_set(), old.cancelled),
                                        (True, True),
                                    )
                                    # The replacement can finish normally.
                                    new.finish.set()
                                    await asyncio.wait_for(new_task, 5)

    async def test_failed_renewal_keeps_lease_recoverable(self) -> None:
        """A false renewal can leave this node's lease awaiting expiry."""
        for name in ("redis", "sql"):
            with self.subTest(backend=name):
                async with _backend(name) as storage:
                    await _seed(storage)
                    worker = _EventWorker(storage)
                    async with _processing(worker) as task:
                        before = await storage.get_knowledge_document(
                            "u",
                            "kb",
                            "doc",
                        )
                        assert before is not None
                        with patch.object(
                            storage,
                            "renew_knowledge_document_lease",
                            return_value=False,
                        ):
                            worker.allow_heartbeat.set()
                            await asyncio.wait_for(task, 5)
                        self.assertEqual(
                            await storage.get_knowledge_document(
                                "u",
                                "kb",
                                "doc",
                            ),
                            before,
                        )
                        now = _after_expiry(name, before)
                        scan = (
                            storage.list_knowledge_documents_with_expired_lease
                        )
                        self.assertEqual(
                            await scan(now=now),
                            [before],
                        )
                        acquire = storage.acquire_knowledge_document_lease
                        with patch.object(
                            storage,
                            "acquire_knowledge_document_lease",
                            side_effect=partial(acquire, now=now),
                        ):
                            replacement = _EventWorker(storage, "new")
                            async with _processing(replacement) as new_task:
                                replacement.finish.set()
                                await asyncio.wait_for(new_task, 5)
                        after = await storage.get_knowledge_document(
                            "u",
                            "kb",
                            "doc",
                        )
                        assert after is not None
                        expected = before.model_copy(deep=True)
                        expected.status = "ready"
                        expected.processing_node = None
                        expected.lease_expires_at = None
                        expected.data.chunk_count = 7
                        self.assertEqual(
                            after.model_dump(),
                            {
                                **expected.model_dump(),
                                "updated_at": AnyValue(),
                            },
                        )

    async def test_lease_loss_wins_when_both_children_finish(self) -> None:
        """A simultaneous pipeline error must not override lost ownership."""
        async with _backend("redis") as storage:
            await _seed(storage)
            worker = _EventWorker(storage)
            worker.pipeline_error = ValueError("old pipeline failed")
            wait = asyncio.wait

            async def both_done(
                tasks: set[asyncio.Task[None]],
                **kwargs: Any,
            ) -> tuple[set[asyncio.Task[None]], set[asyncio.Task[None]]]:
                """Model both children completing before wait returns.

                Args:
                    tasks (`set[asyncio.Task[None]]`):
                        The pipeline and heartbeat tasks.
                    **kwargs (`Any`):
                        The original wait options, replaced for this race.

                Returns:
                    `tuple[set[asyncio.Task[None]], set[asyncio.Task[None]]]`:
                        Both completed tasks and an empty pending set.
                """
                del kwargs
                return await wait(tasks, return_when=asyncio.ALL_COMPLETED)

            with patch(
                "agentscope.app._service._index_worker.asyncio.wait",
                side_effect=both_done,
            ):
                async with _processing(worker) as task:
                    before = await storage.get_knowledge_document(
                        "u",
                        "kb",
                        "doc",
                    )
                    with patch.object(
                        storage,
                        "renew_knowledge_document_lease",
                        return_value=False,
                    ):
                        worker.finish.set()
                        worker.allow_heartbeat.set()
                        await asyncio.wait_for(task, 5)
                    self.assertEqual(
                        await storage.get_knowledge_document("u", "kb", "doc"),
                        before,
                    )
                    self.assertTrue(worker.stopped.is_set())

    async def test_heartbeat_exception_is_handled_and_pipeline_drained(
        self,
    ) -> None:
        """A renewal I/O error keeps its cause and cannot leak the pipeline."""
        async with _backend("redis") as storage:
            await _seed(storage)
            worker = _EventWorker(storage)
            async with _processing(worker) as task:
                before = await storage.get_knowledge_document("u", "kb", "doc")
                assert before is not None
                with patch.object(
                    storage,
                    "renew_knowledge_document_lease",
                    side_effect=OSError("renewal unavailable"),
                ):
                    worker.allow_heartbeat.set()
                    await asyncio.wait_for(task, 5)
                after = await storage.get_knowledge_document("u", "kb", "doc")
                assert after is not None
                expected = before.model_copy(deep=True)
                expected.status = "error"
                expected.data.error = "OSError: renewal unavailable"
                expected.processing_node = None
                expected.lease_expires_at = None
                self.assertEqual(
                    after.model_dump(),
                    {**expected.model_dump(), "updated_at": AnyValue()},
                )
                self.assertEqual(
                    (
                        worker.stopped.is_set(),
                        worker.heartbeat_stopped.is_set(),
                        worker.cancelled,
                    ),
                    (True, True, True),
                )

    async def test_pipeline_failure_still_marks_error_and_releases(
        self,
    ) -> None:
        """Ordinary pipeline failures retain the existing error policy."""
        async with _backend("redis") as storage:
            await _seed(storage)
            worker = _EventWorker(storage)
            worker.pipeline_error = ValueError("parse failed")
            async with _processing(worker) as task:
                before = await storage.get_knowledge_document("u", "kb", "doc")
                assert before is not None
                worker.finish.set()
                await asyncio.wait_for(task, 5)
                after = await storage.get_knowledge_document("u", "kb", "doc")
                assert after is not None
                expected = before.model_copy(deep=True)
                expected.status = "error"
                expected.data.error = "ValueError: parse failed"
                expected.processing_node = None
                expected.lease_expires_at = None
                self.assertEqual(
                    after.model_dump(),
                    {**expected.model_dump(), "updated_at": AnyValue()},
                )

    async def test_lease_loss_does_not_stop_another_users_document(
        self,
    ) -> None:
        """One failed renewal must not cancel independent indexing work."""
        async with _backend("redis") as storage:
            await _seed(storage)
            await _seed(storage, "other", "other-kb", "other-doc")
            old = _EventWorker(storage)
            other = _EventWorker(storage, "other-worker")
            async with _processing(old) as old_task:
                async with _processing(
                    other,
                    "other",
                    "other-kb",
                    "other-doc",
                ) as other_task:
                    before = await storage.get_knowledge_document(
                        "other",
                        "other-kb",
                        "other-doc",
                    )
                    assert before is not None
                    with patch.object(
                        storage,
                        "renew_knowledge_document_lease",
                        return_value=False,
                    ):
                        old.allow_heartbeat.set()
                        await asyncio.wait_for(old_task, 5)
                    self.assertEqual(
                        (other_task.done(), other.stopped.is_set()),
                        (False, False),
                    )
                    self.assertEqual(
                        await storage.get_knowledge_document(
                            "other",
                            "other-kb",
                            "other-doc",
                        ),
                        before,
                    )
                    other.finish.set()
                    await asyncio.wait_for(other_task, 5)
                    after = await storage.get_knowledge_document(
                        "other",
                        "other-kb",
                        "other-doc",
                    )
                    assert after is not None
                    expected = before.model_copy(deep=True)
                    expected.status = "ready"
                    expected.processing_node = None
                    expected.lease_expires_at = None
                    expected.data.chunk_count = 7
                    self.assertEqual(
                        after.model_dump(),
                        {**expected.model_dump(), "updated_at": AnyValue()},
                    )
