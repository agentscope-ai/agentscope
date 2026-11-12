# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Shared Code Mode ownership across independent application instances."""

import asyncio
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis

from agentscope.app._service._code_mode_guard import CodeModeRegistryGuard
from agentscope.app.message_bus import MessageBusKeys, RedisMessageBus
from agentscope.code_mode import CodeModeToolkit
from agentscope.code_mode._script import Script, _GUARD_TASKS
from agentscope.message import ToolResultState
from agentscope.state import AgentState
from agentscope.tool import LocalBackend, Toolkit


class CodeModeGuardTest(IsolatedAsyncioTestCase):
    """Exercise the real Redis CAS operations through separate clients."""

    async def asyncSetUp(self) -> None:
        """Give two replica guards distinct clients on one shared server."""
        server = FakeServer()
        self.buses = [RedisMessageBus(), RedisMessageBus()]
        for bus in self.buses:
            bus._client = FakeRedis(server=server, decode_responses=True)
            self.addAsyncCleanup(bus._client.aclose)
        self.guards = [CodeModeRegistryGuard(bus) for bus in self.buses]
        self.session_id = "session-1"
        self.key = MessageBusKeys.code_mode_execution(self.session_id)
        # pylint: disable=consider-using-with
        self.directory = self.enterContext(tempfile.TemporaryDirectory())

    def script(self, replica: int = 0) -> Script:
        """Build a fresh runtime with no process-local ownership state."""
        return Script(
            CodeModeToolkit(
                Toolkit(),
                backend=LocalBackend(),
                cwd=self.directory,
                execution_guard=self.guards[replica],
            ),
            AgentState(session_id=self.session_id),
        )

    async def test_atomic_acquisition_has_one_owner_without_expiry(
        self,
    ) -> None:
        """Only one replica can claim a session and time cannot unlock it."""
        results = await asyncio.gather(
            *(guard.acquire(self.session_id) for guard in self.guards),
            return_exceptions=True,
        )
        owners = [item for item in results if isinstance(item, str)]
        self.assertEqual(
            (
                len(owners),
                sum(isinstance(item, RuntimeError) for item in results),
                await self.buses[1].registry_getall(self.key),
                await self.buses[1]._client.ttl(self.key),
            ),
            (1, 1, {"owner": owners[0]}, -1),
        )

    async def test_old_token_cannot_release_a_new_execution(self) -> None:
        """A late completion cannot delete the next execution's marker."""
        first, second = self.guards
        old = await first.acquire(self.session_id)
        await first.release(self.session_id, old)
        new = await second.acquire(self.session_id)
        await first.release(self.session_id, old)
        self.assertEqual(
            await self.buses[0].registry_getall(self.key),
            {"owner": new},
        )
        await second.release(self.session_id, new)
        self.assertEqual(await self.buses[0].registry_getall(self.key), {})

    async def test_storage_error_never_starts_a_worker(self) -> None:
        """An unavailable ownership store must fail closed."""
        with (
            patch.object(
                self.buses[0],
                "registry_set_if",
                AsyncMock(side_effect=ConnectionError("store unavailable")),
            ),
            patch("agentscope.code_mode._script.WorkerProcess") as worker,
        ):
            result = await self.script().run("text('unsafe')")
        self.assertEqual(
            (result.state, worker.call_count, result.metadata["code_mode"]),
            (
                ToolResultState.ERROR,
                0,
                {
                    "calls": [],
                    "truncated": False,
                    "error": "ConnectionError: store unavailable",
                },
            ),
        )

    async def test_ambiguous_acquisition_keeps_other_replicas_blocked(
        self,
    ) -> None:
        """A lost response after the write cannot enable another script."""
        original = self.buses[0].registry_set_if

        async def write_then_fail(
            *args: Any,
            expected: str | None,
            **kwargs: Any,
        ) -> bool:
            await original(*args, expected=expected, **kwargs)
            raise ConnectionError("response lost")

        with patch.object(
            self.buses[0],
            "registry_set_if",
            side_effect=write_then_fail,
        ):
            result = await self.script().run("text('unsafe')")
        with patch("agentscope.code_mode._script.WorkerProcess") as worker:
            blocked = await self.script(1).run("text('unsafe')")
        self.assertEqual(
            (result.state, blocked.state, worker.call_count),
            (ToolResultState.ERROR, ToolResultState.ERROR, 0),
        )
        self.assertEqual(
            list((await self.buses[1].registry_getall(self.key)).keys()),
            ["owner"],
        )

    async def test_cancelled_acquisition_does_not_clear_uncertain_owner(
        self,
    ) -> None:
        """Cancellation after a store write leaves recovery to the owner."""
        original = self.buses[0].registry_set_if
        written = asyncio.Event()

        async def write_then_wait(
            *args: Any,
            expected: str | None,
            **kwargs: Any,
        ) -> bool:
            await original(*args, expected=expected, **kwargs)
            written.set()
            await asyncio.Event().wait()
            return True

        with patch.object(
            self.buses[0],
            "registry_set_if",
            side_effect=write_then_wait,
        ):
            task = asyncio.create_task(self.script().run("text('unsafe')"))
            await written.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        with self.assertRaisesRegex(RuntimeError, "shared ownership"):
            await self.guards[1].acquire(self.session_id)

    async def test_release_failure_keeps_the_session_blocked(self) -> None:
        """Completion does not erase ownership when the store is down."""
        with patch.object(
            self.buses[0],
            "registry_del_if",
            AsyncMock(side_effect=ConnectionError("release unavailable")),
        ):
            result = await self.script().run("text('finished')")
        self.assertEqual(result.state, ToolResultState.SUCCESS)
        with self.assertRaisesRegex(RuntimeError, "shared ownership"):
            await self.guards[1].acquire(self.session_id)

    async def test_store_waits_are_bounded(self) -> None:
        """Storage stalls cannot bypass the execution and cleanup budgets."""

        async def stalled(*_args: Any, **_kwargs: Any) -> None:
            await asyncio.Event().wait()

        script = self.script()
        script.toolkit.config.timeout = 0.01
        with (
            patch.object(
                self.buses[0],
                "registry_set_if",
                side_effect=stalled,
            ),
            patch("agentscope.code_mode._script.WorkerProcess") as worker,
        ):
            result = await asyncio.wait_for(script.run("text('unsafe')"), 2)
        self.assertEqual(
            (result.state, worker.call_count, result.metadata["code_mode"]),
            (
                ToolResultState.ERROR,
                0,
                {
                    "calls": [],
                    "truncated": False,
                    "error": "Code Mode execution timed out",
                },
            ),
        )
        script = self.script()
        script.toolkit.config.tool_cleanup_timeout = 0.01
        with patch.object(
            self.buses[0],
            "registry_del_if",
            side_effect=stalled,
        ):
            result = await asyncio.wait_for(script.run("text('finished')"), 5)
        self.assertEqual(result.state, ToolResultState.SUCCESS)
        with self.assertRaisesRegex(RuntimeError, "shared ownership"):
            await self.guards[1].acquire(self.session_id)

    async def test_normal_and_failed_scripts_release_ownership(self) -> None:
        """Completed scripts allow the next replica to execute."""
        for code, state in (
            ("text('finished')", ToolResultState.SUCCESS),
            ("raise ValueError('failed')", ToolResultState.ERROR),
        ):
            with self.subTest(code=code):
                result = await self.script().run(code)
                self.assertEqual(
                    (
                        result.state,
                        await self.buses[1].registry_getall(self.key),
                    ),
                    (state, {}),
                )
                result = await self.script(1).run("text('next replica')")
                self.assertEqual(result.state, ToolResultState.SUCCESS)

    async def test_cancelled_release_wait_keeps_marker(self) -> None:
        """Shutdown cannot release ownership of a still-running host call."""
        token = await self.guards[0].acquire(self.session_id)
        pending = asyncio.create_task(asyncio.Event().wait())
        release = asyncio.create_task(
            self.script().release_execution(token, {pending}),
        )
        await asyncio.sleep(0)
        release.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await release
        self.assertEqual(
            (
                pending.cancelled(),
                await self.buses[1].registry_getall(self.key),
            ),
            (False, {"owner": token}),
        )
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await asyncio.gather(*list(_GUARD_TASKS))
