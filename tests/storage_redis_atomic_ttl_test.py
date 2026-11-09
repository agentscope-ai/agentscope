# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for the atomicity of RedisStorage writes with TTL."""

from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.app.storage import RedisStorage


def make_storage() -> RedisStorage:
    """Create a RedisStorage instance backed by fakeredis."""
    storage = RedisStorage.__new__(RedisStorage)
    storage._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage.key_ttl = None
    storage.key_config = RedisStorage.KeyConfig()
    return storage


class _CommandRecorder:
    """Proxy that records the command names sent to the client."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.commands: list[str] = []

    async def set(self, *args: Any, **kwargs: Any) -> Any:
        """Record and forward a ``SET``."""
        self.commands.append("set")
        return await self._client.set(*args, **kwargs)

    async def expire(self, *args: Any, **kwargs: Any) -> Any:
        """Record and forward an ``EXPIRE``."""
        self.commands.append("expire")
        return await self._client.expire(*args, **kwargs)


class SetWithTTLTest(IsolatedAsyncioTestCase):
    """A write under a TTL must not leave a key without expiry behind."""

    async def asyncSetUp(self) -> None:
        """Create the storage and the command recorder."""
        self.storage = make_storage()
        self.recorder = _CommandRecorder(self.storage._client)
        self.storage._client = self.recorder

    async def test_ttl_is_applied_in_the_same_write(self) -> None:
        """Only ``SET`` may be issued, carrying the expiry itself."""
        self.storage.key_ttl = 60

        await self.storage._set_with_ttl("k", "v")

        self.assertEqual(self.recorder.commands, ["set"])
        self.assertEqual(await self.storage._client._client.ttl("k"), 60)
        self.assertEqual(
            await self.storage._client._client.get("k"),
            "v",
        )

    async def test_no_expiry_when_ttl_is_disabled(self) -> None:
        """Without a configured TTL the key must stay persistent."""
        self.storage.key_ttl = None

        await self.storage._set_with_ttl("k", "v")

        self.assertEqual(self.recorder.commands, ["set"])
        self.assertEqual(await self.storage._client._client.ttl("k"), -1)
