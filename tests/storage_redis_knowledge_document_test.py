# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for knowledge document publication in the Redis backend."""
from datetime import datetime, timedelta
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis
from redis.exceptions import ConnectionError as RedisConnectionError

from agentscope.app.storage import (
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
    RedisStorage,
)


class _PublicationFailure:
    """Raise on a selected SADD or when the transaction is executed."""

    def __init__(
        self,
        failing_sadd: int | None = None,
        fail_execute: bool = False,
    ) -> None:
        self.failing_sadd = failing_sadd
        self.fail_execute = fail_execute
        self.sadd_calls = 0

    def check_sadd(self) -> None:
        """Count a SADD and fail at the configured call."""
        self.sadd_calls += 1
        if self.sadd_calls == self.failing_sadd:
            raise RedisConnectionError("injected SADD failure")

    def check_execute(self) -> None:
        """Fail when the publication transaction is executed."""
        if self.fail_execute:
            raise RedisConnectionError(
                "injected publication failure",
            )


class _PipelineProxy:
    """Intercept pipeline commands before they reach fakeredis."""

    def __init__(
        self,
        pipeline: Any,
        failure: _PublicationFailure,
    ) -> None:
        self._pipeline = pipeline
        self._failure = failure

    async def __aenter__(self) -> "_PipelineProxy":
        await self._pipeline.__aenter__()
        return self

    async def __aexit__(self, *exc_info: Any) -> Any:
        return await self._pipeline.__aexit__(*exc_info)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pipeline, name)

    def sadd(self, *args: Any, **kwargs: Any) -> "_PipelineProxy":
        """Record a queued SADD and inject the configured failure."""
        self._failure.check_sadd()
        self._pipeline.sadd(*args, **kwargs)
        return self

    async def execute(self) -> Any:
        """Run the transaction unless a failure is injected."""
        self._failure.check_execute()
        return await self._pipeline.execute()


class _ClientProxy:
    """Route direct commands and pipelines through the same failure."""

    def __init__(self, client: Any, failure: _PublicationFailure) -> None:
        self._client = client
        self._failure = failure

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    async def sadd(self, *args: Any, **kwargs: Any) -> Any:
        """Record a direct SADD and inject the configured failure."""
        self._failure.check_sadd()
        return await self._client.sadd(*args, **kwargs)

    def pipeline(self, *args: Any, **kwargs: Any) -> _PipelineProxy:
        """Wrap the client pipeline with the same failure injector."""
        return _PipelineProxy(
            self._client.pipeline(*args, **kwargs),
            self._failure,
        )


def make_storage(
    key_ttl: int | None = None,
    failure: _PublicationFailure | None = None,
) -> RedisStorage:
    """Create a RedisStorage instance backed by fakeredis."""
    client: Any = fakeredis.aioredis.FakeRedis(decode_responses=True)
    if failure is not None:
        client = _ClientProxy(client, failure)
    storage = RedisStorage.__new__(RedisStorage)
    storage._client = client
    storage.key_ttl = key_ttl
    storage.key_config = RedisStorage.KeyConfig()
    return storage


def make_document(
    user_id: str,
    knowledge_base_id: str = "kb-1",
    filename: str = "doc.txt",
) -> KnowledgeDocumentRecord:
    """Build a pending KnowledgeDocumentRecord."""
    return KnowledgeDocumentRecord(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        data=KnowledgeDocumentData(
            filename=filename,
            size=12,
            blob_uri=f"local://kb/{filename}",
        ),
    )


class KnowledgeDocumentPublicationTest(IsolatedAsyncioTestCase):
    """Publication must be visible through every index it is listed by."""

    async def _assert_published_record_is_discoverable(
        self,
        storage: RedisStorage,
        user_id: str,
        record: KnowledgeDocumentRecord,
    ) -> None:
        """A stored record must show up in both recovery indexes."""
        stored = await storage.get_knowledge_document(
            user_id,
            record.knowledge_base_id,
            record.id,
        )
        listed = await storage.list_knowledge_documents(
            user_id,
            record.knowledge_base_id,
        )
        pending = await storage.list_knowledge_documents_pending_since(
            datetime.now() + timedelta(seconds=1),
        )

        if stored is None:
            # An interrupted publication must not leave a half-published
            # record behind in either index.
            self.assertEqual(listed, [])
            self.assertEqual(pending, [])
            return

        self.assertEqual(listed, [stored])
        self.assertEqual(pending, [stored])

    async def test_first_sadd_failure_leaves_no_listed_record(self) -> None:
        """A failure on the per-KB index must not leave a record behind."""
        failure = _PublicationFailure(failing_sadd=1)
        storage = make_storage(failure=failure)
        record = make_document("user-1")

        with self.assertRaises(RedisConnectionError):
            await storage.upsert_knowledge_document("user-1", record)

        await self._assert_published_record_is_discoverable(
            storage,
            "user-1",
            record,
        )

    async def test_second_sadd_failure_leaves_no_pending_record(self) -> None:
        """A failure on the global index must not leave a record behind."""
        failure = _PublicationFailure(failing_sadd=2)
        storage = make_storage(failure=failure)
        record = make_document("user-1")

        with self.assertRaises(RedisConnectionError):
            await storage.upsert_knowledge_document("user-1", record)

        await self._assert_published_record_is_discoverable(
            storage,
            "user-1",
            record,
        )

    async def test_execute_failure_rolls_back_publication(self) -> None:
        """A connection loss during EXEC must publish nothing."""
        failure = _PublicationFailure(fail_execute=True)
        storage = make_storage(failure=failure)
        record = make_document("user-1")

        with self.assertRaises(RedisConnectionError):
            await storage.upsert_knowledge_document("user-1", record)

        # The transaction was abandoned before any command reached the
        # server, so the direct lookup and both indexes must all be empty.
        self.assertIsNone(
            await storage.get_knowledge_document(
                "user-1",
                record.knowledge_base_id,
                record.id,
            ),
        )
        await self._assert_published_record_is_discoverable(
            storage,
            "user-1",
            record,
        )

    async def test_upsert_publishes_both_indexes(self) -> None:
        """An ordinary upsert is listed and found by the pending scan."""
        storage = make_storage()
        record = make_document("user-1")

        await storage.upsert_knowledge_document("user-1", record)

        await self._assert_published_record_is_discoverable(
            storage,
            "user-1",
            record,
        )

    async def test_overwrite_preserves_created_at_and_indexes(self) -> None:
        """Overwriting refreshes the record without losing its indexes."""
        storage = make_storage()
        record = make_document("user-1")
        stored = await storage.upsert_knowledge_document("user-1", record)
        created_at = stored.created_at

        record.data.filename = "renamed.txt"
        stored = await storage.upsert_knowledge_document("user-1", record)

        self.assertEqual(stored.created_at, created_at)
        self.assertEqual(stored.data.filename, "renamed.txt")
        await self._assert_published_record_is_discoverable(
            storage,
            "user-1",
            record,
        )

    async def test_upsert_applies_configured_ttl(self) -> None:
        """The configured key TTL is applied to the published record."""
        storage = make_storage(key_ttl=3600)
        record = make_document("user-1")

        await storage.upsert_knowledge_document("user-1", record)

        key = storage._document_key("user-1", "kb-1", record.id)
        ttl = await storage._client.ttl(key)
        self.assertGreater(ttl, 0)
        await self._assert_published_record_is_discoverable(
            storage,
            "user-1",
            record,
        )
