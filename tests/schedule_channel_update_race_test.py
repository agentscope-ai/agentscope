# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Concurrent updates to one schedule or channel must not lose a change.

``PATCH /schedule/{id}`` and ``ChannelService.update`` read the whole
record and write the whole record back. Each test pauses update A just
before its write, lets update B run, then resumes A. Without a
per-object lock B reads the same record as A and one of the two changes
is lost; with it B waits for A and builds on A's write.
"""
import asyncio
from datetime import datetime
from typing import Any, Callable, Coroutine
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from fakeredis.aioredis import FakeRedis
from fastapi import HTTPException
from pydantic import BaseModel

from agentscope.app._manager import SchedulerManager
from agentscope.app._router import _schedule
from agentscope.app._router._schedule import update_schedule
from agentscope.app._router._schema._schedule import UpdateScheduleRequest
from agentscope.app._service import ChannelService
from agentscope.app.channel import (
    ChannelBase,
    ChannelError,
    ChannelTypeRegistry,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    ChannelBinding,
    ChannelRecord,
    ChatModelConfig,
    RedisStorage,
    RoutingConfig,
    ScheduleData,
    ScheduleRecord,
    SessionSettings,
)

# How long B may run while A is paused. With the lock B blocks on it
# for the whole window; without it B finishes well within it.
_B_HEAD_START_SECS = 0.2


class _PausingRedis(FakeRedis):
    """FakeRedis that pauses the next SET on a matching key."""

    def __init__(self) -> None:
        super().__init__(decode_responses=True)
        self.pause_key: str | None = None
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()

    async def set(  # type: ignore[override]
        self,
        name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Pause once on a matching key, then write."""
        if self.pause_key is not None and self.pause_key in name:
            self.pause_key = None
            self.paused.set()
            await self.resume.wait()
        return await super().set(name, *args, **kwargs)


async def _race(
    redis: _PausingRedis,
    pause_key: str,
    a: Callable[[], Coroutine[Any, Any, Any]],
    b: Callable[[], Coroutine[Any, Any, Any]],
) -> tuple[Any, Any]:
    """Pause A before its write, give B a head start, then resume A."""
    redis.pause_key = pause_key
    task_a = asyncio.create_task(a())
    await redis.paused.wait()
    task_b = asyncio.create_task(b())
    await asyncio.wait({task_b}, timeout=_B_HEAD_START_SECS)
    redis.resume.set()
    return await task_a, await task_b


class _Scheduler(SchedulerManager):
    """Scheduler manager without timers."""

    def __init__(self) -> None:
        super().__init__(
            storage=None,
            message_bus=None,
            workspace_manager=None,
        )

    async def notify_changed(self, schedule_id: str) -> None:
        """No timers to reconcile."""


class _FakeChannel(ChannelBase):
    """Minimal channel type for storing records."""

    channel_type = "fake"
    display_name = "Fake"
    platform_bot_id_field = "bot_id"

    class Credentials(BaseModel):
        """Fake credentials."""

        bot_id: str

    class Config(BaseModel):
        """No platform config."""


