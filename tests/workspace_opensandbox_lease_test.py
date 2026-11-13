# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Lease renewal for long OpenSandbox turns (issue #3208).

The lease is modeled with a fake clock. No OpenSandbox SDK, network,
or real sandbox is required.

Entry renewal alone matches the previous cache-access behavior: a
turn that outlasts the lease loses the sandbox. Piggyback renewal
runs on the backend round-trip every builtin tool and gateway call
already makes, so chat, scheduled tasks, and channels share it
without a per-path flag.
"""

import asyncio
import shutil
import tempfile
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.workspace_manager import OpenSandboxWorkspaceManager
from agentscope.workspace import OpenSandboxBackend, OpenSandboxWorkspace
from agentscope.workspace._base import WorkspaceBase
from agentscope.workspace._docker import DockerWorkspace
from agentscope.workspace._local_workspace import LocalWorkspace
from agentscope.workspace._opensandbox._constants import DEFAULT_TIMEOUT
from agentscope.workspace._opensandbox._opensandbox_backend import (
    SandboxLeaseLost,
)

_TURN_TIMES = (10.0, 25.0, 40.0, 70.0, 90.0)


def _not_found(message: str = "sandbox not found") -> RuntimeError:
    """Build a 404-shaped error without importing the SDK."""
    exc = RuntimeError(message)
    exc.status_code = 404  # type: ignore[attr-defined]
    return exc


class _Clock:
    """Monotonic stand-in. Tests advance it; nothing sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward."""
        self.now += seconds


class _LeaseSandbox:
    """Server-side lease: ``renew`` restores it, expiry reclaims it.

    The call that observes ``clock >= expires_at`` fails. A renewal
    that arrives before that moment sets a fresh expiration of
    ``timeout`` from now, matching ``Sandbox.renew``.
    """

    def __init__(self, clock: _Clock, lease: float) -> None:
        self.clock = clock
        self.lease = lease
        self.expires_at = clock() + lease
        self.renew_count = 0
        self.call_count = 0
        self.reclaimed = False
        self.commands = self
        self.files = self
        self.id = "sandbox-1"
        self.failures_left = 0

    def _dead(self) -> bool:
        return self.reclaimed or self.clock() >= self.expires_at

    async def renew(self, timeout: timedelta) -> None:
        """Restore the lease, or fail if the sandbox is already gone."""
        self.renew_count += 1
        if self.failures_left:
            self.failures_left -= 1
            raise TimeoutError("control plane unavailable")
        if self._dead():
            self.reclaimed = True
            raise _not_found()
        self.expires_at = self.clock() + timeout.total_seconds()

    async def run(
        self,
        command: str,
        opts: object = None,
    ) -> SimpleNamespace:
        """One sandbox command. Fails once the lease has lapsed."""
        del command, opts
        self.call_count += 1
        if self._dead():
            self.reclaimed = True
            raise _not_found()
        return SimpleNamespace(exit_code=0, stdout=b"ok", stderr=b"")

    async def write_files(self, entries: object) -> None:
        """File write used by ``write_file`` after its mkdir round-trip."""
        del entries
        if self._dead():
            self.reclaimed = True
            raise _not_found()

    async def pause(self) -> None:
        """Close-path double. Unused by the lease tests."""

    async def close(self) -> None:
        """Close-path double. Unused by the lease tests."""


def _workspace(
    clock: _Clock,
    *,
    lease: float = 60,
    interval: float = 30,
) -> tuple[OpenSandboxWorkspace, _LeaseSandbox, OpenSandboxBackend]:
    """Wire a workspace, its fake sandbox, and a piggyback backend."""
    workspace = OpenSandboxWorkspace(
        workspace_id="wid",
        timeout_seconds=int(lease),
        renew_interval_seconds=interval,
    )
    sandbox = _LeaseSandbox(clock, lease)
    workspace._sandbox = sandbox
    backend = OpenSandboxBackend(
        sandbox,
        "/workspace",
        prepare_for_reuse=workspace.prepare_for_reuse,
    )
    return workspace, sandbox, backend


def _patch_clock(clock: _Clock) -> object:
    """Patch the clock ``prepare_for_reuse`` reads."""
    return patch(
        "agentscope.workspace._opensandbox."
        "_opensandbox_workspace.time.monotonic",
        clock,
    )


class TestMidTurnLease(IsolatedAsyncioTestCase):
    """A turn longer than the lease, with and without piggyback."""

    async def test_entry_renewal_alone_dies_mid_turn(self) -> None:
        """Five calls, 60s lease: calls after expiry fail.

        Only the entry renewal runs. That is the gap when tool
        round-trips do not renew.
        """
        clock = _Clock()
        workspace, sandbox, _backend = _workspace(clock)
        bare = OpenSandboxBackend(sandbox, "/workspace")
        with _patch_clock(clock):
            self.assertTrue(await workspace.prepare_for_reuse())
            results = []
            failures = []
            for moment in _TURN_TIMES:
                clock.now = moment
                result = await bare.exec_shell(["echo", "ok"])
                if result.exit_code == 0:
                    results.append(result)
                else:
                    failures.append(result)

        self.assertEqual(len(results), 3)
        self.assertEqual(len(failures), 2)
        self.assertEqual(sandbox.renew_count, 1)
        self.assertTrue(sandbox.reclaimed)

    async def test_piggyback_turn_completes_and_renewal_is_bounded(
        self,
    ) -> None:
        """The same turn completes, with one renewal per interval."""
        clock = _Clock()
        workspace, sandbox, backend = _workspace(clock)
        with _patch_clock(clock):
            self.assertTrue(await workspace.prepare_for_reuse())
            for moment in _TURN_TIMES:
                clock.now = moment
                result = await backend.exec_shell(["echo", "ok"])
                self.assertEqual(result.exit_code, 0, moment)

        self.assertFalse(sandbox.reclaimed)
        # Entry at t=0, then tool calls at t=40 and t=70. The calls
        # at t=10, t=25, and t=90 are inside the 30s interval.
        self.assertEqual(sandbox.renew_count, 3)
        self.assertLess(sandbox.renew_count, 1 + len(_TURN_TIMES))

    async def test_burst_and_composite_call_do_not_storm(self) -> None:
        """Many round-trips in one interval renew once."""
        clock = _Clock()
        _, sandbox, backend = _workspace(clock)
        with _patch_clock(clock):
            for _ in range(10):
                result = await backend.exec_shell(["echo", "ok"])
                self.assertEqual(result.exit_code, 0)
            await backend.write_file("/workspace/note.txt", b"hello")

        # The burst and the write (mkdir exec + write) share the
        # first attempt. Nothing else is due until t=30.
        self.assertEqual(sandbox.renew_count, 1)
        self.assertFalse(sandbox.reclaimed)

    async def test_concurrent_round_trips_renew_once(self) -> None:
        """Overlapping calls share a single in-flight renewal."""
        clock = _Clock()
        workspace, sandbox, _backend = _workspace(clock)
        started = asyncio.Event()
        release = asyncio.Event()
        original = sandbox.renew

        async def blocking_renew(timeout: timedelta) -> None:
            started.set()
            await release.wait()
            await original(timeout)

        sandbox.renew = blocking_renew  # type: ignore[method-assign]
        with _patch_clock(clock):
            first = asyncio.create_task(workspace.prepare_for_reuse())
            await started.wait()
            second = asyncio.create_task(workspace.prepare_for_reuse())
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.gather(first, second)

        self.assertEqual(results, [True, True])
        self.assertEqual(sandbox.renew_count, 1)


class TestRenewalFailureSemantics(IsolatedAsyncioTestCase):
    """Transient failures stay usable; a 404 does not."""

    async def test_transient_failure_retries_on_the_next_interval(
        self,
    ) -> None:
        """One control-plane error does not end renewal or the turn."""
        clock = _Clock()
        workspace, sandbox, backend = _workspace(clock)
        sandbox.failures_left = 1
        with _patch_clock(clock):
            self.assertTrue(await workspace.prepare_for_reuse())
            self.assertFalse(workspace._lease_lost)
            clock.advance(1)
            self.assertTrue(await workspace.prepare_for_reuse())
            self.assertEqual(sandbox.renew_count, 1)
            clock.advance(30)
            result = await backend.exec_shell(["echo", "ok"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(sandbox.renew_count, 2)
        self.assertFalse(sandbox.reclaimed)
        self.assertFalse(workspace._lease_lost)

    async def test_not_found_is_surfaced_and_not_retried_immediately(
        self,
    ) -> None:
        """A dead sandbox fails the call and is not treated as healthy."""
        clock = _Clock()
        workspace, sandbox, backend = _workspace(clock)
        sandbox.reclaimed = True
        with _patch_clock(clock):
            self.assertFalse(await workspace.prepare_for_reuse())
            with self.assertRaises(SandboxLeaseLost) as caught:
                await backend.exec_shell(["echo", "ok"])
            self.assertEqual(caught.exception.status_code, 404)
            attempts = sandbox.renew_count
            with self.assertRaises(SandboxLeaseLost):
                await backend.exec_shell(["echo", "ok"])

        self.assertEqual(sandbox.renew_count, attempts)
        self.assertTrue(workspace._lease_lost)

    async def test_missing_handle_is_not_reusable(self) -> None:
        """No sandbox object is conclusive, same as a 404."""
        workspace = OpenSandboxWorkspace(workspace_id="wid")
        self.assertIsNone(workspace._sandbox)
        self.assertFalse(await workspace.prepare_for_reuse())
        self.assertFalse(await workspace.prepare_for_reuse())

    async def test_cancelled_renewal_does_not_consume_the_budget(
        self,
    ) -> None:
        """Cancellation is not a finished attempt and not a lost lease."""
        clock = _Clock()
        workspace, sandbox, _backend = _workspace(clock)

        async def cancelled(timeout: timedelta) -> None:
            del timeout
            raise asyncio.CancelledError()

        sandbox.renew = cancelled  # type: ignore[method-assign]

        async def renew_again(timeout: timedelta) -> None:
            await _LeaseSandbox.renew(sandbox, timeout)

        with _patch_clock(clock):
            with self.assertRaises(asyncio.CancelledError):
                await workspace.prepare_for_reuse()
            self.assertIsNone(workspace._last_renew_attempt_at)
            self.assertFalse(workspace._lease_lost)
            sandbox.renew = renew_again  # type: ignore[method-assign]
            self.assertTrue(await workspace.prepare_for_reuse())

        self.assertEqual(sandbox.renew_count, 1)


class TestLeaseLessBackends(IsolatedAsyncioTestCase):
    """Local and Docker keep the default no-op hook."""

    async def test_default_hook_is_noop_without_a_sandbox(self) -> None:
        """The base contract needs no sandbox and always succeeds."""
        self.assertIs(
            LocalWorkspace.prepare_for_reuse,
            WorkspaceBase.prepare_for_reuse,
        )
        self.assertIs(
            DockerWorkspace.prepare_for_reuse,
            WorkspaceBase.prepare_for_reuse,
        )
        workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, workdir, True)
        local = LocalWorkspace(workdir=workdir)
        docker = DockerWorkspace()
        self.assertTrue(await local.prepare_for_reuse())
        self.assertTrue(await docker.prepare_for_reuse())
        self.assertTrue(await local.prepare_for_reuse())


class TestLeaseConfiguration(IsolatedAsyncioTestCase):
    """The longer-lease escape hatch stays, and the default does not move."""

    def test_default_timeout_is_unchanged(self) -> None:
        """The fix is renewal, not a longer default lease."""
        self.assertEqual(DEFAULT_TIMEOUT, 300)
        workspace = OpenSandboxWorkspace(workspace_id="wid")
        self.assertEqual(workspace.timeout_seconds, 300)
        self.assertEqual(workspace.renew_interval_seconds, 150)

    async def test_longer_lease_is_forwarded_to_renew(self) -> None:
        """``timeout_seconds`` still sets the lease ``renew`` requests."""
        clock = _Clock()
        workspace = OpenSandboxWorkspace(
            workspace_id="wid",
            timeout_seconds=7200,
        )
        sandbox = _LeaseSandbox(clock, 7200)
        workspace._sandbox = sandbox
        self.assertEqual(workspace.renew_interval_seconds, 3600)
        with _patch_clock(clock):
            self.assertTrue(await workspace.prepare_for_reuse())
        self.assertEqual(sandbox.renew_count, 1)
        self.assertEqual(sandbox.expires_at, 7200)


class TestManagerReusesHook(IsolatedAsyncioTestCase):
    """Cache access uses the same hook as in-turn renewal."""

    async def asyncSetUp(self) -> None:
        """Replace provisioning with local workspace objects."""
        self.workspaces: list[OpenSandboxWorkspace] = []
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_opensandbox_workspace_manager.OpenSandboxWorkspace",
            side_effect=self._make_workspace,
        )
        self.workspace_patch.start()
        self.addCleanup(self.workspace_patch.stop)

    def _make_workspace(self, **kwargs: object) -> OpenSandboxWorkspace:
        """Construct a workspace whose sandbox never leaves the test."""
        workspace = OpenSandboxWorkspace(**kwargs)  # type: ignore[arg-type]
        sandbox = SimpleNamespace(
            id=f"sandbox-{len(self.workspaces) + 1}",
            renew=AsyncMock(),
            pause=AsyncMock(),
            close=AsyncMock(),
        )
        workspace._sandbox = sandbox
        workspace.initialize = AsyncMock()  # type: ignore[method-assign]
        self.workspaces.append(workspace)
        return workspace

    async def test_cache_hit_renews_with_the_configured_lease(self) -> None:
        """A second entry renews once and keeps the same workspace."""
        manager = OpenSandboxWorkspaceManager(timeout_seconds=90)
        first = await manager.get_workspace("u", "a", "s1", "wid")
        sandbox = first._sandbox
        sandbox.renew.assert_not_awaited()

        second = await manager.get_workspace("u", "a", "s2", "wid")

        self.assertIs(first, second)
        self.assertEqual(first.timeout_seconds, 90)
        self.assertEqual(first.renew_interval_seconds, 45)
        sandbox.renew.assert_awaited_once_with(timedelta(seconds=90))

    async def test_transient_renewal_keeps_the_cached_workspace(self) -> None:
        """API and network failures do not replace a cache hit."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s1", "wid")
        sandbox = first._sandbox
        sandbox.renew.side_effect = TimeoutError("control plane unavailable")

        second = await manager.get_workspace("u", "a", "s2", "wid")

        self.assertIs(first, second)
        self.assertEqual(len(self.workspaces), 1)
        sandbox.close.assert_not_awaited()
        self.assertFalse(first._lease_lost)

    async def test_not_found_recreates_under_the_same_id(self) -> None:
        """A 404 drops the stale workspace and builds a replacement."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s1", "wid")
        sandbox = first._sandbox
        sandbox.renew.side_effect = _not_found()

        second = await manager.get_workspace("u", "a", "s2", "wid")

        self.assertIsNot(first, second)
        self.assertEqual(second.workspace_id, "wid")
        self.assertEqual(len(self.workspaces), 2)
        self.assertIs(manager._cache["wid"][0], second)
        sandbox.close.assert_awaited()
        self.assertIsNone(first._sandbox)

    async def test_cancelled_renewal_does_not_evict(self) -> None:
        """Cancellation leaves the cached workspace in place."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s1", "wid")
        first._sandbox.renew.side_effect = asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await manager.get_workspace("u", "a", "s2", "wid")

        self.assertIs(manager._cache["wid"][0], first)
        self.assertFalse(manager._lock.locked())
        first._sandbox.close.assert_not_awaited()

    async def test_create_wires_hook_without_an_extra_renew(self) -> None:
        """A new sandbox already holds its lease; the hook is attached."""
        clock = _Clock()
        workspace = OpenSandboxWorkspace(
            workspace_id="wid",
            timeout_seconds=60,
            renew_interval_seconds=30,
        )
        sandbox = _LeaseSandbox(clock, 60)
        workspace._find_existing_sandbox = AsyncMock(return_value=None)
        workspace._create_sandbox = AsyncMock(return_value=sandbox)
        workspace._wait_until_running = AsyncMock()
        with _patch_clock(clock):
            await workspace._provision_backend()
        self.assertEqual(sandbox.renew_count, 0)
        self.assertIsNotNone(workspace._last_renew_attempt_at)
        self.assertEqual(
            workspace._backend._prepare_for_reuse,
            workspace.prepare_for_reuse,
        )

    async def test_reattach_not_found_fails_before_the_workspace_is_cached(
        self,
    ) -> None:
        """A sandbox that 404s while reattaching is not left half-open."""
        clock = _Clock()
        workspace = OpenSandboxWorkspace(workspace_id="wid")
        sandbox = _LeaseSandbox(clock, 60)
        sandbox.reclaimed = True
        workspace._find_existing_sandbox = AsyncMock(
            return_value=SimpleNamespace(id="sandbox-1"),
        )
        workspace._attach_existing_sandbox = AsyncMock(return_value=sandbox)
        with _patch_clock(clock):
            with self.assertRaisesRegex(RuntimeError, "disappeared"):
                await workspace._provision_backend()
        self.assertIsNone(workspace._sandbox)
        self.assertIsNone(workspace._backend)

    async def test_explicit_interval_is_forwarded(self) -> None:
        """Managers pass the renewal interval through to the workspace."""
        manager = OpenSandboxWorkspaceManager(
            timeout_seconds=60,
            renew_interval_seconds=10,
        )
        workspace = await manager.get_workspace("u", "a", "s", "wid")
        self.assertEqual(workspace.timeout_seconds, 60)
        self.assertEqual(workspace.renew_interval_seconds, 10)


if __name__ == "__main__":
    unittest.main()
