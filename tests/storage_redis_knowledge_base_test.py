# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the knowledge base persistence layer of RedisStorage."""
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.app.storage import (
    EmbeddingModelConfig,
    KnowledgeBaseData,
    KnowledgeBaseRecord,
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
