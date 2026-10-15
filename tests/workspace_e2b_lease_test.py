"""Lease renewal for long E2B turns (issue #3208, E2B backend wiring).

A turn longer than ``timeout_seconds`` previously lost its sandbox
mid-turn: E2B's SDK provides ``set_timeout()`` to extend the server-side
lease, but AgentScope never called it. These tests verify the new
``prepare_for_reuse`` hook: the first use after create/connect renews,
back-to-back uses inside the interval are bounded to one SDK call, a
conclusive ``NotFoundException`` marks the workspace unavailable, and
transient failures keep the existing connection.
"""
# pylint: disable=protected-access

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentscope.workspace._e2b._e2b_backend import E2BBackend
from agentscope.workspace._e2b._e2b_workspace import E2BWorkspace


def _fake_api_404() -> Exception:
    """Build the SDK's not-found exception shape without importing e2b."""
    exc = Exception("sandbox not found")
    exc.status_code = 404  # type: ignore[attr-defined]
    return exc


class _FakeSandbox:
    """Minimal stand-in for ``e2b.AsyncSandbox``.

    Only the members the renewal path touches are modeled:
    ``set_timeout`` (the lease primitive) and ``sandbox_id``.
    """

    def __init__(self, sandbox_id: str = "sbx-test") -> None:
        self.sandbox_id = sandbox_id
        self.set_timeout = AsyncMock()

    def fail_with_not_found(self) -> None:
        self.set_timeout = AsyncMock(side_effect=_fake_api_404())

    def fail_transiently(self) -> None:
        self.set_timeout = AsyncMock(side_effect=Exception("network down"))


class _Clock:
    """Monotonic stand-in. Tests advance it; nothing sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_workspace(
    lease_units: int = 3,
    clock: _Clock | None = None,
    stamp_lease: bool = False,
) -> tuple[E2BWorkspace, _FakeSandbox]:
    """Build an E2BWorkspace wired to a fake sandbox, no network.

    ``clock`` is injected before any lease stamping so bookkeeping and
    the renewal check read the same fake timeline. ``stamp_lease=True``
    mirrors a just-finished create/connect: the renewal timestamp is
    stamped so the first use skips renewal.
    """
    ws = E2BWorkspace(workspace_id="wid", timeout_seconds=lease_units)
    if clock is not None:
        ws._clock = clock
    ws._sandbox = _FakeSandbox()
    if stamp_lease:
        ws._note_lease_granted()
    return ws, ws._sandbox


class TestE2BLeaseRenewal(unittest.IsolatedAsyncioTestCase):
    """The piggyback contract on the E2B backend."""

    async def test_first_use_after_create_renews(self) -> None:
        """With the lease freshly stamped, the first use skips renewal;
        the second use (interval advanced) renews via set_timeout."""
        clock = _Clock()
        ws, sandbox = _make_workspace(lease_units=3, clock=clock, stamp_lease=True)
        ws._clock = clock

        await ws.prepare_for_reuse()  # interval not elapsed → skipped
        self.assertEqual(sandbox.set_timeout.await_count, 0)

        clock.advance(ws.renew_interval_seconds + 1)
        await ws.prepare_for_reuse()  # interval elapsed → renewed
        self.assertEqual(sandbox.set_timeout.await_count, 1)

    async def test_first_use_without_stamp_renews(self) -> None:
        """No fresh-lease stamp → the first use renews immediately."""
        clock = _Clock()
        ws, sandbox = _make_workspace(clock=clock)
        ws._clock = clock

        self.assertTrue(await ws.prepare_for_reuse())
        sandbox.set_timeout.assert_awaited_once_with(3)

    async def test_not_found_marks_lease_lost(self) -> None:
        """A 404-shaped failure is conclusive: no SDK retry, hook False."""
        clock = _Clock()
        ws, sandbox = _make_workspace(clock=clock)
        ws._clock = clock
        sandbox.fail_with_not_found()

        self.assertFalse(await ws.prepare_for_reuse())
        self.assertTrue(ws._lease_lost)
        # A later call short-circuits without another SDK round-trip.
        self.assertFalse(await ws.prepare_for_reuse())
        self.assertEqual(sandbox.set_timeout.await_count, 1)

    async def test_transient_failure_keeps_connection(self) -> None:
        """A non-404 failure is not conclusive: hook True, retry allowed."""
        clock = _Clock()
        ws, sandbox = _make_workspace(clock=clock)
        ws._clock = clock
        sandbox.fail_transiently()

        self.assertTrue(await ws.prepare_for_reuse())
        self.assertFalse(ws._lease_lost)
        # A later attempt is not blocked by the interval.
        clock.advance(ws.renew_interval_seconds + 1)
        self.assertTrue(await ws.prepare_for_reuse())
        self.assertEqual(sandbox.set_timeout.await_count, 2)

    async def test_teardown_resets_renewal_state(self) -> None:
        """After teardown the bookkeeping is clean for a fresh attach."""
        ws, _ = _make_workspace()
        await ws._teardown_backend()
        self.assertIsNone(ws._last_renew_attempt_at)
        self.assertFalse(ws._lease_lost)


class _FakeCommandsSandbox(_FakeSandbox):
    """Adds the ``commands``/``files`` surface the backend primitives use."""

    def __init__(self, sandbox_id: str = "sbx-test") -> None:
        super().__init__(sandbox_id)
        run_result = SimpleNamespace(exit_code=0, stdout="ok", stderr="")
        self.commands = SimpleNamespace(run=AsyncMock(return_value=run_result))
        self.files = SimpleNamespace(
            read=AsyncMock(return_value=b"data"),
            write=AsyncMock(return_value=None),
        )


class TestE2BBackendRenewWiring(unittest.IsolatedAsyncioTestCase):
    """The exec path must actually invoke the renewal hook (PR #3285)."""

    @staticmethod
    def _make_backend(hook: AsyncMock) -> E2BBackend:
        return E2BBackend(
            _FakeCommandsSandbox(), workdir="/w", renew_hook=hook
        )

    async def test_primitives_invoke_renew_hook(self) -> None:
        """exec_shell / read_file / write_file each renew before use."""
        hook = AsyncMock(return_value=True)
        backend = self._make_backend(hook)

        await backend.exec_shell(["echo", "hi"])
        await backend.read_file("/w/f.txt")
        await backend.write_file("f.txt", b"x")

        self.assertEqual(hook.call_count, 3)
        backend._sandbox.commands.run.assert_awaited()

    async def test_exec_fails_fast_when_lease_lost(self) -> None:
        """Hook reporting False raises instead of hitting a dead sandbox."""
        hook = AsyncMock(return_value=False)
        backend = self._make_backend(hook)

        with self.assertRaises(RuntimeError):
            await backend.exec_shell(["echo", "hi"])
        backend._sandbox.commands.run.assert_not_called()

    async def test_provision_backend_binds_renew_hook(self) -> None:
        """_provision_backend hands prepare_for_reuse to the backend."""
        ws, _ = _make_workspace()
        ws._attach_or_create_sandbox = AsyncMock()

        await ws._provision_backend()

        self.assertEqual(ws._backend._renew_hook, ws.prepare_for_reuse)


if __name__ == "__main__":
    unittest.main()
