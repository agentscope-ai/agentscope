# -*- coding: utf-8 -*-
"""Regression tests for :meth:`SessionService.get_session_status` scoping.

The session-run lock lives on the shared message bus and is keyed by
session id only, so consulting it before the user/agent-scoped storage
lookup reports ``RUNNING`` for sessions the caller does not own (or for
ids that do not exist at all). The storage lookup must come first.
"""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from agentscope.app._service._session import SessionService, SessionStatus


class _ScopedStorage:
    """Storage stub returning one session for one owner triple."""

    def __init__(self, session: object | None) -> None:
        self._session = session
        self.calls: list[tuple[str, str, str]] = []

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> object | None:
        """Return the staged record and remember the lookup scope."""
        self.calls.append((user_id, agent_id, session_id))
        return self._session


class _LockedBus:
    """Bus stub reporting a fixed run-lock state."""

    def __init__(self, locked: bool) -> None:
        self._locked = locked
        self.lock_checks = 0

    async def is_locked(self, _key: str) -> bool:
        """Record and return the staged lock state."""
        self.lock_checks += 1
        return self._locked


def _session(context: list | None = None) -> SimpleNamespace:
    """Build the minimal record shape ``derive_parked_status`` reads."""
    return SimpleNamespace(state=SimpleNamespace(context=context or []))


class SessionStatusScopeTest(IsolatedAsyncioTestCase):
    """Ownership is enforced before cluster liveness is reported."""

    async def test_unowned_locked_session_reports_not_found(self) -> None:
        """A locked session outside the caller's scope must not be RUNNING.

        The storage lookup is scoped by ``user_id`` / ``agent_id``; when
        it finds nothing the caller gets ``None`` (HTTP 404) regardless
        of who currently holds the run lock.
        """
        storage = _ScopedStorage(None)
        bus = _LockedBus(locked=True)
        service = SessionService(storage=storage, message_bus=bus)

        status = await service.get_session_status("user-b", "agent-b", "sid-1")

        self.assertIsNone(status)
        self.assertEqual(storage.calls, [("user-b", "agent-b", "sid-1")])
        self.assertEqual(
            bus.lock_checks,
            0,
            "The cluster run lock was consulted before the session was "
            "resolved, so an unowned/locked id reports RUNNING.",
        )

    async def test_owned_locked_session_reports_running(self) -> None:
        """A session the caller owns still reports RUNNING from the lock."""
        storage = _ScopedStorage(_session())
        bus = _LockedBus(locked=True)
        service = SessionService(storage=storage, message_bus=bus)

        status = await service.get_session_status("user-a", "agent-a", "sid-1")

        self.assertEqual(status, SessionStatus.RUNNING)
        self.assertEqual(bus.lock_checks, 1)

    async def test_owned_unlocked_session_derives_parked_status(self) -> None:
        """Without the lock the persisted context decides the status."""
        storage = _ScopedStorage(_session())
        bus = _LockedBus(locked=False)
        service = SessionService(storage=storage, message_bus=bus)

        status = await service.get_session_status("user-a", "agent-a", "sid-1")

        self.assertEqual(status, SessionStatus.IDLE)
