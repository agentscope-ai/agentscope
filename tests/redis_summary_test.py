# -*- coding: utf-8 -*-
"""Redis summary recovery and lifecycle regression tests."""
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
from redis.exceptions import ConnectionError as RedisConnectionError

from agentscope.memory import RedisMemory
from agentscope.message import Msg


class RedisSummaryTest(IsolatedAsyncioTestCase):
    """Recover summaries through the same backend as their session messages."""

    async def asyncSetUp(self) -> None:
        """Share one fake server while creating independent memory objects."""
        self.server = fakeredis.FakeServer()
        self.memories: list[RedisMemory] = []

    async def asyncTearDown(self) -> None:
        """Close all clients created by the test."""
        for memory in self.memories:
            await memory.close()

    def memory(self, **kwargs: Any) -> RedisMemory:
        """Create an independent memory on the shared Redis backend."""
        fake = fakeredis.aioredis.FakeRedis(
            server=self.server,
            decode_responses=kwargs.pop("decode_responses", True),
        )
        memory = RedisMemory(connection_pool=fake.connection_pool, **kwargs)
        self.memories.append(memory)
        return memory

    async def test_rebuilt_instance_recovers_filtered_context(self) -> None:
        """Recover the summary after reconstructing memory."""
        first = self.memory()
        await first.add(Msg("user", "budget approved", "user"), "compressed")
        await first.add(Msg("user", "continue", "user"))
        await first.update_compressed_summary("SUMMARY: budget approved")
        rebuilt = self.memory()
        result = await rebuilt.get_memory(exclude_mark="compressed")
        self.assertEqual(
            [message.content for message in result],
            ["SUMMARY: budget approved", "continue"],
        )
        self.assertEqual(await rebuilt.size(), 2)
        self.assertEqual(
            [
                m.content
                for m in await rebuilt.get_memory(prepend_summary=False)
            ],
            ["budget approved", "continue"],
        )

    async def test_live_instances_read_each_others_updates(self) -> None:
        """A previously read summary cannot hide another instance's update."""
        first, second = self.memory(), self.memory(decode_responses=False)
        for writer, reader, summary in (
            (first, second, "one"),
            (first, second, "two"),
            (second, first, "three"),
        ):
            await writer.update_compressed_summary(summary)
            self.assertEqual((await reader.get_memory())[0].content, summary)

    async def test_clear_removes_summary_for_existing_and_new_clients(
        self,
    ) -> None:
        """Clearing a session also retires previously cached summaries."""
        first, second = self.memory(), self.memory()
        await first.update_compressed_summary("old")
        await second.get_memory()
        await first.clear()
        for memory in (first, second, self.memory()):
            self.assertEqual(await memory.get_memory(), [])
        await first.clear()
        await first.update_compressed_summary("new")
        await first.update_compressed_summary("")
        self.assertEqual(await second.get_memory(), [])

    async def test_prefix_user_and_session_keep_summaries_separate(
        self,
    ) -> None:
        """The summary follows the existing session namespace conventions."""
        owner = self.memory(key_prefix="app:", user_id="u", session_id="s")
        await owner.update_compressed_summary("owned")
        for kwargs in (
            {"key_prefix": "other:", "user_id": "u", "session_id": "s"},
            {"key_prefix": "app:", "user_id": "other", "session_id": "s"},
            {"key_prefix": "app:", "user_id": "u", "session_id": "other"},
        ):
            other = self.memory(**kwargs)
            self.assertEqual(await other.get_memory(), [])
            await other.clear()
        self.assertEqual((await owner.get_memory())[0].content, "owned")

    async def test_summary_only_session_ttl_refresh_and_expiration(
        self,
    ) -> None:
        """A summary has a sliding lifetime even without ordinary messages."""
        memory = self.memory(key_ttl=30)
        await memory.update_compressed_summary("expires")
        keys = await memory.get_client().keys("*")
        self.assertEqual(len(keys), 1)
        key = keys[0]
        self.assertGreater(await memory.get_client().ttl(key), 0)
        await memory.get_client().expire(key, 1)
        await memory.size()
        self.assertGreater(await memory.get_client().ttl(key), 20)
        await memory.get_client().expire(key, 0)
        self.assertEqual(await memory.get_memory(), [])

    async def test_nonpositive_ttl_does_not_leave_local_summary(self) -> None:
        """Zero/negative TTL preserves Redis's existing expire semantics."""
        for ttl in (0, -1):
            with self.subTest(ttl=ttl):
                memory = self.memory(key_ttl=ttl)
                await memory.update_compressed_summary("expired")
                self.assertEqual(await memory.get_memory(), [])

    async def test_snapshot_restore_is_applied_on_summary_read(self) -> None:
        """Migrate legacy snapshots without rolling back Redis data."""
        first, second = self.memory(), self.memory()
        await first.update_compressed_summary("backend")
        first.load_state_dict({"_compressed_summary": "checkpoint"})
        self.assertEqual(
            first.state_dict(),
            {"_compressed_summary": "checkpoint"},
        )
        await first.get_memory(prepend_summary=False)
        self.assertEqual((await second.get_memory())[0].content, "backend")
        self.assertEqual((await first.get_memory())[0].content, "backend")
        self.assertEqual((await second.get_memory())[0].content, "backend")
        legacy = self.memory(user_id="legacy")
        legacy.load_state_dict({"_compressed_summary": "checkpoint"})
        self.assertEqual((await legacy.get_memory())[0].content, "checkpoint")
        self.assertEqual(
            (await self.memory(user_id="legacy").get_memory())[0].content,
            "checkpoint",
        )
        first.load_state_dict({"_compressed_summary": "discard"})
        first.load_state_dict({}, strict=False)
        await first.clear()
        self.assertEqual(await first.get_memory(), [])
        with self.assertRaises(KeyError):
            first.load_state_dict({})

    async def test_backend_write_and_clear_failures_do_not_acknowledge(
        self,
    ) -> None:
        """A failed backend transaction must not update only local state."""
        memory = self.memory()
        await memory.update_compressed_summary("acknowledged")
        for operation in ("update", "clear"):
            pipeline = memory.get_client().pipeline()
            with patch.object(
                memory.get_client(),
                "pipeline",
                return_value=pipeline,
            ):
                with patch.object(
                    pipeline,
                    "execute",
                    AsyncMock(side_effect=RedisConnectionError("offline")),
                ):
                    with self.assertRaises(RedisConnectionError):
                        if operation == "update":
                            await memory.update_compressed_summary("new")
                        else:
                            await memory.clear()
            self.assertEqual(
                memory.state_dict()["_compressed_summary"],
                "acknowledged",
            )
            self.assertEqual(
                (await self.memory().get_memory())[0].content,
                "acknowledged",
            )

    async def test_snapshot_write_failure_retains_restore_intent(self) -> None:
        """Retain the restore intent when its Redis write fails."""
        memory = self.memory()
        memory.load_state_dict({"_compressed_summary": "checkpoint"})
        pipeline = memory.get_client().pipeline()
        with patch.object(
            memory.get_client(),
            "pipeline",
            return_value=pipeline,
        ):
            with patch.object(
                pipeline,
                "execute",
                AsyncMock(side_effect=RedisConnectionError("offline")),
            ):
                with self.assertRaises(RedisConnectionError):
                    await memory.get_memory()
        self.assertEqual(await self.memory().get_memory(), [])
        self.assertEqual((await memory.get_memory())[0].content, "checkpoint")
        self.assertEqual(
            (await self.memory().get_memory())[0].content,
            "checkpoint",
        )

    async def test_empty_snapshot_preserves_the_backend_summary(self) -> None:
        """An old empty snapshot must not erase a newer Redis summary."""
        memory = self.memory()
        await memory.update_compressed_summary("backend")
        memory.load_state_dict({"_compressed_summary": ""})
        self.assertEqual((await memory.get_memory())[0].content, "backend")
        self.assertEqual(
            (await self.memory().get_memory())[0].content,
            "backend",
        )

    async def test_snapshot_cannot_overwrite_an_interleaved_writer(
        self,
    ) -> None:
        """Preserve a write made while snapshot restoration is waiting."""
        memory, writer = self.memory(), self.memory()
        memory.load_state_dict({"_compressed_summary": "old checkpoint"})
        pipeline = memory.get_client().pipeline()
        execute = pipeline.execute
        ready, release = asyncio.Event(), asyncio.Event()

        async def delayed_execute() -> list:
            """Pause before the actual Redis transaction is executed."""
            ready.set()
            await release.wait()
            return await execute()

        with patch.object(
            memory.get_client(),
            "pipeline",
            return_value=pipeline,
        ):
            with patch.object(pipeline, "execute", delayed_execute):
                task = asyncio.create_task(memory.get_memory())
                try:
                    await asyncio.wait_for(ready.wait(), 5)
                    await writer.update_compressed_summary("new writer")
                finally:
                    release.set()
                result = await asyncio.wait_for(task, 5)
        self.assertEqual(result[0].content, "new writer")

    async def test_backend_read_failure_propagates(self) -> None:
        """Propagate a failed Redis read instead of returning cached data."""
        memory = self.memory()
        await memory.update_compressed_summary("cached")
        with patch.object(
            memory.get_client(),
            "get",
            AsyncMock(side_effect=RedisConnectionError("offline")),
        ):
            with self.assertRaises(RedisConnectionError):
                await memory.get_memory()


