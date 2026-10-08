# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Consumer shutdown must leave indexing jobs recoverable by the sweeper."""
import asyncio
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.app._bus_ops import enqueue_index_task
from agentscope.app._service import IndexTaskConsumer
from agentscope.app._service._index_sweeper import IndexSweeper
from agentscope.app._service._index_worker import IndexWorker
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    AsyncSQLAlchemyStorage,
    EmbeddingModelConfig,
    KnowledgeBaseData,
    KnowledgeBaseRecord,
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
    KnowledgeDocumentStatus,
    RedisStorage,
    StorageBase,
)


class IndexWorkerRecoveryTest(IsolatedAsyncioTestCase):
    """Exercise shutdown and redispatch using real storage on both backends."""

    async def _check_recovery(
        self,
        storage: StorageBase,
        phase: KnowledgeDocumentStatus,
    ) -> None:
        await storage.upsert_knowledge_base(
            "u",
            KnowledgeBaseRecord(
                id="kb",
                user_id="u",
                data=KnowledgeBaseData(
                    name="kb",
                    collection_name="kb",
                    embedding_model_config=EmbeddingModelConfig(
                        type="openai_credential",
                        credential_id="credential",
                        model="text-embedding-3-small",
                        dimensions=1536,
                    ),
                ),
            ),
        )
        record = KnowledgeDocumentRecord(
            user_id="u",
            knowledge_base_id="kb",
            data=KnowledgeDocumentData(
                filename="doc.txt",
                size=4,
                blob_uri="local://doc.txt",
            ),
        )
        await storage.upsert_knowledge_document("u", record)
        bus = InMemoryMessageBus()
        started, finish = asyncio.Event(), asyncio.Event()

        async def pipeline(*args: str) -> None:
            await storage.update_knowledge_document_status(*args, phase)
            started.set()
            await finish.wait()
            await storage.update_knowledge_document_status(
                *args,
                "ready",
                chunk_count=1,
            )

        worker = IndexWorker(
            storage=storage,
            blob_store=MagicMock(),
            knowledge_base_manager=MagicMock(),
            parsers=[],
            node_id="old-node",
        )
        worker._run_pipeline = AsyncMock(side_effect=pipeline)
        await enqueue_index_task(
            bus,
            user_id="u",
            knowledge_base_id="kb",
            document_id=record.id,
        )
        async with IndexTaskConsumer(bus, worker):
            await asyncio.wait_for(started.wait(), timeout=2)

        # The consumer has stopped its worker, but the non-terminal
        # document must retain its recovery signal until the TTL expires.
        stopped = await storage.get_knowledge_document("u", "kb", record.id)
        self.assertEqual(stopped.status, phase)
        self.assertEqual(stopped.processing_node, "old-node")
        self.assertIsNotNone(stopped.lease_expires_at)
        self.assertIsNone(stopped.data.error)
        self.assertFalse(
            await storage.acquire_knowledge_document_lease(
                "u",
                "kb",
                record.id,
                "new-node",
                timedelta(seconds=90),
            ),
        )
        sweeper = IndexSweeper(storage, bus)
        await sweeper._sweep_once()
        self.assertEqual(
            await bus.queue_drain(
                MessageBusKeys.index_tasks_queue(),
                max_count=10,
            ),
            [],
        )

        # Advance all relevant clocks, rather than sleeping for the TTL.
        future = datetime.now() + timedelta(days=1)
        clock_target = (
            "agentscope.app.storage._redis_storage.datetime"
            if isinstance(storage, RedisStorage)
            else "agentscope.app.storage._sql._storage._utcnow"
        )
        with (
            patch(
                "agentscope.app._service._index_sweeper.datetime",
                wraps=datetime,
            ) as sweep_clock,
            patch(clock_target, wraps=datetime) as storage_clock,
        ):
            sweep_clock.now.return_value = future
            if isinstance(storage, RedisStorage):
                storage_clock.now.return_value = future
            else:
                storage_clock.return_value = future.astimezone(
                    timezone.utc,
                ).replace(tzinfo=None)
            await sweeper._sweep_once()
            worker._node_id = "new-node"
            finish.set()
            async with IndexTaskConsumer(bus, worker) as consumer:
                await asyncio.wait_for(
                    asyncio.gather(*consumer._inflight),
                    timeout=2,
                )

        ready = await storage.get_knowledge_document("u", "kb", record.id)
        self.assertEqual(ready.status, "ready")
        self.assertEqual(ready.data.chunk_count, 1)
        self.assertIsNone(ready.processing_node)
        self.assertIsNone(ready.lease_expires_at)
        self.assertEqual(worker._run_pipeline.await_count, 2)
        await bus.aclose()

    @skipUnless(find_spec("fakeredis"), "fakeredis is not installed")
    async def test_redis_shutdown_recovers_processing_phases(self) -> None:
        """Redis preserves cancelled jobs for sweep and lease reacquisition."""
        import fakeredis.aioredis

        phases: tuple[KnowledgeDocumentStatus, ...] = (
            "parsing",
            "chunking",
            "indexing",
        )
        for phase in phases:
            with self.subTest(phase=phase):
                storage = RedisStorage.__new__(RedisStorage)
                storage._client = fakeredis.aioredis.FakeRedis(
                    decode_responses=True,
                )
                storage.key_ttl = None
                storage.key_config = RedisStorage.KeyConfig()
                try:
                    await self._check_recovery(storage, phase)
                finally:
                    await storage._client.aclose()

    @skipUnless(
        find_spec("sqlalchemy") and find_spec("aiosqlite"),
        "SQL storage dependencies are not installed",
    )
    async def test_sql_shutdown_recovers_processing_phases(self) -> None:
        """SQLite keeps cancelled jobs recoverable after lease expiry."""
        phases: tuple[KnowledgeDocumentStatus, ...] = (
            "parsing",
            "chunking",
            "indexing",
        )
        for phase in phases:
            with self.subTest(phase=phase):
                async with AsyncSQLAlchemyStorage(
                    "sqlite+aiosqlite:///:memory:",
                ) as storage:
                    await self._check_recovery(storage, phase)
