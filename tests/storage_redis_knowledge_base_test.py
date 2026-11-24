# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the knowledge base persistence layer of RedisStorage."""
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.app.storage import (
    EmbeddingModelConfig,
    KnowledgeBaseData,
    KnowledgeBaseRecord,
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
    RedisStorage,
)


def make_storage() -> RedisStorage:
    """Create a RedisStorage instance backed by fakeredis."""
    storage = RedisStorage.__new__(RedisStorage)
    storage._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage.key_ttl = None
    storage.key_config = RedisStorage.KeyConfig()
    return storage


def make_record(user_id: str, name: str = "kb") -> KnowledgeBaseRecord:
    """Build a KnowledgeBaseRecord with a default embedding config."""
    return KnowledgeBaseRecord(
        user_id=user_id,
        data=KnowledgeBaseData(
            name=name,
            description="desc",
            embedding_model_config=EmbeddingModelConfig(
                type="openai_credential",
                credential_id="cred-1",
                model="text-embedding-3-small",
                dimensions=1536,
            ),
            collection_name="kb_abc",
        ),
    )


class KnowledgeBaseStorageTest(IsolatedAsyncioTestCase):
    """Tests for the KnowledgeBaseRecord CRUD methods on RedisStorage."""

    async def test_upsert_get_list_delete(self) -> None:
        """Round-trip and isolation across users."""
        storage = make_storage()

        rec_a = make_record("user-1", "first")
        rec_b = make_record("user-1", "second")
        rec_c = make_record("user-2", "other")

        stored_a = await storage.upsert_knowledge_base("user-1", rec_a)
        stored_b = await storage.upsert_knowledge_base("user-1", rec_b)
        stored_c = await storage.upsert_knowledge_base("user-2", rec_c)

        # get returns the persisted record for the right owner
        fetched = await storage.get_knowledge_base("user-1", stored_a.id)
        self.assertEqual(fetched.id, stored_a.id)
        self.assertEqual(fetched.data.name, "first")

        # cross-user lookups return None
        self.assertIsNone(
            await storage.get_knowledge_base("user-2", stored_a.id),
        )

        # list scoped to user-1 returns only its records
        listed = await storage.list_knowledge_bases("user-1")
        self.assertEqual(
            sorted(r.id for r in listed),
            sorted([stored_a.id, stored_b.id]),
        )

        # delete only the requested record; other users are untouched
        deleted = await storage.delete_knowledge_base("user-1", stored_a.id)
        self.assertTrue(deleted)
        self.assertIsNone(
            await storage.get_knowledge_base("user-1", stored_a.id),
        )
        self.assertEqual(
            [r.id for r in await storage.list_knowledge_bases("user-1")],
            [stored_b.id],
        )
        self.assertEqual(
            [r.id for r in await storage.list_knowledge_bases("user-2")],
            [stored_c.id],
        )

        # double-delete returns False
        self.assertFalse(
            await storage.delete_knowledge_base("user-1", stored_a.id),
        )

    async def test_upsert_rejects_user_id_mismatch(self) -> None:
        """upsert refuses records whose user_id does not match arg."""
        storage = make_storage()
        rec = make_record("user-1")
        with self.assertRaises(ValueError):
            await storage.upsert_knowledge_base("user-2", rec)

    async def test_upsert_overwrites_and_preserves_created_at(self) -> None:
        """Re-upsert with same id keeps created_at, refreshes updated_at."""
        storage = make_storage()
        rec = make_record("user-1")
        first = await storage.upsert_knowledge_base("user-1", rec)

        rec.data.name = "renamed"
        second = await storage.upsert_knowledge_base("user-1", rec)

        self.assertEqual(second.id, first.id)
        self.assertEqual(second.created_at, first.created_at)
        self.assertEqual(second.data.name, "renamed")