class ScheduleUpdateRaceTest(IsolatedAsyncioTestCase):
    """Concurrent ``PATCH /schedule/{id}`` keeps both changes."""

    async def asyncSetUp(self) -> None:
        """Store one enabled UTC schedule on FakeRedis."""
        self.redis = _PausingRedis()
        self.storage = RedisStorage()
        self.storage._client = self.redis
        self.bus = InMemoryMessageBus()
        self.scheduler = _Scheduler()
        await self.storage.upsert_schedule(
            "u1",
            ScheduleRecord(
                id="s1",
                user_id="u1",
                agent_id="a1",
                data=ScheduleData(
                    name="daily report",
                    cron_expression="0 9 * * *",
                    timezone="UTC",
                    enabled=True,
                    started_at=datetime(2026, 1, 1),
                    chat_model_config=ChatModelConfig(
                        type="test",
                        credential_id="c1",
                        model="m1",
                        parameters={},
                    ),
                ),
            ),
        )

    def _patch(self, **fields: Any) -> Coroutine[Any, Any, ScheduleRecord]:
        return update_schedule(
            "s1",
            UpdateScheduleRequest(**fields),
            user_id="u1",
            storage=self.storage,
            scheduler=self.scheduler,
            message_bus=self.bus,
        )

    async def test_concurrent_updates_keep_both_changes(self) -> None:
        """A timezone change and a disable both survive."""
        _, b = await _race(
            self.redis,
            ":schedule:",
            lambda: self._patch(timezone="Asia/Shanghai"),
            lambda: self._patch(enabled=False),
        )

        stored = (await self.storage.get_schedule("u1", "s1")).data
        self.assertFalse(stored.enabled)
        self.assertEqual(stored.timezone, "Asia/Shanghai")
        # B ran after A's write, so its response already shows both.
        self.assertEqual(b.data.timezone, "Asia/Shanghai")

    async def test_lock_timeout_returns_409_without_writing(self) -> None:
        """An update that cannot get the lock fails instead of writing."""
        with patch.object(
            _schedule,
            "_SCHEDULE_UPDATE_LOCK_TIMEOUT_SECS",
            0.05,
        ):
            async with self.bus.acquire_lock(
                MessageBusKeys.schedule_lock("s1"),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await self._patch(enabled=False)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertTrue(
            (await self.storage.get_schedule("u1", "s1")).data.enabled,
        )


class ChannelUpdateRaceTest(IsolatedAsyncioTestCase):
    """Concurrent ``ChannelService.update`` keeps both changes."""

    async def asyncSetUp(self) -> None:
        """Store one channel bound to agent a1 on FakeRedis."""
        self.redis = _PausingRedis()
        self.storage = RedisStorage()
        self.storage._client = self.redis
        self.bus = InMemoryMessageBus()
        self.service = ChannelService(
            self.storage,
            self.bus,
            ChannelTypeRegistry([_FakeChannel]),
        )
        now = datetime.now()
        record = ChannelRecord(
            channel_type="fake",
            user_id="u1",
            enabled=False,
            name="support",
            credentials={"bot_id": "bot-1"},
            routing=RoutingConfig(
                bindings=[ChannelBinding(match_value="*", agent_id="a1")],
            ),
            session=SessionSettings(chat_model_config={"type": "x"}),
            created_at=now,
            updated_at=now,
        )
        await self.storage.upsert_channel(record, "bot-1")
        self.channel_id = record.id

    def _routing(self, agent_id: str) -> dict:
        return {
            "routing": {
                "bindings": [{"match_value": "*", "agent_id": agent_id}],
            },
        }

    async def test_concurrent_updates_keep_both_changes(self) -> None:
        """A rename and a routing change both survive."""
        await _race(
            self.redis,
            "channel_record",
            lambda: self.service.update(self.channel_id, {"name": "renamed"}),
            lambda: self.service.update(self.channel_id, self._routing("a2")),
        )

        stored = await self.storage.get_channel(self.channel_id)
        self.assertEqual(stored.name, "renamed")
        self.assertEqual(stored.routing.bindings[0].agent_id, "a2")

    async def test_lock_timeout_raises_409_without_writing(self) -> None:
        """An update that cannot get the lock fails instead of writing."""
        with patch.object(ChannelService, "_UPDATE_LOCK_TIMEOUT_SECS", 0.05):
            async with self.bus.acquire_lock(
                MessageBusKeys.channel_lock(self.channel_id),
            ):
                with self.assertRaises(ChannelError) as ctx:
                    await self.service.update(
                        self.channel_id,
                        {"name": "renamed"},
                    )

        self.assertEqual(ctx.exception.status_code, 409)
        stored = await self.storage.get_channel(self.channel_id)
        self.assertEqual(stored.name, "support")
