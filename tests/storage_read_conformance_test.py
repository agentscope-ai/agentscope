# -*- coding: utf-8 -*-
"""Shared record read contracts for the SQL and Redis backends."""
from unittest.async_case import IsolatedAsyncioTestCase

import pytest

from agentscope.app.storage import (
    MCPRecord,
    RedisStorage,
    SkillRecord,
    StorageBase,
    VoiceProfileData,
    VoiceProfileRecord,
)
from agentscope.mcp import HttpMCPConfig, MCPClient


class StorageReadConformanceTest(IsolatedAsyncioTestCase):
    """Read results and owner isolation must agree across backends."""

    async def test_sql(self) -> None:
        """Exercise the shared contract against in-memory SQLite."""
        pytest.importorskip("sqlalchemy")
        pytest.importorskip("aiosqlite")
        from agentscope.app.storage import AsyncSQLAlchemyStorage

        async with AsyncSQLAlchemyStorage(
            "sqlite+aiosqlite:///:memory:",
        ) as storage:
            await self._check_records(storage)

    async def test_redis(self) -> None:
        """Exercise the same contract against an isolated Redis database."""
        fakeredis = pytest.importorskip("fakeredis.aioredis")
        async with fakeredis.FakeRedis(
            decode_responses=True,
        ) as client:
            async with RedisStorage(
                connection_pool=client.connection_pool,
            ) as storage:
                await self._check_records(storage)

    async def _check_records(self, storage: StorageBase) -> None:
        """Check top-level, computed, and nested record payloads."""
        cases = [
            (
                "skill",
                SkillRecord(
                    user_id="alice",
                    name="lookup",
                    description="Original description",
                    tags=["reference"],
                ),
            ),
            (
                "mcp",
                MCPRecord(
                    user_id="alice",
                    client=MCPClient(
                        name="lookup",
                        is_stateful=False,
                        mcp_config=HttpMCPConfig(
                            url="https://example.com/mcp",
                        ),
                    ),
                    values={"api_key": "test-secret"},
                ),
            ),
            (
                "voice_profile",
                VoiceProfileRecord(
                    user_id="alice",
                    data=VoiceProfileData(
                        name="voice",
                        metadata={"language": "en"},
                    ),
                ),
            ),
        ]
        for entity, record in cases:
            with self.subTest(entity=entity):
                upsert = getattr(storage, f"upsert_{entity}")
                get = getattr(storage, f"get_{entity}")
                list_records = getattr(storage, f"list_{entity}s")
                delete = getattr(storage, f"delete_{entity}")
                self.assertIsNone(await get("alice", "missing"))
                self.assertEqual(await list_records("alice"), [])

                await upsert("alice", record)
                other = record.model_copy(
                    update={"id": f"bob-{entity}", "user_id": "bob"},
                    deep=True,
                )
                await upsert("bob", other)
                fetched = await get("alice", record.id)
                self.assertIsInstance(fetched, type(record))
                self.assertEqual(fetched.model_dump(), record.model_dump())
                self.assertEqual(await list_records("alice"), [fetched])
                self.assertEqual(await list_records("bob"), [other])
                self.assertIsNone(await get("bob", record.id))
                self.assertIsNone(await get("alice", other.id))

                if isinstance(record, VoiceProfileRecord):
                    record.data.metadata = {"language": "zh"}
                else:
                    record.description = "Updated description"
                await upsert("alice", record)
                updated = await get("alice", record.id)
                self.assertEqual(updated.model_dump(), record.model_dump())
                self.assertEqual(await list_records("alice"), [updated])

                self.assertFalse(await delete("bob", record.id))
                self.assertIsNotNone(await get("alice", record.id))
                self.assertTrue(await delete("alice", record.id))
                self.assertFalse(await delete("alice", record.id))
                self.assertIsNone(await get("alice", record.id))
                self.assertEqual(await list_records("alice"), [])
                self.assertEqual(await list_records("bob"), [other])