@skipUnless(shutil.which("redis-server"), "local redis-server is required")
class RedisSummaryProcessTest(IsolatedAsyncioTestCase):
    """Read summaries using a real Redis and a separate Python process."""

    async def test_new_process_reads_compressed_context(self) -> None:
        """Recover the context without the original application's fields."""
        with tempfile.TemporaryDirectory() as directory:
            socket = str(Path(directory) / "redis.sock")
            process = subprocess.Popen(
                [
                    "redis-server",
                    "--port",
                    "0",
                    "--unixsocket",
                    socket,
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            memory = RedisMemory(unix_socket_path=socket)
            try:
                for _ in range(500):
                    try:
                        await memory.get_client().ping()
                        break
                    except RedisConnectionError:
                        await asyncio.sleep(0.01)
                else:
                    self.fail("Redis did not start")
                await memory.add(
                    Msg("user", "old decision", "user"),
                    "compressed",
                )
                await memory.add(Msg("user", "continue", "user"))
                await memory.update_compressed_summary("decision summary")
                code = """
import asyncio, json, sys
from agentscope.memory import RedisMemory
async def main():
    memory = RedisMemory(unix_socket_path=sys.argv[1])
    try:
        messages = await memory.get_memory(exclude_mark='compressed')
        print(json.dumps([message.content for message in messages]))
    finally:
        await memory.close()
asyncio.run(main())
"""
                child = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    code,
                    socket,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    child.communicate(),
                    20,
                )
                self.assertEqual(child.returncode, 0, stderr.decode())
                self.assertEqual(
                    json.loads(stdout),
                    ["decision summary", "continue"],
                )
            finally:
                await memory.close()
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