class _TakeoverPipeline:
    """A pipeline proxy that lets a successor take the lease after a read.

    Every command is forwarded to a real pipeline; the first ``get`` of
    the wrapped document key is answered with the value as it was before
    the successor wrote, which is exactly the stale read a plain
    read-modify-write would write back.
    """

    def __init__(self, owner: "_TakeoverClient", transaction: bool) -> None:
        """Open an underlying pipeline with the same transaction mode."""
        self._owner = owner
        self._pipe = owner.raw.pipeline(transaction=transaction)

    async def __aenter__(self) -> "_TakeoverPipeline":
        """Enter the underlying pipeline."""
        await self._pipe.__aenter__()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Leave the underlying pipeline."""
        await self._pipe.__aexit__(*exc_info)

    async def watch(self, *keys: str) -> None:
        """Watch the given keys on the underlying pipeline."""
        await self._pipe.watch(*keys)

    async def unwatch(self) -> None:
        """Stop watching on the underlying pipeline."""
        await self._pipe.unwatch()

    def multi(self) -> None:
        """Start the transaction on the underlying pipeline."""
        self._pipe.multi()

    def set(self, *args: Any, **kwargs: Any) -> Any:
        """Queue ``SET`` on the underlying pipeline."""
        return self._pipe.set(*args, **kwargs)

    def expire(self, *args: Any, **kwargs: Any) -> Any:
        """Queue ``EXPIRE`` on the underlying pipeline."""
        return self._pipe.expire(*args, **kwargs)

    async def execute(self) -> Any:
        """Run the queued transaction."""
        return await self._pipe.execute()

    async def get(self, key: str) -> Any:
        """Read the key, then let the successor take the lease over."""
        value = await self._pipe.get(key)
        await self._owner.maybe_take_over(key)
        return value


class _TakeoverClient:
    """A Redis client that hands the lease to a successor after one read.

    The first read of the wrapped key is answered with the stored value
    as it was before the successor wrote; the successor's write happens
    immediately afterwards, so a read-modify-write without a CAS writes
    the old lease back.
    """

    def __init__(
        self,
        raw: Any,
        key: str,
        take_over: Callable[[], Awaitable[None]],
    ) -> None:
        """Wrap ``raw``, arming the takeover for ``key``."""
        self.raw = raw
        self._key = key
        self._take_over = take_over
        self._fired = False

    def pipeline(self, transaction: bool = True) -> _TakeoverPipeline:
        """Return a proxy over a pipeline of the wrapped client."""
        return _TakeoverPipeline(self, transaction)

    async def get(self, key: str) -> Any:
        """Read the key, then let the successor take the lease over."""
        value = await self.raw.get(key)
        await self.maybe_take_over(key)
        return value

    async def set(self, *args: Any, **kwargs: Any) -> Any:
        """Forward ``SET`` to the wrapped client."""
        return await self.raw.set(*args, **kwargs)

    async def maybe_take_over(self, key: str) -> None:
        """Run the takeover once, after the first read of the key."""
        if self._fired or key != self._key:
            return
        self._fired = True
        await self._take_over()


class KnowledgeDocumentStatusTest(IsolatedAsyncioTestCase):
    """The status update must not write a lease it read back out."""

    async def test_status_update_keeps_a_successor_lease(self) -> None:
        """A lease taken over during the update survives the write."""
        raw = fakeredis.aioredis.FakeRedis(decode_responses=True)
        storage = make_storage()
        storage._client = raw
        successor = make_storage()
        successor._client = raw

        doc = KnowledgeDocumentRecord(
            user_id="user-1",
            knowledge_base_id="kb-1",
            data=KnowledgeDocumentData(
                filename="f.txt",
                size=42,
                blob_uri="local://f.txt",
            ),
        )
        await storage.upsert_knowledge_document("user-1", doc)

        base = datetime.now()
        self.assertTrue(
            await storage.acquire_knowledge_document_lease(
                "user-1",
                "kb-1",
                doc.id,
                "worker-a",
                timedelta(minutes=1),
                base,
            ),
        )
        takeover_at = base + timedelta(minutes=10)
        successor_deadline = takeover_at + timedelta(minutes=5)

        async def take_over() -> None:
            self.assertTrue(
                await successor.acquire_knowledge_document_lease(
                    "user-1",
                    "kb-1",
                    doc.id,
                    "worker-b",
                    timedelta(minutes=5),
                    takeover_at,
                ),
            )

        storage._client = _TakeoverClient(
            raw,
            storage._document_key("user-1", "kb-1", doc.id),
            take_over,
        )

        await storage.update_knowledge_document_status(
            "user-1",
            "kb-1",
            doc.id,
            "indexing",
        )

        fetched = await storage.get_knowledge_document(
            "user-1",
            "kb-1",
            doc.id,
        )
        self.assertEqual(fetched.status, "indexing")
        self.assertEqual(fetched.processing_node, "worker-b")
        self.assertEqual(fetched.lease_expires_at, successor_deadline)
